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
import os
import tempfile

import numpy as np
import pandas as pd
import pytest
import torch
from packaging import version

from ludwig.api import LudwigModel
from ludwig.backend import create_ray_backend, initialize_backend, LOCAL_BACKEND
from ludwig.constants import (
    BALANCE_PERCENTAGE_TOLERANCE,
    BFILL,
    COLUMN,
    DEFAULT_BATCH_SIZE,
    NAME,
    PREPROCESSING,
    TRAINER,
)
from ludwig.data.preprocessing import balance_data
from ludwig.utils.data_utils import read_parquet
from tests.integration_tests.utils import (
    audio_feature,
    augment_dataset_with_none,
    bag_feature,
    binary_feature,
    category_feature,
    create_data_set_to_use,
    date_feature,
    generate_data,
    h3_feature,
    image_feature,
    number_feature,
    RAY_BACKEND_CONFIG,
    sequence_feature,
    set_feature,
    text_feature,
    timeseries_feature,
    train_with_backend,
    vector_feature,
)

try:
    import modin
    import ray

    from ludwig.backend.ray import get_trainer_kwargs, RayBackend
    from ludwig.data.dataframe.dask import DaskEngine

    @ray.remote(num_cpus=1, num_gpus=1)
    def train_gpu(config, dataset, output_directory):
        model = LudwigModel(config, backend="local")
        _, _, output_dir = model.train(dataset, output_directory=output_directory)
        return os.path.join(output_dir, "model")

    @ray.remote(num_cpus=1, num_gpus=0)
    def predict_cpu(model_dir, dataset):
        model = LudwigModel.load(model_dir, backend="local")
        model.predict(dataset)

    # Ray nightly version is always set to 3.0.0.dev0
    _ray_nightly = version.parse(ray.__version__) >= version.parse("3.0.0.dev0")
    _modin_ray_incompatible = version.parse(modin.__version__) <= version.parse("0.15.2") and version.parse(
        ray.__version__
    ) >= version.parse("1.13.0")

except ImportError:
    modin = None
    ray = None

    _ray_nightly = False
    _modin_ray_incompatible = False


def run_api_experiment(
    config, dataset, backend_config, predict=False, skip_save_processed_input=True, skip_save_predictions=True
):
    # Sanity check that we get 4 slots over 1 host
    kwargs = get_trainer_kwargs()
    if torch.cuda.device_count() > 0:
        assert kwargs.get("num_workers") == torch.cuda.device_count(), kwargs
        assert kwargs.get("use_gpu"), kwargs
    else:
        assert kwargs.get("num_workers") == 1, kwargs
        assert not kwargs.get("use_gpu"), kwargs

    # Train on Parquet
    model = train_with_backend(
        backend_config,
        config,
        dataset=dataset,
        evaluate=True,
        predict=predict,
        skip_save_processed_input=skip_save_processed_input,
        skip_save_predictions=skip_save_predictions,
    )

    assert isinstance(model.backend, RayBackend)
    if isinstance(model.backend.df_engine, DaskEngine):
        assert model.backend.df_engine.parallelism == backend_config["processor"]["parallelism"]

    return model


def run_split_api_experiment(config, data_parquet, backend_config):
    train_fname, val_fname, test_fname = split(data_parquet)

    # Train
    train_with_backend(backend_config, config, training_set=train_fname, evaluate=False, predict=True)

    # Train + Validation
    train_with_backend(
        backend_config, config, training_set=train_fname, validation_set=val_fname, evaluate=False, predict=False
    )

    # Train + Validation + Test
    train_with_backend(
        backend_config,
        config,
        training_set=train_fname,
        validation_set=val_fname,
        test_set=test_fname,
        evaluate=False,
        predict=False,
    )


