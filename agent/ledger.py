"""
Deterministic ledger engine.

The LLM's only job is to turn WhatsApp messages into EVENTS (see rules.md).
This file turns events into a customer ledger. No LLM, no guessing, just arithmetic.

Event types:
  ORDER          {date, items:[{item, qty, unit, needs_clarification, reason}]}
  SUBSTITUTE     {date, from_item, to_item}  customer accepted a replacement
  CANCEL         {date}                      order cancelled before delivery
  DELIVERED      {date}
  PAYMENT        {date, amount}              money actually sent
  PROMISE        {date, amount|None}         "kal de dunga" - NOT a payment
  ADJUST_CREDIT  {date, amount}              use earlier overpayment against a new bill
  DISPUTE        {date, note}                shop and customer disagree on what arrived
"""
import json 
# load prices from prices.json

def load_prices(path="prices.json"):
    with open(path) as f:
        return json.load(f)
# it opens prices.json and combvert it to python dictioary

def build_alias_map(prices):
    m = {}
    for name, info in prices.items():
        m[name.lower()] = name
        for a in info.get("aliases", []):
            m[a.lower()] = name
    return m
#This handles different ways customers refer to the same product.

def _num(x):
    x = round(x, 2)
    return int(x) if x == int(x) else x
# formating number to its round of 


def compute_ledger(events, prices):
    invoices, pending = [], None
    paid, cancelled = 0, False
    credit_avail = 0
    human, clarify = [], []

    def order_amount(items):
        known, unknown = 0, False
        for i in items:
            if i.get("needs_clarification"):
                unknown = True
                clarify.append(f"{i['item']}: {i.get('reason') or 'unclear'}")
                continue
            known += prices[i["item"]]["price"] * i["qty"]
        return _num(known), unknown

    def outstanding():
        return sum(v["amount"] - v["alloc"] for v in invoices
                   if v["amount"] is not None and not v["disputed"])

    for e in events:
        t = e["type"]
        if t == "ORDER":
            pending = {"date": e["date"], "items": [dict(i) for i in e["items"]]}
        elif t == "SUBSTITUTE":
            if pending is None:
                human.append("substitution without a pending order")
                continue
            for i in pending["items"]:
                if i["item"] == e["from_item"]:
                    i["item"] = e["to_item"]
                    i["unit"] = prices[e["to_item"]]["unit"]
        elif t == "CANCEL":
            if pending is None:
                human.append("cancel after delivery or without an order")
            else:
                pending, cancelled = None, True
        elif t == "DELIVERED":
            if pending is None:
                human.append("delivered with no matching order")
                continue
            known, unknown = order_amount(pending["items"])
            invoices.append({"date": pending["date"], "known": known,
                             "amount": None if unknown else known,
                             "alloc": 0, "disputed": False})
            pending = None
        elif t == "PAYMENT":
            a = e["amount"]
            paid += a
            if any(v["amount"] is None for v in invoices):
                continue
            open_ = [v for v in invoices if not v["disputed"] and v["amount"] - v["alloc"] > 0]
            if not open_:
                credit_avail += a
            elif len(open_) == 1:
                use = min(a, open_[0]["amount"] - open_[0]["alloc"])
                open_[0]["alloc"] += use
                credit_avail += a - use
            else:
                human.append(f"payment of {a} on {e['date']} could match "
                             f"{len(open_)} open invoices; allocation ambiguous")
        elif t == "PROMISE":
            amt = e.get("amount")
            if amt is not None and amt > outstanding():
                human.append(f"customer stated {amt} but computed outstanding is {_num(outstanding())}")
        elif t == "ADJUST_CREDIT":
            a = e["amount"]
            open_ = [v for v in invoices if v["amount"] is not None and v["amount"] - v["alloc"] > 0]
            if a > credit_avail or not open_:
                human.append(f"credit adjustment of {a} but only {_num(credit_avail)} credit available")
            else:
                open_[0]["alloc"] += a      # not a new cash payment
                credit_avail -= a
        elif t == "DISPUTE":
            if invoices:
                invoices[-1]["disputed"] = True
            human.append(e.get("note", "delivery dispute"))

    unknown_any = any(v["amount"] is None for v in invoices)
    disputed_any = any(v["disputed"] for v in invoices)
    total_billed = None if unknown_any else _num(sum(v["amount"] for v in invoices))
    known_billed = _num(sum(v["known"] for v in invoices))

    if not invoices:
        status = "not_applicable" if cancelled else "no_orders"
    elif unknown_any:
        status = "unknown"
    elif disputed_any:
        status = "disputed"
    elif paid == 0:
        status = "unpaid"
    elif paid > total_billed:
        status = "overpaid"
    elif paid == total_billed:
        status = "paid"
    else:
        status = "partial"

    if total_billed is None or disputed_any:
        balance = None
    else:
        balance = _num(max(0, total_billed - paid))
    credit = _num(max(0, paid - total_billed)) if total_billed is not None and not disputed_any else None

    out = {
        "total_billed": total_billed,
        "known_billed": known_billed,
        "total_paid": _num(paid),
        "balance": balance,
        "credit": credit,
        "payment_status": status,
        "needs_clarification": bool(clarify),
        "needs_human": bool(human),
        "reasons": clarify + human,
    }
    if disputed_any:
        out["balance_excluding_disputed"] = _num(sum(
            v["amount"] - v["alloc"] for v in invoices if not v["disputed"]))
    return out