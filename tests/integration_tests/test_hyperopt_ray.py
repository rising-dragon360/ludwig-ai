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
import contextlib
import logging
import os.path

import mlflow
import pandas as pd
import pytest
from mlflow.tracking import MlflowClient

from ludwig.backend import initialize_backend
from ludwig.callbacks import Callback
from ludwig.constants import ACCURACY, AUTO, EXECUTOR, MAX_CONCURRENT_TRIALS, TRAINER
from ludwig.contribs import MlflowCallback
from ludwig.globals import HYPEROPT_STATISTICS_FILE_NAME
from ludwig.hyperopt.results import HyperoptResults
from ludwig.hyperopt.run import hyperopt
from ludwig.hyperopt.utils import update_hyperopt_params_with_defaults
from ludwig.utils.defaults import merge_with_defaults
from tests.integration_tests.utils import category_feature, generate_data, text_feature

try:
    import ray

    from ludwig.hyperopt.execution import get_build_hyperopt_executor
except ImportError:
    ray = None


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.getLogger("ludwig").setLevel(logging.INFO)

HYPEROPT_CONFIG = {
    "parameters": {
        "trainer.learning_rate": {
            "space": "loguniform",
            "lower": 0.001,
            "upper": 0.1,
        },
        "combiner.num_fc_layers": {"space": "randint", "lower": 2, "upper": 6},
        "utterance.cell_type": {"space": "grid_search", "values": ["rnn", "gru"]},
        "utterance.bidirectional": {"space": "choice", "categories": [True, False]},
        "utterance.fc_layers": {
            "space": "choice",
            "categories": [
                [{"output_size": 64}, {"output_size": 32}],
                [{"output_size": 64}],
                [{"output_size": 32}],
            ],
        },
    },
    "goal": "minimize",
}


SCENARIOS = [
    {"executor": {"type": "ray"}, "search_alg": {"type": "variant_generator"}},
    {"executor": {"type": "ray", "num_samples": 2}, "search_alg": {"type": "variant_generator"}},
    {
        "executor": {
            "type": "ray",
            "num_samples": 3,
            "scheduler": {
                "type": "hb_bohb",
                "time_attr": "training_iteration",
                "reduction_factor": 4,
            },
        },
        "search_alg": {"type": "bohb"},
    },
]


def _get_config(search_alg, executor):
    input_features = [
        text_feature(name="utterance", encoder={"cell_type": "lstm", "reduce_output": "sum"}),
        category_feature(encoder={"vocab_size": 2}, reduce_input="sum"),
    ]

    output_features = [category_feature(decoder={"vocab_size": 2}, reduce_input="sum")]

    return {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat", "num_fc_layers": 2},
        TRAINER: {"epochs": 2, "learning_rate": 0.001},
        "hyperopt": {
            **HYPEROPT_CONFIG,
            "executor": executor,
            "search_alg": search_alg,
        },
    }


@contextlib.contextmanager
def ray_start_4_cpus():
    res = ray.init(
        num_cpus=4,
        include_dashboard=False,
        object_store_memory=150 * 1024 * 1024,
    )
    try:
        yield res
    finally:
        ray.shutdown()


@pytest.fixture(scope="module")
def ray_cluster_4cpu():
    with ray_start_4_cpus():
        yield


def run_hyperopt_executor(
    search_alg,
    executor,
    csv_filename,
    tmpdir,
    validate_output_feature=False,
    validation_metric=None,
    use_split=True,
):
    config = _get_config(search_alg, executor)
    rel_path = generate_data(config["input_features"], config["output_features"], csv_filename)

    if not use_split:
        df = pd.read_csv(rel_path)
        df["split"] = 0
        df.to_csv(rel_path)

    config = merge_with_defaults(config)

    hyperopt_config = config["hyperopt"]

    if validate_output_feature:
        hyperopt_config["output_feature"] = config["output_features"][0]["name"]
    if validation_metric:
        hyperopt_config["validation_metric"] = validation_metric

    backend = initialize_backend("local")
    update_hyperopt_params_with_defaults(hyperopt_config)
    if hyperopt_config[EXECUTOR].get(MAX_CONCURRENT_TRIALS) == AUTO:
        hyperopt_config[EXECUTOR][MAX_CONCURRENT_TRIALS] = backend.max_concurrent_trials(hyperopt_config)

    parameters = hyperopt_config["parameters"]
    if search_alg.get("type", "") == "bohb":
        # bohb does not support grid_search search space
        del parameters["utterance.cell_type"]
        hyperopt_config["parameters"] = parameters

    split = hyperopt_config["split"]
    output_feature = hyperopt_config["output_feature"]
    metric = hyperopt_config["metric"]
    goal = hyperopt_config["goal"]
    search_alg = hyperopt_config["search_alg"]
    executor = hyperopt_config["executor"]

    hyperopt_executor = get_build_hyperopt_executor(executor["type"])(
        parameters, output_feature, metric, goal, split, search_alg=search_alg, **executor
    )

    hyperopt_executor.execute(config, dataset=rel_path, output_directory=tmpdir, backend=backend)


