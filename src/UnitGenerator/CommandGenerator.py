from Rules import ACTIVE_RULE_SET


def build_all(validated_input: dict) -> list:
    """
    Iterates over the full validated input dict and produces one command
    object per instance, preserving document order.

    The full validated input dict is passed to ACTIVE_RULE_SET.command_builder
    alongside each individual instance so the builder has the global view
    required for calculations such as dependency inversion.

    CommandGenerator.py has no knowledge of the returned type or the
    building logic — both are entirely owned by the active rule-set.
    """
    return [
        ACTIVE_RULE_SET.command_builder(key, fields, validated_input)
        for key, fields in validated_input.items()
    ]
