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
import os
import shutil
import uuid
from collections import namedtuple

import pandas as pd
import pytest
import torchvision
import yaml

from ludwig.api import LudwigModel
from ludwig.backend import LOCAL_BACKEND
from ludwig.constants import H3, TRAINER
from ludwig.data.concatenate_datasets import concatenate_df
from ludwig.data.preprocessing import preprocess_for_training
from ludwig.encoders.registry import get_encoder_classes
from ludwig.experiment import experiment_cli
from ludwig.predict import predict_cli
from ludwig.utils.data_utils import read_csv
from ludwig.utils.defaults import default_random_seed
from tests.integration_tests.utils import (
    audio_feature,
    bag_feature,
    binary_feature,
    category_feature,
    create_data_set_to_use,
    date_feature,
    ENCODERS,
    generate_data,
    generate_output_features_with_dependencies,
    generate_output_features_with_dependencies_complex,
    h3_feature,
    HF_ENCODERS,
    HF_ENCODERS_SHORT,
    image_feature,
    LocalTestBackend,
    number_feature,
    run_experiment,
    sequence_feature,
    set_feature,
    slow,
    text_feature,
    timeseries_feature,
    vector_feature,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.getLogger("ludwig").setLevel(logging.INFO)


@pytest.mark.parametrize("encoder", ENCODERS)
def test_experiment_text_feature_non_HF(encoder, csv_filename):
    input_features = [
        text_feature(vocab_size=30, min_len=1, encoder=encoder, preprocessing={"word_tokenizer": "space"})
    ]
    output_features = [category_feature(vocab_size=2)]
    # Generate test data
    rel_path = generate_data(input_features, output_features, csv_filename)
    run_experiment(input_features, output_features, dataset=rel_path)


def run_experiment_with_encoder(encoder, csv_filename):
    # Run in a subprocess to clear TF and prevent OOM
    # This also allows us to use GPU resources
    input_features = [
        text_feature(
            vocab_size=30,
            min_len=1,
            encoder=encoder,
        )
    ]
    output_features = [category_feature(vocab_size=2)]
    # Generate test data
    rel_path = generate_data(input_features, output_features, csv_filename)
    run_experiment(input_features, output_features, dataset=rel_path)


@pytest.mark.distributed
@pytest.mark.parametrize("encoder", HF_ENCODERS_SHORT)
def test_experiment_text_feature_HF(encoder, csv_filename):
    run_experiment_with_encoder(encoder, csv_filename)


@slow
@pytest.mark.parametrize("encoder", HF_ENCODERS)
def test_experiment_text_feature_HF_full(encoder, csv_filename):
    run_experiment_with_encoder(encoder, csv_filename)


@pytest.mark.parametrize("encoder", ENCODERS)
def test_experiment_seq_seq_generator(csv_filename, encoder):
    input_features = [text_feature(reduce_output=None, encoder=encoder)]
    output_features = [text_feature(decoder="generator")]
    rel_path = generate_data(input_features, output_features, csv_filename)

    run_experiment(input_features, output_features, dataset=rel_path)


@pytest.mark.parametrize("encoder", ["embed", "rnn", "parallel_cnn", "stacked_parallel_cnn", "transformer"])
def test_experiment_seq_seq_tagger(csv_filename, encoder):
    input_features = [text_feature(reduce_output=None, encoder=encoder)]
    output_features = [text_feature(decoder="tagger")]
    rel_path = generate_data(input_features, output_features, csv_filename)

    run_experiment(input_features, output_features, dataset=rel_path)


@pytest.mark.parametrize("encoder", ["cnnrnn", "stacked_cnn"])
def test_experiment_seq_seq_tagger_fails_for_non_length_preserving_encoders(csv_filename, encoder):
    input_features = [text_feature(reduce_output=None, encoder=encoder)]
    output_features = [text_feature(decoder="tagger")]
    rel_path = generate_data(input_features, output_features, csv_filename)

    with pytest.raises(ValueError):
        run_experiment(input_features, output_features, dataset=rel_path)


def test_experiment_seq_seq_model_def_file(csv_filename, yaml_filename):
    # seq-to-seq test to use config file instead of dictionary
    input_features = [text_feature(reduce_output=None, encoder="embed")]
    output_features = [text_feature(reduce_input=None, vocab_size=3, decoder="tagger")]

    # Save the config to a yaml file
    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat", "output_size": 14},
        TRAINER: {"epochs": 2},
    }
    with open(yaml_filename, "w") as yaml_out:
        yaml.safe_dump(config, yaml_out)

    rel_path = generate_data(input_features, output_features, csv_filename)
    run_experiment(None, None, dataset=rel_path, config=yaml_filename)


