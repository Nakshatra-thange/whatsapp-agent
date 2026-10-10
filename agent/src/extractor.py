import json
import os
from datetime import datetime

from anthropic import Anthropic
from dotenv import load_dotenv

from .schema import ExtractionResult
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

client = Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY")
)


SYSTEM_PROMPT = """
You extract structured events from grocery-shop WhatsApp conversations.

Your job is ONLY to identify what the messages say.

Do NOT:
- calculate prices
- calculate bills
- calculate balances
- calculate credits
- infer missing quantities
- invent quantities
- invent payments
- invent deliveries

Extract quantities and payment amounts exactly as stated in the chat.

The Python program will perform unit conversion, validation,
price lookup, billing, balances and all arithmetic.

Canonical item names must come from prices.json.

Follow rules.md exactly.

Event meanings:

ORDER:
Customer asks for items.

SUBSTITUTE:
Customer accepts a replacement.

CANCEL:
Order is cancelled before delivery.

DELIVERED:
A line written by the Shop explicitly confirms delivery.
Never emit DELIVERED for a Customer's message ("delivered", "ho gaya",
"mil gaya"): a customer cannot confirm the shop's delivery.

PAYMENT:
Customer explicitly says money was sent. This is only a claim;
the shop verifies money separately.

PROMISE:
Customer says they will pay later.
This is NOT a payment.

ADJUST_CREDIT:
Customer asks to apply an earlier overpayment/credit.

DISPUTE:
Customer says something ordered did not arrive
or what arrived differs from what was ordered.

Dates must be ISO format YYYY-MM-DD.

Example: "Bhaiya, 2 kilo aloo dena" -> ORDER with
items [{item: "aloo", qty: 2, unit: "kg", needs_clarification: false}].
Use the customer's stated unit; if no quantity is stated, set qty null
and needs_clarification true.

Return structured events only.
"""


def extract_events(
    chat: str,
    rules: str,
    prices: dict,
    max_retries: int = 1,
    context: str = "",
):
    """
    Extract events from one WhatsApp chat.

    If `context` is given, it holds earlier messages that were already
    processed. They help interpret `chat` (e.g. an answer to a
    clarification question) but must not produce events again.

    Returns:
        ExtractionResult

    Raises:
        RuntimeError if extraction fails after retry.
    """

    prompt = f"""
Here is the rules file:

--- RULES ---
{rules}
--- END RULES ---

Here is the price list:

--- PRICES ---
{json.dumps(prices, indent=2)}
--- END PRICES ---

Here is the WhatsApp conversation:

--- CHAT ---
{chat}
--- END CHAT ---

Extract the events from this conversation.
"""

    if context:
        prompt = f"""
{prompt}
Earlier messages from the same customer are below. They were ALREADY
processed. Use them only to understand the conversation above (for
example, if the customer is now answering a clarification question,
emit the complete ORDER). Do NOT emit events for these earlier messages.

--- EARLIER MESSAGES (context only) ---
{context}
--- END EARLIER MESSAGES ---
"""

    for attempt in range(max_retries + 1):

        try:
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                tools=[
                    {
                        "name": "emit_events",
                        "description": (
                            "Emit the extracted grocery ledger events."
                        ),
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
                                                    "DISPUTE",
                                                ],
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
                                                            "type": [
                                                                "number",
                                                                "null"
                                                            ]
                                                        },
                                                        "unit": {
                                                            "type": [
                                                                "string",
                                                                "null"
                                                            ]
                                                        },
                                                        "needs_clarification": {
                                                            "type": "boolean"
                                                        },
                                                        "reason": {
                                                            "type": [
                                                                "string",
                                                                "null"
                                                            ]
                                                        },
                                                    },
                                                    "required": [
                                                        "item",
                                                        "qty",
                                                        "unit",
                                                        "needs_clarification",
                                                        "reason",
                                                    ],
                                                },
                                            },
                                            "from_item": {
                                                "type": [
                                                    "string",
                                                    "null"
                                                ]
                                            },
                                            "to_item": {
                                                "type": [
                                                    "string",
                                                    "null"
                                                ]
                                            },
                                            "amount": {
                                                "type": [
                                                    "number",
                                                    "null"
                                                ]
                                            },
                                            "note": {
                                                "type": [
                                                    "string",
                                                    "null"
                                                ]
                                            },
                                        },
                                        "required": [
                                            "type",
                                            "date",
                                            "items",
                                            "from_item",
                                            "to_item",
                                            "amount",
                                            "note",
                                        ],
                                    },
                                }
                            },
                            "required": ["events"],
                        },
                    }
                ],
                tool_choice={
                    "type": "tool",
                    "name": "emit_events",
                },
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            )

            for block in response.content:
                if block.type == "tool_use":
                    result = ExtractionResult.model_validate(
                        block.input
                    )

                    return result

            raise ValueError(
                "Claude did not return the expected tool output."
            )

        except Exception as e:

            if attempt == max_retries:
                raise RuntimeError(
                    f"Extraction failed after {max_retries + 1} attempts: {e}"
                ) from e

    raise RuntimeError("Unexpected extraction failure.")
