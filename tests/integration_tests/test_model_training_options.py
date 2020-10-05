import json
import os.path
import re
from collections import namedtuple

import numpy as np
import pandas as pd
import pytest
import tensorflow as tf
from sklearn.model_selection import train_test_split

from ludwig.api import LudwigModel
from ludwig.experiment import experiment_cli
from ludwig.modules.optimization_modules import optimizers_registry
from ludwig.utils.data_utils import load_json

RANDOM_SEED = 42
NUMBER_OBSERVATIONS = 500

GeneratedData = namedtuple('GeneratedData',
                           'train_df validation_df test_df')


def get_feature_configs():
    input_features = [
        {'name': 'x', 'type': 'numerical'},
    ]
    output_features = [
        {'name': 'y', 'type': 'numerical',
         'loss': {'type': 'mean_squared_error'},
         'num_fc_layers': 5, 'fc_size': 64}
    ]

    return input_features, output_features


@pytest.fixture(scope='module')
def generated_data():
    # function generates simple training data that guarantee convergence
    # within 30 epochs for suitable config

    # generate data
    np.random.seed(RANDOM_SEED)
    x = np.array(range(NUMBER_OBSERVATIONS)).reshape(-1, 1)
    y = 2 * x + 1 + np.random.normal(size=x.shape[0]).reshape(-1, 1)
    raw_df = pd.DataFrame(np.concatenate((x, y), axis=1), columns=['x', 'y'])

    # create training data
    train, valid_test = train_test_split(raw_df, train_size=0.7)

    # create validation and test data
    validation, test = train_test_split(valid_test, train_size=0.5)

    return GeneratedData(train, validation, test)


@pytest.fixture(scope='module')
def generated_data_for_optimizer():
    # function generates simple training data that guarantee convergence
    # within 30 epochs for suitable config

    # generate data
    np.random.seed(RANDOM_SEED)
    x = np.array(range(NUMBER_OBSERVATIONS)).reshape(-1, 1)
    y = 2 * x + 1 + np.random.normal(size=x.shape[0]).reshape(-1, 1)
    raw_df = pd.DataFrame(np.concatenate((x, y), axis=1), columns=['x', 'y'])
    raw_df['x'] = (raw_df['x'] - raw_df['x'].min()) / \
                  (raw_df['x'].max() - raw_df['x'].min())
    raw_df['y'] = (raw_df['y'] - raw_df['y'].min()) / \
                  (raw_df['y'].max() - raw_df['y'].min())

    # create training data
    train, valid_test = train_test_split(raw_df, train_size=0.7)

    # create validation and test data
    validation, test = train_test_split(valid_test, train_size=0.5)

    return GeneratedData(train, validation, test)


@pytest.mark.parametrize('early_stop', [3, 5])
def test_early_stopping(early_stop, generated_data, tmp_path):
    input_features, output_features = get_feature_configs()

    config = {
        'input_features': input_features,
        'output_features': output_features,
        'combiner': {
            'type': 'concat'
        },
        'training': {
            'epochs': 30,
            'early_stop': early_stop,
            'batch_size': 16
        }
    }

    # create sub-directory to store results
    results_dir = tmp_path / 'results'
    results_dir.mkdir()

    # run experiment
    _, _, _, _, output_dir = experiment_cli(
        training_set=generated_data.train_df,
        validation_set=generated_data.validation_df,
        test_set=generated_data.test_df,
        output_directory=str(results_dir),
        config=config,
        skip_save_processed_input=True,
        skip_save_progress=True,
        skip_save_unprocessed_output=True,
        skip_save_model=True,
        skip_save_log=True
    )

    # test existence of required files
    train_stats_fp = os.path.join(output_dir, 'training_statistics.json')
    metadata_fp = os.path.join(output_dir, 'description.json')
    assert os.path.isfile(train_stats_fp)
    assert os.path.isfile(metadata_fp)

    # retrieve results so we can validate early stopping
    with open(train_stats_fp, 'r') as f:
        train_stats = json.load(f)
    with open(metadata_fp, 'r') as f:
        metadata = json.load(f)

    # get early stopping value
    early_stop_value = metadata['config']['training']['early_stop']

    # retrieve validation losses
    vald_losses = np.array(train_stats['validation']['combined']['loss'])
    last_epoch = vald_losses.shape[0]
    best_epoch = np.argmin(vald_losses)

    # confirm early stopping
    assert (last_epoch - best_epoch - 1) == early_stop_value