def split(data_parquet):
    data_df = read_parquet(data_parquet, LOCAL_BACKEND.df_engine.df_lib)
    train_df = data_df.sample(frac=0.8)
    test_df = data_df.drop(train_df.index).sample(frac=0.5)
    validation_df = data_df.drop(train_df.index).drop(test_df.index)

    basename, ext = os.path.splitext(data_parquet)
    train_fname = basename + ".train" + ext
    val_fname = basename + ".validation" + ext
    test_fname = basename + ".test" + ext

    train_df.to_parquet(train_fname)
    validation_df.to_parquet(val_fname)
    test_df.to_parquet(test_fname)
    return train_fname, val_fname, test_fname


def run_test_with_features(
    input_features,
    output_features,
    num_examples=100,
    run_fn=run_api_experiment,
    expect_error=False,
    df_engine=None,
    dataset_type="parquet",
    predict=False,
    skip_save_processed_input=True,
    skip_save_predictions=True,
    nan_percent=0.0,
    preprocessing=None,
    first_row_none=False,
    last_row_none=False,
    nan_cols=[],
):
    preprocessing = preprocessing or {}
    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat", "output_size": 14},
        TRAINER: {"epochs": 2, "batch_size": 8},
    }
    if preprocessing:
        config[PREPROCESSING] = preprocessing

    backend_config = {**RAY_BACKEND_CONFIG}
    if df_engine:
        backend_config["processor"]["type"] = df_engine

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_filename = os.path.join(tmpdir, "dataset.csv")
        dataset_csv = generate_data(input_features, output_features, csv_filename, num_examples=num_examples)
        dataset = create_data_set_to_use(dataset_type, dataset_csv, nan_percent=nan_percent)
        dataset = augment_dataset_with_none(dataset, first_row_none, last_row_none, nan_cols)

        if expect_error:
            with pytest.raises(ValueError):
                run_fn(
                    config,
                    dataset=dataset,
                    backend_config=backend_config,
                    predict=predict,
                    skip_save_processed_input=skip_save_processed_input,
                    skip_save_predictions=skip_save_predictions,
                )
        else:
            run_fn(
                config,
                dataset=dataset,
                backend_config=backend_config,
                predict=predict,
                skip_save_processed_input=skip_save_processed_input,
                skip_save_predictions=skip_save_predictions,
            )


@pytest.mark.parametrize("df_engine", ["pandas", "dask"])
@pytest.mark.distributed
def test_ray_read_binary_files(tmpdir, df_engine, ray_cluster_2cpu):
    preprocessing_params = {
        "audio_file_length_limit_in_s": 3.0,
        "missing_value_strategy": BFILL,
        "in_memory": True,
        "padding_value": 0,
        "norm": "per_file",
        "audio_feature": {
            "type": "fbank",
            "window_length_in_s": 0.04,
            "window_shift_in_s": 0.02,
            "num_filter_bands": 80,
        },
    }
    audio_dest_folder = os.path.join(tmpdir, "generated_audio")
    audio_params = audio_feature(folder=audio_dest_folder, preprocessing=preprocessing_params)

    dataset_path = os.path.join(tmpdir, "dataset.csv")
    dataset_path = generate_data([audio_params], [], dataset_path, num_examples=10)
    dataset_path = create_data_set_to_use("csv", dataset_path, nan_percent=0.1)

    backend_config = {**RAY_BACKEND_CONFIG}
    backend_config["processor"]["type"] = df_engine
    backend = initialize_backend(backend_config)
    df = backend.df_engine.df_lib.read_csv(dataset_path)
    series = df[audio_params[COLUMN]]
    proc_col = backend.read_binary_files(series)
    proc_col = backend.df_engine.compute(proc_col)

    backend = initialize_backend(LOCAL_BACKEND)
    df = backend.df_engine.df_lib.read_csv(dataset_path)
    series = df[audio_params[COLUMN]]
    proc_col_expected = backend.read_binary_files(series)

    assert proc_col.equals(proc_col_expected)


