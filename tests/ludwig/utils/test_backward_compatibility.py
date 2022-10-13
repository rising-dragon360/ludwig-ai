import copy
import math

import pytest

from ludwig.constants import (
    BFILL,
    CLASS_WEIGHTS,
    DEFAULTS,
    EVAL_BATCH_SIZE,
    EXECUTOR,
    HYPEROPT,
    INPUT_FEATURES,
    LOSS,
    NUMBER,
    OUTPUT_FEATURES,
    PREPROCESSING,
    SCHEDULER,
    SPLIT,
    TRAINER,
    TYPE,
)
from ludwig.schema import validate_config
from ludwig.schema.trainer import ECDTrainerConfig
from ludwig.utils.backward_compatibility import (
    _update_backend_cache_credentials,
    _upgrade_encoder_decoder_params,
    _upgrade_feature,
    _upgrade_preprocessing_split,
    upgrade_missing_value_strategy,
    upgrade_model_progress,
    upgrade_to_latest_version,
)
from ludwig.utils.defaults import merge_with_defaults


def test_preprocessing_backward_compatibility():
    # From v0.5.3.
    preprocessing_config = {
        "force_split": False,
        "split_probabilities": [0.7, 0.1, 0.2],
        "stratify": None,
    }

    _upgrade_preprocessing_split(preprocessing_config)

    assert preprocessing_config == {
        "split": {"probabilities": [0.7, 0.1, 0.2], "type": "random"},
    }


def test_audio_feature_backward_compatibility():
    # From v0.5.3.

    audio_feature_preprocessing_config = {
        "name": "audio_feature",
        "type": "audio",
        "preprocessing": {
            "audio_file_length_limit_in_s": 7.5,
            "missing_value_strategy": BFILL,
            "in_memory": True,
            "padding_value": 0,
            "norm": None,
            "audio_feature": {
                "type": "fbank",
                "window_length_in_s": 0.04,
                "window_shift_in_s": 0.02,
                "num_fft_points": None,
                "window_type": "hamming",
                "num_filter_bands": 80,
            },
        },
    }

    global_preprocessing_config = {
        "audio": {
            "audio_file_length_limit_in_s": 7.5,
            "missing_value_strategy": BFILL,
            "in_memory": True,
            "padding_value": 0,
            "norm": None,
            "audio_feature": {
                "type": "fbank",
                "window_length_in_s": 0.04,
                "window_shift_in_s": 0.02,
                "num_fft_points": None,
                "window_type": "hamming",
                "num_filter_bands": 80,
            },
        },
    }

    _upgrade_feature(audio_feature_preprocessing_config)
    _upgrade_preprocessing_split(global_preprocessing_config)

    assert global_preprocessing_config == {
        "audio": {
            "audio_file_length_limit_in_s": 7.5,
            "missing_value_strategy": BFILL,
            "in_memory": True,
            "padding_value": 0,
            "norm": None,
            "type": "fbank",
            "window_length_in_s": 0.04,
            "window_shift_in_s": 0.02,
            "num_fft_points": None,
            "window_type": "hamming",
            "num_filter_bands": 80,
        }
    }

    assert audio_feature_preprocessing_config == {
        "name": "audio_feature",
        "type": "audio",
        "preprocessing": {
            "audio_file_length_limit_in_s": 7.5,
            "missing_value_strategy": BFILL,
            "in_memory": True,
            "padding_value": 0,
            "norm": None,
            "type": "fbank",
            "window_length_in_s": 0.04,
            "window_shift_in_s": 0.02,
            "num_fft_points": None,
            "window_type": "hamming",
            "num_filter_bands": 80,
        },
    }


