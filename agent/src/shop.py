"""
Order, invoice, payment and ledger service.

Trust boundaries:
  - Orders, cancellations, claims and disputes may come from customer chat.
  - Delivery and payments come ONLY from authenticated shop actions
    (`confirm_delivery`, `record_payment`). Nothing here reads LLM output
    to decide that goods were delivered or money was received.

All money is integer paise; all arithmetic is plain Python.
"""
import secrets
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .db import now, transaction
from .validator import build_alias_map, normalize_order_items

STALE_PROCESSING = timedelta(minutes=5)


class ShopError(Exception):
    """A request the shop cannot perform; carries an HTTP status."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# -------------------------
# Money helpers
# -------------------------

def to_paise(amount):
    """Positive rupee amount with at most 2 decimals -> int paise."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        raise ValueError("amount must be a number")
    if not value.is_finite() or value <= 0:
        raise ValueError("amount must be greater than zero")
    if value != value.quantize(Decimal("0.01")):
        raise ValueError("amount can have at most 2 decimal places")
    return int(value * 100)


def rupees(paise):
    value = Decimal(paise) / 100
    if value == value.to_integral_value():
        return f"₹{int(value)}"
    return f"₹{value:.2f}"


def paise_to_decimal(paise):
    return (Decimal(paise) / 100).quantize(Decimal("0.01"))


def line_total_paise(unit_price, qty):
    price_paise = Decimal(str(unit_price)) * 100
    total = (price_paise * Decimal(str(qty))).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP,
    )
    return int(price_paise), int(total)


# -------------------------
# Customers and messages
# -------------------------

def ensure_customer(conn, phone):
    conn.execute(
        "INSERT OR IGNORE INTO customers (phone, created_at) VALUES (?, ?)",
        (phone, now()),
    )


