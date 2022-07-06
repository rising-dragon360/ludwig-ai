from typing import Optional

from marshmallow_dataclass import dataclass

from ludwig.constants import DATE
from ludwig.encoders.registry import get_encoder_classes
from ludwig.schema import utils as schema_utils
from ludwig.schema.features.base import BaseInputFeatureConfig
from ludwig.schema.preprocessing import BasePreprocessingConfig, PreprocessingDataclassField


@dataclass
class DateInputFeatureConfig(BaseInputFeatureConfig):
    """DateInputFeature is a dataclass that configures the parameters used for a date input feature."""

    preprocessing: BasePreprocessingConfig = PreprocessingDataclassField(feature_type=DATE)

    encoder: Optional[str] = schema_utils.StringOptions(
        list(get_encoder_classes(DATE).keys()),
        default="embed",
        description="Encoder to use for this date feature.",
    )
