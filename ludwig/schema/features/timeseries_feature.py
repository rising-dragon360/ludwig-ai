from marshmallow_dataclass import dataclass

from ludwig.constants import TIMESERIES
from ludwig.schema import utils as schema_utils
from ludwig.schema.encoders.base import BaseEncoderConfig
from ludwig.schema.encoders.utils import EncoderDataclassField
from ludwig.schema.features.base import BaseInputFeatureConfig
from ludwig.schema.features.preprocessing.base import BasePreprocessingConfig
from ludwig.schema.features.preprocessing.utils import PreprocessingDataclassField
from ludwig.schema.features.utils import input_config_registry


@input_config_registry.register(TIMESERIES)
@dataclass
class TimeseriesInputFeatureConfig(BaseInputFeatureConfig):
    """TimeseriesInputFeatureConfig is a dataclass that configures the parameters used for a timeseries input
    feature."""

    preprocessing: BasePreprocessingConfig = PreprocessingDataclassField(feature_type=TIMESERIES)

    encoder: BaseEncoderConfig = EncoderDataclassField(
        feature_type=TIMESERIES,
        default="parallel_cnn",
    )

    tied: str = schema_utils.String(
        default=None,
        allow_none=True,
        description="Name of input feature to tie the weights of the encoder with.  It needs to be the name of a "
        "feature of the same type and with the same encoder parameters.",
    )
