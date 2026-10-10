"""
Outgoing WhatsApp messages that carry money information.

Each bill/receipt message has its own row in `notifications`
(ref_id + kind). A message marked 'sent' is never sent again, so
retrying a failed admin request only sends what is still missing.
A QR is generated only from the invoice's current verified due amount.

Kapso accepting a message is not the same as WhatsApp delivering it:
WhatsApp can reject media afterwards. The WhatsApp message id is stored
so a later `whatsapp.message.failed` webhook can mark the row failed
(see `mark_failed_by_provider_id`), after which a retry resends it.
"""
import logging
from datetime import timedelta

import response
from kapso_client import send_whatsapp_image, send_whatsapp_message
from src import shop
from src.db import now, transaction
from src.payments import generate_payment_qr

# A 'sending' row older than this is assumed to belong to a crashed request.
STALE_SENDING = timedelta(minutes=2)

log = logging.getLogger(__name__)


class NotificationError(Exception):
    def __init__(self, kind):
        super().__init__(f"Could not send {kind}")
        self.kind = kind


class NotificationBusy(Exception):
    pass


def _claim(conn, ref_id, kind, customer):
    with transaction(conn):
        row = conn.execute(
            "SELECT status, updated_at FROM notifications WHERE ref_id = ? AND kind = ?",
            (ref_id, kind),
        ).fetchone()

        if row is None:
            conn.execute(
                "INSERT INTO notifications (ref_id, kind, customer, status, attempts,"
                " updated_at) VALUES (?, ?, ?, 'sending', 1, ?)",
                (ref_id, kind, customer, now()),
            )
            return True

        if row["status"] in ("sent", "skipped"):
            return False

        if row["status"] == "sending" and not shop._older_than(row["updated_at"], STALE_SENDING):
            raise NotificationBusy(f"{kind} for {ref_id} is being sent right now")

        conn.execute(
            "UPDATE notifications SET status = 'sending', attempts = attempts + 1,"
            " updated_at = ? WHERE ref_id = ? AND kind = ?",
            (now(), ref_id, kind),
        )
        return True


def _finish(conn, ref_id, kind, status, amount_paise=None, error=None,
            provider_message_id=None):
    with transaction(conn):
        conn.execute(
            "UPDATE notifications SET status = ?, amount_paise = ?, error = ?,"
            " provider_message_id = ?,"
            " updated_at = ?, sent_at = CASE WHEN ? = 'sent' THEN ? ELSE sent_at END"
            " WHERE ref_id = ? AND kind = ?",
            (status, amount_paise, error, provider_message_id, now(), status, now(),
             ref_id, kind),
        )


def _provider_id(result):
    try:
        return result["messages"][0]["id"]
    except (TypeError, KeyError, IndexError):
        return None


def _deliver(conn, ref_id, kind, customer, prepare):
    """
    prepare() returns None to skip this message, or (amount_paise, send).
    It runs after the claim, so it always sees current ledger state.
    """
    if not _claim(conn, ref_id, kind, customer):
        return

    try:
        plan = prepare()
        if plan is None:
            _finish(conn, ref_id, kind, "skipped")
            return
        amount_paise, send = plan
        result = send()
    except Exception as e:
        # Keep the error short; it never contains request headers or keys.
        error = f"{type(e).__name__}: {e}"[:300]
        log.warning("WhatsApp %s for %s failed: %s", kind, ref_id, error)
        _finish(conn, ref_id, kind, "failed", error=error)
        raise NotificationError(kind) from e

    _finish(conn, ref_id, kind, "sent", amount_paise=amount_paise,
            provider_message_id=_provider_id(result))


def _qr_due(conn, invoice_id):
    """Amount a QR may ask for: current due of an undisputed invoice, else None."""
    s = shop.invoice_summary(conn, invoice_id)
    if s["disputed"] or s["due_paise"] <= 0:
        return None
    return s["due_paise"]