# TODO(geoffrey): Add dataset_type="csv" back to parameters if we can prevent CI timeouts.
@pytest.mark.parametrize("dataset_type", ["parquet"])
@pytest.mark.distributed
def test_ray_save_inputs_with_nans(tmpdir, dataset_type, ray_cluster_2cpu):
    image_dest_folder = os.path.join(tmpdir, "generated_images")
    audio_dest_folder = os.path.join(tmpdir, "generated_audio")
    input_features = [
        image_feature(
            folder=image_dest_folder,
            preprocessing={"in_memory": True, "height": 12, "width": 12, "num_channels": 3, "num_processes": 5},
            encoder={"output_size": 16, "num_filters": 8},
        ),
        audio_feature(
            folder=audio_dest_folder,
            preprocessing={
                "audio_file_length_limit_in_s": 3.0,
                "missing_value_strategy": BFILL,
                "in_memory": True,
                "padding_value": 0,
                "norm": "per_file",
                "type": "fbank",
                "window_length_in_s": 0.04,
                "window_shift_in_s": 0.02,
                "num_filter_bands": 80,
            },
        ),
        sequence_feature(encoder={"reduce_output": "sum"}),
        category_feature(encoder={"vocab_size": 2}, reduce_input="sum"),
        number_feature(normalization="zscore"),
        set_feature(),
        binary_feature(),
        bag_feature(),
        text_feature(),
        timeseries_feature(),
        date_feature(),
        # TODO: NaN handling not supported. See `test_ray_save_inputs_and_outputs_without_nans` below.
        # vector_feature(),  # NaNs are not supported by the feature
        # TODO: feature type not yet supported
        # h3_feature(),  # ValueError casting large int strings (e.g. '5.864041857092157e+17') to int: MLI-72
    ]
    output_features = [
        category_feature(decoder={"vocab_size": 5}),  # Regression test for #1991 requires multi-class predictions.
    ]
    run_test_with_features(
        input_features,
        output_features,
        df_engine="dask",
        dataset_type=dataset_type,
        skip_save_processed_input=False,
        nan_percent=0.1,
    )


@pytest.mark.parametrize("dataset_type", ["csv", "parquet"])
@pytest.mark.distributed
def test_ray_save_inputs_without_nans(dataset_type, ray_cluster_2cpu):
    input_features = [
        vector_feature(),
    ]
    output_features = [
        binary_feature(),
    ]
    run_test_with_features(
        input_features,
        output_features,
        df_engine="dask",
        dataset_type=dataset_type,
        skip_save_processed_input=False,
    )


@pytest.mark.parametrize("dataset_type", ["csv", "parquet"])
@pytest.mark.distributed
def test_ray_save_outputs(dataset_type, ray_cluster_2cpu):
    input_features = [
        binary_feature(),
    ]
    output_features = [
        binary_feature(),
        number_feature(),
        vector_feature(),
        # TODO: feature type not yet supported
        # set_feature(decoder={"vocab_size": 3}),  # Probabilities of set_feature are ragged tensors: MLI-71
        # sequence_feature(decoder={"vocab_size": 3}),  # Error having to do with a missing key: MLI-70
        # text_feature(decoder={"vocab_size": 3}),      # Error having to do with a missing key: MLI-70
    ]
    # NOTE: This test runs without NaNs because having multiple output features with DROP_ROWS strategy leads to
    # flakiness in the test having to do with uneven allocation of samples between Ray workers.
    run_test_with_features(
        input_features,
        output_features,
        df_engine="dask",
        dataset_type=dataset_type,
        predict=True,
        skip_save_predictions=False,
    )


@pytest.mark.distributed
@pytest.mark.parametrize(
    "df_engine",
    [
        "dask",
        pytest.param(
            "modin",
            marks=pytest.mark.skipif(_modin_ray_incompatible, reason="modin<=0.15.2 does not support ray>=1.13.0"),
        ),
    ],
)
def test_ray_tabular(df_engine, ray_cluster_2cpu):
    input_features = [
        sequence_feature(encoder={"reduce_output": "sum"}),
        category_feature(encoder={"vocab_size": 2}, reduce_input="sum"),
        number_feature(normalization="zscore"),
        set_feature(),
        binary_feature(),
        bag_feature(),
        vector_feature(),
        h3_feature(),
        date_feature(),
    ]
    output_features = [
        binary_feature(bool2str=["No", "Yes"]),
        binary_feature(),
        number_feature(normalization="zscore"),
    ]
    run_test_with_features(
        input_features,
        output_features,
        df_engine=df_engine,
    )