def test_experiment_seq_seq_train_test_valid(tmpdir):
    # seq-to-seq test to use train, test, validation files
    input_features = [text_feature(reduce_output=None, encoder="rnn")]
    output_features = [text_feature(reduce_input=None, vocab_size=3, decoder="tagger")]

    train_csv = generate_data(input_features, output_features, os.path.join(tmpdir, "train.csv"))
    test_csv = generate_data(input_features, output_features, os.path.join(tmpdir, "test.csv"), 20)
    valdation_csv = generate_data(input_features, output_features, os.path.join(tmpdir, "val.csv"), 20)

    run_experiment(
        input_features, output_features, training_set=train_csv, test_set=test_csv, validation_set=valdation_csv
    )

    # Save intermediate output
    run_experiment(
        input_features, output_features, training_set=train_csv, test_set=test_csv, validation_set=valdation_csv
    )


@pytest.mark.parametrize("encoder", ENCODERS)
def test_experiment_multi_input_intent_classification(csv_filename, encoder):
    # Multiple inputs, Single category output
    input_features = [text_feature(vocab_size=10, min_len=1, representation="sparse"), category_feature(vocab_size=10)]
    output_features = [category_feature(vocab_size=2, reduce_input="sum")]

    # Generate test data
    rel_path = generate_data(input_features, output_features, csv_filename)

    input_features[0]["encoder"] = encoder
    run_experiment(input_features, output_features, dataset=rel_path)


def test_experiment_with_torch_module_dict_feature_name(csv_filename):
    input_features = [{"type": "category", "name": "type"}]
    output_features = [{"type": "category", "name": "to"}]
    rel_path = generate_data(input_features, output_features, csv_filename)

    run_experiment(input_features, output_features, dataset=rel_path)


def test_experiment_multiclass_with_class_weights(csv_filename):
    # Multiple inputs, Single category output
    input_features = [category_feature(vocab_size=10)]
    output_features = [category_feature(vocab_size=3, loss={"class_weights": [0, 1, 2, 3]})]

    # Generate test data
    rel_path = generate_data(input_features, output_features, csv_filename)
    run_experiment(input_features, output_features, dataset=rel_path)


def test_experiment_multilabel_with_class_weights(csv_filename):
    # Multiple inputs, Single category output
    input_features = [category_feature(vocab_size=10)]
    output_features = [set_feature(vocab_size=3, loss={"class_weights": [0, 1, 2, 3]})]

    # Generate test data
    rel_path = generate_data(input_features, output_features, csv_filename)
    run_experiment(input_features, output_features, dataset=rel_path)


@pytest.mark.parametrize(
    "output_features",
    [
        # baseline test case
        [
            category_feature(vocab_size=2, reduce_input="sum"),
            sequence_feature(vocab_size=10, max_len=5),
            number_feature(),
        ],
        # use generator as decoder
        [
            category_feature(vocab_size=2, reduce_input="sum"),
            sequence_feature(vocab_size=10, max_len=5, decoder="generator"),
            number_feature(),
        ],
        # Generator decoder and reduce_input = None
        [
            category_feature(vocab_size=2, reduce_input="sum"),
            sequence_feature(max_len=5, decoder="generator", reduce_input=None),
            number_feature(normalization="minmax"),
        ],
        # output features with dependencies single dependency
        generate_output_features_with_dependencies("number_feature", ["category_feature"]),
        # output features with dependencies multiple dependencies
        generate_output_features_with_dependencies("number_feature", ["category_feature", "sequence_feature"]),
        # output features with dependencies multiple dependencies
        generate_output_features_with_dependencies("sequence_feature", ["category_feature", "number_feature"]),
        # output features with dependencies
        generate_output_features_with_dependencies("category_feature", ["sequence_feature"]),
        generate_output_features_with_dependencies_complex(),
    ],
)
def test_experiment_multiple_seq_seq(csv_filename, output_features):
    input_features = [
        text_feature(vocab_size=100, min_len=1, encoder="stacked_cnn"),
        number_feature(normalization="zscore"),
        category_feature(vocab_size=10, embedding_size=5),
        set_feature(),
        sequence_feature(vocab_size=10, max_len=10, encoder="embed"),
    ]
    output_features = output_features

    rel_path = generate_data(input_features, output_features, csv_filename)
    run_experiment(input_features, output_features, dataset=rel_path)


