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

import logging
import multiprocessing
import os
import random
import shutil
import sys
import tempfile
import traceback
import unittest
import uuid
from distutils.util import strtobool
from typing import List, Union

import cloudpickle
import numpy as np
import pandas as pd
import torch
from PIL import Image

from ludwig.api import LudwigModel
from ludwig.backend import LocalBackend
from ludwig.constants import COLUMN, DECODER, ENCODER, NAME, PROC_COLUMN, TRAINER, VECTOR
from ludwig.data.dataset_synthesizer import build_synthetic_dataset, DATETIME_FORMATS
from ludwig.experiment import experiment_cli
from ludwig.features.feature_utils import compute_feature_hash
from ludwig.trainers.trainer import Trainer
from ludwig.utils.data_utils import read_csv, replace_file_extension

logger = logging.getLogger(__name__)

# Used in sequence-related unit tests (encoders, features) as well as end-to-end integration tests.
# Missing: passthrough encoder.
ENCODERS = ["embed", "rnn", "parallel_cnn", "cnnrnn", "stacked_parallel_cnn", "stacked_cnn", "transformer"]

HF_ENCODERS_SHORT = ["distilbert"]

HF_ENCODERS = [
    "bert",
    "gpt",
    "gpt2",
    # 'transformer_xl',
    "xlnet",
    "xlm",
    "roberta",
    "distilbert",
    "ctrl",
    "camembert",
    "albert",
    "t5",
    "xlmroberta",
    "longformer",
    "flaubert",
    "electra",
    "mt5",
]

RAY_BACKEND_CONFIG = {
    "type": "ray",
    "processor": {
        "parallelism": 2,
    },
    "trainer": {
        "use_gpu": False,
        "num_workers": 2,
        "resources_per_worker": {
            "CPU": 0.1,
            "GPU": 0,
        },
    },
}


class LocalTestBackend(LocalBackend):
    @property
    def supports_multiprocessing(self):
        return False


# Simulates running training on a separate node from the driver process
class FakeRemoteBackend(LocalBackend):
    def create_trainer(self, **kwargs) -> "BaseTrainer":  # noqa: F821
        return FakeRemoteTrainer(**kwargs)

    @property
    def supports_multiprocessing(self):
        return False


class FakeRemoteTrainer(Trainer):
    def train(self, *args, save_path="model", **kwargs):
        with tempfile.TemporaryDirectory() as tmpdir:
            return super().train(*args, save_path=tmpdir, **kwargs)


def parse_flag_from_env(key, default=False):
    try:
        value = os.environ[key]
    except KeyError:
        # KEY isn't set, default to `default`.
        _value = default
    else:
        # KEY is set, convert it to True or False.
        try:
            _value = strtobool(value)
        except ValueError:
            # More values are supported, but let's keep the message simple.
            raise ValueError(f"If set, {key} must be yes or no.")
    return _value


_run_slow_tests = parse_flag_from_env("RUN_SLOW", default=False)


def slow(test_case):
    """Decorator marking a test as slow.

    Slow tests are skipped by default. Set the RUN_SLOW environment variable to a truth value to run them.
    """
    if not _run_slow_tests:
        test_case = unittest.skip("Skipping: this test is too slow")(test_case)
    return test_case


def generate_data(
    input_features,
    output_features,
    filename="test_csv.csv",
    num_examples=25,
    nan_percent=0.0,
):
    """Helper method to generate synthetic data based on input, output feature specs.

    :param num_examples: number of examples to generate
    :param input_features: schema
    :param output_features: schema
    :param filename: path to the file where data is stored
    :param nan_percent: percent of values in a feature to be NaN
    :return:
    """
    features = input_features + output_features
    df = build_synthetic_dataset(num_examples, features)
    data = [next(df) for _ in range(num_examples + 1)]

    dataframe = pd.DataFrame(data[1:], columns=data[0])
    if nan_percent > 0:
        add_nans_to_df_in_place(dataframe, nan_percent)
    dataframe.to_csv(filename, index=False)

    return filename


def recursive_update(dictionary, values):
    for k, v in values.items():
        if isinstance(v, dict):
            dictionary[k] = recursive_update(dictionary.get(k, {}), v)
        else:
            dictionary[k] = v
    return dictionary


def random_string(length=5):
    return uuid.uuid4().hex[:length].upper()