def _qr_plan(conn, invoice_id, customer):
    due = _qr_due(conn, invoice_id)
    if due is None:
        return None
    qr = generate_payment_qr(amount=shop.paise_to_decimal(due), invoice_ref=invoice_id)
    return due, lambda: send_whatsapp_image(
        to=customer,
        image_path=qr["qr_path"],
        caption=response.qr_caption(invoice_id, due),
    )


def _status(conn, ref_id, kind):
    return shop.notification_states(conn, ref_id).get(kind)


def _qr_then_text(conn, ref_id, qr_kind, text_kind, customer, qr_prepare, text_prepare):
    """
    Send the QR first, then the text. The text mentions the QR only if the
    QR send succeeded. A QR failure is raised after the text has gone out,
    so the shop sees it and can retry just the QR.
    """
    qr_error = None
    try:
        _deliver(conn, ref_id, qr_kind, customer, qr_prepare)
    except NotificationError as e:
        qr_error = e

    _deliver(conn, ref_id, text_kind, customer,
             lambda: text_prepare(_status(conn, ref_id, qr_kind) == "sent"))
    if qr_error:
        raise qr_error
    return shop.notification_states(conn, ref_id)


def send_bill(conn, invoice_id):
    """UPI QR for what is still due on this invoice, then the bill text."""
    customer = shop.invoice_summary(conn, invoice_id)["customer"]

    def bill_text(qr_sent):
        s = shop.invoice_summary(conn, invoice_id)
        body = response.bill_message(s, qr_sent=qr_sent)
        return None, lambda: send_whatsapp_message(to=customer, body=body)

    return _qr_then_text(conn, invoice_id, "bill_qr", "bill_text", customer,
                         lambda: _qr_plan(conn, invoice_id, customer), bill_text)


def send_payment_receipt(conn, payment_id):
    """Receipt text, then a QR for the remaining due of the invoice this payment touched."""
    payment = conn.execute(
        "SELECT customer, amount_paise FROM payments WHERE payment_id = ?", (payment_id,),
    ).fetchone()
    customer = payment["customer"]

    last = conn.execute(
        "SELECT invoice_id FROM allocations WHERE payment_id = ? ORDER BY id DESC LIMIT 1",
        (payment_id,),
    ).fetchone()
    qr_invoice = last["invoice_id"] if last else None

    def receipt_text(qr_sent):
        ledger = shop.customer_ledger(conn, customer)
        body = response.payment_message(payment["amount_paise"], ledger, qr_sent=qr_sent)
        return None, lambda: send_whatsapp_message(to=customer, body=body)

    return _qr_then_text(
        conn, payment_id, "payment_qr", "payment_text", customer,
        lambda: _qr_plan(conn, qr_invoice, customer) if qr_invoice else None,
        receipt_text,
    )


def mark_failed_by_provider_id(conn, provider_message_id, error):
    """WhatsApp rejected a message after Kapso accepted it. Returns True if it was ours."""
    with transaction(conn):
        row = conn.execute(
            "SELECT ref_id, kind FROM notifications WHERE provider_message_id = ?",
            (provider_message_id,),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE notifications SET status = 'failed', error = ?, updated_at = ?"
            " WHERE provider_message_id = ?",
            (error[:300], now(), provider_message_id),
        )
    log.warning("WhatsApp rejected %s for %s: %s", row["kind"], row["ref_id"], error[:300])
    return True


def reopen_skipped_qr(conn, invoice_id):
    """After a dispute is resolved, allow the held-back QR to go out."""
    with transaction(conn):
        conn.execute(
            "UPDATE notifications SET status = 'pending', updated_at = ?"
            " WHERE ref_id = ? AND kind = 'bill_qr' AND status = 'skipped'",
            (now(), invoice_id),
        )


def send_reply(conn, customer, body):
    """Plain chat reply. Returns True if WhatsApp accepted it."""
    try:
        send_whatsapp_message(to=customer, body=body)
    except Exception as e:
        with transaction(conn):
            shop.record_outbound(conn, customer, body, "failed", f"{type(e).__name__}"[:100])
        return False
    with transaction(conn):
        shop.record_outbound(conn, customer, body, "sent")
    return True
