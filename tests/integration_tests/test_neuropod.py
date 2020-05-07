# -*- coding: utf-8 -*-
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
import os
import shutil

import numpy as np
import pandas as pd

from ludwig.api import LudwigModel
from ludwig.neuropod import build_neuropod
from tests.integration_tests.utils import category_feature
from tests.integration_tests.utils import generate_data
from tests.integration_tests.utils import sequence_feature


def test_neuropod(csv_filename):
    #######
    # Setup
    #######
    dir_path = os.path.dirname(csv_filename)

    # Single sequence input, single category output
    sf = sequence_feature()
    sf['encoder'] = 'parallel_cnn'
    input_features = [sf]
    input_feature_name = input_features[0]['name']

    output_features = [category_feature(vocab_size=2)]
    output_feature_name = output_features[0]['name']

    # Generate test data
    data_csv_path = generate_data(input_features, output_features, csv_filename)

    #############
    # Train model
    #############
    model_definition = {
        'input_features': input_features,
        'output_features': output_features,
        'training': {'epochs': 2}
    }
    ludwig_model = LudwigModel(model_definition)
    ludwig_model.train(
        data_csv=data_csv_path,
        skip_save_training_description=True,
        skip_save_training_statistics=True,
        skip_save_model=True,
        skip_save_progress=True,
        skip_save_log=True,
        skip_save_processed_input=True,
    )
    original_predictions_df = ludwig_model.predict(data_csv=data_csv_path)

    ###################
    # save Ludwig model
    ###################
    ludwigmodel_path = os.path.join(dir_path, 'ludwigmodel')
    shutil.rmtree(ludwigmodel_path, ignore_errors=True)
    ludwig_model.save(ludwigmodel_path)

    ################
    # build neuropod
    ################
    neuropod_path = os.path.join(dir_path, 'neuropod')
    build_neuropod(ludwigmodel_path, neuropod_path=neuropod_path)

    ########################
    # predict using neuropod
    ########################
    data_df = pd.read_csv(data_csv_path)
    if_vals = data_df[input_feature_name].tolist()

    from neuropod.loader import load_neuropod
    neuropod_model = load_neuropod(neuropod_path)
    preds = neuropod_model.infer(
        {input_feature_name: np.array(if_vals, dtype='str')}
    )

    #########
    # cleanup
    #########
    for path in [ludwigmodel_path, neuropod_path]:
        if os.path.exists(path):
            if os.path.isfile(path):
                os.remove(path)
            else:
                shutil.rmtree(path, ignore_errors=True)

    ########
    # checks
    ########
    neuropod_pred = preds[output_feature_name + "_predictions"].tolist()
    neuropod_prob = preds[output_feature_name + "_probability"].tolist()

    # print(neuropod_pred)
    # print(neuropod_prob)

    original_pred = original_predictions_df[
        output_feature_name + "_predictions"].tolist()
    original_prob = original_predictions_df[
        output_feature_name + "_probability"].tolist()

    # print(original_pred)
    # print(original_prob)

    assert neuropod_pred == original_pred
    assert np.isclose(neuropod_prob, original_prob).all()
