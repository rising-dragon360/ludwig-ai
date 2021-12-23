import numpy as np
import pandas as pd
import pytest

from ludwig.features.text_feature import TextFeatureMixin
from ludwig.utils import strings_utils


def test_is_numerical():
    assert strings_utils.is_numerical("1.1")
    assert strings_utils.is_numerical("1.000001")
    assert strings_utils.is_numerical("1000001")
    assert strings_utils.is_numerical("Nan")
    assert strings_utils.is_numerical("NaN")
    assert strings_utils.is_numerical(1)
    assert strings_utils.is_numerical(1.1)
    assert not strings_utils.is_numerical("NaNaaa")


def test_str_to_bool():
    # Global bool mappings are used.
    assert strings_utils.str2bool("True")
    assert strings_utils.str2bool(True)
    assert strings_utils.str2bool("true")
    assert not strings_utils.str2bool("0")

    # Error raised if non-mapped value is encountered and no fallback is specified.
    with pytest.raises(Exception):
        strings_utils.str2bool("bot")

    # Fallback label is used.
    assert strings_utils.str2bool("bot", fallback_true_label="bot")
    assert not strings_utils.str2bool("human", fallback_true_label="bot")
    assert strings_utils.str2bool("human", fallback_true_label="human")
    assert not strings_utils.str2bool("human", fallback_true_label="Human")

    # Fallback label is used, strictly as a fallback.
    assert strings_utils.str2bool("True", fallback_true_label="False")


def test_are_conventional_bools():
    assert strings_utils.are_conventional_bools(["True", "False"])
    assert strings_utils.are_conventional_bools([True, False])
    assert strings_utils.are_conventional_bools(["True", False, True])
    assert strings_utils.are_conventional_bools(["T", "F"])
    assert strings_utils.are_conventional_bools(["t", "f"])
    assert not strings_utils.are_conventional_bools(["True", "Fails"])
    assert strings_utils.are_conventional_bools(["0", "1"])
    assert not strings_utils.are_conventional_bools(["0", "2"])
    assert strings_utils.are_conventional_bools(["1.0", "0.0"])
    assert not strings_utils.are_conventional_bools(["high", "low"])
    assert not strings_utils.are_conventional_bools(["human", "bot"])


def test_create_vocabulary_chars():
    data = pd.DataFrame(["Hello, I'm a single sentence!", "And another sentence", "And the very very last one"])
    column = data[0]
    preprocessing_parameters = TextFeatureMixin.preprocessing_defaults()

    vocabulary_output = strings_utils.create_vocabulary(
        column,
        tokenizer_type="characters",
        num_most_frequent=preprocessing_parameters["char_most_common"],
        lowercase=preprocessing_parameters["lowercase"],
        unknown_symbol=preprocessing_parameters["unknown_symbol"],
        padding_symbol=preprocessing_parameters["padding_symbol"],
        pretrained_model_name_or_path=preprocessing_parameters["pretrained_model_name_or_path"],
    )

    assert len(vocabulary_output[0]) == 24
    assert vocabulary_output[0][strings_utils.SpecialSymbol.START.value] == strings_utils.START_SYMBOL
    assert vocabulary_output[0][strings_utils.SpecialSymbol.STOP.value] == strings_utils.STOP_SYMBOL
    assert vocabulary_output[0][strings_utils.SpecialSymbol.PADDING.value] == strings_utils.PADDING_SYMBOL
    assert vocabulary_output[0][strings_utils.SpecialSymbol.UNKNOWN.value] == strings_utils.UNKNOWN_SYMBOL


def test_create_vocabulary_word():
    data = pd.DataFrame(["Hello, I'm a single sentence!", "And another sentence", "And the very very last one"])
    column = data[0]
    preprocessing_parameters = TextFeatureMixin.preprocessing_defaults()

    vocabulary_output = strings_utils.create_vocabulary(
        column,
        tokenizer_type=preprocessing_parameters["word_tokenizer"],
        num_most_frequent=preprocessing_parameters["word_most_common"],
        lowercase=preprocessing_parameters["lowercase"],
        vocab_file=preprocessing_parameters["word_vocab_file"],
        unknown_symbol=preprocessing_parameters["unknown_symbol"],
        padding_symbol=preprocessing_parameters["padding_symbol"],
        pretrained_model_name_or_path=preprocessing_parameters["pretrained_model_name_or_path"],
    )

    assert len(vocabulary_output[0]) == 19
    assert vocabulary_output[0][strings_utils.SpecialSymbol.UNKNOWN.value] == strings_utils.UNKNOWN_SYMBOL
    assert vocabulary_output[0][strings_utils.SpecialSymbol.STOP.value] == strings_utils.STOP_SYMBOL
    assert vocabulary_output[0][strings_utils.SpecialSymbol.PADDING.value] == strings_utils.PADDING_SYMBOL
    assert vocabulary_output[0][strings_utils.SpecialSymbol.UNKNOWN.value] == strings_utils.UNKNOWN_SYMBOL


def test_create_vocabulary_no_special_symbols():
    data = pd.DataFrame(["Hello, I'm a single sentence!", "And another sentence", "And the very very last one"])
    column = data[0]
    preprocessing_parameters = TextFeatureMixin.preprocessing_defaults()

    vocabulary_output = strings_utils.create_vocabulary(
        column,
        tokenizer_type=preprocessing_parameters["word_tokenizer"],
        num_most_frequent=preprocessing_parameters["word_most_common"],
        lowercase=preprocessing_parameters["lowercase"],
        vocab_file=preprocessing_parameters["word_vocab_file"],
        unknown_symbol=preprocessing_parameters["unknown_symbol"],
        padding_symbol=preprocessing_parameters["padding_symbol"],
        pretrained_model_name_or_path=preprocessing_parameters["pretrained_model_name_or_path"],
        add_special_symbols=False,
    )

    assert len(vocabulary_output[0]) == 16
    assert vocabulary_output[0][strings_utils.SpecialSymbol.UNKNOWN.value] == strings_utils.UNKNOWN_SYMBOL


def test_create_vocabulary_from_hf():
    data = pd.DataFrame(["Hello, I'm a single sentence!", "And another sentence", "And the very very last one"])
    column = data[0]
    preprocessing_parameters = TextFeatureMixin.preprocessing_defaults()

    vocabulary_output = strings_utils.create_vocabulary(
        column,
        tokenizer_type="hf_tokenizer",
        num_most_frequent=preprocessing_parameters["char_most_common"],
        lowercase=preprocessing_parameters["lowercase"],
        unknown_symbol=preprocessing_parameters["unknown_symbol"],
        padding_symbol=preprocessing_parameters["padding_symbol"],
        pretrained_model_name_or_path="albert-base-v2",
    )

    assert len(vocabulary_output[0]) == 30000


def test_build_sequence_matrix():
    inverse_vocabulary = {
        "<EOS>": 0,
        "<SOS>": 1,
        "<PAD>": 2,
        "<UNK>": 3,
        "a": 4,
        "b": 5,
        "c": 6,
    }
    sequences = pd.core.series.Series(["a b c", "c b a"])
    sequence_matrix = strings_utils.build_sequence_matrix(
        sequences, inverse_vocabulary, tokenizer_type="space", length_limit=10
    )
    assert not (
        sequence_matrix.tolist() - np.array([[1, 4, 5, 6, 0, 2, 2, 2, 2, 2], [1, 6, 5, 4, 0, 2, 2, 2, 2, 2]])
    ).any()
