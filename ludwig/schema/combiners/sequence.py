from typing import Optional

from marshmallow_dataclass import dataclass

from ludwig.api_annotations import DeveloperAPI
from ludwig.constants import SEQUENCE
from ludwig.schema import utils as schema_utils
from ludwig.schema.combiners.base import BaseCombinerConfig
from ludwig.schema.encoders.base import BaseEncoderConfig
from ludwig.schema.encoders.utils import EncoderDataclassField
from ludwig.schema.metadata.combiner_metadata import COMBINER_METADATA


@DeveloperAPI
@dataclass(repr=False, order=True)
class SequenceCombinerConfig(BaseCombinerConfig):
    """Parameters for sequence combiner."""

    type: str = schema_utils.ProtectedString(
        "sequence",
        description="Type of combiner.",
    )

    main_sequence_feature: Optional[str] = schema_utils.String(
        default=None,
        description="",
        parameter_metadata=COMBINER_METADATA["SequenceCombiner"]["main_sequence_feature"],
    )

    encoder: BaseEncoderConfig = EncoderDataclassField(
        feature_type=SEQUENCE,
        default="parallel_cnn",
    )

    reduce_output: Optional[str] = schema_utils.ReductionOptions(
        default=None,
        description="",
        parameter_metadata=COMBINER_METADATA["SequenceCombiner"]["reduce_output"],
    )
