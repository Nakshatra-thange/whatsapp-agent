from src.payments import generate_payment_qr

result = generate_payment_qr(
    amount=120,
    invoice_ref="test-invoice-001",
)

print("Amount:", result["amount"])
print("QR saved at:", result["qr_path"])
print("UPI link:", result["upi_link"])