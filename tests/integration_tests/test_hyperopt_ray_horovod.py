# Copyright (c) 2019 Uber Technologies, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
import os.path
import shutil
import uuid
from unittest.mock import patch

import pytest
from packaging import version

from ludwig.api import LudwigModel
from ludwig.callbacks import Callback
from ludwig.constants import ACCURACY, AUTO, EXECUTOR, MAX_CONCURRENT_TRIALS, TRAINER
from ludwig.globals import HYPEROPT_STATISTICS_FILE_NAME
from ludwig.hyperopt.results import HyperoptResults
from ludwig.hyperopt.run import hyperopt
from ludwig.hyperopt.utils import update_hyperopt_params_with_defaults
from ludwig.utils.defaults import merge_with_defaults
from tests.integration_tests.utils import binary_feature, create_data_set_to_use, generate_data, number_feature

try:
    import ray

    # Ray nightly version is always set to 3.0.0.dev0
    _ray_nightly = version.parse(ray.__version__) >= version.parse("3.0.0.dev0")
    _ray_114 = version.parse(ray.__version__) >= version.parse("1.14")
    if _ray_114:
        from ray.tune.syncer import get_node_to_storage_syncer, SyncConfig
    else:
        from ray.tune.syncer import get_sync_client

    from ludwig.backend.ray import RayBackend
    from ludwig.hyperopt.execution import _get_relative_checkpoints_dir_parts, RayTuneExecutor
except ImportError:
    ray = None
    _ray_nightly = False
    RayTuneExecutor = object

# Ray mocks

# Dummy sync templates
LOCAL_SYNC_TEMPLATE = "echo {source}/ {target}/"
LOCAL_DELETE_TEMPLATE = "echo {target}"


def mock_storage_client(path):
    """Mocks storage client that treats a local dir as durable storage."""
    os.makedirs(path, exist_ok=True)
    if _ray_114:
        syncer = get_node_to_storage_syncer(SyncConfig(upload_dir=path))
    else:
        syncer = get_sync_client(LOCAL_SYNC_TEMPLATE, LOCAL_DELETE_TEMPLATE)
    return syncer


HYPEROPT_CONFIG = {
    "parameters": {
        "trainer.learning_rate": {
            "space": "loguniform",
            "lower": 0.001,
            "upper": 0.1,
        },
        "combiner.num_fc_layers": {"space": "randint", "lower": 1, "upper": 3},
        "combiner.num_steps": {"space": "grid_search", "values": [1, 2, 3]},
    },
    "goal": "minimize",
}


SCENARIOS = [
    {
        "executor": {"type": "ray", "num_samples": 2, "cpu_resources_per_trial": 1},
        "search_alg": {"type": "variant_generator"},
    },
    # TODO(shreya): Uncomment when https://github.com/ludwig-ai/ludwig/issues/2039 is fixed.
    # {
    #     "type": "ray",
    #     "num_samples": 1,
    #     "scheduler": {
    #         "type": "async_hyperband",
    #         "time_attr": "training_iteration",
    #         "reduction_factor": 2,
    #         "dynamic_resource_allocation": True,
    #     },
    # },
    {
        "executor": {
            "type": "ray",
            "num_samples": 3,
            "scheduler": {
                "type": "hb_bohb",
                "time_attr": "training_iteration",
                "reduction_factor": 4,
            },
            "cpu_resources_per_trial": 1,
        },
        "search_alg": {"type": "bohb"},
    },
]


# TODO ray: replace legacy mode when Ray Train supports placement groups
RAY_BACKEND_KWARGS = {"processor": {"parallelism": 4}}


def _get_config(search_alg, executor):
    input_features = [number_feature(), number_feature()]
    output_features = [binary_feature()]

    return {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat", "num_fc_layers": 2},
        TRAINER: {"epochs": 1, "learning_rate": 0.001},
        "hyperopt": {
            **HYPEROPT_CONFIG,
            "executor": executor,
            "search_alg": search_alg,
        },
    }


class MockRayTuneExecutor(RayTuneExecutor):
    def _get_sync_client_and_remote_checkpoint_dir(self, trial_dir):
        remote_checkpoint_dir = os.path.join(self.mock_path, *_get_relative_checkpoints_dir_parts(trial_dir))
        return mock_storage_client(remote_checkpoint_dir), remote_checkpoint_dir


class TestCallback(Callback):
    def __init__(self):
        self.preprocessed = False

    def on_hyperopt_preprocessing_start(self, *args, **kwargs):
        self.preprocessed = True

    def on_hyperopt_start(self, *args, **kwargs):
        assert self.preprocessed


@pytest.fixture
def ray_mock_dir():
    path = os.path.join(ray._private.utils.get_user_temp_dir(), f"mock-client-{uuid.uuid4().hex[:4]}") + os.sep
    os.makedirs(path, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path)


