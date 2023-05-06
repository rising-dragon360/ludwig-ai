from abc import ABC
from typing import Optional, Type

from ludwig.api_annotations import DeveloperAPI
from ludwig.schema import utils as schema_utils
from ludwig.utils.registry import Registry

_adapter_registry = Registry()


@DeveloperAPI
def register_adapter(name: str):
    """Registers an adapter config class with the adapter config registry."""

    def wrap(adapter_config: BaseAdapterConfig):
        _adapter_registry[name] = adapter_config
        return adapter_config

    return wrap


@DeveloperAPI
def get_adapter_cls(name: str):
    """Returns the adapter config class registered with the given name."""
    return _adapter_registry[name]


@DeveloperAPI
@schema_utils.ludwig_dataclass
class BaseAdapterConfig(schema_utils.BaseMarshmallowConfig, ABC):
    """Base class for adapter configs.

    Not meant to be used directly.
    """

    task_type: str = schema_utils.ProtectedString("CAUSAL_LM")


@DeveloperAPI
@register_adapter("prompt_tuning")
@schema_utils.ludwig_dataclass
class PromptTuningAdapterConfig(BaseAdapterConfig):
    # Explicitly set type property in the config because it is needed when we
    # load a saved PEFT model back into Ludwig.
    type: str = schema_utils.ProtectedString("prompt_tuning")

    peft_type: str = schema_utils.ProtectedString("PROMPT_TUNING")

    num_virtual_tokens: Optional[int] = schema_utils.Integer(
        default=None,
        allow_none=True,
        description="Number of virtual tokens to add to the prompt. Virtual tokens are used to control the behavior of "
        " the model during inference. ",
    )

    token_dim: Optional[int] = schema_utils.Integer(
        default=None,
        allow_none=True,
        description="The hidden embedding dimension of the base transformer model.",
    )

    num_transformer_submodules: Optional[int] = schema_utils.Integer(
        default=None,
        allow_none=True,
        description="The number of transformer submodules in the base transformer model.",
    )

    num_attention_heads: Optional[int] = schema_utils.Integer(
        default=None,
        allow_none=True,
        description="The number of attention heads in the base transformer model.",
    )

    num_layers: Optional[int] = schema_utils.Integer(
        default=None,
        allow_none=True,
        description="The number of layers in the base transformer model.",
    )

    # TODO(Arnav): Refactor to allow both RANDOM and TEXT strategies
    prompt_tuning_init: str = schema_utils.ProtectedString(
        "TEXT", description="The type of initialization to use for the prompt embedding. "
    )

    prompt_tuning_init_text: str = schema_utils.String(
        default="",
        allow_none=False,
        description="The text to use to initialize the prompt embedding.",
    )


@DeveloperAPI
def get_adapter_conds():
    """Returns a JSON schema of conditionals to validate against adapter types."""
    conds = []
    for adapter in _adapter_registry:
        adapter_cls = _adapter_registry[adapter]
        other_props = schema_utils.unload_jsonschema_from_marshmallow_class(adapter_cls)["properties"]
        schema_utils.remove_duplicate_fields(other_props)
        preproc_cond = schema_utils.create_cond(
            {"type": adapter},
            other_props,
        )
        conds.append(preproc_cond)
    return conds


@DeveloperAPI
def AdapterDataclassField(default=None, description=""):
    class AdapterSelection(schema_utils.TypeSelection):
        def __init__(self):
            super().__init__(
                registry=_adapter_registry,
                default_value=default,
                description=description,
            )

        def get_schema_from_registry(self, key: str) -> Type[schema_utils.BaseMarshmallowConfig]:
            return get_adapter_cls(key)

        def _jsonschema_type_mapping(self):
            return {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": list(_adapter_registry.keys()),
                                "default": default,
                                "description": "The type of adapter to use for LLM fine-tuning",
                            },
                        },
                        "title": "adapter_options",
                        "allOf": get_adapter_conds(),
                        "required": ["type"],
                        "description": description,
                    },
                    {
                        "type": "null",
                    },
                ]
            }

    return AdapterSelection().get_default_field()