@pytest.mark.skip(reason="TODO torch")
@pytest.mark.distributed
def test_ray_text(ray_cluster_2cpu):
    input_features = [
        text_feature(),
    ]
    output_features = [
        text_feature(reduce_input=None, decoder={"type": "tagger"}),
    ]
    run_test_with_features(input_features, output_features)


@pytest.mark.skip(reason="TODO torch")
@pytest.mark.distributed
def test_ray_sequence(ray_cluster_2cpu):
    input_features = [
        sequence_feature(encoder={"max_len": 10, "type": "rnn", "cell_type": "lstm", "reduce_output": None})
    ]
    output_features = [
        sequence_feature(decoder={"max_len": 10, "type": "tagger", "attention": False}, reduce_input=None)
    ]
    run_test_with_features(input_features, output_features)


@pytest.mark.parametrize("dataset_type", ["csv", "parquet"])
@pytest.mark.distributed
def test_ray_audio(tmpdir, dataset_type, ray_cluster_2cpu):
    preprocessing_params = {
        "audio_file_length_limit_in_s": 3.0,
        "missing_value_strategy": BFILL,
        "in_memory": True,
        "padding_value": 0,
        "norm": "per_file",
        "type": "fbank",
        "window_length_in_s": 0.04,
        "window_shift_in_s": 0.02,
        "num_filter_bands": 80,
    }
    audio_dest_folder = os.path.join(tmpdir, "generated_audio")
    input_features = [audio_feature(folder=audio_dest_folder, preprocessing=preprocessing_params)]
    output_features = [binary_feature()]
    run_test_with_features(
        input_features,
        output_features,
        dataset_type=dataset_type,
        nan_percent=0.1,
    )


@pytest.mark.parametrize("dataset_type", ["csv", "parquet", "pandas+numpy_images"])
@pytest.mark.distributed
def test_ray_image(tmpdir, dataset_type, ray_cluster_2cpu):
    image_dest_folder = os.path.join(tmpdir, "generated_images")
    input_features = [
        image_feature(
            folder=image_dest_folder,
            preprocessing={"in_memory": True, "height": 12, "width": 12, "num_channels": 3, "num_processes": 5},
            encoder={"output_size": 16, "num_filters": 8},
        ),
    ]
    output_features = [binary_feature()]
    run_test_with_features(
        input_features,
        output_features,
        df_engine="dask",
        dataset_type=dataset_type,
        skip_save_processed_input=False,
        nan_percent=0.1,
    )


@pytest.mark.parametrize(
    "settings",
    [(True, False, "ffill"), (False, True, "bfill"), (True, True, "bfill"), (True, True, "ffill")],
    ids=["first_row_none", "last_row_none", "first_and_last_row_none_bfill", "first_and_last_row_none_ffill"],
)
@pytest.mark.distributed
def test_ray_image_with_fill_strategy_edge_cases(tmpdir, settings, ray_cluster_2cpu):
    first_row_none, last_row_none, missing_value_strategy = settings
    image_dest_folder = os.path.join(tmpdir, "generated_images")
    input_features = [
        image_feature(
            folder=image_dest_folder,
            preprocessing={
                "in_memory": True,
                "height": 12,
                "width": 12,
                "num_channels": 3,
                "num_processes": 5,
                "missing_value_strategy": missing_value_strategy,
            },
            encoder={"output_size": 16, "num_filters": 8},
        ),
    ]
    output_features = [binary_feature()]
    run_test_with_features(
        input_features,
        output_features,
        df_engine="dask",
        dataset_type="pandas+numpy_images",
        skip_save_processed_input=False,
        first_row_none=first_row_none,
        last_row_none=last_row_none,
        nan_cols=[input_features[0][NAME]],
    )


