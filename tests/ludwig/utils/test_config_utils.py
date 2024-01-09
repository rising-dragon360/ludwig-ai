from typing import Any, Dict, Optional

import pytest

from ludwig.constants import (
    BASE_MODEL,
    BINARY,
    ENCODER,
    INPUT_FEATURES,
    MODEL_ECD,
    MODEL_GBM,
    MODEL_LLM,
    MODEL_TYPE,
    NAME,
    OUTPUT_FEATURES,
    TEXT,
    TYPE,
)
from ludwig.schema.encoders.text_encoders import BERTConfig
from ludwig.schema.encoders.utils import get_encoder_cls
from ludwig.schema.features.preprocessing.text import TextPreprocessingConfig
from ludwig.schema.model_config import ModelConfig
from ludwig.utils.config_utils import config_uses_llm


@pytest.mark.parametrize(
    "pretrained_model_name_or_path",
    [None, "bert-large-uncased"],
    ids=["default_model", "override_model"],
)
def test_set_fixed_preprocessing_params(pretrained_model_name_or_path: str):
    expected_model_name = "bert-base-uncased"

    preprocessing = TextPreprocessingConfig.from_dict(
        {
            "tokenizer": "space",
            "lowercase": True,
        }
    )

    encoder_params = {}
    if pretrained_model_name_or_path is not None:
        encoder_params["pretrained_model_name_or_path"] = pretrained_model_name_or_path
        expected_model_name = pretrained_model_name_or_path

    encoder = BERTConfig.from_dict(encoder_params)
    encoder.set_fixed_preprocessing_params(MODEL_ECD, preprocessing)

    assert preprocessing.tokenizer == "hf_tokenizer"
    assert preprocessing.lowercase
    assert preprocessing.pretrained_model_name_or_path == expected_model_name


@pytest.mark.parametrize(
    "encoder_params,expected",
    [
        ({"type": "parallel_cnn"}, False),
        ({"type": "bert", "trainable": False}, True),
        ({"type": "bert", "trainable": True}, False),
    ],
    ids=["parallel_cnn", "bert_fixed", "bert_trainable"],
)
def test_set_fixed_preprocessing_params_cache_embeddings(encoder_params: Dict[str, Any], expected: Optional[bool]):
    preprocessing = TextPreprocessingConfig.from_dict(
        {
            "tokenizer": "space",
            "lowercase": True,
            "cache_encoder_embeddings": True,
        }
    )

    encoder = get_encoder_cls(MODEL_ECD, TEXT, encoder_params[TYPE]).from_dict(encoder_params)
    encoder.set_fixed_preprocessing_params(MODEL_ECD, preprocessing)
    assert preprocessing.cache_encoder_embeddings == expected


@pytest.fixture(scope="module")
def llm_config_dict() -> Dict[str, Any]:
    return {
        MODEL_TYPE: MODEL_LLM,
        BASE_MODEL: "HuggingFaceH4/tiny-random-LlamaForCausalLM",
        INPUT_FEATURES: [{TYPE: TEXT, NAME: "in1"}],
        OUTPUT_FEATURES: [{TYPE: TEXT, NAME: "out1"}],
    }


@pytest.fixture(scope="module")
def llm_config_object(llm_config_dict: Dict[str, Any]) -> ModelConfig:
    return ModelConfig.from_dict(llm_config_dict)


@pytest.fixture(scope="module")
def ecd_config_dict_llm_encoder() -> Dict[str, Any]:
    return {
        MODEL_TYPE: MODEL_ECD,
        INPUT_FEATURES: [
            {
                TYPE: TEXT,
                NAME: "in1",
                ENCODER: {TYPE: MODEL_LLM, BASE_MODEL: "HuggingFaceH4/tiny-random-LlamaForCausalLM"},
            }
        ],
        OUTPUT_FEATURES: [{TYPE: BINARY, NAME: "out1"}],
    }


@pytest.fixture(scope="module")
def ecd_config_object_llm_encoder(ecd_config_dict_llm_encoder: Dict[str, Any]) -> ModelConfig:
    return ModelConfig.from_dict(ecd_config_dict_llm_encoder)


@pytest.fixture(scope="module")
def ecd_config_dict_llm_encoder_multiple_features() -> Dict[str, Any]:
    return {
        MODEL_TYPE: MODEL_ECD,
        INPUT_FEATURES: [
            {TYPE: BINARY, NAME: "in1"},
            {
                TYPE: TEXT,
                NAME: "in2",
                ENCODER: {TYPE: MODEL_LLM, BASE_MODEL: "HuggingFaceH4/tiny-random-LlamaForCausalLM"},
            },
        ],
        OUTPUT_FEATURES: [{TYPE: BINARY, NAME: "out1"}],
    }


