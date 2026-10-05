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