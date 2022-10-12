from marshmallow_dataclass import dataclass

from ludwig.constants import AUDIO
from ludwig.schema.encoders.base import BaseEncoderConfig
from ludwig.schema.encoders.utils import EncoderDataclassField
from ludwig.schema.features.base import BaseInputFeatureConfig
from ludwig.schema.features.preprocessing.base import BasePreprocessingConfig
from ludwig.schema.features.preprocessing.utils import PreprocessingDataclassField
from ludwig.schema.features.utils import input_config_registry, input_mixin_registry
from ludwig.schema.utils import BaseMarshmallowConfig


@input_mixin_registry.register(AUDIO)
@dataclass
class AudioInputFeatureConfigMixin(BaseMarshmallowConfig):
    """AudioInputFeatureConfigMixin is a dataclass that configures the parameters used in both the audio input
    feature and the audio global defaults section of the Ludwig Config."""

    preprocessing: BasePreprocessingConfig = PreprocessingDataclassField(feature_type=AUDIO)

    encoder: BaseEncoderConfig = EncoderDataclassField(
        feature_type=AUDIO,
        default="parallel_cnn",
    )


@input_config_registry.register(AUDIO)
@dataclass(repr=False)
class AudioInputFeatureConfig(BaseInputFeatureConfig, AudioInputFeatureConfigMixin):
    """AudioInputFeatureConfig is a dataclass that configures the parameters used for an audio input feature."""

    pass