@pytest.mark.parametrize('skip_save_progress', [False, True])
@pytest.mark.parametrize('skip_save_model', [False, True])
def test_model_progress_save(
        skip_save_progress,
        skip_save_model,
        generated_data,
        tmp_path
):
    input_features, output_features = get_feature_configs()

    config = {
        'input_features': input_features,
        'output_features': output_features,
        'combiner': {'type': 'concat'},
        'training': {'epochs': 5}
    }

    # create sub-directory to store results
    results_dir = tmp_path / 'results'
    results_dir.mkdir()

    # run experiment
    _, _, _, _, output_dir = experiment_cli(
        training_set=generated_data.train_df,
        validation_set=generated_data.validation_df,
        test_set=generated_data.test_df,
        output_directory=str(results_dir),
        config=config,
        skip_save_processed_input=True,
        skip_save_progress=skip_save_progress,
        skip_save_unprocessed_output=True,
        skip_save_model=skip_save_model,
        skip_save_log=True
    )

    # ========== Check for required result data sets =============
    if skip_save_model:
        model_dir = os.path.join(output_dir, 'model')
        files = [f for f in os.listdir(model_dir) if
                 re.match(r'model_weights', f)]
        assert len(files) == 0
    else:
        model_dir = os.path.join(output_dir, 'model')
        files = [f for f in os.listdir(model_dir) if
                 re.match(r'model_weights', f)]
        # at least one .index and one .data file, but .data may be more
        assert len(files) >= 2
        assert os.path.isfile(
            os.path.join(output_dir, 'model', 'checkpoint'))

    if skip_save_progress:
        assert not os.path.isdir(
            os.path.join(output_dir, 'model', 'training_checkpoints')
        )
    else:
        assert os.path.isdir(
            os.path.join(output_dir, 'model', 'training_checkpoints')
        )


@pytest.mark.parametrize('optimizer', ['sgd', 'adam'])
def test_resume_training(optimizer, generated_data, tmp_path):
    input_features, output_features = get_feature_configs()
    config = {
        'input_features': input_features,
        'output_features': output_features,
        'combiner': {'type': 'concat'},
        'training': {
            'epochs': 2,
            'early_stop': 1000,
            'batch_size': 16,
            'optimizer': {'type': optimizer}
        }
    }

    # create sub-directory to store results
    results_dir = tmp_path / 'results'
    results_dir.mkdir()

    _, _, _, _, output_dir1 = experiment_cli(
        config,
        training_set=generated_data.train_df,
        validation_set=generated_data.validation_df,
        test_set=generated_data.test_df,
    )

    config['training']['epochs'] = 4

    experiment_cli(
        config,
        training_set=generated_data.train_df,
        validation_set=generated_data.validation_df,
        test_set=generated_data.test_df,
        model_resume_path=output_dir1,
    )

    _, _, _, _, output_dir2 = experiment_cli(
        config,
        training_set=generated_data.train_df,
        validation_set=generated_data.validation_df,
        test_set=generated_data.test_df,
    )

    # compare learning curves with and without resuming
    ts1 = load_json(os.path.join(output_dir1, 'training_statistics.json'))
    ts2 = load_json(os.path.join(output_dir2, 'training_statistics.json'))
    print('ts1', ts1)
    print('ts2', ts2)
    assert ts1['training']['combined']['loss'] == ts2['training']['combined'][
        'loss']

    # compare predictions with and without resuming
    y_pred1 = np.load(os.path.join(output_dir1, 'y_predictions.npy'))
    y_pred2 = np.load(os.path.join(output_dir2, 'y_predictions.npy'))
    print('y_pred1', y_pred1)
    print('y_pred2', y_pred2)
    assert np.all(np.isclose(y_pred1, y_pred2))


