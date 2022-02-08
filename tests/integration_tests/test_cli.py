# Copyright (c) 2020 Uber Technologies, Inc.
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
import os
import os.path
import shutil
import subprocess
import tempfile

import pytest
import yaml

from ludwig.constants import TRAINER
from tests.integration_tests.utils import category_feature, generate_data, sequence_feature


def _run_commands(commands, **ludwig_kwargs):
    for arg_name, value in ludwig_kwargs.items():
        commands += ["--" + arg_name, value]
    cmdline = " ".join(commands)
    print(cmdline)
    completed_process = subprocess.run(cmdline, shell=True, stdout=subprocess.PIPE, env=os.environ.copy())
    assert completed_process.returncode == 0

    return completed_process


def _run_ludwig(command, **ludwig_kwargs):
    commands = ["ludwig", command]
    return _run_commands(commands, **ludwig_kwargs)


def _run_ludwig_horovod(command, **ludwig_kwargs):
    commands = ["horovodrun", "-np", "2", "ludwig", command]
    return _run_commands(commands, **ludwig_kwargs)


def _prepare_data(csv_filename, config_filename):
    # Single sequence input, single category output
    input_features = [sequence_feature(reduce_output="sum")]
    output_features = [category_feature(vocab_size=2, reduce_input="sum")]

    # Generate test data
    dataset_filename = generate_data(input_features, output_features, csv_filename)

    # generate config file
    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat", "output_size": 14},
        TRAINER: {"epochs": 2},
    }

    with open(config_filename, "w") as f:
        yaml.dump(config, f)

    return dataset_filename


def _prepare_hyperopt_data(csv_filename, config_filename):
    # Single sequence input, single category output
    input_features = [sequence_feature(reduce_output="sum")]
    output_features = [category_feature(vocab_size=2, reduce_input="sum")]

    # Generate test data
    dataset_filename = generate_data(input_features, output_features, csv_filename)

    # generate config file
    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat", "output_size": 4},
        TRAINER: {"epochs": 2},
        "hyperopt": {
            "parameters": {
                "trainer.learning_rate": {
                    "type": "float",
                    "low": 0.0001,
                    "high": 0.01,
                    "space": "log",
                    "steps": 3,
                }
            },
            "goal": "minimize",
            "output_feature": output_features[0]["name"],
            "validation_metrics": "loss",
            "executor": {"type": "serial"},
            "sampler": {"type": "random", "num_samples": 2},
        },
    }

    with open(config_filename, "w") as f:
        yaml.dump(config, f)

    return dataset_filename


@pytest.mark.distributed
def test_train_cli_dataset(csv_filename):
    """Test training using `ludwig train --dataset`."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_filename = os.path.join(tmpdir, "config.yaml")
        dataset_filename = _prepare_data(csv_filename, config_filename)
        _run_ludwig("train", dataset=dataset_filename, config=config_filename, output_directory=tmpdir)


@pytest.mark.distributed
def test_train_cli_training_set(csv_filename):
    """Test training using `ludwig train --training_set`."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_filename = os.path.join(tmpdir, "config.yaml")
        dataset_filename = _prepare_data(csv_filename, config_filename)
        validation_filename = shutil.copyfile(dataset_filename, os.path.join(tmpdir, "validation.csv"))
        test_filename = shutil.copyfile(dataset_filename, os.path.join(tmpdir, "test.csv"))
        _run_ludwig(
            "train",
            training_set=dataset_filename,
            validation_set=validation_filename,
            test_set=test_filename,
            config=config_filename,
            output_directory=tmpdir,
        )


@pytest.mark.distributed
def test_train_cli_horovod(csv_filename):
    """Test training using `horovodrun -np 2 ludwig train --dataset`."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_filename = os.path.join(tmpdir, "config.yaml")
        dataset_filename = _prepare_data(csv_filename, config_filename)
        _run_ludwig_horovod(
            "train",
            dataset=dataset_filename,
            config=config_filename,
            output_directory=tmpdir,
            experiment_name="horovod_experiment",
        )

        # Check that `model_load_path` works correctly
        _run_ludwig_horovod(
            "train",
            dataset=dataset_filename,
            config=config_filename,
            output_directory=tmpdir,
            model_load_path=os.path.join(tmpdir, "horovod_experiment_run", "model"),
        )


@pytest.mark.skip(reason="Issue #1451: Use torchscript.")
@pytest.mark.distributed
def test_export_savedmodel_cli(csv_filename):
    """Test exporting Ludwig model to Tensorflows savedmodel format."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_filename = os.path.join(tmpdir, "config.yaml")
        dataset_filename = _prepare_data(csv_filename, config_filename)
        _run_ludwig("train", dataset=dataset_filename, config=config_filename, output_directory=tmpdir)
        _run_ludwig(
            "export_savedmodel",
            model=os.path.join(tmpdir, "experiment_run", "model"),
            output_path=os.path.join(tmpdir, "savedmodel"),
        )


