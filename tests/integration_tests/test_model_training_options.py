import json
import logging
import os.path
import re
from collections import namedtuple

import numpy as np
import pandas as pd
import pytest
import torch
from sklearn.model_selection import train_test_split

from ludwig import globals as global_vars
from ludwig.api import LudwigModel
from ludwig.backend import LOCAL_BACKEND
from ludwig.constants import (
    CATEGORY,
    DEFAULTS,
    EPOCHS,
    INPUT_FEATURES,
    OUTPUT_FEATURES,
    PREPROCESSING,
    TRAINER,
    TRAINING,
)
from ludwig.contribs.mlflow import MlflowCallback
from ludwig.experiment import experiment_cli
from ludwig.features.number_feature import numeric_transformation_registry
from ludwig.globals import DESCRIPTION_FILE_NAME, TRAINING_PREPROC_FILE_NAME
from ludwig.schema.optimizers import optimizer_registry
from ludwig.utils.data_utils import load_json, replace_file_extension
from ludwig.utils.misc_utils import get_from_registry
from ludwig.utils.package_utils import LazyLoader
from tests.integration_tests.utils import category_feature, generate_data, LocalTestBackend

mlflow = LazyLoader("mlflow", globals(), "mlflow")

RANDOM_SEED = 42
NUMBER_OBSERVATIONS = 500

GeneratedData = namedtuple("GeneratedData", "train_df validation_df test_df")


def get_feature_configs():
    input_features = [
        {"name": "x", "type": "number"},
    ]
    output_features = [
        {
            "name": "y",
            "type": "number",
            "loss": {"type": "mean_squared_error"},
            "decoder": {
                "num_fc_layers": 5,
                "fc_output_size": 64,
            },
        }
    ]

    return input_features, output_features


@pytest.fixture(scope="module")
def generated_data():
    # function generates simple training data that guarantee convergence
    # within 30 epochs for suitable config

    # generate data
    np.random.seed(RANDOM_SEED)
    x = np.array(range(NUMBER_OBSERVATIONS)).reshape(-1, 1)
    y = 2 * x + 1 + np.random.normal(size=x.shape[0]).reshape(-1, 1)
    raw_df = pd.DataFrame(np.concatenate((x, y), axis=1), columns=["x", "y"])

    # create training data
    train, valid_test = train_test_split(raw_df, train_size=0.7)

    # create validation and test data
    validation, test = train_test_split(valid_test, train_size=0.5)

    return GeneratedData(train, validation, test)


@pytest.fixture(scope="module")
def generated_data_for_optimizer():
    # function generates simple training data that guarantee convergence
    # within 30 epochs for suitable config

    # generate data
    np.random.seed(RANDOM_SEED)
    x = np.array(range(NUMBER_OBSERVATIONS)).reshape(-1, 1)
    y = 2 * x + 1 + np.random.normal(size=x.shape[0]).reshape(-1, 1)
    raw_df = pd.DataFrame(np.concatenate((x, y), axis=1), columns=["x", "y"])
    raw_df["x"] = (raw_df["x"] - raw_df["x"].min()) / (raw_df["x"].max() - raw_df["x"].min())
    raw_df["y"] = (raw_df["y"] - raw_df["y"].min()) / (raw_df["y"].max() - raw_df["y"].min())

    # create training data
    train, valid_test = train_test_split(raw_df, train_size=0.7)

    # create validation and test data
    validation, test = train_test_split(valid_test, train_size=0.5)

    return GeneratedData(train, validation, test)


@pytest.mark.parametrize("early_stop", [3, 5])
def test_early_stopping(early_stop, generated_data, tmp_path):
    input_features, output_features = get_feature_configs()

    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat"},
        TRAINER: {"epochs": 30, "early_stop": early_stop, "batch_size": 16},
    }

    # create sub-directory to store results
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    # run experiment
    _, _, _, _, output_dir = experiment_cli(
        training_set=generated_data.train_df,
        validation_set=generated_data.validation_df,
        test_set=generated_data.test_df,
        output_directory=str(results_dir),
        config=config,
        skip_save_processed_input=True,
        skip_save_progress=True,
        skip_save_unprocessed_output=True,
        skip_save_model=True,
        skip_save_log=True,
    )

    # test existence of required files
    train_stats_fp = os.path.join(output_dir, "training_statistics.json")
    metadata_fp = os.path.join(output_dir, DESCRIPTION_FILE_NAME)
    assert os.path.isfile(train_stats_fp)
    assert os.path.isfile(metadata_fp)

    # retrieve results so we can validate early stopping
    with open(train_stats_fp) as f:
        train_stats = json.load(f)
    with open(metadata_fp) as f:
        metadata = json.load(f)

    # get early stopping value
    early_stop_value = metadata["config"][TRAINER]["early_stop"]

    # retrieve validation losses
    vald_losses_data = train_stats["validation"]["combined"]["loss"]

    last_evaluation = len(vald_losses_data) - 1
    best_evaluation = np.argmin(vald_losses_data)

    assert last_evaluation - best_evaluation == early_stop_value


