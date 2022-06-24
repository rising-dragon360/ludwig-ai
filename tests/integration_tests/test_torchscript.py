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
import shutil
import tempfile
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest
import torch

from ludwig.api import LudwigModel
from ludwig.constants import COMBINER, LOGITS, NAME, PREDICTIONS, PROBABILITIES, TRAINER
from ludwig.data.preprocessing import preprocess_for_prediction
from ludwig.features.number_feature import numeric_transformation_registry
from ludwig.globals import TRAIN_SET_METADATA_FILE_NAME
from ludwig.models.inference import to_inference_module_input_from_dataframe
from ludwig.utils import output_feature_utils
from ludwig.utils.tokenizers import TORCHSCRIPT_COMPATIBLE_TOKENIZERS
from tests.integration_tests import utils
from tests.integration_tests.utils import (
    audio_feature,
    bag_feature,
    binary_feature,
    category_feature,
    date_feature,
    generate_data,
    h3_feature,
    image_feature,
    LocalTestBackend,
    number_feature,
    sequence_feature,
    set_feature,
    text_feature,
    timeseries_feature,
    vector_feature,
)


@pytest.mark.parametrize("should_load_model", [True, False])
def test_torchscript(csv_filename, should_load_model):
    #######
    # Setup
    #######
    with tempfile.TemporaryDirectory() as tmpdir:
        dir_path = tmpdir
        data_csv_path = os.path.join(tmpdir, csv_filename)
        image_dest_folder = os.path.join(tmpdir, "generated_images")
        audio_dest_folder = os.path.join(tmpdir, "generated_audio")

        # Single sequence input, single category output
        input_features = [
            binary_feature(),
            number_feature(),
            category_feature(vocab_size=3),
            sequence_feature(vocab_size=3),
            text_feature(vocab_size=3),
            vector_feature(),
            image_feature(image_dest_folder),
            audio_feature(audio_dest_folder),
            timeseries_feature(),
            date_feature(),
            date_feature(),
            h3_feature(),
            set_feature(vocab_size=3),
            bag_feature(vocab_size=3),
        ]

        output_features = [
            category_feature(vocab_size=3),
            binary_feature(),
            number_feature(),
            set_feature(vocab_size=3),
            vector_feature(),
            sequence_feature(vocab_size=3),
            text_feature(vocab_size=3),
        ]

        predictions_column_name = "{}_predictions".format(output_features[0]["name"])

        # Generate test data
        data_csv_path = generate_data(input_features, output_features, data_csv_path)

        #############
        # Train model
        #############
        backend = LocalTestBackend()
        config = {"input_features": input_features, "output_features": output_features, TRAINER: {"epochs": 2}}
        ludwig_model = LudwigModel(config, backend=backend)
        ludwig_model.train(
            dataset=data_csv_path,
            skip_save_training_description=True,
            skip_save_training_statistics=True,
            skip_save_model=True,
            skip_save_progress=True,
            skip_save_log=True,
            skip_save_processed_input=True,
        )

        ###################
        # save Ludwig model
        ###################
        ludwigmodel_path = os.path.join(dir_path, "ludwigmodel")
        shutil.rmtree(ludwigmodel_path, ignore_errors=True)
        ludwig_model.save(ludwigmodel_path)

        ###################
        # load Ludwig model
        ###################
        if should_load_model:
            ludwig_model = LudwigModel.load(ludwigmodel_path, backend=backend)

        ##############################
        # collect weight tensors names
        ##############################
        original_predictions_df, _ = ludwig_model.predict(dataset=data_csv_path)
        original_weights = deepcopy(list(ludwig_model.model.parameters()))
        original_weights = [t.cpu() for t in original_weights]

        # Move the model to CPU for tracing
        ludwig_model.model.cpu()

        #################
        # save torchscript
        #################
        torchscript_path = os.path.join(dir_path, "torchscript")
        shutil.rmtree(torchscript_path, ignore_errors=True)
        ludwig_model.model.save_torchscript(torchscript_path)

        ###################################################
        # load Ludwig model, obtain predictions and weights
        ###################################################
        ludwig_model = LudwigModel.load(ludwigmodel_path, backend=backend)
        loaded_prediction_df, _ = ludwig_model.predict(dataset=data_csv_path)
        loaded_weights = deepcopy(list(ludwig_model.model.parameters()))
        loaded_weights = [t.cpu() for t in loaded_weights]

        #####################################################
        # restore torchscript, obtain predictions and weights
        #####################################################
        training_set_metadata_json_fp = os.path.join(ludwigmodel_path, TRAIN_SET_METADATA_FILE_NAME)

        dataset, training_set_metadata = preprocess_for_prediction(
            ludwig_model.config,
            dataset=data_csv_path,
            training_set_metadata=training_set_metadata_json_fp,
            include_outputs=False,
            backend=backend,
        )

        restored_model = torch.jit.load(torchscript_path)

        # Check the outputs for one of the features for correctness
        # Here we choose the first output feature (categorical)
        of_name = list(ludwig_model.model.output_features.keys())[0]

        data_to_predict = {
            name: torch.from_numpy(dataset.dataset[feature.proc_column])
            for name, feature in ludwig_model.model.input_features.items()
        }

        # Get predictions from restored torchscript.
        logits = restored_model(data_to_predict)
        restored_predictions = torch.argmax(
            output_feature_utils.get_output_feature_tensor(logits, of_name, "logits"), -1
        )

        restored_predictions = [training_set_metadata[of_name]["idx2str"][idx] for idx in restored_predictions]

        restored_weights = deepcopy(list(restored_model.parameters()))
        restored_weights = [t.cpu() for t in restored_weights]

        ###############################################
        # Check if weights and predictions are the same
        ###############################################

        # Check to weight values match the original model.
        assert utils.is_all_close(original_weights, loaded_weights)
        assert utils.is_all_close(original_weights, restored_weights)

        # Check that predictions are identical to the original model.
        assert np.all(original_predictions_df[predictions_column_name] == loaded_prediction_df[predictions_column_name])

        assert np.all(original_predictions_df[predictions_column_name] == restored_predictions)


