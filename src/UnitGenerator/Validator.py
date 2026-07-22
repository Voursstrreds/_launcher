import yaml
import cerberus


def load_raw(filename: str) -> dict | None:
    """
    Loads a YAML file and returns its parsed contents.

    Returns the parsed object on success, None on parse or I/O error
    (with the error printed to stdout for visibility).
    """
    try:
        with open(filename) as stream:
            return yaml.safe_load(stream)
    except (yaml.YAMLError, OSError) as exc:
        print(exc)
        return None


def validate(doc, schema: dict) -> tuple[bool, dict]:
    """
    Validates `doc` against the given Cerberus schema dict.

    Returns (ok, errors). `errors` is Cerberus's nested error dict;
    empty {} on success.
    """
    v = cerberus.Validator(schema)
    ok = v.validate(doc)
    return ok, v.errors


def load_and_validate(filename: str, rule_set) -> dict | None:
    """
    Loads `filename` as YAML and validates it against `rule_set.schema`.

    Returns the validated document on success, None on failure.
    Errors are printed to stdout for visibility.
    """
    doc = load_raw(filename)
    if doc is None:
        return None

    ok, errors = validate(doc, rule_set.schema)
    if not ok:
        print("Validation failed:")
        for field_name, err in errors.items():
            print(f"  [{field_name}]: {err}")
        return None

    return doc
