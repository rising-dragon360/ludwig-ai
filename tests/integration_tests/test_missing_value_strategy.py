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
import tempfile

import numpy as np
import pandas as pd

from ludwig.api import LudwigModel
from ludwig.constants import DROP_ROW, PREPROCESSING, TRAINER
from tests.integration_tests.utils import (
    binary_feature,
    category_feature,
    generate_data,
    LocalTestBackend,
    number_feature,
    sequence_feature,
    set_feature,
    text_feature,
    vector_feature,
)


def test_missing_value_prediction(csv_filename):
    random.seed(1)
    np.random.seed(1)
    with tempfile.TemporaryDirectory() as tmpdir:
        input_features = [
            category_feature(
                vocab_size=2, reduce_input="sum", preprocessing=dict(missing_value_strategy="fill_with_mode")
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


def test_missing_values_drop_rows(csv_filename, tmpdir):
    data_csv_path = os.path.join(tmpdir, csv_filename)

    kwargs = {PREPROCESSING: {"missing_value_strategy": DROP_ROW}}
    input_features = [
        number_feature(),
        binary_feature(),
        category_feature(vocab_size=3),
    ]
    output_features = [
        binary_feature(**kwargs),
        number_feature(**kwargs),
        category_feature(vocab_size=3, **kwargs),
        sequence_feature(vocab_size=3, **kwargs),
        text_feature(vocab_size=3, **kwargs),
        set_feature(vocab_size=3, **kwargs),
        vector_feature(),
    ]
    backend = LocalTestBackend()
    config = {"input_features": input_features, "output_features": output_features, TRAINER: {"epochs": 2}}

    training_data_csv_path = generate_data(input_features, output_features, data_csv_path)
    df = pd.read_csv(training_data_csv_path)

    # set 10% of values to NaN
    nan_percent = 0.1
    ix = [(row, col) for row in range(df.shape[0]) for col in range(df.shape[1])]
    for row, col in random.sample(ix, int(round(nan_percent * len(ix)))):
        df.iat[row, col] = np.nan

    # run preprocessing
    ludwig_model = LudwigModel(config, backend=backend)
    ludwig_model.preprocess(dataset=df)
