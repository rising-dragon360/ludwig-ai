import os.path
import json
from collections import namedtuple

import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

import pytest

from ludwig.experiment import full_experiment
from ludwig.predict import full_predict

GeneratedData = namedtuple('GeneratedData',
                           'train_df validation_df test_df')

def get_feature_definitions():
    input_features = [
            {'name': 'x', 'type': 'numerical'},
        ]
    output_features = [
        {'name': 'y', 'type': 'numerical', 'loss': {'type': 'mean_squared_error'},
         'num_fc_layers': 5, 'fc_size': 64}
    ]

    return input_features, output_features


@pytest.fixture
def generated_data():
    # function generates simple training data that guarantee convergence
    # within 30 epochs for suitable model definition
    NUMBER_OBSERVATIONS = 500

    # generate data
    np.random.seed(43)
    x = np.array(range(NUMBER_OBSERVATIONS)).reshape(-1, 1)
    y = 2*x + 1 + np.random.normal(size=x.shape[0]).reshape(-1, 1)
    raw_df = pd.DataFrame(np.concatenate((x, y), axis=1), columns=['x', 'y'])

    # create training data
    train, valid_test  = train_test_split(raw_df, train_size=0.7)

    # create validation and test data
    validation, test = train_test_split(valid_test, train_size=0.5)

    return GeneratedData(train, validation, test)

@pytest.mark.parametrize('early_stop', [3, 5])
def test_early_stopping(early_stop, generated_data, tmp_path):

    input_features, output_features = get_feature_definitions()

    model_definition = {
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
    exp_dir_name = full_experiment(
        data_train_df=generated_data.train_df,
        data_validation_df=generated_data.validation_df,
        data_test_df=generated_data.test_df,
        output_directory=str(results_dir),
        model_definition=model_definition,
        skip_save_processed_input=True,
        skip_save_progress=True,
        skip_save_unprocessed_output=True,
        skip_save_model=True,
        skip_save_log=True
    )

    # test existence of required files
    train_stats_fp = os.path.join(exp_dir_name, 'training_statistics.json')
    metadata_fp = os.path.join(exp_dir_name, 'description.json')
    assert os.path.isfile(train_stats_fp)
    assert os.path.isfile(metadata_fp)

    # retrieve results so we can validate early stopping
    with open(train_stats_fp,'r') as f:
        train_stats = json.load(f)
    with open(metadata_fp, 'r') as f:
        metadata = json.load(f)

    # get early stopping value
    early_stop_value = metadata['model_definition']['training']['early_stop']

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

    input_features, output_features = get_feature_definitions()

    model_definition = {
        'input_features': input_features,
        'output_features': output_features,
        'combiner': {'type': 'concat', 'fc_size': 14},
        'training': {'epochs': 10}
    }

    # create sub-directory to store results
    results_dir = tmp_path / 'results'
    results_dir.mkdir()

    # run experiment
    exp_dir_name = full_experiment(
        data_train_df=generated_data.train_df,
        data_validation_df=generated_data.validation_df,
        data_test_df=generated_data.test_df,
        output_directory=str(results_dir),
        model_definition=model_definition,
        skip_save_processed_input=True,
        skip_save_progress=skip_save_progress,
        skip_save_unprocessed_output=True,
        skip_save_model=skip_save_model,
        skip_save_log=True
    )

    #========== Check for required result data sets =============
    if skip_save_model:
        assert not os.path.isfile(
            os.path.join(exp_dir_name, 'model', 'model_weights.index')
        )
    else:
        assert os.path.isfile(
            os.path.join(exp_dir_name, 'model', 'model_weights.index')
        )

    if skip_save_progress:
        assert not os.path.isfile(
            os.path.join(exp_dir_name, 'model', 'model_weights_progress.index')
        )
    else:
        assert os.path.isfile(
            os.path.join(exp_dir_name, 'model', 'model_weights_progress.index')
        )


# work-in-progress
def test_model_save_resume(generated_data, tmp_path):

    input_features, output_features = get_feature_definitions()
    model_definition = {
        'input_features': input_features,
        'output_features': output_features,
        'combiner': {'type': 'concat', 'fc_size': 14},
        'training': {'epochs': 30, 'early_stop': 5}
    }

    # create sub-directory to store results
    results_dir = tmp_path / 'results'
    results_dir.mkdir()

    exp_dir_name = full_experiment(
        model_definition,
        data_train_df=generated_data.train_df,
        data_validation_df=generated_data.validation_df,
        data_test_df=generated_data.test_df,
        output_directory=results_dir
    )

    full_experiment(
        model_definition,
        data_train_df=generated_data.train_df,
        model_resume_path=exp_dir_name
    )

    test_fp = os.path.join(str(tmp_path), 'data_to_predict.csv')
    generated_data.test_df.to_csv(
        test_fp,
        index=False
    )

    full_predict(os.path.join(exp_dir_name, 'model'), data_csv=test_fp)

    y_pred = np.load(os.path.join(exp_dir_name, 'y_predictions.npy'))

    mse = mean_squared_error(y_pred, generated_data.test_df['y'])
