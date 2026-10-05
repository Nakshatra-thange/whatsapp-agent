from .normalizer import normalize_quantity


def build_alias_map(prices):
    aliases = {}

    for canonical_name, info in prices.items():
        # Canonical name
        aliases[canonical_name.lower()] = canonical_name

        # Aliases
        for alias in info.get("aliases", []):
            aliases[alias.lower()] = canonical_name

    return aliases


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

            for item in event.get("items", []):

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

                # Normalize quantity
                target_unit = prices[canonical_name]["unit"]

                try:
                    item["qty"] = normalize_quantity(
                        item["qty"],
                        item["unit"],
                        target_unit,
                    )

                    item["unit"] = target_unit

                except ValueError as e:
                    errors.append(str(e))

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