def run_hyperopt_executor(
    search_alg,
    executor,
    csv_filename,
    ray_mock_dir,
    validate_output_feature=False,
    validation_metric=None,
):
    config = _get_config(search_alg, executor)

    csv_filename = os.path.join(ray_mock_dir, "dataset.csv")
    dataset_csv = generate_data(config["input_features"], config["output_features"], csv_filename, num_examples=100)
    dataset_parquet = create_data_set_to_use("parquet", dataset_csv)

    config = merge_with_defaults(config)

    hyperopt_config = config["hyperopt"]

    if validate_output_feature:
        hyperopt_config["output_feature"] = config["output_features"][0]["name"]
    if validation_metric:
        hyperopt_config["validation_metric"] = validation_metric

    backend = RayBackend(**RAY_BACKEND_KWARGS)
    update_hyperopt_params_with_defaults(hyperopt_config)
    if hyperopt_config[EXECUTOR].get(MAX_CONCURRENT_TRIALS) == AUTO:
        hyperopt_config[EXECUTOR][MAX_CONCURRENT_TRIALS] = backend.max_concurrent_trials(hyperopt_config)

    parameters = hyperopt_config["parameters"]
    if search_alg.get("type", "") == "bohb":
        # bohb does not support grid_search search space
        del parameters["combiner.num_steps"]
        hyperopt_config["parameters"] = parameters

    split = hyperopt_config["split"]
    output_feature = hyperopt_config["output_feature"]
    metric = hyperopt_config["metric"]
    goal = hyperopt_config["goal"]
    search_alg = hyperopt_config["search_alg"]

    # preprocess
    model = LudwigModel(config=config, backend=backend)
    training_set, validation_set, test_set, training_set_metadata = model.preprocess(
        dataset=dataset_parquet,
    )

    # hyperopt
    hyperopt_executor = MockRayTuneExecutor(
        parameters, output_feature, metric, goal, split, search_alg=search_alg, **hyperopt_config[EXECUTOR]
    )
    hyperopt_executor.mock_path = os.path.join(ray_mock_dir, "bucket")

    hyperopt_executor.execute(
        config,
        training_set=training_set,
        validation_set=validation_set,
        test_set=test_set,
        training_set_metadata=training_set_metadata,
        backend=backend,
        output_directory=ray_mock_dir,
        skip_save_processed_input=True,
        skip_save_unprocessed_output=True,
    )


@pytest.mark.skipif(_ray_nightly, reason="https://github.com/ludwig-ai/ludwig/issues/2451")
@pytest.mark.distributed
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_hyperopt_executor(scenario, csv_filename, ray_mock_dir, ray_cluster_7cpu):
    search_alg = scenario["search_alg"]
    executor = scenario["executor"]
    run_hyperopt_executor(search_alg, executor, csv_filename, ray_mock_dir)


@pytest.mark.skip(reason="https://github.com/ludwig-ai/ludwig/issues/1441")
@pytest.mark.distributed
def test_hyperopt_executor_with_metric(csv_filename, ray_mock_dir, ray_cluster_7cpu):
    run_hyperopt_executor(
        # {"type": "ray", "num_samples": 2},
        # {"type": "ray"},
        {"type": "variant_generator"},  # search_alg
        {"type": "ray", "num_samples": 2},  # executor
        csv_filename,
        ray_mock_dir,
        validate_output_feature=True,
        validation_metric=ACCURACY,
    )


@pytest.mark.skip(reason="https://github.com/ludwig-ai/ludwig/issues/1441")
@pytest.mark.distributed
@patch("ludwig.hyperopt.execution.RayTuneExecutor", MockRayTuneExecutor)
def test_hyperopt_run_hyperopt(csv_filename, ray_mock_dir, ray_cluster_7cpu):
    input_features = [number_feature(), number_feature()]
    output_features = [binary_feature()]

    csv_filename = os.path.join(ray_mock_dir, "dataset.csv")
    dataset_csv = generate_data(input_features, output_features, csv_filename, num_examples=100)
    dataset_parquet = create_data_set_to_use("parquet", dataset_csv)

    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat", "num_fc_layers": 2},
        TRAINER: {"epochs": 4, "learning_rate": 0.001},
        "backend": {"type": "ray", **RAY_BACKEND_KWARGS},
    }

    output_feature_name = output_features[0]["name"]

    hyperopt_configs = {
        "parameters": {
            "trainer.learning_rate": {
                "space": "loguniform",
                "lower": 0.001,
                "upper": 0.1,
            },
            output_feature_name + ".output_size": {"space": "randint", "lower": 2, "upper": 8},
            output_feature_name + ".num_fc_layers": {"space": "randint", "lower": 1, "upper": 3},
        },
        "goal": "minimize",
        "output_feature": output_feature_name,
        "validation_metrics": "loss",
        "executor": {"type": "ray", "num_samples": 2},
        "search_alg": {"type": "variant_generator"},
    }

    # add hyperopt parameter space to the config
    config["hyperopt"] = hyperopt_configs
    run_hyperopt(config, dataset_parquet, ray_mock_dir)


def run_hyperopt(
    config,
    rel_path,
    out_dir,
    experiment_name="ray_hyperopt",
):
    callback = TestCallback()
    hyperopt_results = hyperopt(
        config,
        dataset=rel_path,
        output_directory=out_dir,
        experiment_name=experiment_name,
        callbacks=[callback],
    )

    # check for return results
    assert isinstance(hyperopt_results, HyperoptResults)

    # check for existence of the hyperopt statistics file
    assert os.path.isfile(os.path.join(out_dir, experiment_name, HYPEROPT_STATISTICS_FILE_NAME))