@pytest.mark.skip(reason="Issue #1451: Use torchscript.")
@pytest.mark.distributed
def test_export_neuropod_cli(csv_filename):
    """Test exporting Ludwig model to neuropod format."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_filename = os.path.join(tmpdir, "config.yaml")
        dataset_filename = _prepare_data(csv_filename, config_filename)
        _run_ludwig("train", dataset=dataset_filename, config=config_filename, output_directory=tmpdir)
        _run_ludwig(
            "export_neuropod",
            model=os.path.join(tmpdir, "experiment_run", "model"),
            output_path=os.path.join(tmpdir, "neuropod"),
        )


@pytest.mark.distributed
def test_experiment_cli(csv_filename):
    """Test experiment cli."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_filename = os.path.join(tmpdir, "config.yaml")
        dataset_filename = _prepare_data(csv_filename, config_filename)
        _run_ludwig("experiment", dataset=dataset_filename, config=config_filename, output_directory=tmpdir)


@pytest.mark.distributed
def test_predict_cli(csv_filename):
    """Test predict cli."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_filename = os.path.join(tmpdir, "config.yaml")
        dataset_filename = _prepare_data(csv_filename, config_filename)
        _run_ludwig("train", dataset=dataset_filename, config=config_filename, output_directory=tmpdir)
        _run_ludwig(
            "predict",
            dataset=dataset_filename,
            model=os.path.join(tmpdir, "experiment_run", "model"),
            output_directory=os.path.join(tmpdir, "predictions"),
        )


@pytest.mark.distributed
def test_evaluate_cli(csv_filename):
    """Test evaluate cli."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_filename = os.path.join(tmpdir, "config.yaml")
        dataset_filename = _prepare_data(csv_filename, config_filename)
        _run_ludwig("train", dataset=dataset_filename, config=config_filename, output_directory=tmpdir)
        _run_ludwig(
            "evaluate",
            dataset=dataset_filename,
            model=os.path.join(tmpdir, "experiment_run", "model"),
            output_directory=os.path.join(tmpdir, "predictions"),
        )


@pytest.mark.distributed
def test_hyperopt_cli(csv_filename):
    """Test hyperopt cli."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_filename = os.path.join(tmpdir, "config.yaml")
        dataset_filename = _prepare_hyperopt_data(csv_filename, config_filename)
        _run_ludwig("hyperopt", dataset=dataset_filename, config=config_filename, output_directory=tmpdir)


@pytest.mark.distributed
def test_visualize_cli(csv_filename):
    """Test Ludwig 'visualize' cli."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_filename = os.path.join(tmpdir, "config.yaml")
        dataset_filename = _prepare_data(csv_filename, config_filename)
        _run_ludwig("train", dataset=dataset_filename, config=config_filename, output_directory=tmpdir)
        _run_ludwig(
            "visualize",
            visualization="learning_curves",
            model_names="run",
            training_statistics=os.path.join(tmpdir, "experiment_run", "training_statistics.json"),
            output_directory=os.path.join(tmpdir, "visualizations"),
        )


@pytest.mark.distributed
def test_collect_summary_activations_weights_cli(csv_filename):
    """Test collect_summary cli."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_filename = os.path.join(tmpdir, "config.yaml")
        dataset_filename = _prepare_data(csv_filename, config_filename)
        _run_ludwig("train", dataset=dataset_filename, config=config_filename, output_directory=tmpdir)
        completed_process = _run_ludwig("collect_summary", model=os.path.join(tmpdir, "experiment_run", "model"))
        stdout = completed_process.stdout.decode("utf-8")

        assert "Modules" in stdout
        assert "Parameters" in stdout


@pytest.mark.distributed
def test_synthesize_dataset_cli(csv_filename):
    """Test synthesize_data cli."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # test depends on default setting of --dataset_size
        # if this parameter is specified, _run_ludwig fails when
        # attempting to build the cli parameter structure
        _run_ludwig(
            "synthesize_dataset",
            output_path=os.path.join(tmpdir, csv_filename),
            features="'[ \
                  {name: text, type: text}, \
                  {name: category, type: category}, \
                  {name: number, type: number}, \
                  {name: binary, type: binary}, \
                  {name: set, type: set}, \
                  {name: bag, type: bag}, \
                  {name: sequence, type: sequence}, \
                  {name: timeseries, type: timeseries}, \
                  {name: date, type: date}, \
                  {name: h3, type: h3}, \
                  {name: vector, type: vector}, \
                  {name: audio, type: audio}, \
                  {name: image, type: image} \
                ]'",
        )


@pytest.mark.distributed
def test_preprocess_cli(csv_filename):
    """Test preprocess `ludwig preprocess."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_filename = os.path.join(tmpdir, "config.yaml")
        dataset_filename = _prepare_data(csv_filename, config_filename)
        _run_ludwig("preprocess", dataset=dataset_filename, preprocessing_config=config_filename)