def number_feature(normalization=None, **kwargs):
    feature = {
        "name": "num_" + random_string(),
        "type": "number",
        "preprocessing": {"normalization": normalization},
    }
    recursive_update(feature, kwargs)
    feature[COLUMN] = feature[NAME]
    feature[PROC_COLUMN] = compute_feature_hash(feature)
    return feature


def category_feature(output_feature=False, **kwargs):
    if DECODER in kwargs:
        output_feature = True
    feature = {
        "type": "category",
        "name": "category_" + random_string(),
    }
    if output_feature:
        feature.update(
            {
                DECODER: {"type": "classifier", "vocab_size": 10},
            }
        )
    else:
        feature.update(
            {
                ENCODER: {"type": "dense", "vocab_size": 10, "embedding_size": 5},
            }
        )
    recursive_update(feature, kwargs)
    feature[COLUMN] = feature[NAME]
    feature[PROC_COLUMN] = compute_feature_hash(feature)
    return feature


def text_feature(output_feature=False, **kwargs):
    if DECODER in kwargs:
        output_feature = True
    feature = {
        "name": "text_" + random_string(),
        "type": "text",
    }
    if output_feature:
        feature.update(
            {
                DECODER: {"type": "generator", "vocab_size": 5, "max_len": 7},
            }
        )
    else:
        feature.update(
            {
                ENCODER: {
                    "type": "parallel_cnn",
                    "vocab_size": 5,
                    "min_len": 7,
                    "max_len": 7,
                    "embedding_size": 8,
                    "state_size": 8,
                },
            }
        )
    recursive_update(feature, kwargs)
    feature[COLUMN] = feature[NAME]
    feature[PROC_COLUMN] = compute_feature_hash(feature)
    return feature


def set_feature(output_feature=False, **kwargs):
    if DECODER in kwargs:
        output_feature = True
    feature = {
        "type": "set",
        "name": "set_" + random_string(),
    }
    if output_feature:
        feature.update(
            {
                DECODER: {"type": "classifier", "vocab_size": 10, "max_len": 5},
            }
        )
    else:
        feature.update(
            {
                ENCODER: {"type": "embed", "vocab_size": 10, "max_len": 5, "embedding_size": 5},
            }
        )
    recursive_update(feature, kwargs)
    feature[COLUMN] = feature[NAME]
    feature[PROC_COLUMN] = compute_feature_hash(feature)
    return feature


def sequence_feature(output_feature=False, **kwargs):
    if DECODER in kwargs:
        output_feature = True
    feature = {
        "type": "sequence",
        "name": "sequence_" + random_string(),
    }
    if output_feature:
        feature.update(
            {
                DECODER: {
                    "type": "generator",
                    "vocab_size": 10,
                    "max_len": 7,
                }
            }
        )
    else:
        feature.update(
            {
                ENCODER: {
                    "type": "embed",
                    "vocab_size": 10,
                    "max_len": 7,
                    "embedding_size": 8,
                    "output_size": 8,
                    "state_size": 8,
                    "num_filters": 8,
                    "hidden_size": 8,
                },
            }
        )
    recursive_update(feature, kwargs)
    feature[COLUMN] = feature[NAME]
    feature[PROC_COLUMN] = compute_feature_hash(feature)
    return feature


def image_feature(folder, **kwargs):
    feature = {
        "type": "image",
        "name": "image_" + random_string(),
        "preprocessing": {"in_memory": True, "height": 12, "width": 12, "num_channels": 3},
        ENCODER: {
            "type": "resnet",
            "resnet_size": 8,
            "num_filters": 8,
            "output_size": 8,
        },
        "destination_folder": folder,
    }
    recursive_update(feature, kwargs)
    feature[COLUMN] = feature[NAME]
    feature[PROC_COLUMN] = compute_feature_hash(feature)
    return feature


def audio_feature(folder, **kwargs):
    feature = {
        "name": "audio_" + random_string(),
        "type": "audio",
        "preprocessing": {
            "type": "fbank",
            "window_length_in_s": 0.04,
            "window_shift_in_s": 0.02,
            "num_filter_bands": 80,
            "audio_file_length_limit_in_s": 3.0,
        },
        ENCODER: {
            "type": "stacked_cnn",
            "should_embed": False,
            "conv_layers": [
                {"filter_size": 400, "pool_size": 16, "num_filters": 32},
                {"filter_size": 40, "pool_size": 10, "num_filters": 64},
            ],
            "output_size": 16,
        },
        "destination_folder": folder,
    }
    recursive_update(feature, kwargs)
    feature[COLUMN] = feature[NAME]
    feature[PROC_COLUMN] = compute_feature_hash(feature)
    return feature