def test_encoder_decoder_backwards_compatibility():
    old_config = {
        "input_features": [
            {
                "name": "text_feature",
                "type": "text",
                "preprocessing": {
                    "missing_value_strategy": "drop_row",
                },
                "encoder": "rnn",
                "bidirectional": True,
                "representation": "dense",
                "num_layers": 2,
            },
            {
                "name": "image_feature_1",
                "type": "image",
                "preprocessing": {
                    "height": 7.5,
                    "width": 7.5,
                    "num_channels": 4,
                },
                "encoder": "resnet",
                "num_channels": 4,
                "dropout": 0.1,
                "resnet_size": 100,
            },
            {
                "name": "image_feature_2",
                "type": "image",
                "tied": "image_feature_1",
                "preprocessing": {
                    "height": 7.5,
                    "width": 7.5,
                    "num_channels": 4,
                },
                "encoder": "resnet",
            },
        ],
        "output_features": [
            {
                "name": "category_feature",
                "type": "category",
                "top_k": 3,
                "preprocessing": {
                    "missing_value_strategy": BFILL,
                },
                "decoder": "classifier",
                "num_classes": 10,
                "use_bias": False,
            },
            {
                "name": "binary_feature",
                "type": "binary",
                "dependencies": ["category_feature"],
                "loss": {
                    "type": "cross_entropy",
                },
                "reduce_dependencies": "mean",
                "decoder": "regressor",
                "use_bias": True,
                "bias_initializer": "constant",
            },
            {
                "name": "vector_feature",
                "type": "vector",
                "decoder": "projector",
                "num_fc_layers": 5,
                "output_size": 128,
                "activation": "tanh",
                "dropout": 0.1,
            },
        ],
    }

    for feature in old_config[INPUT_FEATURES]:
        _upgrade_encoder_decoder_params(feature, True)

    for feature in old_config[OUTPUT_FEATURES]:
        _upgrade_encoder_decoder_params(feature, False)

    assert old_config == {
        "input_features": [
            {
                "name": "text_feature",
                "type": "text",
                "preprocessing": {
                    "missing_value_strategy": "drop_row",
                },
                "encoder": {
                    "type": "rnn",
                    "bidirectional": True,
                    "representation": "dense",
                    "num_layers": 2,
                },
            },
            {
                "name": "image_feature_1",
                "type": "image",
                "preprocessing": {
                    "height": 7.5,
                    "width": 7.5,
                    "num_channels": 4,
                },
                "encoder": {
                    "type": "resnet",
                    "num_channels": 4,
                    "dropout": 0.1,
                    "resnet_size": 100,
                },
            },
            {
                "name": "image_feature_2",
                "type": "image",
                "tied": "image_feature_1",
                "preprocessing": {
                    "height": 7.5,
                    "width": 7.5,
                    "num_channels": 4,
                },
                "encoder": {"type": "resnet"},
            },
        ],
        "output_features": [
            {
                "name": "category_feature",
                "type": "category",
                "num_classes": 10,
                "top_k": 3,
                "preprocessing": {
                    "missing_value_strategy": BFILL,
                },
                "decoder": {
                    "type": "classifier",
                    "fc_use_bias": False,
                    "use_bias": False,
                },
            },
            {
                "name": "binary_feature",
                "type": "binary",
                "dependencies": ["category_feature"],
                "loss": {
                    "type": "cross_entropy",
                },
                "reduce_dependencies": "mean",
                "decoder": {
                    "type": "regressor",
                    "fc_use_bias": True,
                    "fc_bias_initializer": "constant",
                    "bias_initializer": "constant",
                    "use_bias": True,
                },
            },
            {
                "name": "vector_feature",
                "type": "vector",
                "decoder": {
                    "type": "projector",
                    "num_fc_layers": 5,
                    "fc_output_size": 128,
                    "fc_activation": "tanh",
                    "fc_dropout": 0.1,
                    "output_size": 128,
                    "activation": "tanh",
                    "dropout": 0.1,
                },
            },
        ],
    }


