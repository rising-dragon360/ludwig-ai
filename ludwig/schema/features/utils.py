from ludwig.schema import utils as schema_utils
from ludwig.utils.registry import Registry

input_type_registry = Registry()
input_config_registry = Registry()
output_type_registry = Registry()
output_config_registry = Registry()


def register_input_feature(name: str):
    def wrap(cls):
        input_type_registry[name] = cls
        return cls

    return wrap


def register_output_feature(name: str):
    def wrap(cls):
        output_type_registry[name] = cls
        return cls

    return wrap


def get_input_feature_jsonschema():
    """This function returns a JSON schema structured to only requires a `type` key and then conditionally applies
    a corresponding input feature's field constraints.

    Returns: JSON Schema
    """
    input_feature_types = sorted(list(input_type_registry.keys()))
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string", "enum": input_feature_types},
                "column": {"type": "string"},
            },
            "additionalProperties": True,
            "allOf": get_input_feature_conds(),
            "required": ["name", "type"],
        },
    }


def get_input_feature_conds():
    """This function returns a list of if-then JSON clauses for each input feature type along with their properties
    and constraints.

    Returns: List of JSON clauses
    """
    input_feature_types = sorted(list(input_type_registry.keys()))
    conds = []
    for feature_type in input_feature_types:
        feature_cls = input_type_registry[feature_type]
        schema_cls = feature_cls.get_schema_cls()
        feature_schema = schema_utils.unload_jsonschema_from_marshmallow_class(schema_cls)
        feature_props = feature_schema["properties"]
        feature_cond = schema_utils.create_cond({"type": feature_type}, feature_props)
        conds.append(feature_cond)
    return conds


def get_output_feature_jsonschema():
    """This function returns a JSON schema structured to only requires a `type` key and then conditionally applies
    a corresponding output feature's field constraints.

    Returns: JSON Schema
    """
    output_feature_types = sorted(list(output_type_registry.keys()))
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string", "enum": output_feature_types},
                "column": {"type": "string"},
            },
            "additionalProperties": True,
            "allOf": get_output_feature_conds(),
            "required": ["name", "type"],
        },
    }


def get_output_feature_conds():
    """This function returns a list of if-then JSON clauses for each output feature type along with their
    properties and constraints.

    Returns: List of JSON clauses
    """
    output_feature_types = sorted(list(output_type_registry.keys()))
    conds = []
    for feature_type in output_feature_types:
        feature_cls = output_type_registry[feature_type]
        schema_cls = feature_cls.get_schema_cls()
        feature_schema = schema_utils.unload_jsonschema_from_marshmallow_class(schema_cls)
        feature_props = feature_schema["properties"]
        feature_cond = schema_utils.create_cond({"type": feature_type}, feature_props)
        conds.append(feature_cond)
    return conds
