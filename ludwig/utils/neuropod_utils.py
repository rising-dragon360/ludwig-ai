import argparse
import logging
import os
import shutil

import numpy as np

from ludwig import __file__ as ludwig_path
from ludwig.api import LudwigModel
from ludwig.constants import CATEGORY, NUMERICAL, BINARY, SEQUENCE, TEXT, SET, \
    VECTOR, PREDICTIONS, PROBABILITIES, PROBABILITY
from ludwig.globals import MODEL_HYPERPARAMETERS_FILE_NAME, \
    TRAIN_SET_METADATA_FILE_NAME, MODEL_WEIGHTS_FILE_NAME, LUDWIG_VERSION
from ludwig.utils.data_utils import load_json
from ludwig.utils.print_utils import logging_level_registry, print_ludwig

logger = logging.getLogger(__name__)


class LudwigNeuropodModelWrapper:
    def __init__(self, data_root):
        self.ludwig_model = LudwigModel.load(data_root)

    def __call__(self, **kwargs):
        data_dict = kwargs
        for key in data_dict:
            data_dict[key] = np.squeeze(data_dict[key], axis=1)
        predicted = self.ludwig_model.predict(
            data_dict=data_dict, return_type=dict
        )
        # print(predicted, file=sys.stderr)
        return postprocess_for_neuropod(
            predicted, self.ludwig_model.model_definition
        )


def get_model(data_root):
    return LudwigNeuropodModelWrapper(data_root)


def postprocess_for_neuropod(predicted, model_definition):
    postprocessed = {}
    for output_feature in model_definition['output_features']:
        feature_name = output_feature['name']
        feature_type = output_feature['type']
        if feature_type == BINARY:
            postprocessed[feature_name + "_predictions"] = \
                np.expand_dims(
                    predicted[feature_name][PREDICTIONS].astype('str'), 1)
            postprocessed[feature_name + "_probabilities"] = \
                np.expand_dims(
                    predicted[feature_name][PROBABILITIES].astype('float64'),
                    1)
        elif feature_type == NUMERICAL:
            postprocessed[feature_name + "_predictions"] = \
                np.expand_dims(
                    predicted[feature_name][PREDICTIONS].astype('float64'),
                    1)
        elif feature_type == CATEGORY:
            postprocessed[feature_name + "_predictions"] = np.expand_dims(
                np.array(predicted[feature_name][PREDICTIONS], dtype='str'), 1
            )
            postprocessed[feature_name + "_probability"] = \
                np.expand_dims(
                    predicted[feature_name][PROBABILITY].astype('float64'),
                    1)
            postprocessed[feature_name + "_probabilities"] = \
                predicted[feature_name][PROBABILITIES].astype('float64')
        elif feature_type == SEQUENCE:
            predictions = list(map(
                lambda x: ' '.join(x),
                predicted[feature_name][PREDICTIONS]
            ))
            postprocessed[feature_name + "_predictions"] = np.expand_dims(
                np.array(predictions, dtype='str'), 1
            )
        elif feature_type == TEXT:
            predictions = list(map(
                lambda x: ' '.join(x),
                predicted[feature_name][PREDICTIONS]
            ))
            postprocessed[feature_name + "_predictions"] = np.expand_dims(
                np.array(predictions, dtype='str'), 1
            )
        elif feature_type == SET:
            predictions = list(map(
                lambda x: ' '.join(x),
                predicted[feature_name][PREDICTIONS]
            ))
            postprocessed[feature_name + "_predictions"] = np.expand_dims(
                np.array(predictions, dtype='str'), 1
            )
            probability = list(map(
                lambda x: ' '.join([str(e) for e in x]),
                predicted[feature_name]['probability']
            ))
            postprocessed[feature_name + "_probability"] = np.expand_dims(
                np.array(probability, dtype='str'), 1
            )
            postprocessed[feature_name + "_probabilities"] = \
                predicted[feature_name][PROBABILITIES].astype('float64')
        elif feature_type == VECTOR:
            postprocessed[feature_name + "_predictions"] = \
                predicted[feature_name][PREDICTIONS].astype('float64')
        else:
            postprocessed[feature_name + "_predictions"] = np.expand_dims(
                np.array(predicted[feature_name][PREDICTIONS], dtype='str'), 1
            )
    # print(postprocessed, file=sys.stderr)
    return postprocessed