def test_deprecated_field_aliases():
    config = {
        "ludwig_version": "0.4",
        INPUT_FEATURES: [{"name": "num_in", "type": "numerical"}],
        OUTPUT_FEATURES: [{"name": "num_out", "type": "numerical"}],
        "training": {
            "epochs": 2,
            "eval_batch_size": 0,
        },
        HYPEROPT: {
            "parameters": {
                "training.learning_rate": {
                    "space": "loguniform",
                    "lower": 0.001,
                    "upper": 0.1,
                },
            },
            "goal": "minimize",
            "sampler": {"type": "grid", "num_samples": 2, "scheduler": {"type": "fifo"}},
            "executor": {
                "type": "grid",
                "search_alg": "bohb",
            },
        },
        PREPROCESSING: {
            "numerical": {
                "fill_value": 2,
                "missing_value_strategy": "fill_with_const",
            },
        },
    }

    updated_config = upgrade_to_latest_version(config)

    assert updated_config["input_features"][0][TYPE] == NUMBER
    assert updated_config["output_features"][0][TYPE] == NUMBER

    # "numerical" preprocssing directive should be translated to "number" and moved into the defaults section.
    assert PREPROCESSING not in updated_config
    assert updated_config[DEFAULTS][NUMBER][PREPROCESSING]["fill_value"] == 2

    assert "training" not in updated_config
    assert updated_config[TRAINER]["epochs"] == 2
    assert updated_config[TRAINER][EVAL_BATCH_SIZE] is None

    hparams = updated_config[HYPEROPT]["parameters"]
    assert "training.learning_rate" not in hparams
    assert "trainer.learning_rate" in hparams

    assert "sampler" not in updated_config[HYPEROPT]

    assert updated_config[HYPEROPT]["executor"]["type"] == "ray"
    assert "num_samples" in updated_config[HYPEROPT]["executor"]
    assert "scheduler" in updated_config[HYPEROPT]["executor"]

    validate_config(updated_config)


@pytest.mark.parametrize("force_split", [None, False, True])
@pytest.mark.parametrize("stratify", [None, "cat_in"])
def test_deprecated_split_aliases(stratify, force_split):
    split_probabilities = [0.6, 0.2, 0.2]
    config = {
        "ludwig_version": "0.4",
        INPUT_FEATURES: [{"name": "num_in", "type": "number"}, {"name": "cat_in", "type": "category"}],
        OUTPUT_FEATURES: [{"name": "num_out", "type": "number"}],
        PREPROCESSING: {
            "force_split": force_split,
            "split_probabilities": split_probabilities,
            "stratify": stratify,
        },
    }

    updated_config = upgrade_to_latest_version(config)

    assert "force_split" not in updated_config[PREPROCESSING]
    assert "split_probabilities" not in updated_config[PREPROCESSING]
    assert "stratify" not in updated_config[PREPROCESSING]

    assert SPLIT in updated_config[PREPROCESSING]
    split = updated_config[PREPROCESSING][SPLIT]

    assert split["probabilities"] == split_probabilities
    if stratify is None:
        if force_split:
            assert split.get(TYPE) == "random"
    else:
        assert split.get(TYPE) == "stratify"
        assert split.get("column") == stratify


@pytest.mark.parametrize("use_scheduler", [True, False])
def test_deprecated_hyperopt_sampler_early_stopping(use_scheduler):
    sampler = {
        "type": "ray",
        "num_samples": 2,
    }

    if use_scheduler:
        sampler[SCHEDULER] = {
            "type": "async_hyperband",
            "max_t": 200,
            "time_attr": "time_total_s",
            "grace_period": 72,
            "reduction_factor": 5,
        }

    config = {
        INPUT_FEATURES: [
            {
                "type": "category",
                "name": "cat_input_feature",
            },
        ],
        OUTPUT_FEATURES: [
            {
                "type": "number",
                "name": "num_output_feature",
            },
        ],
        "hyperopt": {
            "search_alg": {
                "type": "hyperopt",
                "random_state_seed": 42,
            },
            "executor": {
                "type": "ray",
                "time_budget_s": 200,
                "cpu_resources_per_trial": 1,
            },
            "sampler": sampler,
            "parameters": {
                "trainer.batch_size": {
                    "space": "choice",
                    "categories": [64, 128, 256],
                },
                "trainer.learning_rate": {
                    "space": "loguniform",
                    "lower": 0.001,
                    "upper": 0.1,
                },
            },
        },
    }

    updated_config = upgrade_to_latest_version(config)
    if use_scheduler:
        assert SCHEDULER in updated_config[HYPEROPT][EXECUTOR]

    merged_config = merge_with_defaults(updated_config)

    # When a scheulder is provided, early stopping in the rendered config needs to be disabled to allow the
    # hyperopt scheduler to manage trial lifecycle.
    expected_early_stop = -1 if use_scheduler else ECDTrainerConfig().early_stop
    assert merged_config[TRAINER]["early_stop"] == expected_early_stop


