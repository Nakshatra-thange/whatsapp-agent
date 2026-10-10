# Chat & carry - WhatsApp Grocery Shop Agent

A WhatsApp-based grocery ordering and customer ledger system built with Python, FastAPI, Claude, Kapso, and SQLite.

The agent receives customer orders, tracks delivery and verified payments, calculates outstanding balances deterministically, and sends UPI payment QR codes through WhatsApp.

**Core principle:** Claude interprets messages. Python controls orders, invoices, payments, and balances.

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [How It Works](#how-it-works)
- [Trust and Safety](#trust-and-safety)
- [Setup](#setup)
- [Running the Agent](#running-the-agent)
- [Admin API](#admin-api)
- [Ledger Dataset and Evaluation](#ledger-dataset-and-evaluation)
- [Testing](#testing)
- [Limitations](#limitations)

## Features

- WhatsApp order intake through Kapso webhooks.
- Claude-based extraction of customer intent and grocery items.
- Item aliases, unit normalization, and price validation.
- Persistent orders, invoices, and payment records using SQLite.
- Authenticated shop-side delivery confirmation.
- Deterministic billing using the configured price list.
- UPI QR generation and WhatsApp image delivery.
- Manually verified payments and updated customer balances.
- Payment receipts and outstanding-balance tracking.
- Dispute handling, clarification requests, and human-review flags.
- Idempotent webhook processing and retryable notifications.

## Architecture

```text
                  CUSTOMER
                     |
                     v
              WhatsApp / Kapso
                     |
                     v
              FastAPI Webhook
                     |
                     v
              Claude Extraction
                     |
                     v
          Validation & Normalization
                     |
                     v
              Persistent Orders
                 (SQLite)
                     |
          +----------+----------+
          |                     |
          v                     v
   Shop Confirms Delivery   Payment Verified
          |                     |
          v                     v
       Invoice              Payment Ledger
          |                     |
          +----------+----------+
                     |
                     v
            Deterministic Ledger
                     |
                     v
              Outstanding Due
                     |
                     v
             UPI QR Generation
                     |
                     v
            WhatsApp Notifications
```

## Project Structure

```text
agent/
├── agent.py
├── webhook.py
├── kapso_client.py
├── ledger.py
├── response.py
├── prices.json
├── rules.md
├── requirements.txt
├── README.md
├── shop.db
├── src/
│   ├── db.py
│   ├── extractor.py
│   ├── normalizer.py
│   ├── validator.py
│   ├── payments.py
│   ├── shop.py
│   └── notify.py
├── chats/
├── labels/
├── tests/
├── payment_qr/
└── .env
```

`shop.db` is created automatically. The exact files and folders present may vary as the project evolves.

## How It Works

### 1. Customer places an order

Example:

> Bhaiya, 2 kilo aloo dena.

The agent extracts the requested items, validates their names and quantities against `prices.json`, stores the order, and returns an order ID.

Example response:

> Ji bhaiya 😊 Aapka order note kar liya hai: 2 kg aloo.  
> Order ID: ORD-XXXXXXXX  
> Delivery ke baad aapko bill aur payment QR mil jayega.

No invoice or payment QR is issued merely because an order was placed.

### 2. Shop confirms delivery

The shop calls the authenticated delivery endpoint with the specific order ID.

The application verifies the order, creates an invoice, snapshots the relevant prices, applies eligible credit according to the ledger rules, and sends the bill and payment QR.

For example, if aloo costs ₹30/kg, an order for 2 kg produces a bill of ₹60.

### 3. Shop verifies payment

After checking the actual UPI or bank transaction, the shop calls the payment-confirmation endpoint.

The application records the verified payment, updates the ledger, and sends a receipt showing the payment and remaining balance.

For example:

| Item | Amount |
|---|---:|
| Invoice total | ₹200 |
| Verified payments | ₹150 |
| Outstanding balance | ₹50 |

A payment promise or customer claim does not count as verified payment.

### 4. QR notifications and retries

The QR image is generated for the outstanding amount and sent through Kapso.

Notification state is stored separately for bill text, bill QR, receipt text, and receipt QR. Failed notifications can be retried without creating another invoice or resending notifications that have already succeeded.

A successful API response from Kapso does not necessarily mean WhatsApp has delivered the image. Subscribe to and process the appropriate Kapso failure webhook events to detect later delivery failures.

## Trust and Safety

The system separates customer input from trusted financial actions.

- **Customer messages:** May create orders, provide payment claims, or raise disputes.
- **Shop delivery confirmation:** Required before an order becomes a delivered invoice.
- **Payment confirmation:** Requires the shop to verify the actual transaction.
- **Python ledger:** Performs all billing and balance arithmetic.
- **Claude:** Extracts meaning but does not independently establish delivery or payment.

Additional rules:

- Promises such as "kal de dunga" are not payments.
- Unknown products or ambiguous quantities require clarification.
- Disputed invoices must not trigger automatic payment requests.
- Repeated requests must not create duplicate invoices or count payments twice.
- API credentials and admin tokens must remain in environment variables.
- Do not expose the admin API publicly without appropriate authentication and webhook security.

## Setup

### Requirements

- Python 3.9 or another Python version supported by the project's installed dependencies.
- An Anthropic API key.
- A Kapso API key and configured WhatsApp integration.
- A shop UPI ID for generating payment QR codes.

### Install dependencies

From the project directory:

```bash
cd agent
../.venv/bin/pip install -r requirements.txt
```

If you use a different virtual environment, replace `../.venv/bin/pip` with its Python executable.

### Configure environment variables

Create or update `.env` in the `agent/` directory:

```dotenv
ANTHROPIC_API_KEY=your_anthropic_api_key
KAPSO_API_KEY=your_kapso_api_key

SHOP_UPI_ID=your_shop_upi_id
SHOP_PAYEE_NAME=Your Shop Name
SHOP_ADMIN_TOKEN=your_random_admin_secret

# Optional
KAPSO_PHONE_NUMBER_ID=your_kapso_phone_number_id
SHOP_DB_PATH=shop.db
```

Use real values locally. Never commit `.env` or paste credentials into source code.

`SHOP_DB_PATH` can point to another SQLite file. Confirm the database path before running maintenance commands.

### Configure Kapso

1. Configure the WhatsApp integration and authorized test recipient.
2. Expose the local FastAPI application using a secure development tunnel or an appropriate deployment.
3. Configure the inbound webhook URL to point to `/webhook`.
4. Subscribe to the required message-received and message-failed events.
5. Verify that webhook authenticity is checked before using the integration with real customers.

The tunnel URL may change when the tunnel restarts. Update the configured webhook URL when necessary.

## Running the Agent

Start the API from the `agent/` directory:

```bash
../.venv/bin/uvicorn webhook:app --port 8000 --reload
```

Check the health endpoint:

```bash
curl http://localhost:8000/
```

The development server should be available at `http://localhost:8000`.

Keep the server running while testing webhooks and admin endpoints.

## Admin API

All admin endpoints require the header:

```http
x-shop-admin-token: <SHOP_ADMIN_TOKEN>
```

Use your configured token locally. The examples below intentionally use placeholders.

### 1. Confirm delivery

`POST /admin/confirm-delivery`

Confirms delivery of a specific order and triggers invoice and payment notification processing.

```bash
curl -X POST http://localhost:8000/admin/confirm-delivery \
  -H "x-shop-admin-token: $SHOP_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "order_id": "ORD-1A2B3C4D",
    "customer": "919999000001",
    "delivered_by": "Shop Administrator",
    "note": "Delivery completed"
  }'
```

- `order_id` is required.
- `customer`, `delivered_by`, and `note` are optional, according to the current implementation.
- Confirming the same order again must not create another invoice.
- Previously successful notifications should not be resent unnecessarily.

### 2. Confirm a payment

`POST /admin/confirm-payment`

Call this endpoint only after verifying the payment in the UPI or bank app.

```bash
curl -X POST http://localhost:8000/admin/confirm-payment \
  -H "x-shop-admin-token: $SHOP_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "customer": "919999000001",
    "amount": 60,
    "reference": "UPI-TXN-123456",
    "invoice_id": "INV-1A2B3C4D"
  }'
```

- `customer`, `amount`, and `reference` identify the payment.
- `invoice_id` may be supplied when the payment belongs to a specific invoice.
- Use the actual transaction reference.
- Repeating a payment request with the same reference must not double-count the payment.

### 3. Resolve a dispute

`POST /admin/resolve-dispute`

```bash
curl -X POST http://localhost:8000/admin/resolve-dispute \
  -H "x-shop-admin-token: $SHOP_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "invoice_id": "INV-1A2B3C4D",
    "note": "Dispute reviewed by the shop"
  }'
```

Follow the application's response to determine whether further action is required.

### 4. Look up an order

`GET /admin/orders/{order_id}`

```bash
curl -H "x-shop-admin-token: $SHOP_ADMIN_TOKEN" \
  http://localhost:8000/admin/orders/ORD-1A2B3C4D
```

### 5. Look up a customer's ledger

`GET /admin/customers/{phone}/ledger`

```bash
curl -H "x-shop-admin-token: $SHOP_ADMIN_TOKEN" \
  http://localhost:8000/admin/customers/919999000001/ledger
```

Use the phone number format expected by the application.

> Check the current implementation and API responses if endpoint behavior differs from these examples. The code is the source of truth for exact validation and status codes.

## Ledger Dataset and Evaluation

The project also includes a labelled dataset for evaluating event extraction and ledger calculations independently of the live WhatsApp workflow.

| File or directory | Purpose |
|---|---|
| `prices.json` | Canonical grocery names, prices, units, and aliases |
| `rules.md` | Event extraction and ledger rules |
| `chats/` | 20 customer WhatsApp conversation threads |
| `labels/` | Expected events and ledger outputs |
| `ledger.py` | Deterministic event-to-ledger calculation |

Evaluation pipeline:

```text
WhatsApp Conversation
        |
        v
  Claude Extraction
        |
        v
  Normalized Events
        |
        v
   ledger.py
        |
        v
Compare with labels/*.json
```

The dataset includes deliberate hard cases:

- **Sharma ji:** Ambiguous payment allocation.
- **Mahesh:** Stated amount differs from the computed bill.
- **Gupta ji:** Vague item size.
- **Sunil:** Partial payment and dispute.
- **Seema:** Repeat order.
- **Pooja:** Overpayment and credit.
- **Deepak:** Multiple partial payments.
- **Vikram:** Substitution.

The evaluation dataset tests extraction and ledger correctness. It is separate from testing real WhatsApp delivery and external API behavior.

## Testing

Run the main test suite from `agent/`:

```bash
../.venv/bin/python -m unittest discover -s tests -v
```

The suite includes workflow, authentication, billing, payment, QR, persistence, retry, and failure scenarios. External Claude and Kapso calls are mocked in the unit tests.

A passing test suite does not prove that live WhatsApp delivery will succeed. Use a controlled test recipient to verify the real webhook, QR image, and notification flow.

Some older standalone scripts, such as `test_extraction.py` or `test_send_qr.py`, may call external services. Inspect a script before running it.

## Limitations

- Payments are verified manually unless a trusted payment-verification integration is added.
- WhatsApp may reject media after Kapso accepts the send request; failure-event handling is required to detect such cases.
- Inbound webhook authenticity must be verified before deployment to real customers.
- Disputes and complex payment allocations may require human review.
- Any unsupported bill correction or dispute-settlement workflow must be resolved through an explicit, audited process rather than manual database edits.
- SQLite is suitable for a local MVP, but production deployment requires reliable backups, access controls, monitoring, and careful concurrency handling.

## Development Principles

1. Accuracy over guessing.
2. Never bill an order before trusted delivery confirmation.
3. Never count unverified payments.
4. Keep financial arithmetic deterministic.
5. Preserve an audit trail for orders, invoices, payments, and notifications.
6. Make retries idempotent.
7. Never expose secrets in logs or version control.