def export_neuropod(
        ludwig_model_path,
        neuropod_path,
        neuropod_model_name="ludwig_model",
):
    try:
        from neuropod.backends.python.packager import create_python_neuropod
    except ImportError:
        raise RuntimeError(
            'The "neuropod" package is not installed in your environment.'
        )

    data_paths = [
        {
            "path": os.path.join(
                ludwig_model_path, MODEL_HYPERPARAMETERS_FILE_NAME
            ),
            "packaged_name": MODEL_HYPERPARAMETERS_FILE_NAME
        },
        {
            "path": os.path.join(
                ludwig_model_path, TRAIN_SET_METADATA_FILE_NAME
            ),
            "packaged_name": TRAIN_SET_METADATA_FILE_NAME
        },
        {
            "path": os.path.join(
                ludwig_model_path, 'checkpoint'
            ),
            "packaged_name": 'checkpoint'
        },
    ]
    for filename in os.listdir(ludwig_model_path):
        if MODEL_WEIGHTS_FILE_NAME in filename:
            data_paths.append(
                {
                    "path": os.path.join(
                        ludwig_model_path, filename
                    ),
                    "packaged_name": filename
                }
            )

    logger.debug('data_paths: {}'.format(data_paths))

    ludwig_model_definition = load_json(
        os.path.join(
            ludwig_model_path,
            MODEL_HYPERPARAMETERS_FILE_NAME
        )
    )
    training_set_metadata = load_json(
        os.path.join(
            ludwig_model_path,
            TRAIN_SET_METADATA_FILE_NAME
        )
    )

    input_spec = []
    for feature in ludwig_model_definition['input_features']:
        input_spec.append({
            "name": feature['name'],
            "dtype": "str",
            "shape": (None, 1)
        })
    logger.debug('input_spec: {}'.format(input_spec))

    output_spec = []
    for feature in ludwig_model_definition['output_features']:
        feature_type = feature['type']
        feature_name = feature['name']
        if feature_type == BINARY:
            output_spec.append({
                "name": feature['name'] + '_predictions',
                "dtype": "str",
                "shape": (None, 1)
            })
            output_spec.append({
                "name": feature['name'] + '_probabilities',
                "dtype": "float64",
                "shape": (None, 1)
            })
        elif feature_type == NUMERICAL:
            output_spec.append({
                "name": feature['name'] + '_predictions',
                "dtype": "float64",
                "shape": (None, 1)
            })
        elif feature_type == CATEGORY:
            output_spec.append({
                "name": feature['name'] + '_predictions',
                "dtype": "str",
                "shape": (None, 1)
            })
            output_spec.append({
                "name": feature['name'] + '_probability',
                "dtype": "float64",
                "shape": (None, 1)
            })
            output_spec.append({
                "name": feature['name'] + '_probabilities',
                "dtype": "float64",
                "shape": (
                    None, training_set_metadata[feature_name]['vocab_size']
                )
            })
        elif feature_type == SEQUENCE:
            output_spec.append({
                "name": feature['name'] + '_predictions',
                "dtype": "str",
                "shape": (None, 1)
            })
        elif feature_type == TEXT:
            output_spec.append({
                "name": feature['name'] + '_predictions',
                "dtype": "str",
                "shape": (None, 1)
            })
        elif feature_type == SET:
            output_spec.append({
                "name": feature['name'] + '_predictions',
                "dtype": "str",
                "shape": (None, 1)
            })
            output_spec.append({
                "name": feature['name'] + '_probability',
                "dtype": "str",
                "shape": (None, 1)
            })
            output_spec.append({
                "name": feature['name'] + '_probabilities',
                "dtype": "float64",
                "shape": (
                    None, training_set_metadata[feature_name]['vocab_size']
                )
            })
        elif feature_type == VECTOR:
            output_spec.append({
                "name": feature['name'] + '_predictions',
                "dtype": "float64",
                "shape": (
                    None, training_set_metadata[feature_name]['vector_size']
                )
            })
        else:
            output_spec.append({
                "name": feature['name'] + '_predictions',
                "dtype": "str",
                "shape": (None, 1)
            })
    logger.debug('output_spec: {}'.format(output_spec))

    if os.path.exists(neuropod_path):
        if os.path.isfile(neuropod_path):
            logger.warning('Removing file: {}'.format(neuropod_path))
            os.remove(neuropod_path)
        else:
            logger.warning('Removing directory: {}'.format(neuropod_path))
            shutil.rmtree(neuropod_path, ignore_errors=True)

    from pathlib import Path
    path = Path(ludwig_path)
    logger.debug('python_root: {}'.format(path.parent.parent))

    create_python_neuropod(
        neuropod_path=neuropod_path,
        model_name=neuropod_model_name,
        data_paths=data_paths,
        code_path_spec=[{
            "python_root": path.parent.parent,
            "dirs_to_package": [
                "ludwig"  # Package everything in the python_root
            ],
        }],
        entrypoint_package="ludwig.utils.neuropod_utils",
        entrypoint="get_model",
        skip_virtualenv=True,
        input_spec=input_spec,
        output_spec=output_spec
    )
    logger.info('Neuropod saved to: {}'.format(neuropod_path))


def cli():
    parser = argparse.ArgumentParser(
        description='This script exports a Ludwig model in the Neuropod format'
    )

    # ----------------
    # Model parameters
    # ----------------
    parser.add_argument(
        '-m',
        '--ludwig_model_path',
        help='path to the Ludwig model to export',
        required=True
    )

    parser.add_argument(
        '-l',
        '--logging_level',
        default='info',
        help='the level of logging to use',
        choices=['critical', 'error', 'warning', 'info', 'debug', 'notset']
    )

    # -------------------
    # Neuropod parameters
    # -------------------
    parser.add_argument(
        '-n',
        '--neuropod_path',
        help='path of the output Neuropod package file',
        required=True
    )
    parser.add_argument(
        '-nm',
        '--neuropod_model_name',
        help='path of the output Neuropod package file',
        default='ludwig_model'
    )

    args = parser.parse_args()

    logging.getLogger('ludwig').setLevel(
        logging_level_registry[args.logging_level]
    )
    global logger
    logger = logging.getLogger('ludwig.serve')

    print_ludwig('Export Neuropod', LUDWIG_VERSION)

    export_neuropod(
        args.ludwig_model_path,
        args.neuropod_path,
        args.neuropod_model_name,
    )


if __name__ == '__main__':
    cli()
