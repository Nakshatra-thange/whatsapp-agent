
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

import qrcode
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

UPI_ID = os.getenv("SHOP_UPI_ID")
PAYEE_NAME = os.getenv("SHOP_PAYEE_NAME", "Nakshatra's shop")


def generate_payment_qr(amount, invoice_ref="Grocery invoice"):
    if not UPI_ID:
        raise RuntimeError("SHOP_UPI_ID is missing from .env")

    amount = Decimal(str(amount))

    if not amount.is_finite() or amount <= 0:
        raise ValueError("Payment amount must be greater than zero")

    # Create a UPI payment link for this invoice amount.
    payment_details = {
        "pa": UPI_ID,
        "pn": PAYEE_NAME,
        "am": f"{amount:.2f}",
        "cu": "INR",
        "tn": invoice_ref[:80],
    }

    upi_link = "upi://pay?" + urlencode(payment_details)

    # Save the QR image locally.
    output_dir = BASE_DIR / "payment_qr"
    output_dir.mkdir(exist_ok=True)

    safe_ref = "".join(
        c for c in invoice_ref if c.isalnum() or c in "-_"
    )[:40] or "invoice"

    qr_path = output_dir / f"{safe_ref}_{amount:.2f}.png"

    qr = qrcode.make(upi_link)
    qr.save(qr_path)

    return {
        "upi_link": upi_link,
        "qr_path": str(qr_path),
        "amount": f"{amount:.2f}",
    }