@pytest.mark.parametrize("skip_save_processed_input", [True, False])
@pytest.mark.parametrize("in_memory", [True, False])
@pytest.mark.parametrize("image_source", ["file", "tensor"])
@pytest.mark.parametrize("num_channels", [1, 3])
def test_basic_image_feature(num_channels, image_source, in_memory, skip_save_processed_input, tmpdir):
    # Image Inputs
    image_dest_folder = os.path.join(tmpdir, "generated_images")

    input_features = [
        image_feature(
            folder=image_dest_folder,
            encoder="stacked_cnn",
            preprocessing={
                "in_memory": in_memory,
                "height": 12,
                "width": 12,
                "num_channels": num_channels,
                "num_processes": 5,
            },
            output_size=16,
            num_filters=8,
        )
    ]
    output_features = [category_feature(vocab_size=2, reduce_input="sum")]

    rel_path = generate_data(input_features, output_features, os.path.join(tmpdir, "dataset.csv"))

    if image_source == "file":
        # use images from file
        run_experiment(
            input_features, output_features, dataset=rel_path, skip_save_processed_input=skip_save_processed_input
        )
    else:
        # import image from file and store in dataframe as tensors.
        df = pd.read_csv(rel_path)
        image_feature_name = input_features[0]["name"]
        df[image_feature_name] = df[image_feature_name].apply(lambda x: torchvision.io.read_image(x))

        run_experiment(input_features, output_features, dataset=df, skip_save_processed_input=skip_save_processed_input)


def test_experiment_infer_image_metadata(tmpdir):
    # Image Inputs
    image_dest_folder = os.path.join(tmpdir, "generated_images")

    # Resnet encoder
    input_features = [
        image_feature(folder=image_dest_folder, encoder="stacked_cnn", output_size=16, num_filters=8),
        text_feature(encoder="embed", min_len=1),
        number_feature(normalization="zscore"),
    ]
    output_features = [category_feature(vocab_size=2, reduce_input="sum"), number_feature()]

    rel_path = generate_data(input_features, output_features, os.path.join(tmpdir, "dataset.csv"))

    # remove image preprocessing section to force inferring image meta data
    input_features[0].pop("preprocessing")

    run_experiment(input_features, output_features, dataset=rel_path)


ImageParams = namedtuple("ImageTestParams", "image_encoder in_memory_flag skip_save_processed_input")


@pytest.mark.parametrize(
    "image_params",
    [
        ImageParams("resnet", True, True),
        ImageParams("stacked_cnn", True, True),
        ImageParams("stacked_cnn", False, False),
    ],
)
def test_experiment_image_inputs(image_params: ImageParams, tmpdir):
    # Image Inputs
    image_dest_folder = os.path.join(tmpdir, "generated_images")

    # Resnet encoder
    input_features = [
        image_feature(
            folder=image_dest_folder,
            encoder="resnet",
            preprocessing={"in_memory": True, "height": 12, "width": 12, "num_channels": 3, "num_processes": 5},
            output_size=16,
            num_filters=8,
        ),
        text_feature(encoder="embed", min_len=1),
        number_feature(normalization="zscore"),
    ]
    output_features = [category_feature(vocab_size=2, reduce_input="sum"), number_feature()]

    input_features[0]["encoder"] = image_params.image_encoder
    input_features[0]["preprocessing"]["in_memory"] = image_params.in_memory_flag
    rel_path = generate_data(input_features, output_features, os.path.join(tmpdir, "dataset.csv"))

    run_experiment(
        input_features,
        output_features,
        dataset=rel_path,
        skip_save_processed_input=image_params.skip_save_processed_input,
    )


