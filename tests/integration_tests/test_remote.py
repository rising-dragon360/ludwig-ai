import os
import shutil
import tempfile

import pytest
import yaml

from ludwig.api import LudwigModel
from ludwig.backend import initialize_backend
from tests.integration_tests.utils import sequence_feature, category_feature, generate_data


@pytest.mark.parametrize('cache_format', ['hdf5', 'parquet', 'tfrecord'])
@pytest.mark.parametrize('fs_protocol', ['file'])
def test_remote_training_set(tmpdir, fs_protocol, cache_format):
    with tempfile.TemporaryDirectory() as outdir:
        output_directory = f'{fs_protocol}://{outdir}'

        input_features = [sequence_feature(reduce_output='sum')]
        output_features = [category_feature(vocab_size=2, reduce_input='sum')]

        csv_filename = os.path.join(tmpdir, 'training.csv')
        data_csv = generate_data(input_features, output_features, csv_filename)
        val_csv = shutil.copyfile(data_csv,
                                  os.path.join(tmpdir, 'validation.csv'))
        test_csv = shutil.copyfile(data_csv, os.path.join(tmpdir, 'test.csv'))

        data_csv = f'{fs_protocol}://{os.path.abspath(data_csv)}'
        val_csv = f'{fs_protocol}://{os.path.abspath(val_csv)}'
        test_csv = f'{fs_protocol}://{os.path.abspath(test_csv)}'

        config = {
            'input_features': input_features,
            'output_features': output_features,
            'combiner': {'type': 'concat', 'fc_size': 14},
            'training': {'epochs': 2},
        }

        config_path = os.path.join(tmpdir, 'config.yaml')
        with open(config_path, 'w') as f:
            yaml.dump(config, f)
        config_path = f'{fs_protocol}://{config_path}'

        backend_config = {
            'type': 'local',
            'cache_format': cache_format
        }
        backend = initialize_backend(backend_config)

        model = LudwigModel(config_path, backend=backend)
        _, _, output_directory = model.train(
            training_set=data_csv,
            validation_set=val_csv,
            test_set=test_csv,
            output_directory=output_directory
        )
        model.predict(dataset=test_csv,
                      output_directory=output_directory)

        # Train again, this time the cache will be used
        # Resume from the remote output directory
        model.train(training_set=data_csv,
                    validation_set=val_csv,
                    test_set=test_csv,
                    model_resume_path=output_directory)