def claim_inbound(conn, message_id, customer, body):
    """
    Record an inbound message. Returns False if it was already seen.

    A message stuck in 'processing' (crash mid-way) is handed out again
    after STALE_PROCESSING; everything it writes is idempotent.
    """
    with transaction(conn):
        ensure_customer(conn, customer)
        row = conn.execute(
            "SELECT status, created_at FROM messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()

        if row is None:
            conn.execute(
                "INSERT INTO messages (message_id, customer, direction, body,"
                " status, created_at) VALUES (?, ?, 'inbound', ?, 'processing', ?)",
                (message_id, customer, body, now()),
            )
            return True

        if row["status"] == "processing" and _older_than(row["created_at"], STALE_PROCESSING):
            conn.execute(
                "UPDATE messages SET created_at = ? WHERE message_id = ?",
                (now(), message_id),
            )
            return True

        return False


def release_inbound(conn, message_id):
    """Forget a message whose processing failed, so a retry can redo it."""
    with transaction(conn):
        conn.execute(
            "DELETE FROM messages WHERE message_id = ? AND status = 'processing'",
            (message_id,),
        )


def record_outbound(conn, customer, body, status, error=None):
    conn.execute(
        "INSERT INTO messages (customer, direction, body, status, error, created_at)"
        " VALUES (?, 'outbound', ?, ?, ?, ?)",
        (customer, body, status, error, now()),
    )


def conversation_context(conn, customer, exclude_message_id, limit=10):
    rows = conn.execute(
        "SELECT direction, body, created_at FROM messages"
        " WHERE customer = ? AND (message_id IS NULL OR message_id != ?)"
        " AND (direction = 'outbound' OR status = 'processed')"
        " ORDER BY id DESC LIMIT ?",
        (customer, exclude_message_id, limit),
    ).fetchall()
    lines = []
    for r in reversed(rows):
        who = "Customer" if r["direction"] == "inbound" else "Assistant"
        lines.append(f"[{r['created_at'][:16].replace('T', ' ')}] {who}: {r['body']}")
    return "\n".join(lines)


def _older_than(timestamp, delta):
    then = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - then > delta


# -------------------------
# Customer chat events
# -------------------------

def apply_customer_events(conn, customer, message_id, events, prices):
    """
    Apply events extracted from ONE customer message, atomically, and
    mark the message processed. Returns a list of outcome dicts used to
    build the reply.

    DELIVERED and PAYMENT events from a customer are stored as unverified
    claims only.
    """
    outcomes = []

    with transaction(conn):
        ensure_customer(conn, customer)

        for seq, event in enumerate(events):
            kind = event.get("type")

            if kind == "ORDER":
                outcomes.append(_customer_order(conn, customer, message_id, seq, event, prices))

            elif kind == "DELIVERED":
                _add_claim(conn, customer, message_id, "DELIVERY_CLAIM", None)
                outcomes.append({"kind": "delivery_claim"})

            elif kind == "PAYMENT":
                amount = _claim_amount(event.get("amount"))
                _add_claim(conn, customer, message_id, "PAYMENT_CLAIM", amount)
                outcomes.append({"kind": "payment_claim", "amount_paise": amount})

            elif kind == "PROMISE":
                amount = _claim_amount(event.get("amount"))
                _add_claim(conn, customer, message_id, "PROMISE", amount)
                outcomes.append({"kind": "promise", "amount_paise": amount})

            elif kind == "CANCEL":
                outcomes.append(_customer_cancel(conn, customer, message_id))

            elif kind == "DISPUTE":
                outcomes.append(_customer_dispute(conn, customer, message_id, event))

            elif kind == "SUBSTITUTE":
                aliases = build_alias_map(prices)
                src = aliases.get((event.get("from_item") or "").lower(), event.get("from_item"))
                dst = aliases.get((event.get("to_item") or "").lower(), event.get("to_item"))
                _add_flag(conn, customer, f"customer accepted substitution {src} -> {dst};"
                          " shop must update the order", source_message_id=message_id)
                outcomes.append({"kind": "substitute_review"})

            elif kind == "ADJUST_CREDIT":
                # Credit is applied automatically when an invoice is created.
                outcomes.append({"kind": "credit_note"})

        conn.execute(
            "UPDATE messages SET status = 'processed' WHERE message_id = ?",
            (message_id,),
        )

    return outcomes


def _claim_amount(amount):
    try:
        return to_paise(amount)
    except ValueError:
        return None


def _customer_order(conn, customer, message_id, seq, event, prices):
    items, problems = normalize_order_items(event.get("items") or [], prices)
    if problems:
        return {"kind": "clarify", "problems": problems}

    existing = conn.execute(
        "SELECT order_id FROM orders WHERE source_message_id = ? AND seq = ?",
        (message_id, seq),
    ).fetchone()
    if existing:
        order_id = existing["order_id"]
    else:
        order_id = "ORD-" + secrets.token_hex(4).upper()
        conn.execute(
            "INSERT INTO orders (order_id, customer, source_message_id, seq, status,"
            " created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
            (order_id, customer, message_id, seq, now()),
        )
        conn.executemany(
            "INSERT INTO order_items (order_id, line_no, item, qty, unit)"
            " VALUES (?, ?, ?, ?, ?)",
            [(order_id, n, i["item"], str(i["qty"]), i["unit"]) for n, i in enumerate(items)],
        )

    return {"kind": "order_created", "order_id": order_id, "items": items}


def _customer_cancel(conn, customer, message_id):
    pending = conn.execute(
        "SELECT order_id FROM orders WHERE customer = ? AND status = 'pending'",
        (customer,),
    ).fetchall()

    if len(pending) == 1:
        order_id = pending[0]["order_id"]
        conn.execute(
            "UPDATE orders SET status = 'cancelled', cancelled_at = ? WHERE order_id = ?",
            (now(), order_id),
        )
        return {"kind": "cancelled", "order_id": order_id}

    if len(pending) > 1:
        _add_flag(conn, customer, "customer asked to cancel but has several pending orders",
                  source_message_id=message_id)
        return {"kind": "cancel_unclear", "order_ids": [r["order_id"] for r in pending]}

    return {"kind": "cancel_nothing"}


def _customer_dispute(conn, customer, message_id, event):
    note = event.get("note") or "customer disputed delivery"
    invoice = conn.execute(
        "SELECT invoice_id FROM invoices WHERE customer = ?"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (customer,),
    ).fetchone()

    invoice_id = invoice["invoice_id"] if invoice else None
    if invoice_id:
        # Hold payment requests until the shop looks at it.
        conn.execute("UPDATE invoices SET disputed = 1 WHERE invoice_id = ?", (invoice_id,))
    _add_flag(conn, customer, f"dispute: {note}", invoice_id=invoice_id,
              source_message_id=message_id)
    return {"kind": "dispute", "invoice_id": invoice_id}


def _add_claim(conn, customer, message_id, kind, amount_paise):
    conn.execute(
        "INSERT OR IGNORE INTO claims (customer, message_id, kind, amount_paise, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (customer, message_id, kind, amount_paise, now()),
    )


def _add_flag(conn, customer, reason, order_id=None, invoice_id=None, source_message_id=None):
    conn.execute(
        "INSERT OR IGNORE INTO review_flags (customer, order_id, invoice_id, reason,"
        " source_message_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (customer, order_id, invoice_id, reason, source_message_id, now()),
    )


# -------------------------
# Shop action: delivery
# -------------------------

def confirm_delivery(conn, order_id, prices, customer=None, delivered_by=None, note=None):
    """
    Trusted shop action. Marks one order delivered and creates its single
    invoice. Safe to call again: an already-delivered order returns its
    existing invoice and nothing new is billed.

    Returns (invoice_id, created: bool).
    """
    invoice_id, outcome = _deliver_order(conn, order_id, prices, customer, delivered_by, note)
    if invoice_id is None:
        raise ShopError(422, outcome)
    return invoice_id, outcome is None


def _deliver_order(conn, order_id, prices, customer, delivered_by, note):
    """Returns (invoice_id, None) when billed now, (invoice_id, "already_delivered"),
    or (None, refusal_reason) after flagging the order for review."""
    with transaction(conn):
        order = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (order_id,),
        ).fetchone()

        if order is None:
            raise ShopError(404, "Order not found")
        if customer and order["customer"] != customer:
            raise ShopError(409, "Order does not belong to this customer")
        if order["status"] == "cancelled":
            raise ShopError(409, "Order was cancelled; it cannot be delivered")

        if order["status"] == "delivered":
            invoice = conn.execute(
                "SELECT invoice_id FROM invoices WHERE order_id = ?", (order_id,),
            ).fetchone()
            return invoice["invoice_id"], "already_delivered"

        items = conn.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY line_no", (order_id,),
        ).fetchall()

        lines, problems = [], []
        for it in items:
            info = prices.get(it["item"])
            qty = Decimal(it["qty"])
            if info is None:
                problems.append(f"{it['item']} is no longer in prices.json")
            elif info["unit"] != it["unit"]:
                problems.append(f"{it['item']} unit changed from {it['unit']} to {info['unit']}")
            elif qty <= 0:
                problems.append(f"{it['item']} has invalid quantity {it['qty']}")
            else:
                unit_price, total = line_total_paise(info["price"], qty)
                lines.append((it, unit_price, total))

        if problems or not lines:
            # Keep the review flag (committed), but bill nothing.
            refusal = "cannot bill order: " + ("; ".join(problems) or "no items")
            _add_flag(conn, order["customer"], refusal, order_id=order_id)
            return None, refusal

        invoice_id = "INV-" + order_id[len("ORD-"):]
        amount = sum(total for _, _, total in lines)

        conn.execute(
            "UPDATE orders SET status = 'delivered', delivered_at = ?, delivered_by = ?,"
            " delivery_note = ? WHERE order_id = ?",
            (now(), delivered_by, note, order_id),
        )
        conn.execute(
            "INSERT INTO invoices (invoice_id, order_id, customer, amount_paise, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (invoice_id, order_id, order["customer"], amount, now()),
        )
        conn.executemany(
            "INSERT INTO invoice_items (invoice_id, line_no, item, qty, unit,"
            " unit_price_paise, line_total_paise) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(invoice_id, it["line_no"], it["item"], it["qty"], it["unit"], up, tot)
             for it, up, tot in lines],
        )

        # Existing credit (verified money not yet used) pays this bill first.
        _apply_credit(conn, order["customer"], invoice_id, amount)

    return invoice_id, None


