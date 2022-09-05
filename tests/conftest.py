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
import os
import tempfile
import uuid
from unittest import mock

import pytest

from ludwig.constants import COMBINER, EPOCHS, HYPEROPT, INPUT_FEATURES, NAME, OUTPUT_FEATURES, TRAINER, TYPE
from ludwig.hyperopt.run import hyperopt
from tests.integration_tests.utils import category_feature, generate_data, text_feature


@pytest.fixture(autouse=True)
def setup_tests(request):
    if "distributed" not in request.keywords:
        # Only run this patch if we're running distributed tests, otherwise Ray will not be installed
        # and this will fail.
        # See: https://stackoverflow.com/a/38763328
        yield
        return

    with mock.patch("ludwig.backend.ray.init_ray_local") as mock_init_ray_local:
        mock_init_ray_local.side_effect = RuntimeError("Ray must be initialized explicitly when running tests")
        yield mock_init_ray_local


@pytest.fixture()
def csv_filename():
    """Yields a csv filename for holding temporary data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_filename = os.path.join(tmpdir, uuid.uuid4().hex[:10].upper() + ".csv")
        yield csv_filename


@pytest.fixture()
def yaml_filename():
    """Yields a yaml filename for holding a temporary config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yaml_filename = os.path.join(tmpdir, "model_def_" + uuid.uuid4().hex[:10].upper() + ".yaml")
        yield yaml_filename


@pytest.fixture(scope="module")
def hyperopt_results():
    """This function generates hyperopt results."""
    input_features = [
        text_feature(name="utterance", encoder={"cell_type": "lstm", "reduce_output": "sum"}),
        category_feature(encoder={"vocab_size": 2}, reduce_input="sum"),
    ]

    output_features = [category_feature(decoder={"vocab_size": 2}, reduce_input="sum")]

    csv_filename = uuid.uuid4().hex[:10].upper() + ".csv"
    rel_path = generate_data(input_features, output_features, csv_filename)

    config = {
        INPUT_FEATURES: input_features,
        OUTPUT_FEATURES: output_features,
        COMBINER: {TYPE: "concat", "num_fc_layers": 2},
        TRAINER: {EPOCHS: 2, "learning_rate": 0.001},
    }

    output_feature_name = output_features[0][NAME]

    hyperopt_configs = {
        "parameters": {
            "trainer.learning_rate": {
                "space": "loguniform",
                "lower": 0.0001,
                "upper": 0.01,
            },
            output_feature_name + ".decoder.output_size": {"space": "choice", "categories": [32, 64, 128, 256]},
            output_feature_name + ".decoder.num_fc_layers": {"space": "randint", "lower": 1, "upper": 6},
        },
        "goal": "minimize",
        "output_feature": output_feature_name,
        "validation_metrics": "loss",
        "executor": {
            "type": "ray",
            "num_samples": 2,
        },
        "search_alg": {
            "type": "variant_generator",
        },
    }

    # add hyperopt parameter space to the config
    config[HYPEROPT] = hyperopt_configs

    hyperopt(config, dataset=rel_path, output_directory="results", experiment_name="hyperopt_test")

    return os.path.join(os.path.abspath("results"), "hyperopt_test")


@pytest.fixture(scope="module")
def ray_cluster_2cpu(request):
    with _ray_start(request, num_cpus=2):
        yield


@pytest.fixture(scope="module")
def ray_cluster_3cpu(request):
    with _ray_start(request, num_cpus=3):
        yield


@pytest.fixture(scope="module")
def ray_cluster_7cpu(request):
    with _ray_start(request, num_cpus=7):
        yield


@contextlib.contextmanager
def _ray_start(request, **kwargs):
    try:
        import ray
    except ImportError:
        if "distributed" in request.keywords:
            raise

        # Allow this fixture to run in environments where Ray is not installed
        # for parameterized tests that mix Ray with non-Ray backends
        yield None
        return

    init_kwargs = _get_default_ray_kwargs()
    init_kwargs.update(kwargs)
    res = ray.init(**init_kwargs)
    try:
        yield res
    finally:
        ray.shutdown()


def _get_default_ray_kwargs():
    system_config = _get_default_system_config()
    ray_kwargs = {
        "num_cpus": 1,
        "object_store_memory": 150 * 1024 * 1024,
        "dashboard_port": None,
        "include_dashboard": False,
        "namespace": "default_test_namespace",
        "_system_config": system_config,
    }
    return ray_kwargs


def _get_default_system_config():
    system_config = {
        "object_timeout_milliseconds": 200,
        "num_heartbeats_timeout": 10,
        "object_store_full_delay_ms": 100,
    }
    return system_config
