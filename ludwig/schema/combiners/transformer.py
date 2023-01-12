from typing import Optional

from marshmallow_dataclass import dataclass

from ludwig.api_annotations import DeveloperAPI
from ludwig.schema import utils as schema_utils
from ludwig.schema.combiners.base import BaseCombinerConfig
from ludwig.schema.combiners.common_transformer_options import CommonTransformerConfig
from ludwig.schema.metadata import COMBINER_METADATA


@DeveloperAPI
@dataclass(repr=False, order=True)
class TransformerCombinerConfig(BaseCombinerConfig, CommonTransformerConfig):
    """Parameters for transformer combiner."""

    @staticmethod
    def module_name():
        return "TransformerCombiner"

    type: str = schema_utils.ProtectedString(
        "transformer",
        description=COMBINER_METADATA["TransformerCombiner"]["type"].long_description,
    )

    reduce_output: Optional[str] = schema_utils.ReductionOptions(
        default="mean", description="", parameter_metadata=COMBINER_METADATA["TransformerCombiner"]["reduce_output"]
    )
