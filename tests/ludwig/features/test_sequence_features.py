from typing import Tuple, List, Union
import pytest

import numpy as np

import torch

from ludwig.features.sequence_feature import SequenceInputFeature
from ludwig.features.text_feature import TextInputFeature
from tests.integration_tests.utils import sequence_feature, set_feature
from tests.integration_tests.utils import ENCODERS

BATCH_SIZE = 8
SEQ_SIZE = 6
VOCAB_SIZE = 64


@pytest.fixture(scope='module')
def input_sequence() -> Tuple[torch.Tensor, List]:
    # generates a realistic looking synthetic sequence tensor, i.e.
    # each sequence will have non-zero tokens at the beginning with
    # trailing zero tokens, including a max length token with a single
    # zero token at the end.  Example:
    # [
    #   [3, 5, 6, 0, 0, 0],
    #   [10, 11, 12, 13, 14, 0],   # max length sequence
    #   [32, 0, 0, 0, 0, 0]        # minimum length sequence
    # ]
    input_tensor = torch.zeros([BATCH_SIZE, SEQ_SIZE], dtype=torch.int32)
    sequence_lengths = np.random.randint(1, SEQ_SIZE, size=BATCH_SIZE)
    for i in range(input_tensor.shape[0]):
        input_tensor[i, :sequence_lengths[i]] = torch.tensor(
            np.random.randint(2, VOCAB_SIZE, size=sequence_lengths[i]))

    # emulate idx2str structure
    idx2str = ['<PAD>', '<UNK>'] + [str(i) for i in range(2, VOCAB_SIZE)]

    return input_tensor, idx2str


@pytest.mark.parametrize('encoder', ENCODERS)
@pytest.mark.parametrize(
    'sequence_type',
    [SequenceInputFeature, TextInputFeature]
)
def test_sequence_input_feature(
        input_sequence: tuple,
        encoder: str,
        sequence_type: Union[SequenceInputFeature]
) -> None:
    # test assumes "sequence data" has been tokenized and converted to
    # numeric representation.  Focus of this test is primarily on
    # integration with encoder with correctly sized encoder tensor and
    # required properties are present

    input_sequence, idx2str = input_sequence

    # setup input sequence feature definition
    # use sequence_feature() to generate baseline
    # sequence definition and then augment with
    # pre-processing metadata parameters
    input_feature_defn = sequence_feature(
        encoder=encoder,
        max_len=SEQ_SIZE,
        # augment with emulated pre-processing metadata
        max_sequence_length=SEQ_SIZE,
        vocab=idx2str
    )

    # create sequence input feature object
    input_feature_obj = sequence_type(input_feature_defn)

    # confirm dtype property
    assert input_feature_obj.input_dtype == torch.int32

    # confirm input_shape property
    assert input_feature_obj.input_shape == (SEQ_SIZE,)

    # confirm output_shape property default output shape
    # from sequence_feature() function
    encoder_output = input_feature_obj(input_sequence)
    assert encoder_output['encoder_output'].shape == \
           (BATCH_SIZE, *input_feature_obj.output_shape)


# todo: add unit test for sequence output feature
def test_sequence_output_feature():
    pass
