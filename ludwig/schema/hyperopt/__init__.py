from abc import ABC
from typing import Dict

from marshmallow_dataclass import dataclass

import ludwig.schema.hyperopt.parameter  # noqa: F401
from ludwig.api_annotations import DeveloperAPI
from ludwig.constants import LOSS, TEST, TRAIN, VALIDATION
from ludwig.modules import metric_modules  # noqa: Needed to ensure that the metric registry is populated.
from ludwig.modules.metric_registry import get_metric_registry
from ludwig.schema import utils as schema_utils
from ludwig.schema.hyperopt.executor import ExecutorConfig, ExecutorDataclassField
from ludwig.schema.hyperopt.search_algorithm import BaseSearchAlgorithmConfig, SearchAlgorithmDataclassField


@DeveloperAPI
@dataclass
class HyperoptConfig(schema_utils.BaseMarshmallowConfig, ABC):
    """Basic hyperopt settings."""

    output_feature: str = schema_utils.String(  # TODO: make more restrictive
        default="combined",
        description=(
            "The name of the output feature that we want to optimize the metric or loss of. Available values "
            "are `combined` or the name of any output feature provided in the configuration. `combined` is a special "
            "output feature that allows to optimize for the aggregated loss and metrics of all output features."
        ),
    )

    goal: str = schema_utils.StringOptions(
        options=["minimize", "maximize"],
        default="minimize",
        allow_none=False,
        description=(
            "Indicates if to minimize or maximize a metric or a loss of any of the output features on any of the "
            "dataset splits. Available values are: minimize (default) or maximize."
        ),
    )

    metric: str = schema_utils.StringOptions(
        options=get_metric_registry().keys(),
        default=LOSS,
        allow_none=False,
        description=(
            "The metric that we want to optimize for. The default one is loss, but depending on the type of the "
            "feature defined in output_feature, different metrics and losses are available. Check the metrics section "
            "of the specific output feature type to figure out what metrics are available to use."
        ),
    )

    split: str = schema_utils.StringOptions(
        options=[TRAIN, VALIDATION, TEST],
        default=VALIDATION,
        allow_none=False,
        description=(
            "The split of data that we want to compute our metric on. By default it is the validation split, but "
            "you have the flexibility to specify also train or test splits."
        ),
    )

    eval_split: str = schema_utils.StringOptions(
        options=[TRAIN, VALIDATION, TEST],
        default=VALIDATION,
        allow_none=False,
        description=(
            "The split of data that we want to run evaluation on. By default it is the validation split, but "
            "you have the flexibility to specify also train or test splits."
        ),
    )

    search_alg: BaseSearchAlgorithmConfig = SearchAlgorithmDataclassField(
        description=(
            "Specifies the algorithm to sample the defined parameters space. Candidate algorithms are those "
            "found in [Ray Tune's Search Algorithms](https://docs.ray.io/en/latest/tune/api/suggestion.html)."
        )
    )

    executor: ExecutorConfig = ExecutorDataclassField(
        description=(
            "specifies how to execute the hyperparameter optimization. The execution could happen locally in a serial "
            "manner or in parallel across multiple workers and with GPUs as well if available. The executor section "
            "includes specification for work scheduling and the number of samples to generate."
        )
    )

    parameters: Dict = schema_utils.Dict(
        allow_none=False,
        description=(
            "This section consists of a set of hyperparameters to optimize. They are provided as keys (the names of "
            "the parameters) and values associated with them (that define the search space). The values vary depending "
            "on the type of the hyperparameter. Syntax for this section is based on [Ray Tune's Search Space "
            "parameters](https://docs.ray.io/en/latest/tune/api/search_space.html)."
        ),
    )


@DeveloperAPI
def get_hyperopt_jsonschema():
    props = schema_utils.unload_jsonschema_from_marshmallow_class(HyperoptConfig)["properties"]

    return {
        "type": ["object", "null"],
        "properties": props,
        "title": "hyperopt_options",
        "description": "Settings for hyperopt",
    }


@DeveloperAPI
class HyperoptField(schema_utils.DictMarshmallowField):
    def __init__(self):
        super().__init__(HyperoptConfig, default_missing=True)

    def _jsonschema_type_mapping(self):
        return get_hyperopt_jsonschema()