def timeseries_feature(**kwargs):
    feature = {
        "name": "timeseries_" + random_string(),
        "type": "timeseries",
        ENCODER: {"type": "parallel_cnn", "max_len": 7},
    }
    recursive_update(feature, kwargs)
    feature[COLUMN] = feature[NAME]
    feature[PROC_COLUMN] = compute_feature_hash(feature)
    return feature


def binary_feature(**kwargs):
    feature = {
        "name": "binary_" + random_string(),
        "type": "binary",
    }
    recursive_update(feature, kwargs)
    feature[COLUMN] = feature[NAME]
    feature[PROC_COLUMN] = compute_feature_hash(feature)
    return feature


def bag_feature(**kwargs):
    feature = {
        "name": "bag_" + random_string(),
        "type": "bag",
        ENCODER: {"type": "embed", "max_len": 5, "vocab_size": 10, "embedding_size": 5},
    }
    recursive_update(feature, kwargs)
    feature[COLUMN] = feature[NAME]
    feature[PROC_COLUMN] = compute_feature_hash(feature)
    return feature


def date_feature(**kwargs):
    feature = {
        "name": "date_" + random_string(),
        "type": "date",
        "preprocessing": {"datetime_format": random.choice(list(DATETIME_FORMATS.keys()))},
    }
    recursive_update(feature, kwargs)
    feature[COLUMN] = feature[NAME]
    feature[PROC_COLUMN] = compute_feature_hash(feature)
    return feature


def h3_feature(**kwargs):
    feature = {"name": "h3_" + random_string(), "type": "h3"}
    recursive_update(feature, kwargs)
    feature[COLUMN] = feature[NAME]
    feature[PROC_COLUMN] = compute_feature_hash(feature)
    return feature


def vector_feature(**kwargs):
    feature = {
        "type": VECTOR,
        "name": "vector_" + random_string(),
        "preprocessing": {
            "vector_size": 5,
        },
    }
    recursive_update(feature, kwargs)
    feature[COLUMN] = feature[NAME]
    feature[PROC_COLUMN] = compute_feature_hash(feature)
    return feature


