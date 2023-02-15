from typing import Any, Dict, List, Optional, Union

from ludwig.api_annotations import DeveloperAPI
from ludwig.schema import utils as schema_utils
from ludwig.schema.combiners.base import BaseCombinerConfig
from ludwig.schema.metadata import COMBINER_METADATA
from ludwig.schema.utils import ludwig_dataclass


@DeveloperAPI
@ludwig_dataclass
class ConcatCombinerConfig(BaseCombinerConfig):
    """Parameters for concat combiner."""

    @staticmethod
    def module_name():
        return "ConcatCombiner"

    type: str = schema_utils.ProtectedString(
        "concat",
        description=COMBINER_METADATA["ConcatCombiner"]["type"].long_description,
    )

    dropout: float = schema_utils.FloatRange(
        default=0.0,
        min=0,
        max=1,
        description="",
        parameter_metadata=COMBINER_METADATA["ConcatCombiner"]["dropout"],
    )

    activation: str = schema_utils.ActivationOptions(
        default="relu",
        description="",
        parameter_metadata=COMBINER_METADATA["ConcatCombiner"]["activation"],
    )

    flatten_inputs: bool = schema_utils.Boolean(
        default=False,
        description="Whether to flatten input tensors to a vector.",
        parameter_metadata=COMBINER_METADATA["ConcatCombiner"]["flatten_inputs"],
    )

    residual: bool = schema_utils.Boolean(
        default=False,
        description="Whether to add a residual connection to each fully connected layer block. All fully connected "
        "layers must have the same size ",
        parameter_metadata=COMBINER_METADATA["ConcatCombiner"]["residual"],
    )

    use_bias: bool = schema_utils.Boolean(
        default=True,
        description="Whether the layer uses a bias vector.",
        parameter_metadata=COMBINER_METADATA["ConcatCombiner"]["use_bias"],
    )

    bias_initializer: Union[str, Dict] = schema_utils.InitializerOrDict(
        default="zeros",
        description="",
        parameter_metadata=COMBINER_METADATA["ConcatCombiner"]["bias_initializer"],
    )

    weights_initializer: Union[str, Dict] = schema_utils.InitializerOrDict(
        default="xavier_uniform",
        description="",
        parameter_metadata=COMBINER_METADATA["ConcatCombiner"]["weights_initializer"],
    )

    num_fc_layers: int = schema_utils.NonNegativeInteger(
        default=0,
        allow_none=False,
        description="",
        parameter_metadata=COMBINER_METADATA["ConcatCombiner"]["num_fc_layers"],
    )

    output_size: int = schema_utils.PositiveInteger(
        default=256,
        description="Output size of a fully connected layer.",
        parameter_metadata=COMBINER_METADATA["ConcatCombiner"]["output_size"],
    )

    norm: Optional[str] = schema_utils.StringOptions(
        ["batch", "layer", "ghost"],
        default=None,
        allow_none=True,
        description="",
        parameter_metadata=COMBINER_METADATA["ConcatCombiner"]["norm"],
    )

    norm_params: Optional[dict] = schema_utils.Dict(
        description="",
        parameter_metadata=COMBINER_METADATA["ConcatCombiner"]["norm_params"],
    )

    fc_layers: Optional[List[Dict[str, Any]]] = schema_utils.DictList(
        description="",
        parameter_metadata=COMBINER_METADATA["ConcatCombiner"]["fc_layers"],
    )
