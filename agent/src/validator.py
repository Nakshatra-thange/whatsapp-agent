from decimal import Decimal

from .normalizer import canonical_unit, normalize_quantity

# Units that are sold whole: "1.5 bottles" is not a real order.
WHOLE_UNITS = {"piece", "packet", "bottle", "bar", "tube"}


def build_alias_map(prices):
    aliases = {}

    for canonical_name, info in prices.items():
        # Canonical name
        aliases[canonical_name.lower()] = canonical_name

        # Aliases
        for alias in info.get("aliases", []):
            aliases[alias.lower()] = canonical_name

    return aliases


def normalize_order_items(items, prices):
    """
    Check every item of one ORDER against prices.json.

    Returns (clean_items, problems). An order may only be stored when
    problems is empty; otherwise the customer is asked to clarify.
    clean_items: [{"item", "qty" (Decimal), "unit"}]
    """

    alias_map = build_alias_map(prices)
    clean, problems = [], []

    if not items:
        return [], ["order mein koi item nahi mila"]

    for raw in items:
        name = (raw.get("item") or "").strip()
        canonical = alias_map.get(name.lower())

        if canonical is None:
            problems.append(f"'{name}' hamari price list mein nahi hai")
            continue

        if raw.get("needs_clarification"):
            problems.append(
                f"{canonical}: {raw.get('reason') or 'quantity clear nahi hai'}"
            )
            continue

        if raw.get("qty") is None or not raw.get("unit"):
            problems.append(f"{canonical}: kitna chahiye, yeh clear nahi hai")
            continue

        info = prices[canonical]
        try:
            qty = normalize_quantity(
                raw["qty"], raw["unit"], info["unit"], info.get("conversions"),
            )
        except ValueError:
            problems.append(
                f"{canonical}: '{raw['qty']} {raw['unit']}' samajh nahi aaya"
            )
            continue

        qty = Decimal(str(qty))
        if qty <= 0:
            problems.append(f"{canonical}: quantity zero se zyada honi chahiye")
            continue

        if canonical_unit(info["unit"]) in WHOLE_UNITS and qty != qty.to_integral_value():
            problems.append(f"{canonical}: poore {info['unit']} mein batayein")
            continue

        clean.append({"item": canonical, "qty": qty, "unit": info["unit"]})

    return clean, problems


def validate_and_normalize(result, prices):
    """
    Validate extracted events and normalize them
    into the canonical format expected by ledger.py.
    """

    alias_map = build_alias_map(prices)

    # ExtractionResult -> Python dict
    events = result.model_dump()["events"]

    errors = []

    for event in events:

        event_type = event["type"]

        # -------------------------
        # ORDER
        # -------------------------

        if event_type == "ORDER":

            for item in event.get("items") or []:

                item_name = item["item"].lower().strip()

                # Check item exists
                if item_name not in alias_map:
                    errors.append(
                        f"Unknown item: {item['item']}"
                    )
                    continue

                # Convert alias -> canonical name
                canonical_name = alias_map[item_name]
                item["item"] = canonical_name

                # A missing quantity is never guessed; ledger.py
                # leaves the bill unknown for flagged items.
                if item["qty"] is None or item["unit"] is None:
                    item["needs_clarification"] = True
                    item["reason"] = item.get("reason") or "quantity not stated"
                    continue

                # Normalize quantity
                target_unit = prices[canonical_name]["unit"]

                try:
                    item["qty"] = normalize_quantity(
                        item["qty"],
                        item["unit"],
                        target_unit,
                        prices[canonical_name].get("conversions"),
                    )

                    item["unit"] = target_unit

                except ValueError as e:
                    errors.append(str(e))

        # -------------------------
        # SUBSTITUTE
        # -------------------------

        elif event_type == "SUBSTITUTE":

            for field in ("from_item", "to_item"):
                name = (event.get(field) or "").lower().strip()
                if name not in alias_map:
                    errors.append(
                        f"Unknown item in substitution: {event.get(field)}"
                    )
                else:
                    event[field] = alias_map[name]

        # -------------------------
        # PAYMENT
        # -------------------------

        elif event_type == "PAYMENT":

            if event.get("amount") is None:
                errors.append(
                    "PAYMENT event has no amount"
                )

            elif event["amount"] < 0:
                errors.append(
                    "PAYMENT amount cannot be negative"
                )

    if errors:
        raise ValueError(
            "Validation failed:\n" +
            "\n".join(errors)
        )

    return events
