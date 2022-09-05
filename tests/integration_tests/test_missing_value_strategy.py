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
import random

import numpy as np
import pandas as pd
import pytest

from ludwig.api import LudwigModel
from ludwig.constants import DROP_ROW, FILL_WITH_MEAN, PREPROCESSING, TRAINER
from tests.integration_tests.utils import (
    binary_feature,
    category_feature,
    generate_data,
    LocalTestBackend,
    number_feature,
    read_csv_with_nan,
    sequence_feature,
    set_feature,
    text_feature,
    vector_feature,
)


def test_missing_value_prediction(tmpdir, csv_filename):
    random.seed(1)
    np.random.seed(1)
    input_features = [
        category_feature(
            encoder={"vocab_size": 2}, reduce_input="sum", preprocessing=dict(missing_value_strategy="fill_with_mode")
        )
    ]
    output_features = [binary_feature()]

    dataset = pd.read_csv(generate_data(input_features, output_features, csv_filename))

    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat", "output_size": 14},
    }
    model = LudwigModel(config)
    _, _, output_dir = model.train(dataset=dataset, output_directory=tmpdir)

    # Set the input column to None, we should be able to replace the missing value with the mode
    # from the training set
    dataset[input_features[0]["name"]] = None
    model.predict(dataset=dataset)

    model = LudwigModel.load(os.path.join(output_dir, "model"))
    model.predict(dataset=dataset)


@pytest.mark.parametrize(
    "backend",
    [
        pytest.param("local", id="local"),
        pytest.param("ray", id="ray", marks=pytest.mark.distributed),
    ],
)
def test_missing_values_fill_with_mean(backend, csv_filename, tmpdir, ray_cluster_2cpu):
    data_csv_path = os.path.join(tmpdir, csv_filename)

    kwargs = {PREPROCESSING: {"missing_value_strategy": FILL_WITH_MEAN}}
    input_features = [
        number_feature(**kwargs),
        binary_feature(),
        category_feature(encoder={"vocab_size": 3}),
    ]
    output_features = [binary_feature()]
    training_data_csv_path = generate_data(input_features, output_features, data_csv_path)

    config = {"input_features": input_features, "output_features": output_features, TRAINER: {"epochs": 2}}

    # run preprocessing
    ludwig_model = LudwigModel(config, backend=backend)
    ludwig_model.preprocess(dataset=training_data_csv_path)


def test_missing_values_drop_rows(csv_filename, tmpdir):
    data_csv_path = os.path.join(tmpdir, csv_filename)

    kwargs = {PREPROCESSING: {"missing_value_strategy": DROP_ROW}}
    input_features = [
        number_feature(),
        binary_feature(),
        category_feature(encoder={"vocab_size": 3}),
    ]
    output_features = [
        binary_feature(**kwargs),
        number_feature(**kwargs),
        category_feature(decoder={"vocab_size": 3}, **kwargs),
        sequence_feature(decoder={"vocab_size": 3}, **kwargs),
        text_feature(decoder={"vocab_size": 3}, **kwargs),
        set_feature(decoder={"vocab_size": 3}, **kwargs),
        vector_feature(**kwargs),
    ]
    backend = LocalTestBackend()
    config = {"input_features": input_features, "output_features": output_features, TRAINER: {"epochs": 2}}

    training_data_csv_path = generate_data(input_features, output_features, data_csv_path)
    df = read_csv_with_nan(training_data_csv_path, nan_percent=0.1)

    # run preprocessing
    ludwig_model = LudwigModel(config, backend=backend)
    ludwig_model.preprocess(dataset=df)