def run_experiment(
    input_features=None, output_features=None, config=None, skip_save_processed_input=True, backend=None, **kwargs
):
    """Helper method to avoid code repetition in running an experiment. Deletes the data saved to disk related to
    running an experiment.

    :param input_features: list of input feature dictionaries
    :param output_features: list of output feature dictionaries
    **kwargs you may also pass extra parameters to the experiment as keyword
    arguments
    :return: None
    """
    if input_features is None and output_features is None and config is None:
        raise ValueError("Cannot run test experiment without features nor config.")

    if config is None:
        config = {
            "input_features": input_features,
            "output_features": output_features,
            "combiner": {"type": "concat", "output_size": 14},
            TRAINER: {"epochs": 2},
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        args = {
            "config": config,
            "backend": backend or LocalTestBackend(),
            "skip_save_training_description": True,
            "skip_save_training_statistics": True,
            "skip_save_processed_input": skip_save_processed_input,
            "skip_save_progress": True,
            "skip_save_unprocessed_output": True,
            "skip_save_model": True,
            "skip_save_predictions": True,
            "skip_save_eval_stats": True,
            "skip_collect_predictions": True,
            "skip_collect_overall_stats": True,
            "skip_save_log": True,
            "output_directory": tmpdir,
        }
        args.update(kwargs)

        experiment_cli(**args)


def generate_output_features_with_dependencies(main_feature, dependencies):
    """Generates multiple output features specifications with dependencies.

    Example usage:
        generate_output_features_with_dependencies('sequence_feature', ['category_feature', 'number_feature'])

    Args:
        main_feature: feature identifier, valid values 'category_feature', 'sequence_feature', 'number_feature'
        dependencies: list of dependencies for 'main_feature', do not li
    """

    output_features = [
        category_feature(decoder={"type": "classifier", "vocab_size": 2}, reduce_input="sum"),
        sequence_feature(decoder={"type": "generator", "vocab_size": 10, "max_len": 5}),
        number_feature(),
    ]

    # value portion of dictionary is a tuple: (position, feature_name)
    #   position: location of output feature in the above output_features list
    #   feature_name: Ludwig generated feature name
    feature_names = {
        "category_feature": (0, output_features[0]["name"]),
        "sequence_feature": (1, output_features[1]["name"]),
        "number_feature": (2, output_features[2]["name"]),
    }

    # generate list of dependencies with real feature names
    generated_dependencies = [feature_names[feat_name][1] for feat_name in dependencies]

    # specify dependencies for the main_feature
    output_features[feature_names[main_feature][0]]["dependencies"] = generated_dependencies

    return output_features


def generate_output_features_with_dependencies_complex():
    """Generates multiple output features specifications with dependencies."""

    tf = text_feature(decoder={"vocab_size": 4, "max_len": 5, "type": "generator"})
    sf = sequence_feature(decoder={"vocab_size": 4, "max_len": 5, "type": "generator"}, dependencies=[tf["name"]])
    nf = number_feature(dependencies=[tf["name"]])
    vf = vector_feature(dependencies=[sf["name"], nf["name"]])
    set_f = set_feature(decoder={"type": "classifier", "vocab_size": 4}, dependencies=[tf["name"], vf["name"]])
    cf = category_feature(
        decoder={"type": "classifier", "vocab_size": 4}, dependencies=[sf["name"], nf["name"], set_f["name"]]
    )

    # The correct order ids[tf, sf, nf, vf, set_f, cf]
    # # shuffling it to test the robustness of the topological sort
    output_features = [nf, tf, set_f, vf, cf, sf, nf]

    return output_features


def _subproc_wrapper(fn, queue, *args, **kwargs):
    fn = cloudpickle.loads(fn)
    try:
        results = fn(*args, **kwargs)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        results = e
    queue.put(results)


def spawn(fn):
    def wrapped_fn(*args, **kwargs):
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()

        p = ctx.Process(target=_subproc_wrapper, args=(cloudpickle.dumps(fn), queue, *args), kwargs=kwargs)

        p.start()
        p.join()
        results = queue.get()
        if isinstance(results, Exception):
            raise RuntimeError(
                f"Spawned subprocess raised {type(results).__name__}, " f"check log output above for stack trace."
            )
        return results

    return wrapped_fn


def get_weights(model: torch.nn.Module) -> List[torch.Tensor]:
    return [param.data for param in model.parameters()]


def has_no_grad(
    val: Union[np.ndarray, torch.Tensor, str, list],
):
    """Checks if two values are close to each other."""
    if isinstance(val, list):
        return all(has_no_grad(v) for v in val)
    if isinstance(val, torch.Tensor):
        return not val.requires_grad
    return True


def is_all_close(
    val1: Union[np.ndarray, torch.Tensor, str, list],
    val2: Union[np.ndarray, torch.Tensor, str, list],
    tolerance=1e-4,
):
    """Checks if two values are close to each other."""
    if isinstance(val1, list):
        return all(is_all_close(v1, v2, tolerance) for v1, v2 in zip(val1, val2))
    if isinstance(val1, str):
        return val1 == val2
    if isinstance(val1, torch.Tensor):
        val1 = val1.cpu().detach().numpy()
    if isinstance(val2, torch.Tensor):
        val2 = val2.cpu().detach().numpy()
    return val1.shape == val2.shape and np.allclose(val1, val2, atol=tolerance)


def is_all_tensors_cuda(val: Union[np.ndarray, torch.Tensor, str, list]) -> bool:
    if isinstance(val, list):
        return all(is_all_tensors_cuda(v) for v in val)

    if isinstance(val, torch.Tensor):
        return val.is_cuda
    return True


def run_api_experiment(input_features, output_features, data_csv):
    """Helper method to avoid code repetition in running an experiment.

    :param input_features: input schema
    :param output_features: output schema
    :param data_csv: path to data
    :return: None
    """
    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat", "output_size": 14},
        TRAINER: {"epochs": 2},
    }

    model = LudwigModel(config)
    output_dir = None

    try:
        # Training with csv
        _, _, output_dir = model.train(
            dataset=data_csv, skip_save_processed_input=True, skip_save_progress=True, skip_save_unprocessed_output=True
        )
        model.predict(dataset=data_csv)

        model_dir = os.path.join(output_dir, "model")
        loaded_model = LudwigModel.load(model_dir)

        # Necessary before call to get_weights() to materialize the weights
        loaded_model.predict(dataset=data_csv)

        model_weights = get_weights(model.model)
        loaded_weights = get_weights(loaded_model.model)
        for model_weight, loaded_weight in zip(model_weights, loaded_weights):
            assert torch.allclose(model_weight, loaded_weight)
    finally:
        # Remove results/intermediate data saved to disk
        shutil.rmtree(output_dir, ignore_errors=True)

    try:
        # Training with dataframe
        data_df = read_csv(data_csv)
        _, _, output_dir = model.train(
            dataset=data_df, skip_save_processed_input=True, skip_save_progress=True, skip_save_unprocessed_output=True
        )
        model.predict(dataset=data_df)
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def add_nans_to_df_in_place(df: pd.DataFrame, nan_percent: float):
    """Adds nans to a pandas dataframe in-place."""
    if nan_percent == 0:
        # No-op if nan_percent is 0
        return None
    if nan_percent < 0 or nan_percent > 1:
        raise ValueError("nan_percent must be between 0 and 1")

    num_rows = len(df)
    num_nans_per_col = int(round(nan_percent * num_rows))
    for col in df.columns:
        col_idx = df.columns.get_loc(col)
        for row_idx in random.sample(range(num_rows), num_nans_per_col):
            df.iloc[row_idx, col_idx] = np.nan
    return None


