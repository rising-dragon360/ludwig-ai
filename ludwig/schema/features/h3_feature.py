from typing import Optional

from marshmallow_dataclass import dataclass

from ludwig.constants import H3
from ludwig.encoders.registry import get_encoder_classes
from ludwig.schema import utils as schema_utils
from ludwig.schema.features.base import BaseInputFeatureConfig
from ludwig.schema.preprocessing import BasePreprocessingConfig, PreprocessingDataclassField


@dataclass
class H3InputFeatureConfig(BaseInputFeatureConfig):
    """H3InputFeatureConfig is a dataclass that configures the parameters used for an h3 input feature."""

    preprocessing: BasePreprocessingConfig = PreprocessingDataclassField(feature_type=H3)

    encoder: Optional[str] = schema_utils.StringOptions(
        list(get_encoder_classes(H3).keys()),
        default="embed",
        description="Encoder to use for this h3 feature.",
    )
