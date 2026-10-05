# Shop Ledger Rules

The LLM turns WhatsApp messages into **events**. Plain Python (`ledger.py`) turns events into balances.
The LLM never does arithmetic.

## Event types

| Event | Meaning |
|---|---|
| ORDER | Customer asks for items (`item`, `qty`, `unit`) |
| SUBSTITUTE | Customer accepts a replacement item |
| CANCEL | Order cancelled before delivery |
| DELIVERED | Shop confirms delivery |
| PAYMENT | Money was actually sent |
| PROMISE | "kal de dunga". **Not a payment** |
| ADJUST_CREDIT | Customer asks to use an earlier overpayment on a new bill |
| DISPUTE | Customer says what arrived differs from what was ordered |

## Conventions (so labels and agent output are comparable)

- Use **canonical names** from `prices.json` keys. Aliases map to them (onion -> `pyaz`, dhaniya -> `kothimbir`, oil -> `sunflower oil`).
- `qty` is always in the price list's unit for that item.
  - 500 gram paneer = 2 (unit is 250g). 1 dozen eggs = 12 (unit is piece). 500 gram carrot = 0.5 (unit is kg).
  - 1 packet of milk = 500 ml, so 1 litre milk = 2 packets.
  - "10 rs ki kothimbir": the price list says one pack is Rs 10, so qty = 1.
- Dates are ISO (`2026-10-05`). "kal" means the date + 1.
- Process messages in time order, one customer at a time. A customer's history carries across days.

## Two flags, defined once

- **`needs_clarification`**: asking the *customer* would fix it (vague size, unclear item).
- **`needs_human`**: the *shopkeeper* must decide (conflicting numbers, disputes, unclear payment allocation).

If a case could be either, prefer `needs_human`.

## Rules

1. **Promises are not payments.** "kal de dunga", "payment kal karunga", "baad mein dunga" -> `PROMISE`. The balance does not change.
2. **Payment must be explicit.** Only count money when the customer says it was sent ("gpay kar diye", "bhej diye").
3. **Cancel before delivery -> customer owes nothing.** No invoice is created. Status `not_applicable`.
4. **Delivery needs evidence.** An order is delivered only when the shop says so. Placing an order is not delivery.
5. **Never guess prices or quantities.** Only price items that match `prices.json`. Vague items ("chhota pack", "thoda") get `needs_clarification = true` and the bill is left unknown. Do not invent a number.
6. **Partial payments add up.** `balance = billed - sum(payments)`. Several small payments are summed before computing what is left.
7. **Overpayment creates credit.** `credit = paid - billed`, `balance = 0`.
8. **Credit adjustment is not new cash.** If the customer says "extra paise isme adjust kar lena", apply the existing credit to the new bill. Do not add it to `total_paid`.
9. **Old dues.** "pichle wale ka 200 bhej diye" pays the earlier outstanding invoice, not a new order.
10. **Payment allocation.** If a payment arrives and more than one invoice is open, and the message doesn't say which, set `needs_human = true`. The payment still counts toward `total_paid`.
11. **Stated amount vs computed bill.** If a customer's stated amount is *larger* than the computed outstanding balance, trust neither. Keep the computed number and set `needs_human = true`. A promise for *less* than the balance is fine (it's a partial promise).
12. **Substitutions.** If the shop offers a replacement and the customer agrees, bill the replacement item.
13. **Missing items.** If the customer says an item didn't arrive, record a `DISPUTE`, leave the balance unknown, and set `needs_human = true`.
14. **Repeat orders.** "kal jaisa hi bhej do": if exactly one earlier order exists, reuse it. If several exist and the right one is unclear, `needs_clarification = true`.
15. **Accuracy over guessing.** Order of resolution: conversation context, then `prices.json`, then previous orders. If still unclear, flag it. Never invent a price, quantity, payment, delivery, or invoice relationship.