@pytest.mark.parametrize("skip_save_progress", [False])
@pytest.mark.parametrize("skip_save_model", [False, True])
def test_model_progress_save(skip_save_progress, skip_save_model, generated_data, tmp_path):
    input_features, output_features = get_feature_configs()

    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat"},
        TRAINER: {"epochs": 5},
    }

    # create sub-directory to store results
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    # run experiment
    _, _, _, _, output_dir = experiment_cli(
        training_set=generated_data.train_df,
        validation_set=generated_data.validation_df,
        test_set=generated_data.test_df,
        output_directory=str(results_dir),
        config=config,
        skip_save_processed_input=True,
        skip_save_progress=skip_save_progress,
        skip_save_unprocessed_output=True,
        skip_save_model=skip_save_model,
        skip_save_log=True,
    )

    # ========== Check for required result data sets =============
    model_dir = os.path.join(output_dir, "model")
    files = [f for f in os.listdir(model_dir) if re.match(r"model_weights", f)]
    if skip_save_model:
        assert len(files) == 0
    else:
        assert len(files) == 1

    training_checkpoints_dir = os.path.join(output_dir, "model", "training_checkpoints")
    training_checkpoints = os.listdir(training_checkpoints_dir)
    if skip_save_progress:
        assert len(training_checkpoints) == 0
    else:
        assert len(training_checkpoints) > 0


@pytest.mark.parametrize("optimizer", ["sgd", "adam"])
def test_resume_training(optimizer, generated_data, tmp_path):
    input_features, output_features = get_feature_configs()
    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat"},
        TRAINER: {"epochs": 2, "batch_size": 16, "optimizer": {"type": optimizer}},
    }

    # create sub-directory to store results
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    _, _, _, _, output_dir1 = experiment_cli(
        config,
        training_set=generated_data.train_df,
        validation_set=generated_data.validation_df,
        test_set=generated_data.test_df,
    )

    config[TRAINER]["epochs"] = 5

    experiment_cli(
        config,
        training_set=generated_data.train_df,
        validation_set=generated_data.validation_df,
        test_set=generated_data.test_df,
        model_resume_path=output_dir1,
    )

    _, _, _, _, output_dir2 = experiment_cli(
        config,
        training_set=generated_data.train_df,
        validation_set=generated_data.validation_df,
        test_set=generated_data.test_df,
    )

    # compare learning curves with and without resuming
    ts1 = load_json(os.path.join(output_dir1, "training_statistics.json"))
    ts2 = load_json(os.path.join(output_dir2, "training_statistics.json"))
    print("ts1", ts1)
    print("ts2", ts2)
    assert ts1[TRAINING]["combined"]["loss"] == ts2[TRAINING]["combined"]["loss"]

    # compare predictions with and without resuming
    y_pred1 = np.load(os.path.join(output_dir1, "y_predictions.npy"))
    y_pred2 = np.load(os.path.join(output_dir2, "y_predictions.npy"))
    print("y_pred1", y_pred1)
    print("y_pred2", y_pred2)
    assert np.all(np.isclose(y_pred1, y_pred2))


@pytest.mark.parametrize("optimizer", ["sgd", "adam"])
def test_resume_training_mlflow(optimizer, generated_data, tmp_path):
    input_features, output_features = get_feature_configs()
    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat"},
        TRAINER: {"epochs": 2, "batch_size": 16, "optimizer": {"type": optimizer}},
    }

    # create sub-directory to store results
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    mlflow_uri = f"file://{tmp_path}/mlruns"
    experiment_name = optimizer + "_experiment"

    _, _, _, _, output_dir1 = experiment_cli(
        config,
        training_set=generated_data.train_df,
        validation_set=generated_data.validation_df,
        test_set=generated_data.test_df,
        callbacks=[MlflowCallback(mlflow_uri)],
        experiment_name=experiment_name,
    )
    # Can't change any artifact spec on a run once it has been logged to mlflow, so skipping changing epochs

    _, _, _, _, output_dir2 = experiment_cli(
        config,
        training_set=generated_data.train_df,
        validation_set=generated_data.validation_df,
        test_set=generated_data.test_df,
        model_resume_path=output_dir1,
        callbacks=[MlflowCallback(mlflow_uri)],
        experiment_name=experiment_name,
    )

    # make sure there is only one mlflow run id
    experiment = mlflow.get_experiment_by_name(experiment_name)
    previous_runs = mlflow.search_runs([experiment.experiment_id])
    assert len(previous_runs) == 1