# TODO(geoffrey): Fold modin tests into test_ray_image as @pytest.mark.parametrized once tests are optimized
@pytest.mark.distributed
@pytest.mark.skipif(_modin_ray_incompatible, reason="modin<=0.15.2 does not support ray>=1.13.0")
def test_ray_image_modin(tmpdir, ray_cluster_2cpu):
    image_dest_folder = os.path.join(tmpdir, "generated_images")
    input_features = [
        image_feature(
            folder=image_dest_folder,
            encoder={"type": "resnet", "output_size": 16, "num_filters": 8},
            preprocessing={"in_memory": True, "height": 12, "width": 12, "num_channels": 3, "num_processes": 5},
        ),
    ]
    output_features = [binary_feature()]
    run_test_with_features(
        input_features,
        output_features,
        df_engine="modin",
        dataset_type="csv",
        nan_percent=0.1,
    )


@pytest.mark.distributed
def test_ray_image_multiple_features(tmpdir, ray_cluster_2cpu):
    input_features = [
        image_feature(
            folder=os.path.join(tmpdir, "generated_images_1"),
            preprocessing={"in_memory": True, "height": 12, "width": 12, "num_channels": 3, "num_processes": 5},
            encoder={"output_size": 16, "num_filters": 8},
        ),
        image_feature(
            folder=os.path.join(tmpdir, "generated_images_2"),
            preprocessing={"in_memory": True, "height": 12, "width": 12, "num_channels": 3, "num_processes": 5},
            encoder={"output_size": 16, "num_filters": 8},
        ),
    ]
    output_features = [binary_feature()]
    run_test_with_features(
        input_features,
        output_features,
        df_engine="dask",
        dataset_type="csv",
        nan_percent=0.1,
    )


@pytest.mark.skip(reason="flaky: ray is running out of resources")
@pytest.mark.distributed
def test_ray_split(ray_cluster_2cpu):
    input_features = [
        number_feature(normalization="zscore"),
        set_feature(),
        binary_feature(),
    ]
    output_features = [category_feature(decoder={"vocab_size": 2}, reduce_input="sum")]
    run_test_with_features(
        input_features,
        output_features,
        run_fn=run_split_api_experiment,
    )


@pytest.mark.distributed
def test_ray_timeseries(ray_cluster_2cpu):
    input_features = [timeseries_feature()]
    output_features = [number_feature()]
    run_test_with_features(input_features, output_features)


@pytest.mark.distributed
def test_ray_lazy_load_audio_error(tmpdir, ray_cluster_2cpu):
    audio_dest_folder = os.path.join(tmpdir, "generated_audio")
    input_features = [
        audio_feature(
            folder=audio_dest_folder,
            preprocessing={
                "in_memory": False,
            },
        )
    ]
    output_features = [binary_feature()]
    run_test_with_features(input_features, output_features, expect_error=True)


@pytest.mark.distributed
def test_ray_lazy_load_image_error(tmpdir, ray_cluster_2cpu):
    image_dest_folder = os.path.join(tmpdir, "generated_images")
    input_features = [
        image_feature(
            folder=image_dest_folder,
            encoder={"type": "resnet", "output_size": 16, "num_filters": 8},
            preprocessing={"in_memory": False, "height": 12, "width": 12, "num_channels": 3, "num_processes": 5},
        ),
    ]
    output_features = [binary_feature()]
    run_test_with_features(input_features, output_features, expect_error=True)


