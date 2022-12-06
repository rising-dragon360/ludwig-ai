from typing import Dict

from ludwig.api_annotations import DeveloperAPI
from ludwig.constants import MODEL_GBM
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


def prune_gbm_features(schema: Dict):
    """Removes unsupported feature types from the given JSON schema.

    Designed for use with `get_{input/output}_feature_jsonschema`.
    """
    gbm_feature_types = ["binary", "category", "number"]
    pruned_all_of = []
    for cond in schema["items"]["allOf"]:
        if_type = cond["if"]["properties"]["type"]["const"]
        if if_type in gbm_feature_types:
            pruned_all_of += [cond]
    schema["items"]["allOf"] = pruned_all_of


@DeveloperAPI
def get_input_feature_jsonschema(model_type: str):
    """This function returns a JSON schema structured to only requires a `type` key and then conditionally applies
    a corresponding input feature's field constraints.

    Returns: JSON Schema
    """
    input_feature_types = sorted(list(input_config_registry.keys()))
    schema = {
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

    if model_type == MODEL_GBM:
        prune_gbm_features(schema)

    return schema


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
def get_output_feature_jsonschema(model_type: str):
    """This function returns a JSON schema structured to only requires a `type` key and then conditionally applies
    a corresponding output feature's field constraints.

    Returns: JSON Schema
    """
    output_feature_types = sorted(list(output_config_registry.keys()))
    schema = {
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

    if model_type == MODEL_GBM:
        prune_gbm_features(schema)
        schema["maxItems"] = 1

    return schema


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