def test_torchscript_e2e_tabular(csv_filename, tmpdir):
    data_csv_path = os.path.join(tmpdir, csv_filename)
    # Configure features to be tested:
    bin_str_feature = binary_feature()
    transformed_number_features = [
        number_feature(preprocessing={"normalization": numeric_transformer})
        for numeric_transformer in numeric_transformation_registry.keys()
    ]
    input_features = [
        bin_str_feature,
        binary_feature(),
        *transformed_number_features,
        category_feature(vocab_size=3),
        bag_feature(vocab_size=3),
        set_feature(vocab_size=3),
        vector_feature(),
        # TODO: future support
        # date_feature(),
        # h3_feature(),
    ]
    output_features = [
        bin_str_feature,
        binary_feature(),
        number_feature(),
        category_feature(vocab_size=3),
        set_feature(vocab_size=3),
        vector_feature(),
        sequence_feature(vocab_size=3),
        text_feature(vocab_size=3),
    ]
    backend = LocalTestBackend()
    config = {"input_features": input_features, "output_features": output_features, TRAINER: {"epochs": 2}}

    # Generate training data
    training_data_csv_path = generate_data(input_features, output_features, data_csv_path)

    # Convert bool values to strings, e.g., {'Yes', 'No'}
    df = pd.read_csv(training_data_csv_path)
    false_value, true_value = "No", "Yes"
    df[bin_str_feature[NAME]] = df[bin_str_feature[NAME]].map(lambda x: true_value if x else false_value)
    df.to_csv(training_data_csv_path)

    validate_torchscript_outputs(tmpdir, config, backend, training_data_csv_path)


def test_torchscript_e2e_binary_only(csv_filename, tmpdir):
    data_csv_path = os.path.join(tmpdir, csv_filename)

    input_features = [
        binary_feature(),
    ]
    output_features = [
        binary_feature(),
    ]
    backend = LocalTestBackend()
    config = {"input_features": input_features, "output_features": output_features, TRAINER: {"epochs": 2}}

    # Generate training data
    training_data_csv_path = generate_data(input_features, output_features, data_csv_path)

    validate_torchscript_outputs(tmpdir, config, backend, training_data_csv_path)


def test_torchscript_e2e_tabnet_combiner(csv_filename, tmpdir):
    data_csv_path = os.path.join(tmpdir, csv_filename)
    # Configure features to be tested:
    input_features = [
        binary_feature(),
        number_feature(),
        category_feature(vocab_size=3),
        bag_feature(vocab_size=3),
        set_feature(vocab_size=3),
    ]
    output_features = [
        binary_feature(),
        number_feature(),
        category_feature(vocab_size=3),
    ]
    backend = LocalTestBackend()
    config = {
        "input_features": input_features,
        "output_features": output_features,
        COMBINER: {
            "type": "tabnet",
            "num_total_blocks": 2,
            "num_shared_blocks": 2,
        },
        TRAINER: {"epochs": 2},
    }

    # Generate training data
    training_data_csv_path = generate_data(input_features, output_features, data_csv_path)

    validate_torchscript_outputs(tmpdir, config, backend, training_data_csv_path)


