from ludwig.api_annotations import DeveloperAPI
from ludwig.schema import utils as schema_utils
from ludwig.utils.registry import Registry

input_config_registry = Registry()
input_mixin_registry = Registry()
output_config_registry = Registry()
output_mixin_registry = Registry()


@DeveloperAPI
def get_input_feature_cls(name: str):
    return input_config_registry[name]


@DeveloperAPI
def get_output_feature_cls(name: str):
    return output_config_registry[name]


@DeveloperAPI
def get_input_feature_jsonschema():
    """This function returns a JSON schema structured to only requires a `type` key and then conditionally applies
    a corresponding input feature's field constraints.

    Returns: JSON Schema
    """
    input_feature_types = sorted(list(input_config_registry.keys()))
    return {
        "type": "array",
        "minItems": 1,
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "title": "name", "description": "Name of the input feature."},
                "type": {
                    "type": "string",
                    "enum": input_feature_types,
                    "title": "type",
                    "description": "Type of the input feature",
                },
                "column": {"type": "string", "title": "column", "description": "Name of the column."},
            },
            "additionalProperties": True,
            "allOf": get_input_feature_conds(),
            "required": ["name", "type"],
            "title": "input_features",
        },
        "uniqueItemProperties": ["name"],
    }


@DeveloperAPI
def get_input_feature_conds():
    """This function returns a list of if-then JSON clauses for each input feature type along with their properties
    and constraints.

    Returns: List of JSON clauses
    """
    input_feature_types = sorted(list(input_config_registry.keys()))
    conds = []
    for feature_type in input_feature_types:
        schema_cls = get_input_feature_cls(feature_type)
        feature_schema = schema_utils.unload_jsonschema_from_marshmallow_class(schema_cls)
        feature_props = feature_schema["properties"]
        schema_utils.remove_duplicate_fields(feature_props)
        feature_cond = schema_utils.create_cond({"type": feature_type}, feature_props)
        conds.append(feature_cond)
    return conds


@DeveloperAPI
def get_output_feature_jsonschema():
    """This function returns a JSON schema structured to only requires a `type` key and then conditionally applies
    a corresponding output feature's field constraints.

    Returns: JSON Schema
    """
    output_feature_types = sorted(list(output_config_registry.keys()))
    return {
        "type": "array",
        "minItems": 1,
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "title": "name", "description": "Name of the output feature."},
                "type": {
                    "type": "string",
                    "enum": output_feature_types,
                    "title": "type",
                    "description": "Type of the output feature",
                },
                "column": {"type": "string", "title": "column", "description": "Name of the column."},
            },
            "additionalProperties": True,
            "allOf": get_output_feature_conds(),
            "required": ["name", "type"],
            "title": "output_features",
        },
    }


@DeveloperAPI
def get_output_feature_conds():
    """This function returns a list of if-then JSON clauses for each output feature type along with their
    properties and constraints.

    Returns: List of JSON clauses
    """
    output_feature_types = sorted(list(output_config_registry.keys()))
    conds = []
    for feature_type in output_feature_types:
        schema_cls = get_output_feature_cls(feature_type)
        feature_schema = schema_utils.unload_jsonschema_from_marshmallow_class(schema_cls)
        feature_props = feature_schema["properties"]
        schema_utils.remove_duplicate_fields(feature_props)
        feature_cond = schema_utils.create_cond({"type": feature_type}, feature_props)
        conds.append(feature_cond)
    return conds
