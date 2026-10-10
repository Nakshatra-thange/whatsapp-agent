# Shop ledger agent: dataset

- `prices.json`   price list with units and aliases
- `rules.md`      the rules the agent must follow
- `chats/`        20 customer WhatsApp threads (the agent's INPUT)
- `labels/`       expected events + expected ledger per customer (the answer key)
- `ledger.py`     deterministic engine: events -> balances (no LLM)

Eval idea: LLM(chat) -> events -> ledger.py -> compare with labels/*.json
Deliberate hard cases: Sharma ji (ambiguous payment), Mahesh (stated 240 vs bill 205),
Gupta ji (vague size), Sunil (partial + dispute), Seema (repeat order),
Pooja (overpay + credit), Deepak (two partial payments), Vikram (substitution).

---

# WhatsApp shop agent (live)

## Flow and trust boundaries

1. **Customer orders on WhatsApp** -> `POST /webhook`. Claude extracts events from
   *that one message* (earlier messages are context only). Python validates items
   against `prices.json` and stores a **pending order** in SQLite. The customer gets
   the order ID. No bill and no QR yet. Unclear items/quantities get a
   clarification question instead of an order.
2. **Shop confirms delivery** -> `POST /admin/confirm-delivery` with an `order_id`
   and the admin token. This is the only way an order becomes delivered.
   Python creates exactly one invoice (prices are snapshotted), applies any existing
   credit, then sends the bill text and a UPI QR for the amount still due.
3. **Shop verifies money** -> `POST /admin/confirm-payment`. Only these payments
   count. The customer gets a receipt with the remaining balance (and a QR for the
   rest, if anything is still due on an undisputed invoice).

Customer messages such as "delivered", "gpay kar diya" or "kal de dunga" are
stored as **unverified claims**; they never change delivery state or the balance.
A customer dispute holds the invoice: no QR is sent until the shop resolves it.

## Storage

SQLite file `shop.db` (override with `SHOP_DB_PATH`), created automatically
(`src/db.py`). It stores customers, inbound message IDs (webhook dedup),
conversation, orders + items, invoices + priced items, verified payments,
payment-to-invoice allocations (unallocated = credit), unverified claims,
review flags and per-message notification state. Money is integer paise.

## `.env` variables (names only)

`ANTHROPIC_API_KEY`, `KAPSO_API_KEY`, `SHOP_UPI_ID`, `SHOP_PAYEE_NAME`,
`SHOP_ADMIN_TOKEN`; optional `KAPSO_PHONE_NUMBER_ID`, `SHOP_DB_PATH`.

## Run

```bash
cd agent
../.venv/bin/pip install -r requirements.txt
../.venv/bin/uvicorn webhook:app --port 8000
```

## Admin API

All admin endpoints need the header `x-shop-admin-token: <SHOP_ADMIN_TOKEN>`.
Failed WhatsApp sends return `502` with the saved state; repeating the same
request only sends what has not been sent yet and never bills twice.

### Confirm delivery

```bash
curl -X POST http://localhost:8000/admin/confirm-delivery \
  -H "x-shop-admin-token: $SHOP_ADMIN_TOKEN" -H "content-type: application/json" \
  -d '{"order_id": "ORD-1A2B3C4D", "customer": "919999000001", "delivered_by": "Ramesh", "note": "left at door"}'
```

`order_id` is required; `customer` (checked against the order), `delivered_by`
and `note` are optional. Responses: `200` `confirmed` / `already_confirmed`,
`404` unknown order, `409` cancelled order or wrong customer, `422` order cannot
be priced (flagged for review), `502` saved but WhatsApp send failed.

### Confirm a payment (after checking your UPI/bank app)

```bash
curl -X POST http://localhost:8000/admin/confirm-payment \
  -H "x-shop-admin-token: $SHOP_ADMIN_TOKEN" -H "content-type: application/json" \
  -d '{"customer": "919999000001", "amount": 60, "reference": "UPI-TXN-123456", "invoice_id": "INV-1A2B3C4D"}'
```

`reference` (UPI transaction ID) is required and makes the call idempotent.
`invoice_id` is optional when only one invoice is open or the amount covers all
open invoices; otherwise `409` asks for it. Extra money becomes credit and is
applied to the next invoice automatically.

### Resolve a dispute

```bash
curl -X POST http://localhost:8000/admin/resolve-dispute \
  -H "x-shop-admin-token: $SHOP_ADMIN_TOKEN" -H "content-type: application/json" \
  -d '{"invoice_id": "INV-1A2B3C4D", "note": "aloo re-delivered"}'
```

### Look up

```bash
curl -H "x-shop-admin-token: $SHOP_ADMIN_TOKEN" http://localhost:8000/admin/orders/ORD-1A2B3C4D
curl -H "x-shop-admin-token: $SHOP_ADMIN_TOKEN" http://localhost:8000/admin/customers/919999000001/ledger
```

## Tests

```bash
cd agent
../.venv/bin/python -m unittest discover -s tests -v
```

Claude and Kapso are mocked; any real HTTP request fails the test. The older
`test_*.py` scripts in this folder call the real APIs and are not part of this suite.
