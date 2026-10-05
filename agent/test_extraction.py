import os
import json
from pathlib import Path
from src.validator import validate_events
from dotenv import load_dotenv
from anthropic import Anthropic
from src.schema import ExtractionResult


# -------------------------
# 1. Setup
# -------------------------

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

client = Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY")
)


# -------------------------
# 2. Read input files
# -------------------------

chat = (BASE_DIR / "chats" / "anjali.txt").read_text()

rules = (BASE_DIR / "rules.md").read_text()

prices = json.loads(
    (BASE_DIR / "prices.json").read_text()
)


# -------------------------
# 3. Ask Claude
# -------------------------

prompt = f"""
Extract grocery ledger events from this WhatsApp conversation.

Rules:
{rules}

Prices:
{json.dumps(prices, indent=2)}

Chat:
{chat}

Important:

- Extract what the customer/shop actually said.
- Do not calculate prices.
- Do not calculate balances.
- Do not calculate credits.
- Do not invent quantities.
- Do not invent payments.
- Do not invent deliveries.
- Preserve the exact quantity and unit stated in the chat.
- If the chat says "500 gram", output qty=500 and unit="gram".
- If the chat says "1 kg", output qty=1 and unit="kg".
- If the chat says "1 dozen", output qty=1 and unit="dozen".
- Keep quantities in the units actually stated in the chat.
- Use canonical item names from prices.json.
"""


response = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=2000,

    tools=[
        {
            "name": "extract_events",
            "description": "Extract grocery ledger events from the conversation.",

            "input_schema": {
                "type": "object",

                "properties": {
                    "events": {
                        "type": "array",

                        "items": {
                            "type": "object",

                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": [
                                        "ORDER",
                                        "SUBSTITUTE",
                                        "CANCEL",
                                        "DELIVERED",
                                        "PAYMENT",
                                        "PROMISE",
                                        "ADJUST_CREDIT",
                                        "DISPUTE"
                                    ]
                                },

                                "date": {
                                    "type": "string"
                                },

                                "items": {
                                    "type": "array",

                                    "items": {
                                        "type": "object",

                                        "properties": {
                                            "item": {
                                                "type": "string"
                                            },

                                            "qty": {
                                                "type": "number"
                                            },

                                            "unit": {
                                                "type": "string"
                                            }
                                        },

                                        "required": [
                                            "item",
                                            "qty",
                                            "unit"
                                        ]
                                    }
                                },

                                "amount": {
                                    "type": "number"
                                },

                                "from_item": {
                                    "type": "string"
                                },

                                "to_item": {
                                    "type": "string"
                                },

                                "note": {
                                    "type": "string"
                                }
                            },

                            "required": [
                                "type",
                                "date"
                            ]
                        }
                    }
                },

                "required": [
                    "events"
                ]
            }
        }
    ],

    # Force Claude to use our extraction tool
    tool_choice={
        "type": "tool",
        "name": "extract_events"
    },

    messages=[
        {
            "role": "user",
            "content": prompt
        }
    ]
)


# -------------------------
# 4. Get structured result
# -------------------------

events = None

for block in response.content:

    if block.type == "tool_use":

        events = block.input

        print(
            json.dumps(
                events,
                indent=2
            )
        )


# -------------------------
# 5. Pydantic validation
# -------------------------

print("\nSCHEMA VALIDATION:")

try:
    validated = ExtractionResult.model_validate(events)

    print("Valid")

except Exception as e:

    print("Invalid")

    print(e)

# -------------------------
# 6. Business validation
# -------------------------

errors = validate_events(
    events["events"],
    prices
)

print("\nVALIDATION ERRORS:")

if errors:

    for error in errors:
        print("-", error)

else:

    print("None")