# Primary focus of this test is to determine if exceptions are raised for different data set formats and in_memory
# setting.
@pytest.mark.parametrize("test_in_memory", [True, False])
@pytest.mark.parametrize("test_format", ["csv", "df", "hdf5"])
@pytest.mark.parametrize("train_in_memory", [True, False])
@pytest.mark.parametrize("train_format", ["csv", "df", "hdf5"])
def test_experiment_image_dataset(train_format, train_in_memory, test_format, test_in_memory, tmpdir):
    # Image Inputs
    image_dest_folder = os.path.join(tmpdir, "generated_images")

    input_features = [
        image_feature(
            folder=image_dest_folder,
            encoder="stacked_cnn",
            preprocessing={"in_memory": True, "height": 12, "width": 12, "num_channels": 3, "num_processes": 5},
            output_size=16,
            num_filters=8,
        ),
    ]
    output_features = [
        category_feature(vocab_size=2, reduce_input="sum"),
    ]

    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat", "output_size": 14},
        "preprocessing": {},
        TRAINER: {"epochs": 2},
    }

    # create temporary name for train and test data sets
    train_csv_filename = os.path.join(tmpdir, "train_" + uuid.uuid4().hex[:10].upper() + ".csv")
    test_csv_filename = os.path.join(tmpdir, "test_" + uuid.uuid4().hex[:10].upper() + ".csv")

    # setup training data format to test
    train_data = generate_data(input_features, output_features, train_csv_filename)
    config["input_features"][0]["preprocessing"]["in_memory"] = train_in_memory
    training_set_metadata = None

    backend = LocalTestBackend()
    if train_format == "hdf5":
        # hdf5 format
        train_set, _, _, training_set_metadata = preprocess_for_training(
            config,
            dataset=train_data,
            backend=backend,
        )
        train_dataset_to_use = train_set.data_hdf5_fp
    else:
        train_dataset_to_use = create_data_set_to_use(train_format, train_data)

    # define Ludwig model
    model = LudwigModel(
        config=config,
        backend=backend,
    )
    model.train(dataset=train_dataset_to_use, training_set_metadata=training_set_metadata)

    model.config["input_features"][0]["preprocessing"]["in_memory"] = test_in_memory

    # setup test data format to test
    test_data = generate_data(input_features, output_features, test_csv_filename)

    if test_format == "hdf5":
        # hdf5 format
        # create hdf5 data set
        _, test_set, _, training_set_metadata_for_test = preprocess_for_training(
            model.config,
            dataset=test_data,
            backend=backend,
        )
        test_dataset_to_use = test_set.data_hdf5_fp
    else:
        test_dataset_to_use = create_data_set_to_use(test_format, test_data)

    # run functions with the specified data format
    model.evaluate(dataset=test_dataset_to_use)
    model.predict(dataset=test_dataset_to_use)


DATA_FORMATS_TO_TEST = [
    "csv",
    "df",
    "dict",
    "excel",
    "excel_xls",
    "feather",
    "fwf",
    "hdf5",
    "html",
    "json",
    "jsonl",
    "parquet",
    "pickle",
    "stata",
    "tsv",
]


@pytest.mark.parametrize("data_format", DATA_FORMATS_TO_TEST)
def test_experiment_dataset_formats(data_format, csv_filename):
    # primary focus of this test is to determine if exceptions are
    # raised for different data set formats and in_memory setting

    input_features = [number_feature(), category_feature()]
    output_features = [category_feature(), number_feature()]

    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat", "output_size": 14},
        "preprocessing": {},
        TRAINER: {"epochs": 2},
    }

    # setup training data format to test
    raw_data = generate_data(input_features, output_features, csv_filename)

    training_set_metadata = None

    if data_format == "hdf5":
        # hdf5 format
        training_set, _, _, training_set_metadata = preprocess_for_training(config, dataset=raw_data)
        dataset_to_use = training_set.data_hdf5_fp
    else:
        dataset_to_use = create_data_set_to_use(data_format, raw_data)

    # define Ludwig model
    model = LudwigModel(config=config)
    model.train(dataset=dataset_to_use, training_set_metadata=training_set_metadata, random_seed=default_random_seed)

    # # run functions with the specified data format
    model.evaluate(dataset=dataset_to_use)
    model.predict(dataset=dataset_to_use)


def test_experiment_audio_inputs(tmpdir):
    # Audio Inputs
    audio_dest_folder = os.path.join(tmpdir, "generated_audio")

    input_features = [audio_feature(folder=audio_dest_folder)]
    output_features = [binary_feature()]

    rel_path = generate_data(input_features, output_features, os.path.join(tmpdir, "dataset.csv"))

    run_experiment(input_features, output_features, dataset=rel_path)