@pytest.mark.parametrize("optimizer_type", optimizer_registry)
def test_optimizers(optimizer_type, generated_data_for_optimizer, tmp_path):
    input_features, output_features = get_feature_configs()

    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat"},
        TRAINER: {"epochs": 5, "batch_size": 16, "optimizer": {"type": optimizer_type}},
    }

    # special handling for adadelta, break out of local minima
    if optimizer_type == "adadelta":
        config[TRAINER]["learning_rate"] = 0.1

    model = LudwigModel(config)

    # create sub-directory to store results
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    # run experiment
    train_stats, preprocessed_data, output_directory = model.train(
        training_set=generated_data_for_optimizer.train_df,
        output_directory=str(results_dir),
        config=config,
        skip_save_processed_input=True,
        skip_save_progress=True,
        skip_save_unprocessed_output=True,
        skip_save_model=True,
        skip_save_log=True,
    )

    # retrieve training losses for first and last entries.
    train_losses = train_stats[TRAINING]["combined"]["loss"]
    last_entry = len(train_losses)

    # ensure train loss for last entry is less than first entry
    assert train_losses[last_entry - 1] < train_losses[0]


def test_regularization(generated_data, tmp_path):
    input_features, output_features = get_feature_configs()

    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat"},
        TRAINER: {
            "epochs": 1,
            "batch_size": 16,
            "regularization_lambda": 1,
        },
    }

    # create sub-directory to store results
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    regularization_losses = []
    for regularizer in [None, "l1", "l2", "l1_l2"]:
        np.random.seed(RANDOM_SEED)
        torch.manual_seed(RANDOM_SEED)

        # setup regularization parameters
        config[TRAINER]["regularization_type"] = regularizer

        # run experiment
        _, _, _, _, output_dir = experiment_cli(
            training_set=generated_data.train_df,
            validation_set=generated_data.validation_df,
            test_set=generated_data.test_df,
            output_directory=str(results_dir),
            config=config,
            experiment_name="regularization",
            model_name=str(regularizer),
            skip_save_processed_input=True,
            skip_save_progress=True,
            skip_save_unprocessed_output=True,
            skip_save_model=True,
            skip_save_log=True,
        )

        # test existence of required files
        train_stats_fp = os.path.join(output_dir, "training_statistics.json")
        metadata_fp = os.path.join(output_dir, DESCRIPTION_FILE_NAME)
        assert os.path.isfile(train_stats_fp)
        assert os.path.isfile(metadata_fp)

        # retrieve results so we can compare training loss with regularization
        with open(train_stats_fp) as f:
            train_stats = json.load(f)

        # retrieve training losses for all epochs
        train_losses = train_stats[TRAINING]["combined"]["loss"]
        regularization_losses.append(train_losses[0])

    # create a set of losses
    regularization_losses_set = set(regularization_losses)

    # ensure all losses obtained with the different methods are different
    assert len(regularization_losses) == len(regularization_losses_set)


