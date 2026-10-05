def build_alias_map(prices):
    aliases = {}

    for canonical_name, info in prices.items():
        # Canonical name
        aliases[canonical_name.lower()] = canonical_name

        # Aliases
        for alias in info.get("aliases", []):
            aliases[alias.lower()] = canonical_name

    return aliases


def validate_events(events, prices):
    alias_map = build_alias_map(prices)

    errors = []

    for event in events:

        event_type = event["type"]

        # Validate ORDER
        if event_type == "ORDER":

            for item in event.get("items", []):

                item_name = item["item"].lower().strip()

                if item_name not in alias_map:
                    errors.append(
                        f"Unknown item: {item['item']}"
                    )

        # Validate PAYMENT
        elif event_type == "PAYMENT":

            if "amount" not in event:
                errors.append(
                    "PAYMENT event has no amount"
                )

            elif event["amount"] < 0:
                errors.append(
                    "PAYMENT amount cannot be negative"
                )

    return errors