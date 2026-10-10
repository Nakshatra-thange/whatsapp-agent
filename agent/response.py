from decimal import Decimal

from src.shop import rupees


def format_response(result):
    ledger = result["ledger"]

    status = ledger["payment_status"]
    billed = ledger["total_billed"]
    paid = ledger["total_paid"]
    balance = ledger["balance"]
    credit = ledger["credit"]

    lines = []

    if status == "unpaid":
        lines.append("Order recorded ✅")
    elif status == "partial":
        lines.append("Payment partially received ✅")
    elif status == "paid":
        lines.append("Payment received in full ✅")
    elif status == "overpaid":
        lines.append("Payment received ✅")
    elif status == "disputed":
        lines.append("Order has been marked for review ⚠️")
    elif status == "unknown":
        lines.append("Order needs clarification ⚠️")
    elif status == "not_applicable":
        lines.append("Order cancelled.")
    else:
        lines.append("Order processed.")

    lines.append("")

    if billed is not None:
        lines.append(f"Total: ₹{billed}")

    lines.append(f"Paid: ₹{paid}")

    if balance is not None:
        lines.append(f"Balance: ₹{balance}")

    if credit:
        lines.append(f"Credit: ₹{credit}")

    if ledger["needs_clarification"]:
        lines.append("")
        lines.append("⚠️ Clarification needed:")
        for reason in ledger["reasons"]:
            lines.append(f"- {reason}")

    if ledger["needs_human"]:
        lines.append("")
        lines.append("⚠️ This order needs shopkeeper review.")

    return "\n".join(lines)

# ---------------------------------------------------------------
# Live WhatsApp messages (Hinglish). Amounts come from src/shop.py,
# never from the LLM.
# ---------------------------------------------------------------


def _qty(qty):
    qty = Decimal(str(qty)).normalize()
    return f"{qty:f}"


def describe_items(items):
    parts = []
    for i in items:
        unit = i["unit"]
        if unit[:1].isdigit():
            parts.append(f"{_qty(i['qty'])} x {unit} {i['item']}")
        else:
            parts.append(f"{_qty(i['qty'])} {unit} {i['item']}")
    return ", ".join(parts)


def customer_reply(outcomes):
    """Reply to one customer message, from shop.apply_customer_events()."""
    if not outcomes:
        return (
            "Ji bhaiya 😊 Aapko kya chahiye? Item aur quantity bata dijiye, "
            "jaise: 2 kilo aloo."
        )

    lines = []
    for o in outcomes:
        kind = o["kind"]
        if kind == "order_created":
            lines.append(
                f"Ji bhaiya 😊 Aapka order note kar liya hai: {describe_items(o['items'])}.\n"
                f"Order ID: {o['order_id']}\n"
                "Delivery ke baad aapko bill aur payment QR mil jayega."
            )
        elif kind == "clarify":
            problems = "\n".join(f"- {p}" for p in o["problems"])
            lines.append(
                "Ji bhaiya 😊 Order note karne se pehle thoda confirm kar dijiye:\n"
                f"{problems}\n"
                "Item aur quantity saaf bata dijiye (jaise: 2 kg aloo)."
            )
        elif kind == "delivery_claim":
            lines.append(
                "Ji 😊 Delivery shop ki taraf se confirm hone ke baad hi "
                "bill aur payment QR bheja jayega."
            )
        elif kind == "payment_claim":
            amount = f" {rupees(o['amount_paise'])} ka" if o.get("amount_paise") else ""
            lines.append(
                f"Dhanyavaad 🙏 Aapke{amount} payment ka message mil gaya. "
                "Shop verify karne ke baad hi ledger update hoga."
            )
        elif kind == "promise":
            lines.append(
                "Ji theek hai 😊 Koi baat nahi. Payment aane aur verify hone par "
                "ledger update ho jayega."
            )
        elif kind == "cancelled":
            lines.append(f"Ji, aapka order {o['order_id']} cancel kar diya hai.")
        elif kind == "cancel_unclear":
            ids = ", ".join(o["order_ids"])
            lines.append(
                f"Aapke kai orders pending hain ({ids}). Kaunsa cancel karna hai? "
                "Shopkeeper bhi check kar lenge."
            )
        elif kind == "cancel_nothing":
            lines.append("Aapka koi pending order nahi mila jise cancel kiya ja sake.")
        elif kind == "dispute":
            lines.append(
                "Maaf kijiye 🙏 Shopkeeper check karke aapse baat karenge. "
                "Tab tak is bill ka payment request rok diya gaya hai."
            )
        elif kind == "substitute_review":
            lines.append("Ji 😊 Shopkeeper aapka order update karke confirm karenge.")
        elif kind == "credit_note":
            lines.append(
                "Ji 😊 Aapka pehle ka verified credit agle bill mein "
                "apne aap adjust ho jayega."
            )
    return "\n\n".join(lines)


def bill_message(summary, qr_sent):
    """Sent after the shop confirms delivery. `summary` from shop.invoice_summary()."""
    lines = [
        "Ji bhaiya 😊 Aapka order deliver ho gaya hai.",
        "",
        f"Order: {describe_items(summary['items'])}",
        f"Bill amount: {rupees(summary['amount_paise'])}",
        f"Pehle verified payment: {rupees(summary['paid_paise'])}",
        f"Abhi baaki: {rupees(summary['due_paise'])}",
    ]
    if summary["other_due_paise"] > 0:
        lines.append(f"Pichle bills ka baaki: {rupees(summary['other_due_paise'])}")
    lines.append("")

    if summary["disputed"]:
        lines.append(
            "Is bill par aapki shikayat shopkeeper dekh rahe hain, "
            "isliye abhi payment QR nahi bheja gaya."
        )
    elif summary["due_paise"] <= 0:
        lines.append("Aapke pehle ke credit se poora bill adjust ho gaya. Kuch baaki nahi hai ✅")
    elif qr_sent:
        lines.append(
            "Upar bheje gaye UPI QR se payment kar sakte hain. "
            "Payment verify hone ke baad aapka ledger update kar diya jayega."
        )
    else:
        lines.append(
            "Payment QR abhi nahi bhej paaye, shop se alag se bheja jayega. "
            "Payment verify hone ke baad aapka ledger update kar diya jayega."
        )
    return "\n".join(lines)


def qr_caption(invoice_id, due_paise):
    return (
        f"Bill {invoice_id}: {rupees(due_paise)}\n"
        "Is QR ko apne UPI app se scan karein.\n"
        "Payment verify hone ke baad hi ledger update hoga."
    )


def payment_message(amount_paise, ledger, qr_sent):
    """Sent after the shop verifies a payment. `ledger` from shop.customer_ledger()."""
    def r(value):
        return rupees(int(value * 100))

    lines = [
        f"Ji bhaiya 😊 Aapka {rupees(amount_paise)} ka payment verify ho gaya hai. Dhanyavaad 🙏",
        "",
        f"Kul bill: {r(ledger['total_billed'])}",
        f"Kul verified payment: {r(ledger['total_verified_payments'])}",
        f"Abhi baaki: {r(ledger['remaining_balance'])}",
    ]
    if ledger["available_credit"] > 0:
        lines.append(
            f"Aapka credit: {r(ledger['available_credit'])} "
            "(agle bill mein adjust ho jayega)"
        )
    lines.append("")
    if ledger["remaining_balance"] == 0:
        lines.append("Aapka poora hisaab clear hai ✅")
    elif qr_sent:
        lines.append("Baaki amount ke liye naya UPI QR upar bheja hai.")
    return "\n".join(lines)