def test_torchscript_e2e_audio(csv_filename, tmpdir):
    data_csv_path = os.path.join(tmpdir, csv_filename)
    audio_dest_folder = os.path.join(tmpdir, "generated_audio")

    input_features = [
        audio_feature(audio_dest_folder),
    ]
    output_features = [
        binary_feature(),
    ]
    backend = LocalTestBackend()
    config = {"input_features": input_features, "output_features": output_features, TRAINER: {"epochs": 2}}
    training_data_csv_path = generate_data(input_features, output_features, data_csv_path)

    # NOTE: audio preprocessing mismatches by very small margins ~O(1e-6) but causes flakiness in e2e test.
    # Increasing tolerance is a workaround to reduce flakiness for now.
    # TODO: remove this workaround when audio preprocessing is fixed.
    validate_torchscript_outputs(tmpdir, config, backend, training_data_csv_path, tolerance=1e-6)


def test_torchscript_e2e_image(tmpdir, csv_filename):
    data_csv_path = os.path.join(tmpdir, csv_filename)
    image_dest_folder = os.path.join(tmpdir, "generated_images")
    input_features = [
        image_feature(image_dest_folder),
    ]
    output_features = [
        binary_feature(),
    ]
    backend = LocalTestBackend()
    config = {"input_features": input_features, "output_features": output_features, TRAINER: {"epochs": 2}}
    training_data_csv_path = generate_data(input_features, output_features, data_csv_path)

    validate_torchscript_outputs(tmpdir, config, backend, training_data_csv_path)


def test_torchscript_e2e_text(tmpdir, csv_filename):
    data_csv_path = os.path.join(tmpdir, csv_filename)
    input_features = [
        text_feature(vocab_size=3, preprocessing={"tokenizer": tokenizer})
        for tokenizer in TORCHSCRIPT_COMPATIBLE_TOKENIZERS
    ]
    output_features = [
        text_feature(vocab_size=3),
    ]
    backend = LocalTestBackend()
    config = {"input_features": input_features, "output_features": output_features, TRAINER: {"epochs": 2}}
    training_data_csv_path = generate_data(input_features, output_features, data_csv_path)

    validate_torchscript_outputs(tmpdir, config, backend, training_data_csv_path)


def test_torchscript_e2e_sequence(tmpdir, csv_filename):
    data_csv_path = os.path.join(tmpdir, csv_filename)
    input_features = [
        sequence_feature(vocab_size=3, preprocessing={"tokenizer": "space"}),
    ]
    output_features = [
        sequence_feature(vocab_size=3),
    ]
    backend = LocalTestBackend()
    config = {"input_features": input_features, "output_features": output_features, TRAINER: {"epochs": 2}}
    training_data_csv_path = generate_data(input_features, output_features, data_csv_path)

    validate_torchscript_outputs(tmpdir, config, backend, training_data_csv_path)


def test_torchscript_e2e_timeseries(tmpdir, csv_filename):
    data_csv_path = os.path.join(tmpdir, csv_filename)
    input_features = [
        timeseries_feature(),
    ]
    output_features = [
        binary_feature(),
    ]
    backend = LocalTestBackend()
    config = {"input_features": input_features, "output_features": output_features, TRAINER: {"epochs": 2}}
    training_data_csv_path = generate_data(input_features, output_features, data_csv_path)

    validate_torchscript_outputs(tmpdir, config, backend, training_data_csv_path)


def test_torchscript_e2e_h3(tmpdir, csv_filename):
    data_csv_path = os.path.join(tmpdir, csv_filename)
    input_features = [
        h3_feature(),
    ]
    output_features = [
        binary_feature(),
    ]
    backend = LocalTestBackend()
    config = {"input_features": input_features, "output_features": output_features, TRAINER: {"epochs": 2}}
    training_data_csv_path = generate_data(input_features, output_features, data_csv_path)

    validate_torchscript_outputs(tmpdir, config, backend, training_data_csv_path)


def test_torchscript_e2e_date(tmpdir, csv_filename):
    data_csv_path = os.path.join(tmpdir, csv_filename)
    input_features = [
        date_feature(),
    ]
    output_features = [
        binary_feature(),
    ]
    backend = LocalTestBackend()
    config = {"input_features": input_features, "output_features": output_features, TRAINER: {"epochs": 2}}
    training_data_csv_path = generate_data(input_features, output_features, data_csv_path)

    validate_torchscript_outputs(tmpdir, config, backend, training_data_csv_path)