def test_validate_old_model_config():
    old_valid_config = {
        "input_features": [
            {"name": "feature_1", "type": "category"},
            {"name": "Sex", "type": "category", "encoder": "dense"},
        ],
        "output_features": [
            {"name": "Survived", "type": "category"},
        ],
    }

    old_invalid_config = {
        "input_features": [
            {"name": "feature_1", "type": "category"},
            {"name": "Sex", "type": "category", "encoder": "fake_encoder"},
        ],
        "output_features": [
            {"name": "Survived", "type": "category"},
        ],
    }

    validate_config(old_valid_config)

    with pytest.raises(Exception):
        validate_config(old_invalid_config)


@pytest.mark.parametrize("missing_value_strategy", ["backfill", "pad"])
def test_update_missing_value_strategy(missing_value_strategy: str):
    old_valid_config = {
        "input_features": [
            {
                "name": "input_feature_1",
                "type": "category",
                "preprocessing": {"missing_value_strategy": missing_value_strategy},
            }
        ],
        "output_features": [
            {"name": "output_feature_1", "type": "category"},
        ],
    }

    updated_config = upgrade_missing_value_strategy(old_valid_config)

    expected_config = copy.deepcopy(old_valid_config)
    if missing_value_strategy == "backfill":
        expected_config["input_features"][0]["preprocessing"]["missing_value_strategy"] == "bfill"
    else:
        expected_config["input_features"][0]["preprocessing"]["missing_value_strategy"] == "ffill"

    assert updated_config == expected_config


def test_update_increase_batch_size_on_plateau_max():
    old_valid_config = {
        "input_features": [{"name": "input_feature_1", "type": "category"}],
        "output_features": [{"name": "output_feature_1", "type": "category"}],
        "trainer": {
            "increase_batch_size_on_plateau_max": 256,
        },
    }

    updated_config = upgrade_to_latest_version(old_valid_config)
    del updated_config["ludwig_version"]

    expected_config = copy.deepcopy(old_valid_config)
    del expected_config["trainer"]["increase_batch_size_on_plateau_max"]
    expected_config["trainer"]["max_batch_size"] = 256

    assert updated_config == expected_config


def test_old_class_weights_default():
    old_config = {
        "input_features": [
            {
                "name": "input_feature_1",
                "type": "category",
            }
        ],
        "output_features": [
            {"name": "output_feature_1", "type": "category", "loss": {"class_weights": 1}},
        ],
    }

    new_config = {
        "input_features": [
            {
                "name": "input_feature_1",
                "type": "category",
            }
        ],
        "output_features": [
            {"name": "output_feature_1", "type": "category", "loss": {"class_weights": None}},
        ],
    }

    upgraded_config = upgrade_to_latest_version(old_config)
    del upgraded_config["ludwig_version"]
    assert new_config == upgraded_config

    old_config[OUTPUT_FEATURES][0][LOSS][CLASS_WEIGHTS] = [0.5, 0.8, 1]
    new_config[OUTPUT_FEATURES][0][LOSS][CLASS_WEIGHTS] = [0.5, 0.8, 1]

    upgraded_config = upgrade_to_latest_version(old_config)
    del upgraded_config["ludwig_version"]
    assert new_config == upgraded_config


