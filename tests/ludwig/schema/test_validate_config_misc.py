import pytest
from jsonschema.exceptions import ValidationError

from ludwig.constants import TRAINER
from ludwig.features.audio_feature import AudioFeatureMixin
from ludwig.features.bag_feature import BagFeatureMixin
from ludwig.features.binary_feature import BinaryFeatureMixin
from ludwig.features.category_feature import CategoryFeatureMixin
from ludwig.features.date_feature import DateFeatureMixin
from ludwig.features.feature_registries import output_type_registry
from ludwig.features.h3_feature import H3FeatureMixin
from ludwig.features.image_feature import ImageFeatureMixin
from ludwig.features.number_feature import NumberFeatureMixin
from ludwig.features.sequence_feature import SequenceFeatureMixin
from ludwig.features.set_feature import SetFeatureMixin
from ludwig.features.text_feature import TextFeatureMixin
from ludwig.features.timeseries_feature import TimeseriesFeatureMixin
from ludwig.features.vector_feature import VectorFeatureMixin
from ludwig.schema import validate_config
from ludwig.schema.defaults.defaults import DefaultsConfig
from ludwig.utils.defaults import merge_with_defaults
from tests.integration_tests.utils import (
    audio_feature,
    bag_feature,
    binary_feature,
    category_feature,
    date_feature,
    ENCODERS,
    h3_feature,
    image_feature,
    number_feature,
    sequence_feature,
    set_feature,
    text_feature,
    timeseries_feature,
    vector_feature,
)


def test_config_features():
    all_input_features = [
        audio_feature("/tmp/destination_folder", encoder={"type": "parallel_cnn"}),
        bag_feature(encoder={"type": "embed"}),
        binary_feature(encoder={"type": "passthrough"}),
        category_feature(encoder={"type": "dense"}),
        date_feature(encoder={"type": "embed"}),
        h3_feature(encoder={"type": "embed"}),
        image_feature("/tmp/destination_folder", encoder={"type": "stacked_cnn"}),
        number_feature(encoder={"type": "passthrough"}),
        sequence_feature(encoder={"type": "parallel_cnn"}),
        set_feature(encoder={"type": "embed"}),
        text_feature(encoder={"type": "parallel_cnn"}),
        timeseries_feature(encoder={"type": "parallel_cnn"}),
        vector_feature(encoder={"type": "dense"}),
    ]
    all_output_features = [
        binary_feature(decoder={"type": "regressor"}),
        category_feature(decoder={"type": "classifier"}),
        number_feature(decoder={"type": "regressor"}),
        sequence_feature(decoder={"type": "generator"}),
        set_feature(decoder={"type": "classifier"}),
        text_feature(decoder={"type": "generator"}),
        vector_feature(decoder={"type": "projector"}),
    ]

    # validate config with all features
    config = {
        "input_features": all_input_features,
        "output_features": all_output_features,
    }
    validate_config(config)

    # make sure all defaults provided also registers as valid
    config = merge_with_defaults(config)
    validate_config(config)

    # test various invalid output features
    input_only_features = [
        feature for feature in all_input_features if feature["type"] not in output_type_registry.keys()
    ]
    for input_feature in input_only_features:
        config = {
            "input_features": all_input_features,
            "output_features": all_output_features + [input_feature],
        }

        dtype = input_feature["type"]
        with pytest.raises(ValidationError, match=rf"^'{dtype}' is not one of .*"):
            validate_config(config)


def test_config_encoders():
    for encoder in ENCODERS:
        config = {
            "input_features": [
                sequence_feature(encoder={"type": encoder, "reduce_output": "sum"}),
                image_feature("/tmp/destination_folder"),
            ],
            "output_features": [category_feature(decoder={"type": "classifier", "vocab_size": 2}, reduce_input="sum")],
            "combiner": {"type": "concat", "output_size": 14},
        }
        validate_config(config)


def test_config_tabnet():
    config = {
        "input_features": [
            category_feature(encoder={"type": "dense", "vocab_size": 2}, reduce_input="sum"),
            number_feature(),
        ],
        "output_features": [binary_feature(weight_regularization=None)],
        "combiner": {
            "type": "tabnet",
            "size": 24,
            "output_size": 26,
            "sparsity": 0.000001,
            "bn_virtual_divider": 32,
            "bn_momentum": 0.4,
            "num_steps": 5,
            "relaxation_factor": 1.5,
            "bn_virtual_bs": 512,
        },
        TRAINER: {
            "batch_size": 16384,
            "eval_batch_size": 500000,
            "epochs": 1000,
            "early_stop": 20,
            "learning_rate": 0.02,
            "optimizer": {"type": "adam"},
            "decay": True,
            "decay_steps": 20000,
            "decay_rate": 0.9,
            "staircase": True,
            "regularization_lambda": 1,
            "regularization_type": "l2",
            "validation_field": "label",
        },
    }
    validate_config(config)


def test_config_bad_feature_type():
    config = {
        "input_features": [{"name": "foo", "type": "fake"}],
        "output_features": [category_feature(encoder={"vocab_size": 2}, reduce_input="sum")],
        "combiner": {"type": "concat", "output_size": 14},
    }

    with pytest.raises(ValidationError, match=r"^'fake' is not one of .*"):
        validate_config(config)