@pytest.mark.parametrize(
    "feature",
    [
        number_feature(),
        binary_feature(),
        category_feature(vocab_size=3),
        bag_feature(vocab_size=3),
        set_feature(vocab_size=3),
        text_feature(vocab_size=3),
        sequence_feature(vocab_size=3),
        timeseries_feature(),
        h3_feature(),
        # TODO: future support
        # audio_feature(),  # default BACKFILL strategy is unintuitive at inference time
        # image_feature(),  # default BACKFILL strategy is unintuitive at inference time
        # vector_feature(), # does not have a missing_value_strategy
        # date_feature(),   # default fill with datetime.now() strategy is not scriptable
    ],
)
def test_torchscript_preproc_with_nans(tmpdir, csv_filename, feature):
    data_csv_path = os.path.join(tmpdir, csv_filename)

    input_features = [feature]

    output_features = [
        binary_feature(),
    ]
    backend = LocalTestBackend()
    config = {"input_features": input_features, "output_features": output_features, TRAINER: {"epochs": 2}}
    training_data_csv_path = generate_data(input_features, output_features, data_csv_path, nan_percent=0.2)

    # Initialize Ludwig model
    ludwig_model = LudwigModel(config, backend=backend)
    ludwig_model.train(
        dataset=training_data_csv_path,
        skip_save_training_description=True,
        skip_save_training_statistics=True,
        skip_save_model=True,
        skip_save_progress=True,
        skip_save_log=True,
        skip_save_processed_input=True,
    )
    preproc_inputs_expected, _ = preprocess_for_prediction(
        ludwig_model.config,
        training_data_csv_path,
        ludwig_model.training_set_metadata,
        backend=backend,
        include_outputs=False,
    )

    # Create graph inference model (Torchscript) from trained Ludwig model.
    script_module = ludwig_model.to_torchscript()
    # Ensure torchscript saving/loading does not affect final predictions.
    script_module_path = os.path.join(tmpdir, "inference_module.pt")
    torch.jit.save(script_module, script_module_path)
    script_module = torch.jit.load(script_module_path)

    df = pd.read_csv(training_data_csv_path)
    inputs = to_inference_module_input_from_dataframe(df, config, load_paths=True)
    preproc_inputs = script_module.preprocess(inputs)

    # Check that preproc_inputs is the same as preproc_inputs_expected.
    for feature_name_expected, feature_values_expected in preproc_inputs_expected.dataset.items():
        feature_name = feature_name_expected[: feature_name_expected.rfind("_")]  # remove proc suffix
        if feature_name not in preproc_inputs.keys():
            continue

        feature_values = preproc_inputs[feature_name]
        assert utils.is_all_close(feature_values, feature_values_expected), f"feature: {feature_name}"


def validate_torchscript_outputs(tmpdir, config, backend, training_data_csv_path, tolerance=1e-8):
    # Train Ludwig (Pythonic) model:
    ludwig_model = LudwigModel(config, backend=backend)
    ludwig_model.train(
        dataset=training_data_csv_path,
        skip_save_training_description=True,
        skip_save_training_statistics=True,
        skip_save_model=True,
        skip_save_progress=True,
        skip_save_log=True,
        skip_save_processed_input=True,
    )

    # Obtain predictions from Python model
    preds_dict, _ = ludwig_model.predict(dataset=training_data_csv_path, return_type=dict)

    # Create graph inference model (Torchscript) from trained Ludwig model.
    script_module = ludwig_model.to_torchscript()

    # Ensure torchscript saving/loading does not affect final predictions.
    script_module_path = os.path.join(tmpdir, "inference_module.pt")
    torch.jit.save(script_module, script_module_path)
    script_module = torch.jit.load(script_module_path)

    df = pd.read_csv(training_data_csv_path)
    inputs = to_inference_module_input_from_dataframe(df, config, load_paths=True)
    outputs = script_module(inputs)

    # TODO: these are the only outputs we provide from Torchscript for now
    ts_outputs = {PREDICTIONS, PROBABILITIES, LOGITS}

    # Compare results from Python trained model against Torchscript
    for feature_name, feature_outputs_expected in preds_dict.items():
        assert feature_name in outputs

        feature_outputs = outputs[feature_name]
        for output_name, output_values_expected in feature_outputs_expected.items():
            if output_name not in ts_outputs:
                continue

            assert output_name in feature_outputs
            output_values = feature_outputs[output_name]
            assert utils.is_all_close(
                output_values, output_values_expected
            ), f"feature: {feature_name}, output: {output_name}"
