
import hmac
import json
import os
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import agent
import response
from src import db, notify, shop

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI()


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def load_prices():
    with open(BASE_DIR / "prices.json", encoding="utf-8") as f:
        return json.load(f)


def normalize_phone(phone):
    return "".join(ch for ch in str(phone) if ch.isdigit())


def require_admin(x_shop_admin_token: Optional[str] = Header(None)):
    # Require a configured secret; fail closed if it is missing.
    expected = os.getenv("SHOP_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="SHOP_ADMIN_TOKEN is not configured")
    if not hmac.compare_digest(x_shop_admin_token or "", expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def shop_error(e):
    return HTTPException(status_code=e.status, detail=e.detail)


@app.get("/")
def home():
    return {"status": "WhatsApp agent is running"}


# -------------------------
# Customer messages (untrusted)
# -------------------------

@app.post("/webhook")
def webhook(
    payload: dict = Body(...),
    x_webhook_event: Optional[str] = Header(None),
    conn=Depends(get_conn),
):
    message = payload.get("message") or {}

    if x_webhook_event == "whatsapp.message.failed":
        return delivery_failed(conn, message)

    if x_webhook_event != "whatsapp.message.received":
        return {"status": "ignored", "event": x_webhook_event}

    if (message.get("kapso") or {}).get("direction") != "inbound":
        return {"status": "ignored", "reason": "not inbound"}

    if message.get("type") != "text":
        return {"status": "ignored", "reason": "not text"}

    sender = normalize_phone(message.get("from", ""))
    message_id = message.get("id")
    body = ((message.get("text") or {}).get("body") or "").strip()

    if not sender or not message_id or not body:
        raise HTTPException(status_code=400, detail="Missing message data")

    if not shop.claim_inbound(conn, message_id, sender, body):
        return {"status": "duplicate"}

    try:
        timestamp = int(message.get("timestamp") or 0) or None
        date = datetime.fromtimestamp(
            timestamp, tz=timezone.utc,
        ) if timestamp else datetime.now(timezone.utc)

        history = shop.conversation_context(conn, sender, exclude_message_id=message_id)
        events = agent.extract_customer_events(
            body, date.strftime("%Y-%m-%d %H:%M"), history,
        )
        outcomes = shop.apply_customer_events(
            conn, sender, message_id, events, load_prices(),
        )
    except Exception:
        # Nothing was committed for this message; let Kapso retry it.
        shop.release_inbound(conn, message_id)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Could not process message")

    print(f"Processed message {message_id}: {[o['kind'] for o in outcomes]}")

    # The message is already stored; a failed reply is logged, not retried
    # by Kapso (that would be dropped as a duplicate anyway).
    reply_sent = notify.send_reply(conn, sender, response.customer_reply(outcomes))

    return {
        "status": "processed",
        "reply_sent": reply_sent,
        "orders_created": [o["order_id"] for o in outcomes if o["kind"] == "order_created"],
    }


def delivery_failed(conn, message):
    """WhatsApp rejected one of our outgoing messages (e.g. an invalid image)."""
    provider_id = message.get("id")
    if not provider_id:
        return {"status": "ignored", "reason": "no message id"}

    errors = []
    for status in (message.get("kapso") or {}).get("statuses") or []:
        errors += status.get("errors") or []
    errors += message.get("errors") or []
    error = "; ".join(
        f"{e.get('code')} {e.get('title')}: {(e.get('error_data') or {}).get('details', '')}"
        for e in errors
    ) or "WhatsApp reported the message as failed"

    if notify.mark_failed_by_provider_id(conn, provider_id, error):
        return {"status": "recorded_failure"}
    return {"status": "ignored", "reason": "not a tracked notification"}


# -------------------------
# Shop actions (authenticated)
# -------------------------

class DeliveryConfirmation(BaseModel):
    order_id: str
    customer: Optional[str] = None
    delivered_by: Optional[str] = None
    note: Optional[str] = None


class PaymentConfirmation(BaseModel):
    customer: str
    amount: Decimal
    reference: str
    invoice_id: Optional[str] = None
    method: Optional[str] = "UPI"
    verified_by: Optional[str] = None
    note: Optional[str] = None


class DisputeResolution(BaseModel):
    invoice_id: str
    note: Optional[str] = None


def _send_or_502(send, conn, ref_id, payload):
    """Run a notify.send_* call; on failure the saved state stays and a retry is safe."""
    try:
        payload["notifications"] = send(conn, ref_id)
    except notify.NotificationBusy as e:
        raise HTTPException(status_code=409, detail=str(e))
    except notify.NotificationError as e:
        traceback.print_exc()
        payload["notifications"] = shop.notification_states(conn, ref_id)
        payload["detail"] = (
            f"Saved, but WhatsApp {e.kind} failed. "
            "Repeat the same request to retry; nothing will be billed twice."
        )
        return JSONResponse(status_code=502, content=_jsonable(payload))
    return _jsonable(payload)


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


@app.post("/admin/confirm-delivery", dependencies=[Depends(require_admin)])
def confirm_delivery(data: DeliveryConfirmation, conn=Depends(get_conn)):
    customer = normalize_phone(data.customer) if data.customer else None

    try:
        invoice_id, created = shop.confirm_delivery(
            conn, data.order_id.strip(), load_prices(),
            customer=customer, delivered_by=data.delivered_by, note=data.note,
        )
    except shop.ShopError as e:
        raise shop_error(e)

    s = shop.invoice_summary(conn, invoice_id)
    payload = {
        "status": "confirmed" if created else "already_confirmed",
        "order_id": s["order_id"],
        "invoice_id": invoice_id,
        "bill_amount": shop.paise_to_decimal(s["amount_paise"]),
        "previously_verified": shop.paise_to_decimal(s["paid_paise"]),
        "amount_due": shop.paise_to_decimal(s["due_paise"]),
        "disputed": s["disputed"],
    }
    return _send_or_502(notify.send_bill, conn, invoice_id, payload)


@app.post("/admin/confirm-payment", dependencies=[Depends(require_admin)])
def confirm_payment(data: PaymentConfirmation, conn=Depends(get_conn)):
    customer = normalize_phone(data.customer)
    if not customer or not data.reference.strip():
        raise HTTPException(status_code=400, detail="customer and reference are required")

    try:
        amount_paise = shop.to_paise(data.amount)
        payment_id, created = shop.record_payment(
            conn, customer, amount_paise, data.reference,
            invoice_id=data.invoice_id, method=data.method,
            verified_by=data.verified_by, note=data.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except shop.ShopError as e:
        raise shop_error(e)

    ledger = shop.customer_ledger(conn, customer)
    payload = {
        "status": "recorded" if created else "already_recorded",
        "payment_id": payment_id,
        "amount": shop.paise_to_decimal(amount_paise),
        "total_billed": ledger["total_billed"],
        "total_verified_payments": ledger["total_verified_payments"],
        "remaining_balance": ledger["remaining_balance"],
        "available_credit": ledger["available_credit"],
        "payment_status": ledger["payment_status"],
    }
    return _send_or_502(notify.send_payment_receipt, conn, payment_id, payload)


@app.post("/admin/resolve-dispute", dependencies=[Depends(require_admin)])
def resolve_dispute(data: DisputeResolution, conn=Depends(get_conn)):
    try:
        shop.resolve_dispute(conn, data.invoice_id, data.note)
    except shop.ShopError as e:
        raise shop_error(e)

    notify.reopen_skipped_qr(conn, data.invoice_id)
    return _send_or_502(notify.send_bill, conn, data.invoice_id,
                        {"status": "resolved", "invoice_id": data.invoice_id})


@app.get("/admin/orders/{order_id}", dependencies=[Depends(require_admin)])
def get_order(order_id: str, conn=Depends(get_conn)):
    try:
        return _jsonable(shop.order_details(conn, order_id))
    except shop.ShopError as e:
        raise shop_error(e)


@app.get("/admin/customers/{phone}/ledger", dependencies=[Depends(require_admin)])
def get_ledger(phone: str, conn=Depends(get_conn)):
    customer = normalize_phone(phone)
    if conn.execute("SELECT 1 FROM customers WHERE phone = ?", (customer,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return _jsonable(shop.customer_ledger(conn, customer))