# TODO(travis): move this to separate gpu module so we only have one ray cluster running at a time
# @pytest.mark.skipif(torch.cuda.device_count() == 0, reason="test requires at least 1 gpu")
# @pytest.mark.skipif(not torch.cuda.is_available(), reason="test requires gpu support")
# @pytest.mark.distributed
# def test_train_gpu_load_cpu(ray_cluster_2cpu):
#     input_features = [
#         category_feature(encoder={"vocab_size": 2}, reduce_input="sum"),
#         number_feature(normalization="zscore"),
#     ]
#     output_features = [
#         binary_feature(),
#     ]
#     run_test_with_features(input_features, output_features, run_fn=_run_train_gpu_load_cpu, num_gpus=1)


@pytest.mark.distributed
@pytest.mark.parametrize(
    "method, balance",
    [
        ("oversample_minority", 0.25),
        ("oversample_minority", 0.5),
        ("oversample_minority", 0.75),
        ("undersample_majority", 0.25),
        ("undersample_majority", 0.5),
        ("undersample_majority", 0.75),
    ],
)
def test_balance_ray(method, balance, ray_cluster_2cpu):
    config = {
        "input_features": [
            {"name": "Index", "proc_column": "Index", "type": "number"},
            {"name": "random_1", "proc_column": "random_1", "type": "number"},
            {"name": "random_2", "proc_column": "random_2", "type": "number"},
        ],
        "output_features": [{"name": "Label", "proc_column": "Label", "type": "binary"}],
        "preprocessing": {"oversample_minority": None, "undersample_majority": None},
    }
    input_df = pd.DataFrame(
        {
            "Index": np.arange(0, 200, 1),
            "random_1": np.random.randint(0, 50, 200),
            "random_2": np.random.choice(["Type A", "Type B", "Type C", "Type D"], 200),
            "Label": np.concatenate((np.zeros(180), np.ones(20))),
            "split": np.zeros(200),
        }
    )
    config["preprocessing"][method] = balance
    target = config["output_features"][0][NAME]

    backend = create_ray_backend()
    input_df = backend.df_engine.from_pandas(input_df)
    test_df = balance_data(input_df, config["output_features"], config["preprocessing"], backend)

    majority_class = test_df[target].value_counts().compute()[test_df[target].value_counts().compute().idxmax()]
    minority_class = test_df[target].value_counts().compute()[test_df[target].value_counts().compute().idxmin()]
    new_class_balance = round(minority_class / majority_class, 2)

    assert abs(balance - new_class_balance) < BALANCE_PERCENTAGE_TOLERANCE


def _run_train_gpu_load_cpu(config, data_parquet):
    with tempfile.TemporaryDirectory() as output_dir:
        model_dir = ray.get(train_gpu.remote(config, data_parquet, output_dir))
        ray.get(predict_cpu.remote(model_dir, data_parquet))


# TODO(geoffrey): add a GPU test for batch size tuning
@pytest.mark.distributed
def test_tune_batch_size_lr_cpu(tmpdir, ray_cluster_2cpu):
    config = {
        "input_features": [
            number_feature(normalization="zscore"),
            set_feature(),
            binary_feature(),
        ],
        "output_features": [category_feature(decoder={"vocab_size": 2}, reduce_input="sum")],
        "combiner": {"type": "concat", "output_size": 14},
        TRAINER: {"epochs": 2, "batch_size": "auto", "learning_rate": "auto"},
    }

    backend_config = {**RAY_BACKEND_CONFIG}

    csv_filename = os.path.join(tmpdir, "dataset.csv")
    dataset_csv = generate_data(config["input_features"], config["output_features"], csv_filename, num_examples=200)
    dataset_parquet = create_data_set_to_use("parquet", dataset_csv)
    model = run_api_experiment(config, dataset=dataset_parquet, backend_config=backend_config)
    assert (
        model.config[TRAINER]["batch_size"] == DEFAULT_BATCH_SIZE
    )  # On CPU, batch size tuning is disabled, so assert it is equal to default
    assert model.config[TRAINER]["learning_rate"] != "auto"