def test_config_bad_encoder_name():
    config = {
        "input_features": [sequence_feature(encoder={"type": "fake", "reduce_output": "sum"})],
        "output_features": [category_feature(decoder={"type": "classifier", "vocab_size": 2}, reduce_input="sum")],
        "combiner": {"type": "concat", "output_size": 14},
    }

    with pytest.raises(ValidationError, match=r"^'fake' is not one of .*"):
        validate_config(config)


def test_config_bad_preprocessing_param():
    config = {
        "input_features": [
            sequence_feature(encoder={"type": "parallel_cnn", "reduce_output": "sum"}),
            image_feature(
                "/tmp/destination_folder",
                preprocessing={
                    "in_memory": True,
                    "height": 12,
                    "width": 12,
                    "num_channels": 3,
                    "tokenizer": "space",
                },
            ),
        ],
        "output_features": [category_feature(encoder={"vocab_size": 2}, reduce_input="sum")],
        "combiner": {"type": "concat", "output_size": 14},
    }

    with pytest.raises(ValidationError, match=r"^Additional properties are not allowed .*"):
        validate_config(config)


def test_config_fill_values():
    vector_fill_values = ["1.0 0.0 1.04 10.49", "1 2 3 4 5" "0" "1.0" ""]
    binary_fill_values = ["yes", "No", "1", "TRUE", 1]
    for vector_fill_value, binary_fill_value in zip(vector_fill_values, binary_fill_values):
        config = {
            "input_features": [
                vector_feature(preprocessing={"fill_value": vector_fill_value}),
            ],
            "output_features": [binary_feature(preprocessing={"fill_value": binary_fill_value})],
        }
        validate_config(config)

    bad_vector_fill_values = ["one two three", "1,2,3", 0]
    bad_binary_fill_values = ["one", 2, "maybe"]
    for vector_fill_value, binary_fill_value in zip(bad_vector_fill_values, bad_binary_fill_values):
        config = {
            "input_features": [
                vector_feature(preprocessing={"fill_value": vector_fill_value}),
            ],
            "output_features": [binary_feature(preprocessing={"fill_value": binary_fill_value})],
        }
        with pytest.raises(ValidationError):
            validate_config(config)


def test_validate_with_preprocessing_defaults():
    config = {
        "input_features": [
            audio_feature(
                "/tmp/destination_folder",
                preprocessing=AudioFeatureMixin.preprocessing_defaults(),
                encoder={"type": "parallel_cnn"},
            ),
            bag_feature(preprocessing=BagFeatureMixin.preprocessing_defaults(), encoder={"type": "embed"}),
            binary_feature(preprocessing=BinaryFeatureMixin.preprocessing_defaults(), encoder={"type": "passthrough"}),
            category_feature(preprocessing=CategoryFeatureMixin.preprocessing_defaults(), encoder={"type": "dense"}),
            date_feature(preprocessing=DateFeatureMixin.preprocessing_defaults(), encoder={"type": "embed"}),
            h3_feature(preprocessing=H3FeatureMixin.preprocessing_defaults(), encoder={"type": "embed"}),
            image_feature(
                "/tmp/destination_folder",
                preprocessing=ImageFeatureMixin.preprocessing_defaults(),
                encoder={"type": "stacked_cnn"},
            ),
            number_feature(preprocessing=NumberFeatureMixin.preprocessing_defaults(), encoder={"type": "passthrough"}),
            sequence_feature(
                preprocessing=SequenceFeatureMixin.preprocessing_defaults(), encoder={"type": "parallel_cnn"}
            ),
            set_feature(preprocessing=SetFeatureMixin.preprocessing_defaults(), encoder={"type": "embed"}),
            text_feature(preprocessing=TextFeatureMixin.preprocessing_defaults(), encoder={"type": "parallel_cnn"}),
            timeseries_feature(
                preprocessing=TimeseriesFeatureMixin.preprocessing_defaults(), encoder={"type": "parallel_cnn"}
            ),
            vector_feature(preprocessing=VectorFeatureMixin.preprocessing_defaults(), encoder={"type": "dense"}),
        ],
        "output_features": [{"name": "target", "type": "category"}],
        TRAINER: {
            "decay": True,
            "learning_rate": 0.001,
            "validation_field": "target",
            "validation_metric": "accuracy",
        },
    }

    validate_config(config)
    config = merge_with_defaults(config)
    validate_config(config)


def test_defaults_schema():
    schema = DefaultsConfig()
    assert schema.binary.decoder.type == "regressor"
    assert schema.binary.encoder.type == "passthrough"

    assert schema.category.top_k == 3
    assert schema.category.encoder.dropout == 0.0


def test_validate_defaults_schema():
    config = {
        "input_features": [
            category_feature(),
            number_feature(),
        ],
        "output_features": [category_feature()],
        "defaults": {
            "category": {
                "preprocessing": {
                    "missing_value_strategy": "drop_row",
                },
                "encoder": {
                    "type": "sparse",
                },
                "decoder": {
                    "type": "classifier",
                    "norm_params": None,
                    "dropout": 0.0,
                    "use_bias": True,
                },
                "loss": {
                    "type": "softmax_cross_entropy",
                    "confidence_penalty": 0,
                },
            },
            "number": {
                "preprocessing": {
                    "missing_value_strategy": "fill_with_const",
                    "fill_value": 0,
                },
                "loss": {"type": "mean_absolute_error"},
            },
        },
    }

    validate_config(config)
