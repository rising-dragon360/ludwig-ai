# -*- coding: utf-8 -*-
# Copyright (c) 2020 Uber Technologies, Inc.
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

import argparse
import json
import os
import shutil
import sys

import horovod.tensorflow as hvd

PATH_HERE = os.path.abspath(os.path.dirname(__file__))
PATH_ROOT = os.path.join(PATH_HERE, '..', '..', '..')
sys.path.insert(0, os.path.abspath(PATH_ROOT))

import ludwig.globals

from ludwig.api import LudwigModel

parser = argparse.ArgumentParser()
parser.add_argument('--rel-path', required=True)
parser.add_argument('--input-features', required=True)
parser.add_argument('--output-features', required=True)
parser.add_argument('--ludwig-kwargs', required=True)


def run_api_experiment(input_features, output_features, data_csv, **kwargs):
    model_definition = {
        'input_features': input_features,
        'output_features': output_features,
        'combiner': {'type': 'concat', 'fc_size': 14},
        'training': {'epochs': 2}
    }

    model = LudwigModel(model_definition)

    try:
        # Training with csv
        model.train(
            data_csv=data_csv,
            **kwargs
        )

        model.predict(data_csv=data_csv)
    finally:
        if model.exp_dir_name:
            shutil.rmtree(model.exp_dir_name, ignore_errors=True)


def test_horovod_intent_classification(rel_path, input_features,
                                       output_features, **kwargs):
    run_api_experiment(input_features, output_features, data_csv=rel_path,
                       **kwargs)

    # Horovod should be initialized following training. If not, this will raise an exception.
    assert hvd.size() == 2
    assert ludwig.globals.ON_MASTER == (hvd.rank() == 0)


if __name__ == "__main__":
    args = parser.parse_args()
    test_horovod_intent_classification(args.rel_path,
                                       json.loads(args.input_features),
                                       json.loads(args.output_features),
                                       **json.loads(args.ludwig_kwargs))