def read_csv_with_nan(path, nan_percent=0.0):
    """Converts `nan_percent` of samples in each row of the CSV at `path` to NaNs."""
    df = pd.read_csv(path)
    add_nans_to_df_in_place(df, nan_percent)
    return df


def create_data_set_to_use(data_format, raw_data, nan_percent=0.0):
    # helper function for generating training and test data with specified format
    # handles all data formats except for hdf5
    # assumes raw_data is a csv dataset generated by
    # tests.integration_tests.utils.generate_data() function

    # support for writing to a fwf dataset based on this stackoverflow posting:
    # https://stackoverflow.com/questions/16490261/python-pandas-write-dataframe-to-fixed-width-file-to-fwf
    from tabulate import tabulate

    def to_fwf(df, fname):
        content = tabulate(df.values.tolist(), list(df.columns), tablefmt="plain")
        open(fname, "w").write(content)

    pd.DataFrame.to_fwf = to_fwf

    dataset_to_use = None

    if data_format == "csv":
        # Replace the original CSV with a CSV with NaNs
        dataset_to_use = raw_data
        read_csv_with_nan(raw_data, nan_percent=nan_percent).to_csv(dataset_to_use, index=False)

    elif data_format in {"df", "dict"}:
        dataset_to_use = read_csv_with_nan(raw_data, nan_percent=nan_percent)
        if data_format == "dict":
            dataset_to_use = dataset_to_use.to_dict(orient="list")

    elif data_format == "excel":
        dataset_to_use = replace_file_extension(raw_data, "xlsx")
        read_csv_with_nan(raw_data, nan_percent=nan_percent).to_excel(dataset_to_use, index=False)

    elif data_format == "excel_xls":
        dataset_to_use = replace_file_extension(raw_data, "xls")
        read_csv_with_nan(raw_data, nan_percent=nan_percent).to_excel(dataset_to_use, index=False)

    elif data_format == "feather":
        dataset_to_use = replace_file_extension(raw_data, "feather")
        read_csv_with_nan(raw_data, nan_percent=nan_percent).to_feather(dataset_to_use)

    elif data_format == "fwf":
        dataset_to_use = replace_file_extension(raw_data, "fwf")
        read_csv_with_nan(raw_data, nan_percent=nan_percent).to_fwf(dataset_to_use)

    elif data_format == "html":
        dataset_to_use = replace_file_extension(raw_data, "html")
        read_csv_with_nan(raw_data, nan_percent=nan_percent).to_html(dataset_to_use, index=False)

    elif data_format == "json":
        dataset_to_use = replace_file_extension(raw_data, "json")
        read_csv_with_nan(raw_data, nan_percent=nan_percent).to_json(dataset_to_use, orient="records")

    elif data_format == "jsonl":
        dataset_to_use = replace_file_extension(raw_data, "jsonl")
        read_csv_with_nan(raw_data, nan_percent=nan_percent).to_json(dataset_to_use, orient="records", lines=True)

    elif data_format == "parquet":
        dataset_to_use = replace_file_extension(raw_data, "parquet")
        read_csv_with_nan(raw_data, nan_percent=nan_percent).to_parquet(dataset_to_use, index=False)

    elif data_format == "pickle":
        dataset_to_use = replace_file_extension(raw_data, "pickle")
        read_csv_with_nan(raw_data, nan_percent=nan_percent).to_pickle(dataset_to_use)

    elif data_format == "stata":
        dataset_to_use = replace_file_extension(raw_data, "stata")
        read_csv_with_nan(raw_data, nan_percent=nan_percent).to_stata(dataset_to_use)

    elif data_format == "tsv":
        dataset_to_use = replace_file_extension(raw_data, "tsv")
        read_csv_with_nan(raw_data, nan_percent=nan_percent).to_csv(dataset_to_use, sep="\t", index=False)

    elif data_format == "pandas+numpy_images":
        df = read_csv_with_nan(raw_data, nan_percent=nan_percent)
        processed_df_rows = []
        for _, row in df.iterrows():
            processed_df_row = {}
            for feature_name, raw_feature in row.iteritems():
                if "image" in feature_name and not (type(raw_feature) == float and np.isnan(raw_feature)):
                    feature = np.array(Image.open(raw_feature))
                else:
                    feature = raw_feature
                processed_df_row[feature_name] = feature
            processed_df_rows.append(processed_df_row)
        dataset_to_use = pd.DataFrame(processed_df_rows)

    else:
        ValueError(f"'{data_format}' is an unrecognized data format")

    return dataset_to_use


