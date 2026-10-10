
from kapso_client import send_whatsapp_message

result = send_whatsapp_message(
    to="918999379616",
    body="Hello! Your grocery agent is connected to Kapso. 🥔",
)

print(result)