def test_experiment_tied_weights(csv_filename):
    # Single sequence input, single category output
    input_features = [
        text_feature(name="text_feature1", min_len=1, encoder="cnnrnn", reduce_output="sum"),
        text_feature(
            name="text_feature2", min_len=1, encoder="cnnrnn", reduce_output="sum", tied_weights="text_feature1"
        ),
    ]
    output_features = [category_feature(vocab_size=2, reduce_input="sum")]

    # Generate test data
    rel_path = generate_data(input_features, output_features, csv_filename)
    for encoder in ENCODERS:
        input_features[0]["encoder"] = encoder
        input_features[1]["encoder"] = encoder
        run_experiment(input_features, output_features, dataset=rel_path)


@pytest.mark.parametrize("enc_cell_type", ["lstm", "rnn", "gru"])
@pytest.mark.parametrize("attention", [False, True])
def test_sequence_tagger(enc_cell_type, attention, csv_filename):
    # Define input and output features
    input_features = [sequence_feature(max_len=10, encoder="rnn", cell_type=enc_cell_type, reduce_output=None)]
    output_features = [sequence_feature(max_len=10, decoder="tagger", attention=attention, reduce_input=None)]

    # Generate test data
    rel_path = generate_data(input_features, output_features, csv_filename)

    # run the experiment
    run_experiment(input_features, output_features, dataset=rel_path)


def test_sequence_tagger_text(csv_filename):
    # Define input and output features
    input_features = [text_feature(max_len=10, encoder="rnn", reduce_output=None)]
    output_features = [sequence_feature(max_len=10, decoder="tagger", reduce_input=None)]

    # Generate test data
    rel_path = generate_data(input_features, output_features, csv_filename)

    # run the experiment
    run_experiment(input_features, output_features, dataset=rel_path)


def test_experiment_sequence_combiner_with_reduction_fails(csv_filename):
    config = {
        "input_features": [
            sequence_feature(
                name="seq1",
                min_len=5,
                max_len=5,
                encoder="embed",
                cell_type="lstm",
                reduce_output="sum",
            ),
            sequence_feature(
                name="seq2",
                min_len=5,
                max_len=5,
                encoder="embed",
                cell_type="lstm",
                reduce_output="sum",
            ),
            category_feature(vocab_size=5),
        ],
        "output_features": [category_feature(reduce_input="sum", vocab_size=5)],
        TRAINER: {"epochs": 2},
        "combiner": {
            "type": "sequence",
            "encoder": "rnn",
            "main_sequence_feature": "seq1",
            "reduce_output": None,
        },
    }

    # Generate test data
    rel_path = generate_data(config["input_features"], config["output_features"], csv_filename)

    # Encoding sequence features with 'embed' should fail with SequenceConcatCombiner, since at least one sequence
    # feature should be rank 3.
    with pytest.raises(ValueError):
        run_experiment(config=config, dataset=rel_path)


@pytest.mark.parametrize("sequence_encoder", ENCODERS[1:])
def test_experiment_sequence_combiner(sequence_encoder, csv_filename):
    config = {
        "input_features": [
            sequence_feature(
                name="seq1", min_len=5, max_len=5, encoder=sequence_encoder, cell_type="lstm", reduce_output=None
            ),
            sequence_feature(
                name="seq2", min_len=5, max_len=5, encoder=sequence_encoder, cell_type="lstm", reduce_output=None
            ),
            category_feature(vocab_size=5),
        ],
        "output_features": [category_feature(reduce_input="sum", vocab_size=5)],
        TRAINER: {"epochs": 2},
        "combiner": {
            "type": "sequence",
            "encoder": "rnn",
            "main_sequence_feature": "seq1",
            "reduce_output": None,
        },
    }

    # Generate test data
    rel_path = generate_data(config["input_features"], config["output_features"], csv_filename)

    run_experiment(config=config, dataset=rel_path)


def test_experiment_model_resume(tmpdir):
    # Single sequence input, single category output
    # Tests saving a model file, loading it to rerun training and predict
    input_features = [sequence_feature(encoder="rnn", reduce_output="sum")]
    output_features = [category_feature(vocab_size=2, reduce_input="sum")]
    # Generate test data
    rel_path = generate_data(input_features, output_features, os.path.join(tmpdir, "dataset.csv"))

    config = {
        "input_features": input_features,
        "output_features": output_features,
        "combiner": {"type": "concat", "output_size": 14},
        TRAINER: {"epochs": 2},
    }

    _, _, _, _, output_dir = experiment_cli(config, dataset=rel_path, output_directory=tmpdir)

    experiment_cli(config, dataset=rel_path, model_resume_path=output_dir)

    predict_cli(os.path.join(output_dir, "model"), dataset=rel_path)
    shutil.rmtree(output_dir, ignore_errors=True)


