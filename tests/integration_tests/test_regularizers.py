import random
import tempfile

import numpy as np
import pytest
import torch

from ludwig.api import LudwigModel
from ludwig.constants import TRAINER
from ludwig.data.preprocessing import preprocess_for_training
from ludwig.utils.data_utils import read_csv
from tests.integration_tests.utils import (
    binary_feature,
    category_feature,
    date_feature,
    generate_data,
    image_feature,
    LocalTestBackend,
    number_feature,
    sequence_feature,
    set_feature,
)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
BATCH_SIZE = 32
RANDOM_SEED = 42
IMAGE_DIR = tempfile.mkdtemp()


@pytest.mark.parametrize(
    "input_features,output_features",
    [
        (
            [number_feature(num_layers=2, encoder="dense", preprocessing={"normalization": "zscore"})],
            [number_feature()],
        ),
        ([image_feature(IMAGE_DIR, encoder="stacked_cnn")], [number_feature()]),
        ([image_feature(IMAGE_DIR, encoder="resnet")], [category_feature()]),
        (
            [category_feature(representation="dense")],
            [number_feature(decoder="regressor", loss={"type": "mean_squared_error"}, num_fc_layers=5)],
        ),
        ([date_feature()], [binary_feature()]),
        ([sequence_feature(encoder="parallel_cnn", cell_type="gru")], [binary_feature()]),
        ([set_feature()], [set_feature()]),
    ],
)
def test_regularizers(
    input_features,
    output_features,
):

    np.random.seed(RANDOM_SEED)
    torch.manual_seed(RANDOM_SEED)
    random.seed(0)

    data_file = generate_data(input_features, output_features, num_examples=BATCH_SIZE)
    data_df = read_csv(data_file)

    regularizer_losses = []
    for regularization_type in [None, "l1", "l2", "l1_l2"]:

        config = {
            "input_features": input_features,
            "output_features": output_features,
            "combiner": {"type": "concat", "output_size": 14},
            TRAINER: {"epochs": 2, "regularization_type": regularization_type, "regularization_lambda": 0.1},
        }

        backend = LocalTestBackend()
        model = LudwigModel(config, backend=backend)
        processed_data_df, _, _, _ = preprocess_for_training(config, data_df, backend=backend)
        with processed_data_df.initialize_batcher(batch_size=BATCH_SIZE) as batcher:
            batch = batcher.next_batch()

        _, _, _ = model.train(
            training_set=data_df,
            skip_save_processed_input=True,
            skip_save_progress=True,
            skip_save_unprocessed_output=True,
        )

        inputs = {
            i_feat.feature_name: torch.from_numpy(batch[i_feat.proc_column]).to(DEVICE)
            for i_feat in model.model.input_features.values()
        }
        targets = {
            o_feat.feature_name: torch.from_numpy(batch[o_feat.proc_column]).to(DEVICE)
            for o_feat in model.model.output_features.values()
        }
        predictions = model.model((inputs, targets))

        loss, _ = model.model.train_loss(targets, predictions, regularization_type, 0.1)
        regularizer_losses.append(loss)

    # Regularizer_type=None has lowest regularizer loss
    assert min(regularizer_losses) == regularizer_losses[0]

    # l1, l2 and l1_l2 should be greater than zero
    assert torch.all(torch.tensor([t - regularizer_losses[0] > 0.0 for t in regularizer_losses[1:]]))

    # using default setting l1 + l2 == l1_l2 losses
    assert torch.isclose(
        regularizer_losses[1] + regularizer_losses[2] - regularizer_losses[0], regularizer_losses[3], rtol=0.1
    )
