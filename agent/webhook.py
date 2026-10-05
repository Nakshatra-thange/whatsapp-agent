from fastapi import FastAPI, Request

from agent import process_chat
from response import format_response

app = FastAPI()


@app.get("/")
def home():
    return {"status": "WhatsApp agent is running"}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    print("\n=== INCOMING MESSAGE ===")
    print(data)

    # We'll extract the actual WhatsApp message here later.
    # For now, test with a simple message.

    message = "2 kilo aloo dena"

    result = process_chat(message)
    reply = format_response(result)

    print("\n=== AGENT REPLY ===")
    print(reply)

    return {
        "status": "ok",
        "reply": reply,
    }