def test_experiment_various_feature_types(csv_filename):
    input_features = [binary_feature(), bag_feature()]
    output_features = [set_feature(max_len=3, vocab_size=5)]

    # Generate test data
    rel_path = generate_data(input_features, output_features, csv_filename)
    run_experiment(input_features, output_features, dataset=rel_path)


def test_experiment_timeseries(csv_filename):
    input_features = [timeseries_feature()]
    output_features = [binary_feature()]

    # Generate test data
    rel_path = generate_data(input_features, output_features, csv_filename)
    input_features[0]["encoder"] = "transformer"
    run_experiment(input_features, output_features, dataset=rel_path)


def test_visual_question_answering(tmpdir):
    image_dest_folder = os.path.join(tmpdir, "generated_images")
    input_features = [
        image_feature(
            folder=image_dest_folder,
            encoder="resnet",
            preprocessing={"in_memory": True, "height": 8, "width": 8, "num_channels": 3, "num_processes": 5},
            output_size=8,
            num_filters=8,
        ),
        text_feature(encoder="embed", min_len=1, level="word"),
    ]
    output_features = [sequence_feature(decoder="generator", cell_type="lstm")]
    rel_path = generate_data(input_features, output_features, os.path.join(tmpdir, "dataset.csv"))

    run_experiment(input_features, output_features, dataset=rel_path)


def test_image_resizing_num_channel_handling(tmpdir):
    """This test creates two image datasets with 3 channels and 1 channel. The combination of this data is used to
    train a model. This checks the cases where the user may or may not specify a number of channels in the config.

    :param csv_filename:
    :return:
    """
    # Image Inputs
    image_dest_folder = os.path.join(tmpdir, "generated_images")

    # Resnet encoder
    input_features = [
        image_feature(
            folder=image_dest_folder,
            encoder="resnet",
            preprocessing={"in_memory": True, "height": 8, "width": 8, "num_channels": 3, "num_processes": 5},
            output_size=8,
            num_filters=8,
        ),
        text_feature(encoder="embed", min_len=1),
        number_feature(normalization="minmax"),
    ]
    output_features = [binary_feature(), number_feature()]
    rel_path = generate_data(input_features, output_features, os.path.join(tmpdir, "dataset1.csv"), num_examples=50)

    df1 = read_csv(rel_path)

    input_features[0]["preprocessing"]["num_channels"] = 1
    rel_path = generate_data(input_features, output_features, os.path.join(tmpdir, "dataset2.csv"), num_examples=50)
    df2 = read_csv(rel_path)

    df = concatenate_df(df1, df2, None, LOCAL_BACKEND)
    df.to_csv(rel_path, index=False)

    # Here the user specifies number of channels. Exception shouldn't be thrown
    run_experiment(input_features, output_features, dataset=rel_path)

    del input_features[0]["preprocessing"]["num_channels"]

    # User doesn't specify num channels, but num channels is inferred. Exception shouldn't be thrown
    run_experiment(input_features, output_features, dataset=rel_path)


@pytest.mark.parametrize("encoder", ["wave", "embed"])
def test_experiment_date(encoder, csv_filename):
    input_features = [date_feature()]
    output_features = [category_feature(vocab_size=2)]

    # Generate test data
    rel_path = generate_data(input_features, output_features, csv_filename)

    input_features[0]["encoder"] = encoder
    run_experiment(input_features, output_features, dataset=rel_path)


@pytest.mark.parametrize("encoder", get_encoder_classes(H3).keys())
def test_experiment_h3(encoder, csv_filename):
    input_features = [h3_feature()]
    output_features = [binary_feature()]

    # Generate test data
    rel_path = generate_data(input_features, output_features, csv_filename)

    input_features[0]["encoder"] = encoder
    run_experiment(input_features, output_features, dataset=rel_path)


def test_experiment_vector_feature_1(csv_filename):
    input_features = [vector_feature()]
    output_features = [binary_feature()]
    # Generate test data
    rel_path = generate_data(input_features, output_features, csv_filename)

    run_experiment(input_features, output_features, dataset=rel_path)


def test_experiment_vector_feature_2(csv_filename):
    input_features = [vector_feature()]
    output_features = [vector_feature()]
    # Generate test data
    rel_path = generate_data(input_features, output_features, csv_filename)

    run_experiment(input_features, output_features, dataset=rel_path)
