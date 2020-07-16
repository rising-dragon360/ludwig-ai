#! /usr/bin/env python
# coding=utf-8
# Copyright (c) 2019 Uber Technologies, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import logging
import sys

from ludwig.constants import TEST, TRAINING, VALIDATION, FULL
from ludwig.contrib import contrib_command, contrib_import
from ludwig.globals import set_on_master, is_on_master, LUDWIG_VERSION
from ludwig.predict import full_predict
from ludwig.utils.print_utils import logging_level_registry, print_ludwig

logger = logging.getLogger(__name__)


def cli(sys_argv):
    parser = argparse.ArgumentParser(
        description='This script loads a pretrained model '
                    'and tests its performance by comparing'
                    'its predictions with ground truth.',
        prog='ludwig test',
        usage='%(prog)s [options]'
    )

    # ---------------
    # Data parameters
    # ---------------
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '--data_csv',
        help='input data CSV file. '
             'If it has a split column, it will be used for splitting '
             '(0: train, 1: validation, 2: test), '
             'otherwise the dataset will be randomly split'
    )
    group.add_argument(
        '--data_hdf5',
        help='input data HDF5 file. It is an intermediate preprocess version of'
             ' the input CSV created the first time a CSV file is used in the '
             'same directory with the same name and a hdf5 extension'
    )
    parser.add_argument(
        '--train_set_metadata_json',
        help='input metadata JSON file. It is an intermediate preprocess file '
             'containing the mappings of the input CSV created the first time '
             'a CSV file is used in the same directory with the same name and '
             'a json extension'
    )

    parser.add_argument(
        '-s',
        '--split',
        default=TEST,
        choices=[TRAINING, VALIDATION, TEST, FULL],
        help='the split to test the model on'
    )

    # ----------------
    # Model parameters
    # ----------------
    parser.add_argument(
        '-m',
        '--model_path',
        help='model to load',
        required=True
    )

    # -------------------------
    # Output results parameters
    # -------------------------
    parser.add_argument(
        '-od',
        '--output_directory',
        type=str,
        default='results',
        help='directory that contains the results'
    )
    parser.add_argument(
        '-ssuo',
        '--skip_save_unprocessed_output',
        help='skips saving intermediate NPY output files',
        action='store_true', default=False
    )

    # ------------------
    # Generic parameters
    # ------------------
    parser.add_argument(
        '-bs',
        '--batch_size',
        type=int,
        default=128,
        help='size of batches'
    )

    # ------------------
    # Runtime parameters
    # ------------------
    parser.add_argument(
        '-g',
        '--gpus',
        type=int,
        default=0,
        help='list of gpu to use'
    )
    parser.add_argument(
        '-gml',
        '--gpu_memory_limit',
        type=int,
        default=None,
        help='maximum memory in MB to allocate per GPU device'
    )
    parser.add_argument(
        '-dpt',
        '--disable_parallel_threads',
        action='store_false',
        dest='allow_parallel_threads',
        help='disable TensorFlow from using multithreading for reproducibility'
    )
    parser.add_argument(
        '-uh',
        '--use_horovod',
        action='store_true',
        default=None,
        help='uses horovod for distributed training'
    )
    parser.add_argument(
        '-dbg',
        '--debug',
        action='store_true',
        default=False,
        help='enables debugging mode'
    )
    parser.add_argument(
        '-l',
        '--logging_level',
        default='info',
        help='the level of logging to use',
        choices=['critical', 'error', 'warning', 'info', 'debug', 'notset']
    )

    args = parser.parse_args(sys_argv)
    args.evaluate_performance = True

    logging.getLogger('ludwig').setLevel(
        logging_level_registry[args.logging_level]
    )
    global logger
    logger = logging.getLogger('ludwig.test_performance')

    set_on_master(args.use_horovod)

    if is_on_master():
        print_ludwig('Test', LUDWIG_VERSION)

    full_predict(**vars(args))


if __name__ == '__main__':
    contrib_import()
    contrib_command("test", *sys.argv)
    cli(sys.argv[1:])