def _apply_credit(conn, customer, invoice_id, due):
    for p in _payments_with_unallocated(conn, customer):
        if due <= 0:
            break
        use = min(p["free"], due)
        _allocate(conn, p["payment_id"], invoice_id, use)
        due -= use


def _payments_with_unallocated(conn, customer):
    rows = conn.execute(
        "SELECT p.payment_id, p.amount_paise - COALESCE(SUM(a.amount_paise), 0) AS free"
        " FROM payments p LEFT JOIN allocations a ON a.payment_id = p.payment_id"
        " WHERE p.customer = ? GROUP BY p.payment_id"
        " ORDER BY p.verified_at, p.rowid",
        (customer,),
    ).fetchall()
    return [r for r in rows if r["free"] > 0]


def _allocate(conn, payment_id, invoice_id, amount):
    conn.execute(
        "INSERT INTO allocations (payment_id, invoice_id, amount_paise, created_at)"
        " VALUES (?, ?, ?, ?)",
        (payment_id, invoice_id, amount, now()),
    )


# -------------------------
# Shop action: verified payment
# -------------------------

def record_payment(conn, customer, amount_paise, reference, invoice_id=None,
                   method=None, verified_by=None, note=None):
    """
    Trusted shop action: record money the shop has actually received.

    Allocation:
      - invoice_id given: that invoice first, then other open invoices
        (oldest first); anything left is credit.
      - no invoice_id: if the money covers every open invoice, or only one
        is open, allocate oldest first; otherwise the allocation is
        ambiguous and the shop must name the invoice.
    Disputed invoices are only paid when named explicitly.

    The reference (UPI transaction id) makes this idempotent.
    Returns (payment_id, created: bool).
    """
    reference = reference.strip()

    with transaction(conn):
        existing = conn.execute(
            "SELECT * FROM payments WHERE reference = ?", (reference,),
        ).fetchone()
        if existing:
            same = (existing["customer"] == customer
                    and existing["amount_paise"] == amount_paise
                    and (invoice_id is None or existing["invoice_id"] == invoice_id))
            if not same:
                raise ShopError(409, "This payment reference is already recorded with different details")
            return existing["payment_id"], False

        if conn.execute("SELECT 1 FROM customers WHERE phone = ?", (customer,)).fetchone() is None:
            raise ShopError(404, "Customer not found")

        open_invoices = [i for i in _invoice_rows(conn, customer) if i["due_paise"] > 0]
        targets = [i for i in open_invoices if not i["disputed"]]

        if invoice_id:
            chosen = next((i for i in open_invoices if i["invoice_id"] == invoice_id), None)
            if chosen is None:
                inv = conn.execute(
                    "SELECT customer FROM invoices WHERE invoice_id = ?", (invoice_id,),
                ).fetchone()
                if inv is None or inv["customer"] != customer:
                    raise ShopError(404, "Invoice not found for this customer")
                raise ShopError(409, "Invoice is already fully paid")
            targets = [chosen] + [i for i in targets if i["invoice_id"] != invoice_id]
        elif len(targets) > 1 and amount_paise < sum(i["due_paise"] for i in targets):
            raise ShopError(409, "Several invoices are open; specify invoice_id for this payment")

        payment_id = "PAY-" + secrets.token_hex(4).upper()
        conn.execute(
            "INSERT INTO payments (payment_id, customer, amount_paise, reference, method,"
            " invoice_id, verified_by, note, verified_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (payment_id, customer, amount_paise, reference, method, invoice_id,
             verified_by, note, now()),
        )

        left = amount_paise
        for inv in targets:
            if left <= 0:
                break
            use = min(left, inv["due_paise"])
            _allocate(conn, payment_id, inv["invoice_id"], use)
            left -= use
        # Whatever is left stays unallocated: that is the customer's credit.

    return payment_id, True


