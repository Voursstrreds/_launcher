import os
import yaml
import cerberus
from Rules import ACTIVE_RULE_SET


def load_raw(filename: str) -> dict:

    load_val = ""

    with open(filename) as stream:
        try:
            load_val = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)

    return load_val


def normalise(instance_data: dict) -> dict:
    """
    Prepares a raw instance dict for validation:
      - Fields in ACTIVE_RULE_SET.list_fields that arrive as a plain string
        are split on whitespace into a Python list.
      - All other fields whose value is an integer are cast to str.
        YAML parses unquoted numeric values as int automatically; this
        coercion makes them valid against 'type: string' schema rules
        without requiring the user to quote every numeric argument.
      - Fields whose value is a dict have their values recursively coerced
        — any integer value inside the dict is cast to str. This covers
        dict-typed fields such as Environment where the user may write
        unquoted numeric values (e.g. PORT: 8080).
      - Fields whose value is a list have their elements coerced — any
        integer element inside the list is cast to str. This covers
        list-typed fields such as Ports, Command, and Entrypoint where
        the user may write unquoted numeric values (e.g. 5432:5432).
      - The Path field, if present and a string, is resolved to an
        absolute path using the process working directory.
    """
    result = {}
    for key, value in instance_data.items():
        if key in ACTIVE_RULE_SET.list_fields and isinstance(value, str):
            result[key] = value.split()
        elif key == 'Path' and isinstance(value, str):
            result[key] = os.path.abspath(value)
        elif isinstance(value, dict):
            result[key] = {
                k: str(v) if isinstance(v, int) else v
                for k, v in value.items()
            }
        elif isinstance(value, list):
            result[key] = [
                str(v) if isinstance(v, int) else v
                for v in value
            ]
        elif isinstance(value, int):
            result[key] = str(value)
        else:
            result[key] = value
    return result


def resolve_schema(instance_data: dict) -> dict | None:
    """
    Reads the discriminator field from a normalised instance block and
    returns the matching Cerberus schema dict.
    The discriminator field name is read from the active schema descriptor.
    Returns None if the discriminator value is missing or unrecognised.
    """
    discriminator_value = instance_data.get(ACTIVE_RULE_SET.discriminator)
    return ACTIVE_RULE_SET.schemas.get(discriminator_value)


def make_validator(schema: dict) -> cerberus.Validator:
    """
    Constructs a Cerberus Validator for the given schema dict.
    If the active descriptor permits unknown fields, the validator is
    configured to accept any extra field whose value is a string.
    """
    v = cerberus.Validator(schema)
    if ACTIVE_RULE_SET.allow_unknown_fields:
        v.allow_unknown = {'type': 'string'}
    return v


def validate_types(instances: dict) -> dict[str, list]:
    """
    Validates each instance block against the sub-schema selected by its
    discriminator field value.
    Returns a dict of {instance_name: [error messages]}.
    An empty dict means all instances passed.
    """
    errors = {}

    for name, raw_data in instances.items():
        data = normalise(raw_data)
        schema = resolve_schema(data)

        if schema is None:
            discriminator_value = raw_data.get(ACTIVE_RULE_SET.discriminator, '<missing>')
            errors[name] = (
                f"Unknown or missing {ACTIVE_RULE_SET.discriminator} "
                f"'{discriminator_value}'. "
                f"Allowed values: {list(ACTIVE_RULE_SET.schemas.keys())}"
            )
            continue

        v = make_validator(schema)
        if not v.validate(data):
            errors[name] = v.errors

    return errors


def validate_references(instances: dict) -> dict[str, list]:
    """
    Checks that every value referenced inside ref fields actually exists
    as a top-level key in the document.
    The set of ref fields is read from the active schema descriptor —
    no field names are hardcoded here.
    Returns {instance_name: [error messages]}.
    """
    known_names = set(instances.keys())
    errors = {}

    for name, raw_data in instances.items():
        data = normalise(raw_data)
        instance_errors = []

        for field in ACTIVE_RULE_SET.ref_fields:
            refs = data.get(field, [])
            for ref in refs:
                if ref not in known_names:
                    instance_errors.append(
                        f"'{field}' references unknown instance '{ref}'"
                    )

        if instance_errors:
            errors[name] = instance_errors

    return errors


def load_and_validate(filename: str) -> dict | None:
    instances = load_raw(filename)

    # Pass 1 — structural + type validation (sub-schema selected per instance)
    type_errors = validate_types(instances)
    if type_errors:
        print("Type validation failed:")
        for instance, errs in type_errors.items():
            print(f"  [{instance}]: {errs}")
        return None

    # Pass 2 — referential integrity
    ref_errors = validate_references(instances)
    if ref_errors:
        print("Reference validation failed:")
        for instance, errs in ref_errors.items():
            for e in errs:
                print(f"  [{instance}]: {e}")
        return None

    # Return normalised, validated records
    return {name: normalise(data) for name, data in instances.items()}