def augment_dataset_with_none(
    df: pd.DataFrame, first_row_none: bool = False, last_row_none: bool = False, nan_cols: List = []
) -> pd.DataFrame:
    """Optionally sets the first and last rows of nan_cols of the given dataframe to nan.

    :param df: dataframe containg input features/output features
    :type df: pd.DataFrame
    :param first_row_none: indicates whether to set the first rowin the dataframe to np.nan
    :type first_row_none: bool
    :param last_row_none: indicates whether to set the last row in the dataframe to np.nan
    :type last_row_none: bool
    :param nan_cols: a list of columns in the dataframe to explicitly set the first or last rows to np.nan
    :type nan_cols: list
    """
    if first_row_none:
        for col in nan_cols:
            df.iloc[0, df.columns.get_loc(col)] = np.nan
    if last_row_none:
        for col in nan_cols:
            df.iloc[-1, df.columns.get_loc(col)] = np.nan
    return df


def train_with_backend(
    backend,
    config,
    dataset=None,
    training_set=None,
    validation_set=None,
    test_set=None,
    predict=True,
    evaluate=True,
    callbacks=None,
    skip_save_processed_input=True,
    skip_save_predictions=True,
):
    model = LudwigModel(config, backend=backend, callbacks=callbacks)
    output_dir = None

    try:
        _, _, output_dir = model.train(
            dataset=dataset,
            training_set=training_set,
            validation_set=validation_set,
            test_set=test_set,
            skip_save_processed_input=skip_save_processed_input,
            skip_save_progress=True,
            skip_save_unprocessed_output=True,
            skip_save_log=True,
        )

        if dataset is None:
            dataset = training_set

        if predict:
            preds, _ = model.predict(dataset=dataset, skip_save_predictions=skip_save_predictions)
            assert preds is not None

        if evaluate:
            eval_stats, eval_preds, _ = model.evaluate(
                dataset=dataset, collect_overall_stats=False, collect_predictions=True
            )
            assert eval_preds is not None

            # Test that eval_stats are approx equal when using local backend
            with tempfile.TemporaryDirectory() as tmpdir:
                model.save(tmpdir)
                local_model = LudwigModel.load(tmpdir, backend=LocalTestBackend())
                local_eval_stats, _, _ = local_model.evaluate(
                    dataset=dataset, collect_overall_stats=False, collect_predictions=False
                )

                # Filter out metrics that are not being aggregated correctly for now
                # TODO(travis): https://github.com/ludwig-ai/ludwig/issues/1956
                def filter(stats):
                    return {
                        k: {
                            metric_name: value
                            for metric_name, value in v.items()
                            if metric_name not in {"loss", "root_mean_squared_percentage_error"}
                        }
                        for k, v in stats.items()
                    }

                for (k1, v1), (k2, v2) in zip(filter(eval_stats).items(), filter(local_eval_stats).items()):
                    assert k1 == k2
                    for (name1, metric1), (name2, metric2) in zip(v1.items(), v2.items()):
                        assert name1 == name2
                        assert np.isclose(
                            metric1, metric2, rtol=1e-04, atol=1e-5
                        ), f"metric {name1}: {metric1} != {metric2}"

        return model
    finally:
        # Remove results/intermediate data saved to disk
        shutil.rmtree(output_dir, ignore_errors=True)
