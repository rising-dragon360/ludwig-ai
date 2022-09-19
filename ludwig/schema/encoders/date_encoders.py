from typing import List

from marshmallow_dataclass import dataclass

from ludwig.constants import DATE
from ludwig.schema import utils as schema_utils
from ludwig.schema.encoders.base import BaseEncoderConfig
from ludwig.schema.encoders.utils import register_encoder_config
from ludwig.schema.metadata.encoder_metadata import ENCODER_METADATA


@register_encoder_config("embed", DATE)
@dataclass
class DateEmbedConfig(BaseEncoderConfig):

    type: str = schema_utils.StringOptions(
        ["embed"],
        default="embed",
        allow_none=False,
        description="Type of encoder.",
    )

    embedding_size: int = schema_utils.PositiveInteger(
        default=10,
        description="The maximum embedding size adopted.",
        parameter_metadata=ENCODER_METADATA["DateEmbed"]["embedding_size"],
    )

    embeddings_on_cpu: bool = schema_utils.Boolean(
        default=False,
        description="Whether to force the placement of the embedding matrix in regular memory and have the CPU "
        "resolve them.",
        parameter_metadata=ENCODER_METADATA["DateEmbed"]["embeddings_on_cpu"],
    )

    # TODO (Connor): Add nesting logic for fc_layers, see fully_connected_module.py
    fc_layers: List[dict] = schema_utils.DictList(
        default=None,
        description="List of dictionaries containing the parameters for each fully connected layer.",
        parameter_metadata=ENCODER_METADATA["DateEmbed"]["fc_layers"],
    )

    num_fc_layers: int = schema_utils.NonNegativeInteger(
        default=0,
        description="The number of stacked fully connected layers.",
        parameter_metadata=ENCODER_METADATA["DateEmbed"]["num_fc_layers"],
    )

    output_size: int = schema_utils.PositiveInteger(
        default=10,
        description="If an output_size is not already specified in fc_layers this is the default output_size that "
        "will be used for each layer. It indicates the size of the output of a fully connected layer.",
        parameter_metadata=ENCODER_METADATA["DateEmbed"]["output_size"],
    )

    use_bias: bool = schema_utils.Boolean(
        default=True,
        description="Whether the layer uses a bias vector.",
        parameter_metadata=ENCODER_METADATA["DateEmbed"]["use_bias"],
    )

    weights_initializer: str = schema_utils.InitializerOptions(
        description="Initializer to use for the weights matrix.",
        parameter_metadata=ENCODER_METADATA["DateEmbed"]["weights_initializer"],
    )

    bias_initializer: str = schema_utils.InitializerOptions(
        default="zeros",
        description="Initializer to use for the bias vector.",
        parameter_metadata=ENCODER_METADATA["DateEmbed"]["bias_initializer"],
    )

    norm: str = schema_utils.StringOptions(
        ["batch", "layer"],
        default=None,
        description="The default norm that will be used for each layer.",
        parameter_metadata=ENCODER_METADATA["DateEmbed"]["norm"],
    )

    norm_params: dict = schema_utils.Dict(
        default=None,
        description="Parameters used if norm is either `batch` or `layer`.",
        parameter_metadata=ENCODER_METADATA["DateEmbed"]["norm_params"],
    )

    activation: str = schema_utils.ActivationOptions(
        description="The default activation function that will be used for each layer.",
        parameter_metadata=ENCODER_METADATA["DateEmbed"]["activation"],
    )

    dropout: float = schema_utils.FloatRange(
        default=0.0,
        min=0,
        max=1,
        description="Dropout probability for the embedding.",
        parameter_metadata=ENCODER_METADATA["DateEmbed"]["dropout"],
    )


@register_encoder_config("wave", DATE)
@dataclass
class DateWaveConfig(BaseEncoderConfig):

    type: str = schema_utils.StringOptions(
        ["wave"],
        default="wave",
        allow_none=False,
        description="Type of encoder.",
    )

    # TODO (Connor): Add nesting logic for fc_layers, see fully_connected_module.py
    fc_layers: List[dict] = schema_utils.DictList(
        default=None,
        description="List of dictionaries containing the parameters for each fully connected layer.",
        parameter_metadata=ENCODER_METADATA["DateWave"]["fc_layers"],
    )

    num_fc_layers: int = schema_utils.PositiveInteger(
        default=1,
        description="The number of stacked fully connected layers.",
        parameter_metadata=ENCODER_METADATA["DateWave"]["num_fc_layers"],
    )

    output_size: int = schema_utils.PositiveInteger(
        default=10,
        description="If an output_size is not already specified in fc_layers this is the default output_size that "
        "will be used for each layer. It indicates the size of the output of a fully connected layer.",
        parameter_metadata=ENCODER_METADATA["DateWave"]["output_size"],
    )

    use_bias: bool = schema_utils.Boolean(
        default=True,
        description="Whether the layer uses a bias vector.",
        parameter_metadata=ENCODER_METADATA["DateWave"]["use_bias"],
    )

    weights_initializer: str = schema_utils.InitializerOptions(
        description="Initializer to use for the weights matrix.",
        parameter_metadata=ENCODER_METADATA["DateWave"]["weights_initializer"],
    )

    bias_initializer: str = schema_utils.InitializerOptions(
        default="zeros",
        description="Initializer to use for the bias vector.",
        parameter_metadata=ENCODER_METADATA["DateWave"]["bias_initializer"],
    )

    norm: str = schema_utils.StringOptions(
        ["batch", "layer"],
        default=None,
        description="The default norm that will be used for each layer.",
        parameter_metadata=ENCODER_METADATA["DateWave"]["norm"],
    )

    norm_params: dict = schema_utils.Dict(
        default=None,
        description="Parameters used if norm is either `batch` or `layer`.",
        parameter_metadata=ENCODER_METADATA["DateWave"]["norm_params"],
    )

    activation: str = schema_utils.ActivationOptions(
        description="The default activation function that will be used for each layer.",
        parameter_metadata=ENCODER_METADATA["DateWave"]["activation"],
    )

    dropout: float = schema_utils.FloatRange(
        default=0.0,
        min=0,
        max=1,
        description="Dropout probability for the embedding.",
        parameter_metadata=ENCODER_METADATA["DateWave"]["dropout"],
    )