@pytest.fixture(scope="module")
def ecd_config_object_llm_encoder_multiple_features(
    ecd_config_dict_llm_encoder_multiple_features: Dict[str, Any]
) -> ModelConfig:
    return ModelConfig.from_dict(ecd_config_dict_llm_encoder_multiple_features)


@pytest.fixture(scope="module")
def ecd_config_dict_no_llm_encoder() -> Dict[str, Any]:
    return {
        MODEL_TYPE: MODEL_ECD,
        INPUT_FEATURES: [{TYPE: TEXT, NAME: "in1", ENCODER: {TYPE: "parallel_cnn"}}],
        OUTPUT_FEATURES: [{TYPE: BINARY, NAME: "out1"}],
    }


@pytest.fixture(scope="module")
def ecd_config_object_no_llm_encoder(ecd_config_dict_no_llm_encoder: Dict[str, Any]) -> ModelConfig:
    return ModelConfig.from_dict(ecd_config_dict_no_llm_encoder)


@pytest.fixture(scope="module")
def ecd_config_dict_no_text_features() -> Dict[str, Any]:
    return {
        MODEL_TYPE: MODEL_ECD,
        INPUT_FEATURES: [{TYPE: BINARY, NAME: "in1"}],
        OUTPUT_FEATURES: [{TYPE: BINARY, NAME: "out1"}],
    }


@pytest.fixture(scope="module")
def ecd_config_object_no_text_features(ecd_config_dict_no_text_features: Dict[str, Any]) -> ModelConfig:
    return ModelConfig.from_dict(ecd_config_dict_no_text_features)


@pytest.fixture(scope="module")
def gbm_config_dict() -> Dict[str, Any]:
    return {
        MODEL_TYPE: MODEL_GBM,
        INPUT_FEATURES: [{TYPE: TEXT, NAME: "in1", ENCODER: {TYPE: "tf_idf"}}],
        OUTPUT_FEATURES: [{TYPE: BINARY, NAME: "out1"}],
    }


@pytest.fixture(scope="module")
def gbm_config_object(gbm_config_dict: Dict[str, Any]) -> ModelConfig:
    return ModelConfig.from_dict(gbm_config_dict)


@pytest.fixture(scope="module")
def gbm_config_dict_no_text_features() -> Dict[str, Any]:
    return {
        MODEL_TYPE: MODEL_GBM,
        INPUT_FEATURES: [{TYPE: BINARY, NAME: "in1"}],
        OUTPUT_FEATURES: [{TYPE: BINARY, NAME: "out1"}],
    }


@pytest.fixture(scope="module")
def gbm_config_object_no_text_features(gbm_config_dict_no_text_features: Dict[str, Any]) -> ModelConfig:
    return ModelConfig.from_dict(gbm_config_dict_no_text_features)


@pytest.mark.parametrize(
    "config,expectation",
    [
        # LLM configurations
        ("llm_config_dict", True),
        ("llm_config_object", True),
        # LLM encoder configurations
        ("ecd_config_dict_llm_encoder", True),
        ("ecd_config_object_llm_encoder", True),
        # LLM encoder configurations, multiple features
        ("ecd_config_dict_llm_encoder_multiple_features", True),
        ("ecd_config_object_llm_encoder_multiple_features", True),
        # ECD configuration with text feature and non-LLM encoder
        ("ecd_config_dict_no_llm_encoder", False),
        ("ecd_config_object_no_llm_encoder", False),
        # ECD configuration with no text features
        ("ecd_config_dict_no_text_features", False),
        ("ecd_config_object_no_text_features", False),
        # GBM configuration with text feature. "tf_idf" is the only valid text encoder
        ("gbm_config_dict", False),
        ("gbm_config_object", False),
        # GBM configuration with no text features
        ("gbm_config_dict_no_text_features", False),
        ("gbm_config_object_no_text_features", False),
    ],
)
def test_is_or_uses_llm(config, expectation, request):
    """Test LLM detection on a variety of configs. Configs that use an LLM anywhere should return True, otherwise
    False.

    Args:
        config: The name of the config fixture to test
        expectation: The expected result
        request: pytest `request` fixture
    """
    config = request.getfixturevalue(config)
    assert config_uses_llm(config) == expectation


@pytest.mark.parametrize("invalid_config", [1, 1.0, "foo", True, False, None, [], {}, {"foo": "bar"}])
def test_is_or_uses_llm_invalid_input(invalid_config):
    """Sanity checks for invalid config handling.

    These should all raise an exception.

    Args:
        invalid_config: An invalid argument to `config_uses_llm`
    """
    with pytest.raises(ValueError):
        config_uses_llm(invalid_config)