def resolve_dispute(conn, invoice_id, note=None):
    with transaction(conn):
        inv = conn.execute(
            "SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,),
        ).fetchone()
        if inv is None:
            raise ShopError(404, "Invoice not found")
        if not inv["disputed"]:
            raise ShopError(409, "Invoice is not disputed")
        conn.execute("UPDATE invoices SET disputed = 0 WHERE invoice_id = ?", (invoice_id,))
        conn.execute(
            "UPDATE review_flags SET resolved_at = ?, resolution_note = ?"
            " WHERE invoice_id = ? AND resolved_at IS NULL",
            (now(), note, invoice_id),
        )


# -------------------------
# Ledger (read side)
# -------------------------

def _invoice_rows(conn, customer):
    rows = conn.execute(
        "SELECT i.invoice_id, i.order_id, i.amount_paise, i.disputed, i.created_at,"
        " COALESCE((SELECT SUM(amount_paise) FROM allocations a"
        "           WHERE a.invoice_id = i.invoice_id), 0) AS paid_paise"
        " FROM invoices i WHERE i.customer = ? ORDER BY i.created_at, i.rowid",
        (customer,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["disputed"] = bool(d["disputed"])
        d["due_paise"] = d["amount_paise"] - d["paid_paise"]
        out.append(d)
    return out


def invoice_summary(conn, invoice_id):
    inv = conn.execute(
        "SELECT customer FROM invoices WHERE invoice_id = ?", (invoice_id,),
    ).fetchone()
    if inv is None:
        raise ShopError(404, "Invoice not found")

    rows = _invoice_rows(conn, inv["customer"])
    summary = next(r for r in rows if r["invoice_id"] == invoice_id)
    summary["customer"] = inv["customer"]
    summary["items"] = [dict(r) for r in conn.execute(
        "SELECT item, qty, unit, unit_price_paise, line_total_paise FROM invoice_items"
        " WHERE invoice_id = ? ORDER BY line_no", (invoice_id,),
    )]
    summary["other_due_paise"] = sum(
        r["due_paise"] for r in rows if r["invoice_id"] != invoice_id
    )
    return summary


def customer_ledger(conn, customer):
    invoices = _invoice_rows(conn, customer)
    total_billed = sum(i["amount_paise"] for i in invoices)
    total_paid = conn.execute(
        "SELECT COALESCE(SUM(amount_paise), 0) FROM payments WHERE customer = ?",
        (customer,),
    ).fetchone()[0]
    allocated = sum(i["paid_paise"] for i in invoices)
    remaining = sum(i["due_paise"] for i in invoices)
    disputed_due = sum(i["due_paise"] for i in invoices if i["disputed"])

    if not invoices:
        status = "no_invoices"
    elif disputed_due > 0:
        status = "disputed"
    elif remaining == 0:
        status = "paid"
    elif allocated == 0:
        status = "unpaid"
    else:
        status = "partial"

    orders = [dict(r) for r in conn.execute(
        "SELECT order_id, status, created_at, delivered_at, delivered_by FROM orders"
        " WHERE customer = ? ORDER BY created_at, rowid", (customer,),
    )]
    for o in orders:
        o["items"] = [dict(r) for r in conn.execute(
            "SELECT item, qty, unit FROM order_items WHERE order_id = ? ORDER BY line_no",
            (o["order_id"],),
        )]

    return {
        "customer": customer,
        "total_billed": paise_to_decimal(total_billed),
        "total_verified_payments": paise_to_decimal(total_paid),
        "remaining_balance": paise_to_decimal(remaining),
        "disputed_balance": paise_to_decimal(disputed_due),
        "available_credit": paise_to_decimal(total_paid - allocated),
        "payment_status": status,
        "orders": orders,
        "invoices": [
            {
                "invoice_id": i["invoice_id"],
                "order_id": i["order_id"],
                "amount": paise_to_decimal(i["amount_paise"]),
                "paid": paise_to_decimal(i["paid_paise"]),
                "due": paise_to_decimal(i["due_paise"]),
                "disputed": i["disputed"],
                "created_at": i["created_at"],
            }
            for i in invoices
        ],
        "payments": [
            {"payment_id": r["payment_id"], "amount": paise_to_decimal(r["amount_paise"]),
             "reference": r["reference"], "method": r["method"],
             "invoice_id": r["invoice_id"], "verified_at": r["verified_at"]}
            for r in conn.execute(
                "SELECT payment_id, amount_paise, reference, method, invoice_id, verified_at"
                " FROM payments WHERE customer = ? ORDER BY verified_at, rowid", (customer,),
            )
        ],
        "unverified_claims": [dict(r) for r in conn.execute(
            "SELECT kind, amount_paise, status, created_at FROM claims"
            " WHERE customer = ? ORDER BY id", (customer,),
        )],
        "review_flags": [dict(r) for r in conn.execute(
            "SELECT id, order_id, invoice_id, reason, created_at FROM review_flags"
            " WHERE customer = ? AND resolved_at IS NULL ORDER BY id", (customer,),
        )],
    }


def order_details(conn, order_id):
    order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    if order is None:
        raise ShopError(404, "Order not found")
    out = dict(order)
    out["items"] = [dict(r) for r in conn.execute(
        "SELECT item, qty, unit FROM order_items WHERE order_id = ? ORDER BY line_no",
        (order_id,),
    )]
    invoice = conn.execute(
        "SELECT invoice_id FROM invoices WHERE order_id = ?", (order_id,),
    ).fetchone()
    out["invoice"] = None
    if invoice:
        s = invoice_summary(conn, invoice["invoice_id"])
        out["invoice"] = {
            "invoice_id": s["invoice_id"],
            "amount": paise_to_decimal(s["amount_paise"]),
            "paid": paise_to_decimal(s["paid_paise"]),
            "due": paise_to_decimal(s["due_paise"]),
            "disputed": s["disputed"],
            "items": s["items"],
            "notifications": notification_states(conn, s["invoice_id"]),
        }
    return out


def notification_states(conn, ref_id):
    return {
        r["kind"]: r["status"]
        for r in conn.execute(
            "SELECT kind, status FROM notifications WHERE ref_id = ?", (ref_id,),
        )
    }
