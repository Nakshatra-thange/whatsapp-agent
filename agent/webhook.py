
import os
import hmac
import traceback
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException

from agent import process_chat
from response import format_response
from src.payments import generate_payment_qr
from kapso_client import send_whatsapp_image, send_whatsapp_message

load_dotenv()

app = FastAPI()

# Temporary storage for this development trial.
# It resets when the server restarts.
customer_chats = {}
processed_message_ids = set()
confirmed_deliveries = set()

SHOP_ADMIN_TOKEN = os.getenv("SHOP_ADMIN_TOKEN")


@app.get("/")
def home():
    return {"status": "WhatsApp agent is running"}


def normalize_phone(phone):
    return "".join(ch for ch in str(phone) if ch.isdigit())


def get_known_balance(ledger):
    balance = ledger.get("balance")

    if balance is None:
        return None

    try:
        balance = float(balance)
    except (TypeError, ValueError):
        return None

    if balance <= 0:
        return None

    if ledger.get("payment_status") not in ("unpaid", "partial"):
        return None

    if ledger.get("needs_clarification") or ledger.get("needs_human"):
        return None

    return balance


@app.post("/webhook")
async def webhook(request: Request):
    event = request.headers.get("x-webhook-event")

    if event != "whatsapp.message.received":
        return {"status": "ignored", "event": event}

    data = await request.json()
    message = data.get("message", {})

    if message.get("kapso", {}).get("direction") != "inbound":
        return {"status": "ignored", "reason": "not inbound"}

    if message.get("type") != "text":
        return {"status": "ignored", "reason": "not text"}

    sender = normalize_phone(message.get("from", ""))
    message_id = message.get("id")
    body = message.get("text", {}).get("body", "").strip()

    if not sender or not message_id or not body:
        raise HTTPException(
            status_code=400,
            detail="Missing message data",
        )

    if message_id in processed_message_ids:
        return {"status": "duplicate"}

    processed_message_ids.add(message_id)

    try:
        timestamp = int(message.get("timestamp", 0))
        date = datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        ).strftime("%Y-%m-%d %H:%M")

        customer_chats.setdefault(sender, [])
        customer_chats[sender].append(
            f"[{date}] Customer: {body}"
        )

        result = process_chat("\n".join(customer_chats[sender]))

        # Customer messages never trigger a payment QR.
        reply = format_response(result)
        send_whatsapp_message(to=sender, body=reply)

        print(f"Processed customer message from {sender}")
        print("Events:", result.get("events"))
        print("Ledger:", result.get("ledger"))

        return {"status": "processed"}

    except Exception:
        processed_message_ids.discard(message_id)
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail="Could not process message",
        )


@app.post("/admin/confirm-delivery")
async def confirm_delivery(request: Request):
    # Require a configured secret; fail closed if it is missing.
    if not SHOP_ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="SHOP_ADMIN_TOKEN is not configured",
        )

    supplied_token = request.headers.get("x-shop-admin-token", "")

    if not hmac.compare_digest(supplied_token, SHOP_ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")

    data = await request.json()
    customer = normalize_phone(data.get("customer", ""))

    if not customer:
        raise HTTPException(
            status_code=400,
            detail="Provide the customer's WhatsApp phone number",
        )

    if customer not in customer_chats:
        raise HTTPException(
            status_code=404,
            detail="No conversation found for this customer",
        )

    # Prevent repeated confirmations during this server session.
    if customer in confirmed_deliveries:
        raise HTTPException(
            status_code=409,
            detail="Delivery already confirmed in this session",
        )

    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

        # This entry is created only through the authenticated shop endpoint.
        # The extraction rules must treat shop statements as delivery evidence.
        customer_chats[customer].append(
            f"[{timestamp}] Shop: Delivery confirmed by shop administrator."
        )

        result = process_chat("\n".join(customer_chats[customer]))
        ledger = result["ledger"]

        print("Shop-confirmed events:", result.get("events"))
        print("Ledger after shop confirmation:", ledger)

        balance = get_known_balance(ledger)

        if balance is None:
            # Don't send a QR if the amount is unknown, zero, disputed,
            # already paid, or needs human review.
            customer_chats[customer].pop()

            raise HTTPException(
                status_code=409,
                detail=(
                    "No safe positive balance is available. "
                    "Check the ledger and resolve any review flags."
                ),
            )

        qr = generate_payment_qr(
            amount=balance,
            invoice_ref=f"grocery-{customer}",
        )

        send_whatsapp_image(
            to=customer,
            image_path=qr["qr_path"],
            caption=(
                "Nakshatra's shop 🛒\n"
                "Delivery confirmed by the shop.\n"
                f"Outstanding amount: ₹{balance:.2f}\n"
                "Scan this QR using your UPI app.\n"
                "Your payment will be recorded after it is verified."
            ),
        )

        send_whatsapp_message(
            to=customer,
            body=(
                f"Your delivery has been confirmed by the shop. "
                f"Your outstanding balance is ₹{balance:.2f}. "
                "I've sent your UPI payment QR."
            ),
        )

        confirmed_deliveries.add(customer)

        return {
            "status": "confirmed",
            "customer": customer,
            "balance": round(balance, 2),
            "qr_sent": True,
        }

    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail="Delivery confirmation or payment QR failed",
        )
