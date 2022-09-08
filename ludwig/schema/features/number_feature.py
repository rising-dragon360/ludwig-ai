from typing import List, Tuple, Union

from marshmallow_dataclass import dataclass

from ludwig.constants import MEAN_SQUARED_ERROR, NUMBER
from ludwig.schema import utils as schema_utils
from ludwig.schema.decoders.base import BaseDecoderConfig
from ludwig.schema.decoders.utils import DecoderDataclassField
from ludwig.schema.encoders.base import BaseEncoderConfig
from ludwig.schema.encoders.utils import EncoderDataclassField
from ludwig.schema.features.base import BaseInputFeatureConfig, BaseOutputFeatureConfig
from ludwig.schema.features.loss.loss import BaseLossConfig
from ludwig.schema.features.loss.utils import LossDataclassField
from ludwig.schema.features.preprocessing.base import BasePreprocessingConfig
from ludwig.schema.features.preprocessing.utils import PreprocessingDataclassField
from ludwig.schema.features.utils import input_config_registry, output_config_registry


@input_config_registry.register(NUMBER)
@dataclass
class NumberInputFeatureConfig(BaseInputFeatureConfig):
    """NumberInputFeatureConfig is a dataclass that configures the parameters used for a number input feature."""

    preprocessing: BasePreprocessingConfig = PreprocessingDataclassField(feature_type=NUMBER)

    encoder: BaseEncoderConfig = EncoderDataclassField(
        feature_type=NUMBER,
        default="passthrough",
    )


@output_config_registry.register(NUMBER)
@dataclass
class NumberOutputFeatureConfig(BaseOutputFeatureConfig):
    """NumberOutputFeatureConfig is a dataclass that configures the parameters used for a category output
    feature."""

    preprocessing: BasePreprocessingConfig = PreprocessingDataclassField(feature_type="number_output")

    loss: BaseLossConfig = LossDataclassField(
        feature_type=NUMBER,
        default=MEAN_SQUARED_ERROR,
    )

    clip: Union[List[int], Tuple[int]] = schema_utils.FloatRangeTupleDataclassField(
        n=2,
        default=None,
        allow_none=True,
        min=0,
        max=999999999,
        description="Clip the predicted output to the specified range.",
    )

    decoder: BaseDecoderConfig = DecoderDataclassField(
        feature_type=NUMBER,
        default="regressor",
    )

    reduce_input: str = schema_utils.ReductionOptions(
        default="sum",
        description="How to reduce an input that is not a vector, but a matrix or a higher order tensor, on the first "
        "dimension (second if you count the batch dimension)",
    )

    dependencies: list = schema_utils.List(
        default=[],
        description="List of input features that this feature depends on.",
    )

    reduce_dependencies: str = schema_utils.ReductionOptions(
        default="sum",
        description="How to reduce the dependencies of the output feature.",
    )
