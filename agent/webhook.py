
from datetime import datetime, timezone

from fastapi import FastAPI, Request, HTTPException

from agent import process_chat
from response import format_response

app = FastAPI()

# Temporary memory for this trial.
# This resets whenever the server restarts.
customer_chats = {}
processed_message_ids = set()


@app.get("/")
def home():
    return {"status": "WhatsApp agent is running"}


@app.post("/webhook")
async def webhook(request: Request):
    event = request.headers.get("x-webhook-event")

    # Ignore conversation-created, delivery, and other events.
    if event != "whatsapp.message.received":
        return {"status": "ignored", "event": event}

    data = await request.json()
    message = data.get("message", {})

    # Process only incoming text messages.
    if message.get("kapso", {}).get("direction") != "inbound":
        return {"status": "ignored", "reason": "not inbound"}

    if message.get("type") != "text":
        return {"status": "ignored", "reason": "not text"}

    sender = message.get("from")
    message_id = message.get("id")
    body = message.get("text", {}).get("body", "").strip()

    if not sender or not message_id or not body:
        raise HTTPException(status_code=400, detail="Missing message data")

    # Avoid processing the same WhatsApp message twice.
    if message_id in processed_message_ids:
        return {"status": "duplicate"}

    processed_message_ids.add(message_id)

    timestamp = int(message["timestamp"])
    date = datetime.fromtimestamp(
        timestamp, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M")

    customer_chats.setdefault(sender, [])
    customer_chats[sender].append(
        f"[{date}] Customer: {body}"
    )

    chat = "\n".join(customer_chats[sender])

    print(f"\n=== MESSAGE FROM {sender} ===")
    print(body)

    try:
        result = process_chat(chat)
        print("\n=== EXTRACTED EVENTS ===")
        print(result["events"])

        print("\n=== LEDGER DEBUG ===")
        print(result["ledger"])
        reply = format_response(result)

        print("\n=== AGENT RESPONSE ===")
        print(reply)

        from kapso_client import send_whatsapp_message
        send_whatsapp_message(to=sender,body=reply)

        return {"status": "processed"}

    except Exception:
        # Keep internal errors in the server logs.
        processed_message_ids.discard(message_id)
        print("\n=== AGENT ERROR ===")
        import traceback
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail="Could not process message",
        )
