import os

import numpy as np
import pandas as pd
import pytest

from ludwig.api import LudwigModel
from ludwig.constants import COLUMN, PROC_COLUMN
from tests.integration_tests.utils import (
    audio_feature,
    binary_feature,
    category_feature,
    generate_data,
    image_feature,
    init_backend,
    LocalTestBackend,
    sequence_feature,
)


@pytest.mark.parametrize("backend", ["local", "ray"])
@pytest.mark.distributed
def test_sample_ratio(backend, tmpdir):
    num_examples = 100
    sample_ratio = 0.25

    input_features = [sequence_feature(reduce_output="sum")]
    output_features = [category_feature(vocab_size=5, reduce_input="sum")]
    data_csv = generate_data(
        input_features, output_features, os.path.join(tmpdir, "dataset.csv"), num_examples=num_examples
    )
    config = {
        "input_features": input_features,
        "output_features": output_features,
        "trainer": {
            "epochs": 2,
        },
        "preprocessing": {"sample_ratio": sample_ratio},
    }

    with init_backend(backend):
        model = LudwigModel(config, backend=backend)
        train_set, val_set, test_set, _ = model.preprocess(
            data_csv,
            skip_save_processed_input=True,
        )

        sample_size = num_examples * sample_ratio
        count = len(train_set) + len(val_set) + len(test_set)
        assert sample_size == count


def test_strip_whitespace_category(csv_filename, tmpdir):
    data_csv_path = os.path.join(tmpdir, csv_filename)

    input_features = [binary_feature()]
    cat_feat = category_feature(vocab_size=3)
    output_features = [cat_feat]
    backend = LocalTestBackend()
    config = {"input_features": input_features, "output_features": output_features}

    training_data_csv_path = generate_data(input_features, output_features, data_csv_path)
    df = pd.read_csv(training_data_csv_path)

    # prefix with whitespace
    df[cat_feat[COLUMN]] = df[cat_feat[COLUMN]].apply(lambda s: " " + s)

    # run preprocessing
    ludwig_model = LudwigModel(config, backend=backend)
    train_ds, _, _, metadata = ludwig_model.preprocess(dataset=df)

    # expect values containing whitespaces to be properly mapped to vocab_size unique values
    assert len(np.unique(train_ds.dataset[cat_feat[PROC_COLUMN]])) == cat_feat["vocab_size"]


@pytest.mark.parametrize("backend", ["local", "ray"])
@pytest.mark.distributed
def test_with_split(backend, csv_filename, tmpdir):
    num_examples = 10
    train_set_size = int(num_examples * 0.8)
    val_set_size = int(num_examples * 0.1)
    test_set_size = int(num_examples * 0.1)

    input_features = [sequence_feature(reduce_output="sum")]
    output_features = [category_feature(vocab_size=5, reduce_input="sum")]
    data_csv = generate_data(
        input_features, output_features, os.path.join(tmpdir, csv_filename), num_examples=num_examples
    )
    data_df = pd.read_csv(data_csv)
    data_df["split"] = [0] * train_set_size + [1] * val_set_size + [2] * test_set_size
    data_df.to_csv(data_csv, index=False)
    config = {
        "input_features": input_features,
        "output_features": output_features,
        "trainer": {
            "epochs": 2,
        },
    }

    with init_backend(backend):
        model = LudwigModel(config, backend=backend)
        train_set, val_set, test_set, _ = model.preprocess(
            data_csv,
            skip_save_processed_input=False,
        )
        assert len(train_set) == train_set_size
        assert len(val_set) == val_set_size
        assert len(test_set) == test_set_size


@pytest.mark.parametrize("feature_fn", [image_feature, audio_feature])
@pytest.mark.distributed
def test_dask_known_divisions(feature_fn, csv_filename, tmpdir):
    import dask.dataframe as dd

    num_examples = 10

    input_features = [feature_fn(os.path.join(tmpdir, "generated_output"))]
    output_features = [category_feature(vocab_size=5, reduce_input="sum")]
    data_csv = generate_data(
        input_features, output_features, os.path.join(tmpdir, csv_filename), num_examples=num_examples
    )
    data_df = dd.from_pandas(pd.read_csv(data_csv), npartitions=1)
    assert data_df.known_divisions

    config = {
        "input_features": input_features,
        "output_features": output_features,
        "trainer": {
            "epochs": 2,
        },
    }

    backend = "ray"
    with init_backend(backend):
        model = LudwigModel(config, backend=backend)
        train_set, val_set, test_set, _ = model.preprocess(
            data_df,
            skip_save_processed_input=False,
        )
