import numpy as np
import pytest
import torch

from ludwig.features import feature_utils


def test_ludwig_feature_dict():
    feature_dict = feature_utils.LudwigFeatureDict()

    to_module = torch.nn.Module()
    type_module = torch.nn.Module()

    feature_dict.set("to", to_module)
    feature_dict.set("type", type_module)

    assert iter(feature_dict) is not None
    assert next(feature_dict) is not None
    assert len(feature_dict) == 2
    assert feature_dict.keys() == ["to", "type"]
    assert feature_dict.items() == [("to", to_module), ("type", type_module)]
    assert feature_dict.get("to"), to_module

    feature_dict.update({"to_empty": torch.nn.Module()})

    assert len(feature_dict) == 3
    assert [key for key in feature_dict] == ["to", "type", "to_empty"]


def test_ludwig_feature_dict_with_periods():
    feature_dict = feature_utils.LudwigFeatureDict()

    to_module = torch.nn.Module()

    feature_dict.set("to.", to_module)

    assert feature_dict.keys() == ["to."]
    assert feature_dict.items() == [("to.", to_module)]
    assert feature_dict.get("to.") == to_module


@pytest.mark.parametrize("sequence_type", [list, tuple, np.array])
def test_compute_token_probabilities(sequence_type):
    inputs = sequence_type(
        [
            [0.1, 0.2, 0.7],
            [0.3, 0.4, 0.3],
            [0.6, 0.3, 0.2],
        ]
    )

    token_probabilities = feature_utils.compute_token_probabilities(inputs)
    assert np.allclose(token_probabilities, [0.7, 0.4, 0.6])


def test_compute_sequence_probability():
    inputs = np.array([0.7, 0.4, 0.6])

    sequence_probability = feature_utils.compute_sequence_probability(
        inputs, max_sequence_length=2, return_log_prob=False
    )

    assert np.allclose(sequence_probability, [0.28])  # 0.7 * 0.4