def test_upgrade_model_progress():
    old_model_progress = {
        "batch_size": 64,
        "best_eval_metric": 0.5,
        "best_increase_batch_size_eval_metric": math.inf,
        "best_reduce_learning_rate_eval_metric": math.inf,
        "epoch": 2,
        "last_improvement": 1,
        "last_improvement_epoch": 1,
        "last_increase_batch_size": 0,
        "last_increase_batch_size_epoch": 0,
        "last_increase_batch_size_eval_metric_improvement": 0,
        "last_learning_rate_reduction": 0,
        "last_learning_rate_reduction_epoch": 0,
        "last_reduce_learning_rate_eval_metric_improvement": 0,
        "learning_rate": 0.001,
        "num_increases_batch_size": 0,
        "num_reductions_learning_rate": 0,
        "steps": 224,
        "test_metrics": {
            "combined": {"loss": [0.59, 0.56]},
            "delinquent": {
                "accuracy": [0.77, 0.78],
            },
        },
        "train_metrics": {"combined": {"loss": [0.58, 0.55]}, "delinquent": {"roc_auc": [0.53, 0.54]}},
        "vali_metrics": {"combined": {"loss": [0.59, 0.60]}, "delinquent": {"roc_auc": [0.53, 0.44]}},
    }

    new_model_progress = upgrade_model_progress(old_model_progress)

    for stat in ("improvement", "increase_batch_size", "learning_rate_reduction"):
        assert f"last_{stat}_epoch" not in new_model_progress
        assert f"last_{stat}_steps" in new_model_progress
        assert (
            new_model_progress[f"last_{stat}_steps"]
            == old_model_progress[f"last_{stat}_epoch"] * old_model_progress["batch_size"]
        )

    assert "tune_checkpoint_num" in new_model_progress

    assert "vali_metrics" not in new_model_progress
    assert "validation_metrics" in new_model_progress

    metric = new_model_progress["validation_metrics"]["combined"]["loss"][0]
    assert len(metric) == 3
    assert metric[-1] == 0.59

    # Verify that we don't make changes to already-valid model progress dicts.
    # To do so, we modify the batch size value and re-run the upgrade on the otherwise-valid `new_model_progress` dict.
    new_model_progress["batch_size"] = 1
    unchanged_model_progress = upgrade_model_progress(new_model_progress)
    assert unchanged_model_progress == new_model_progress


def test_upgrade_model_progress_already_valid():
    # Verify that we don't make changes to already-valid model progress dicts.
    valid_model_progress = {
        "batch_size": 128,
        "best_eval_metric": 5.541325569152832,
        "best_increase_batch_size_eval_metric": math.inf,
        "best_reduce_learning_rate_eval_metric": math.inf,
        "epoch": 5,
        "last_improvement": 0,
        "last_improvement_steps": 25,
        "last_increase_batch_size": 0,
        "last_increase_batch_size_eval_metric_improvement": 0,
        "last_increase_batch_size_steps": 0,
        "last_learning_rate_reduction": 0,
        "last_learning_rate_reduction_steps": 0,
        "last_reduce_learning_rate_eval_metric_improvement": 0,
        "learning_rate": 0.001,
        "num_increases_batch_size": 0,
        "num_reductions_learning_rate": 0,
        "steps": 25,
        "test_metrics": {
            "Survived": {"accuracy": [[0, 5, 0.39], [1, 10, 0.38]], "loss": [[0, 5, 7.35], [1, 10, 7.08]]},
            "combined": {"loss": [[0, 5, 7.35], [1, 10, 6.24]]},
        },
        "train_metrics": {
            "Survived": {"accuracy": [[0, 5, 0.39], [1, 10, 0.40]], "loss": [[0, 5, 7.67], [1, 10, 6.57]]},
            "combined": {"loss": [[0, 5, 7.67], [1, 10, 6.57]]},
        },
        "validation_metrics": {
            "Survived": {"accuracy": [[0, 5, 0.38], [1, 10, 0.38]], "loss": [[0, 5, 6.56], [1, 10, 5.54]]},
            "combined": {"loss": [[0, 5, 6.56], [1, 10, 5.54]]},
        },
        "tune_checkpoint_num": 0,
    }

    unchanged_model_progress = upgrade_model_progress(valid_model_progress)
    assert unchanged_model_progress == valid_model_progress


def test_cache_credentials_backward_compatibility():
    # From v0.6.3.
    creds = {"s3": {"client_kwargs": {}}}
    backend = {"type": "local", "cache_dir": "/foo/bar", "cache_credentials": creds}

    _update_backend_cache_credentials(backend)

    assert backend == {"type": "local", "cache_dir": "/foo/bar", "credentials": {"cache": creds}}