@pytest.mark.distributed
def test_ray_progress_bar(ray_cluster_2cpu):
    # This is a simple test that is just meant to make sure that the progress bar isn't breaking
    input_features = [
        sequence_feature(encoder={"reduce_output": "sum"}),
    ]
    output_features = [
        binary_feature(bool2str=["No", "Yes"]),
    ]
    run_test_with_features(
        input_features,
        output_features,
        df_engine="dask",
    )


@pytest.mark.parametrize("calibration", [True, False])
@pytest.mark.distributed
def test_ray_calibration(calibration, ray_cluster_2cpu):
    input_features = [
        number_feature(normalization="zscore"),
        set_feature(),
        binary_feature(),
    ]
    output_features = [
        binary_feature(calibration=calibration),
        category_feature(decoder={"vocab_size": 3}, calibration=calibration),
    ]
    run_test_with_features(input_features, output_features)


@pytest.mark.distributed
def test_ray_distributed_predict(tmpdir, ray_cluster_2cpu):
    preprocessing_params = {
        "audio_file_length_limit_in_s": 3.0,
        "missing_value_strategy": BFILL,
        "in_memory": True,
        "padding_value": 0,
        "norm": "per_file",
        "type": "fbank",
        "window_length_in_s": 0.04,
        "window_shift_in_s": 0.02,
        "num_filter_bands": 80,
    }
    audio_dest_folder = os.path.join(tmpdir, "generated_audio")
    input_features = [audio_feature(folder=audio_dest_folder, preprocessing=preprocessing_params)]
    output_features = [binary_feature()]

    config = {
        "input_features": input_features,
        "output_features": output_features,
        TRAINER: {"epochs": 2, "batch_size": 8},
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        backend_config = {**RAY_BACKEND_CONFIG}
        csv_filename = os.path.join(tmpdir, "dataset.csv")
        dataset_csv = generate_data(input_features, output_features, csv_filename, num_examples=100)
        dataset = create_data_set_to_use("csv", dataset_csv, nan_percent=0.0)
        model = LudwigModel(config, backend=backend_config)
        output_dir = None

        _, _, output_dir = model.train(
            dataset=dataset,
            training_set=dataset,
            skip_save_processed_input=True,
            skip_save_progress=True,
            skip_save_unprocessed_output=True,
            skip_save_log=True,
        )

        preds, _ = model.predict(dataset=dataset)

        # compute the predictions
        preds = preds.compute()
        assert preds.iloc[1].name != preds.iloc[42].name


@pytest.mark.distributed
def test_ray_preprocessing_placement_group(tmpdir, ray_cluster_2cpu):
    preprocessing_params = {
        "audio_file_length_limit_in_s": 3.0,
        "missing_value_strategy": BFILL,
        "in_memory": True,
        "padding_value": 0,
        "norm": "per_file",
        "type": "fbank",
        "window_length_in_s": 0.04,
        "window_shift_in_s": 0.02,
        "num_filter_bands": 80,
    }
    audio_dest_folder = os.path.join(tmpdir, "generated_audio")
    input_features = [audio_feature(folder=audio_dest_folder, preprocessing=preprocessing_params)]
    output_features = [binary_feature()]

    config = {
        "input_features": input_features,
        "output_features": output_features,
        TRAINER: {"epochs": 2, "batch_size": 8},
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        backend_config = {**RAY_BACKEND_CONFIG}
        backend_config["preprocessor_kwargs"] = {"num_cpu": 1}
        csv_filename = os.path.join(tmpdir, "dataset.csv")
        dataset_csv = generate_data(input_features, output_features, csv_filename, num_examples=100)
        dataset = create_data_set_to_use("csv", dataset_csv, nan_percent=0.0)
        model = LudwigModel(config, backend=backend_config)
        _, _, output_dir = model.train(
            dataset=dataset,
            training_set=dataset,
            skip_save_processed_input=True,
            skip_save_progress=True,
            skip_save_unprocessed_output=True,
            skip_save_log=True,
        )
        preds, _ = model.predict(dataset=dataset)
