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
import shutil
import tempfile

import numpy as np
import torch

from ludwig.api import LudwigModel
from ludwig.collect import collect_activations, collect_weights, print_model_summary
from ludwig.constants import TRAINER
from tests.integration_tests.utils import category_feature, ENCODERS, generate_data, sequence_feature, spawn

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


def _prepare_data(csv_filename):
    # Single sequence input, single category output
    input_features = [sequence_feature(reduce_output="sum")]
    output_features = [category_feature(vocab_size=2, reduce_input="sum")]

    input_features[0]["encoder"] = ENCODERS[0]

    # Generate test data
    data_csv = generate_data(input_features, output_features, csv_filename)
    return input_features, output_features, data_csv


def _train(input_features, output_features, data_csv, **kwargs):
    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat", "output_size": 14},
        TRAINER: {"epochs": 2},
    }

    model = LudwigModel(config)
    _, _, output_dir = model.train(dataset=data_csv, **kwargs)
    return model, output_dir


@spawn
def _get_layers(model_path):
    model = LudwigModel.load(model_path)
    return [name for name, _ in model.model.named_children()]


@spawn
def _collect_activations(model_path, layers, csv_filename, output_directory):
    return collect_activations(model_path, layers, dataset=csv_filename, output_directory=output_directory)


def test_collect_weights(csv_filename):
    output_dir = None
    try:
        model, output_dir = _train(*_prepare_data(csv_filename))
        model_path = os.path.join(output_dir, "model")

        # 1 for the encoder (embeddings).
        # 2 for the decoder classifier (w and b).
        weights = [w for _, w in model.model.collect_weights()]
        assert len(weights) == 3

        # Load model from disk to ensure correct weight names
        model_loaded = LudwigModel.load(model_path)
        tensor_names = [name for name, w in model_loaded.collect_weights()]
        assert len(tensor_names) == 3

        with tempfile.TemporaryDirectory() as output_directory:
            filenames = collect_weights(model_path, tensor_names, output_directory)
            assert len(filenames) == 3

            for weight, filename in zip(weights, filenames):
                saved_weight = np.load(filename)
                assert torch.allclose(weight, torch.from_numpy(saved_weight).to(DEVICE), rtol=1.0e-4), filename
    finally:
        if output_dir:
            shutil.rmtree(output_dir, ignore_errors=True)


def test_collect_activations(csv_filename):
    output_dir = None
    try:
        model, output_dir = _train(*_prepare_data(csv_filename))
        model_path = os.path.join(output_dir, "model")

        with tempfile.TemporaryDirectory() as output_directory:
            # [last_hidden, logits, projection_input]
            filenames = _collect_activations(
                model_path, [name for name, _ in model.model.named_children()], csv_filename, output_directory
            )
            assert len(filenames) == 3
    finally:
        if output_dir:
            shutil.rmtree(output_dir, ignore_errors=True)


def test_print_model_summary(csv_filename):
    output_dir = None
    model, output_dir = _train(*_prepare_data(csv_filename))
    model_path = os.path.join(output_dir, "model")
    print_model_summary(model_path)
