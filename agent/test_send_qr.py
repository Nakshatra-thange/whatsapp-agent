from src.payments import generate_payment_qr
from kapso_client import send_whatsapp_image

qr = generate_payment_qr(
    amount=120,
    invoice_ref="test-invoice-001",
)

result = send_whatsapp_image(
    to="918999379616",
    image_path=qr["qr_path"],
    caption=(
        "Nakshatra's shop 🛒\n"
        "Payment request: ₹120.00\n"
        "Scan this QR in your UPI app.\n"
        "Payment is confirmed only after we verify receipt."
    ),
)

print(result)