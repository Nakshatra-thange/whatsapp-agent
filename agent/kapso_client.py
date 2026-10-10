
import os

import requests
from dotenv import load_dotenv
from pathlib import Path
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

def send_whatsapp_image(to: str, image_path: str, caption: str):
    if not KAPSO_API_KEY:
        raise RuntimeError("KAPSO_API_KEY is missing from .env")

    image_path = Path(image_path)

    # Upload the QR image to Kapso.
    with image_path.open("rb") as image_file:
        upload = requests.post(
            f"https://api.kapso.ai/meta/whatsapp/v24.0/"
            f"{PHONE_NUMBER_ID}/media",
            headers={"X-API-Key": KAPSO_API_KEY},
            data={
                "messaging_product": "whatsapp",
                "type": "image",
            },
            files={
                "file": (
                    image_path.name,
                    image_file,
                    "image/png",
                )
            },
            timeout=30,
        )

    if not upload.ok:
        raise RuntimeError(
            f"QR upload failed: {upload.status_code} {upload.text}"
        )

    media_id = upload.json()["id"]

    # Send the uploaded QR image to the customer.
    response = requests.post(
        API_URL,
        headers={
            "X-API-Key": KAPSO_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "image",
            "image": {
                "id": media_id,
                "caption": caption,
            },
        },
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(
            f"QR send failed: {response.status_code} {response.text}"
        )

    return response.json()