@pytest.mark.distributed
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_hyperopt_executor(scenario, csv_filename, tmpdir, ray_cluster_4cpu):
    search_alg = scenario["search_alg"]
    executor = scenario["executor"]
    run_hyperopt_executor(search_alg, executor, csv_filename, tmpdir)


@pytest.mark.distributed
@pytest.mark.parametrize("use_split", [True, False], ids=["split", "no_split"])
def test_hyperopt_executor_with_metric(use_split, csv_filename, tmpdir, ray_cluster_4cpu):
    run_hyperopt_executor(
        {"type": "variant_generator"},  # search_alg
        {"type": "ray", "num_samples": 2},  # executor
        csv_filename,
        tmpdir,
        validate_output_feature=True,
        validation_metric=ACCURACY,
        use_split=use_split,
    )


@pytest.mark.distributed
@pytest.mark.parametrize("backend", ["local", "ray"])
def test_hyperopt_run_hyperopt(csv_filename, backend, tmpdir, ray_cluster_4cpu):
    input_features = [
        text_feature(name="utterance", encoder={"cell_type": "lstm", "reduce_output": "sum"}),
        category_feature(encoder={"vocab_size": 2}, reduce_input="sum"),
    ]
    output_features = [category_feature(decoder={"vocab_size": 2}, reduce_input="sum")]

    rel_path = generate_data(input_features, output_features, csv_filename)

    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat", "num_fc_layers": 2},
        TRAINER: {"epochs": 2, "learning_rate": 0.001},
        "backend": {
            "type": backend,
        },
    }

    output_feature_name = output_features[0]["name"]

    hyperopt_configs = {
        "parameters": {
            "trainer.learning_rate": {
                "space": "loguniform",
                "lower": 0.001,
                "upper": 0.1,
            },
            output_feature_name + ".output_size": {"space": "randint", "lower": 32, "upper": 64},
            output_feature_name + ".num_fc_layers": {"space": "randint", "lower": 2, "upper": 6},
        },
        "goal": "minimize",
        "output_feature": output_feature_name,
        "validation_metrics": "loss",
        "executor": {
            "type": "ray",
            "num_samples": 2,
            "cpu_resources_per_trial": 2,
            "max_concurrent_trials": "auto",
        },
        "search_alg": {"type": "variant_generator"},
    }

    @ray.remote(num_cpus=0)
    class Event:
        def __init__(self):
            self._set = False

        def is_set(self):
            return self._set

        def set(self):
            self._set = True

    # Used to trigger a cancel event in the trial, which should subsequently be retried
    event = Event.remote()

    class CancelCallback(Callback):
        def on_epoch_start(self, trainer, progress_tracker, save_path: str):
            if progress_tracker.epoch == 1 and not ray.get(event.is_set.remote()):
                ray.get(event.set.remote())
                raise KeyboardInterrupt()

    # add hyperopt parameter space to the config
    config["hyperopt"] = hyperopt_configs

    # run for one epoch, then cancel, then resume from where we left off
    run_hyperopt(config, rel_path, tmpdir, callbacks=[CancelCallback()])


@pytest.mark.distributed
def test_hyperopt_ray_mlflow(csv_filename, tmpdir, ray_cluster_4cpu):
    mlflow_uri = f"file://{tmpdir}/mlruns"
    mlflow.set_tracking_uri(mlflow_uri)
    client = MlflowClient(tracking_uri=mlflow_uri)

    num_samples = 2
    config = _get_config(
        {"type": "variant_generator"}, {"type": "ray", "num_samples": num_samples}  # search_alg  # executor
    )

    rel_path = generate_data(config["input_features"], config["output_features"], csv_filename)

    exp_name = "mlflow_test"
    run_hyperopt(config, rel_path, tmpdir, experiment_name=exp_name, callbacks=[MlflowCallback(mlflow_uri)])

    experiment = client.get_experiment_by_name(exp_name)
    assert experiment is not None

    runs = client.search_runs([experiment.experiment_id])
    assert len(runs) > 0

    for run in runs:
        artifacts = [f.path for f in client.list_artifacts(run.info.run_id, "")]
        assert "config.yaml" in artifacts
        assert "model" in artifacts


def run_hyperopt(
    config,
    rel_path,
    tmpdir,
    experiment_name="ray_hyperopt",
    callbacks=None,
):
    hyperopt_results = hyperopt(
        config,
        dataset=rel_path,
        output_directory=tmpdir,
        experiment_name=experiment_name,
        callbacks=callbacks,
    )

    # check for return results
    assert isinstance(hyperopt_results, HyperoptResults)

    # check for existence of the hyperopt statistics file
    assert os.path.isfile(os.path.join(tmpdir, experiment_name, HYPEROPT_STATISTICS_FILE_NAME))