@pytest.mark.parametrize('optimizer_type', optimizers_registry)
def test_optimizers(optimizer_type, generated_data_for_optimizer, tmp_path):
    input_features, output_features = get_feature_configs()

    config = {
        'input_features': input_features,
        'output_features': output_features,
        'combiner': {
            'type': 'concat'
        },
        'training': {
            'epochs': 5,
            'batch_size': 16,
            'optimizer': {'type': optimizer_type}
        }
    }

    # special handling for adadelta, break out of local minima
    if optimizer_type == 'adadelta':
        config['training']['learning_rate'] = 0.1

    model = LudwigModel(config)

    # create sub-directory to store results
    results_dir = tmp_path / 'results'
    results_dir.mkdir()

    # run experiment
    train_stats, preprocessed_data, output_directory = model.train(
        training_set=generated_data_for_optimizer.train_df,
        output_directory=str(results_dir),
        config=config,
        skip_save_processed_input=True,
        skip_save_progress=True,
        skip_save_unprocessed_output=True,
        skip_save_model=True,
        skip_save_log=True
    )

    # retrieve training losses for first and last epochs
    train_losses = np.array(train_stats['training']['combined']['loss'])
    last_epoch = train_losses.shape[0]

    # ensure train loss for last epoch is less than first epoch
    assert train_losses[last_epoch - 1] < train_losses[0]


def test_regularization(generated_data, tmp_path):
    input_features, output_features = get_feature_configs()

    config = {
        'input_features': input_features,
        'output_features': output_features,
        'combiner': {
            'type': 'concat'
        },
        'training': {
            'epochs': 1,
            'batch_size': 16,
            'regularization_lambda': 1
        }
    }

    # create sub-directory to store results
    results_dir = tmp_path / 'results'
    results_dir.mkdir()

    regularization_losses = []
    for regularizer in [None, 'l1', 'l2', 'l1_l2']:
        tf.keras.backend.clear_session()
        np.random.seed(RANDOM_SEED)
        tf.random.set_seed(RANDOM_SEED)

        # setup regularization parameters
        config['output_features'][0][
            'weights_regularizer'] = regularizer
        config['output_features'][0][
            'bias_regularizer'] = regularizer
        config['output_features'][0][
            'activity_regularizer'] = regularizer

        # run experiment
        _, _, _, _, output_dir = experiment_cli(
            training_set=generated_data.train_df,
            validation_set=generated_data.validation_df,
            test_set=generated_data.test_df,
            output_directory=str(results_dir),
            config=config,
            experiment_name='regularization',
            model_name=str(regularizer),
            skip_save_processed_input=True,
            skip_save_progress=True,
            skip_save_unprocessed_output=True,
            skip_save_model=True,
            skip_save_log=True
        )

        # test existence of required files
        train_stats_fp = os.path.join(output_dir, 'training_statistics.json')
        metadata_fp = os.path.join(output_dir, 'description.json')
        assert os.path.isfile(train_stats_fp)
        assert os.path.isfile(metadata_fp)

        # retrieve results so we can compare training loss with regularization
        with open(train_stats_fp, 'r') as f:
            train_stats = json.load(f)

        # retrieve training losses for all epochs
        train_losses = np.array(train_stats['training']['combined']['loss'])
        regularization_losses.append(train_losses[0])

    # create a set of losses
    regularization_losses_set = set(regularization_losses)

    # ensure all losses obtained with the different methods are different
    assert len(regularization_losses) == len(regularization_losses_set)
