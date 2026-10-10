
import os

import requests
from dotenv import load_dotenv

load_dotenv()

KAPSO_API_KEY = os.getenv("KAPSO_API_KEY")
PHONE_NUMBER_ID = "597907523413541"

API_URL = (
    f"https://api.kapso.ai/meta/whatsapp/v24.0/"
    f"{PHONE_NUMBER_ID}/messages"
)


def send_whatsapp_message(to: str, body: str):
    if not KAPSO_API_KEY:
        raise RuntimeError("KAPSO_API_KEY is missing from .env")

    response = requests.post(
        API_URL,
        headers={
            "X-API-Key": KAPSO_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": body},
        },
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(
            f"Kapso API error {response.status_code}: "
            f"{response.text}"
        )

    return response.json()