# test cache checksum function
def test_cache_checksum(csv_filename, tmp_path):
    # setup for training
    input_features = [category_feature(encoder={"vocab_size": 5})]
    output_features = [category_feature(decoder={"vocab_size": 2}, top_k=2)]

    source_dataset = os.path.join(tmp_path, csv_filename)
    source_dataset = generate_data(input_features, output_features, source_dataset)

    config = {
        INPUT_FEATURES: input_features,
        OUTPUT_FEATURES: output_features,
        DEFAULTS: {CATEGORY: {PREPROCESSING: {"fill_value": "<UNKNOWN>"}}},
        TRAINER: {EPOCHS: 2},
    }

    backend = LocalTestBackend()
    cache_fname = replace_file_extension(source_dataset, TRAINING_PREPROC_FILE_NAME)

    # conduct initial training
    output_directory = os.path.join(tmp_path, "results")
    model = LudwigModel(config, backend=backend)
    model.train(dataset=source_dataset, output_directory=output_directory)
    first_training_timestamp = os.path.getmtime(cache_fname)

    # conduct second training, should not force recreating hdf5
    model = LudwigModel(config, backend=backend)
    model.train(dataset=source_dataset, output_directory=output_directory)
    current_training_timestamp = os.path.getmtime(cache_fname)

    # time stamps should be the same
    assert first_training_timestamp == current_training_timestamp

    # force recreating cache file by changing checksum by updating defaults
    prior_training_timestamp = current_training_timestamp
    config[DEFAULTS][CATEGORY][PREPROCESSING]["fill_value"] = "<EMPTY>"
    model = LudwigModel(config, backend=backend)
    model.train(dataset=source_dataset, output_directory=output_directory)
    current_training_timestamp = os.path.getmtime(cache_fname)

    # timestamp should differ
    assert prior_training_timestamp < current_training_timestamp

    # force recreating cache by updating modification time of source dataset
    prior_training_timestamp = current_training_timestamp
    os.utime(source_dataset)
    model = LudwigModel(config, backend=backend)
    model.train(dataset=source_dataset, output_directory=output_directory)
    current_training_timestamp = os.path.getmtime(cache_fname)

    # timestamps should be different
    assert prior_training_timestamp < current_training_timestamp

    # force change in feature preprocessing
    prior_training_timestamp = current_training_timestamp
    input_features = config[INPUT_FEATURES].copy()
    input_features[0][PREPROCESSING] = {"lowercase": True}
    config[INPUT_FEATURES] = input_features
    model = LudwigModel(config, backend=backend)
    model.train(dataset=source_dataset, output_directory=output_directory)
    current_training_timestamp = os.path.getmtime(cache_fname)

    # timestamps should be different
    assert prior_training_timestamp < current_training_timestamp

    # force change in features names (and properties)
    prior_training_timestamp = current_training_timestamp
    input_features = [category_feature(encoder={"vocab_size": 5}), category_feature()]
    source_dataset = generate_data(input_features, output_features, source_dataset)
    config[INPUT_FEATURES] = input_features
    model = LudwigModel(config, backend=backend)
    model.train(dataset=source_dataset, output_directory=output_directory)
    current_training_timestamp = os.path.getmtime(cache_fname)

    # timestamps should be different
    assert prior_training_timestamp < current_training_timestamp

    # force change in Ludwig version
    prior_training_timestamp = current_training_timestamp
    global_vars.LUDWIG_VERSION = "new_version"
    model = LudwigModel(config, backend=backend)
    model.train(dataset=source_dataset, output_directory=output_directory)
    current_training_timestamp = os.path.getmtime(cache_fname)

    # timestamps should be different
    assert prior_training_timestamp < current_training_timestamp


@pytest.mark.parametrize("transformer_key", list(numeric_transformation_registry.keys()))
def test_numeric_transformer(transformer_key, tmpdir):
    Transformer = get_from_registry(transformer_key, numeric_transformation_registry)
    transformer_name = Transformer().__class__.__name__
    if transformer_name == "Log1pTransformer":
        raw_values = np.random.lognormal(5, 2, size=100)
    else:
        raw_values = np.random.normal(5, 2, size=100)

    backend = LOCAL_BACKEND
    parameters = Transformer.fit_transform_params(raw_values, backend)
    if transformer_name in {"Log1pTransformer", "IdentityTransformer"}:
        # should be empty
        assert not bool(parameters)
    else:
        # should not be empty
        assert bool(parameters)

    # instantiate numeric transformer
    numeric_transfomer = Transformer(**parameters)

    # transform values
    transformed_values = numeric_transfomer.transform(raw_values)

    # inverse transform the prior transformed values
    reconstructed_values = numeric_transfomer.inverse_transform(transformed_values)

    # should now match
    assert np.allclose(raw_values, reconstructed_values)

    # now test numeric transformer with output feature
    df = pd.DataFrame(np.array([raw_values, raw_values]).T, columns=["x", "y"])
    config = {
        "input_features": [{"name": "x", "type": "number"}],
        "output_features": [{"name": "y", "type": "number", "preprocessing": {"normalization": transformer_key}}],
        "combiner": {
            "type": "concat",
        },
        TRAINER: {
            "epochs": 2,
            "batch_size": 16,
        },
    }

    args = {
        "config": config,
        "skip_save_processed_input": True,
        "output_directory": os.path.join(tmpdir, "results"),
        "logging_level": logging.WARN,
    }

    # ensure no exceptions are raised
    experiment_cli(dataset=df, **args)
