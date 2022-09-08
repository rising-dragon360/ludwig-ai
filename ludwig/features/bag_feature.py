#! /usr/bin/env python
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
import logging
from collections import Counter
from typing import Any, Dict, Union

import numpy as np
import torch

from ludwig.constants import BAG, COLUMN, ENCODER, NAME, PROC_COLUMN, TIED, TYPE
from ludwig.features.base_feature import BaseFeatureMixin, InputFeature
from ludwig.features.feature_utils import set_str_to_idx
from ludwig.features.set_feature import _SetPreprocessing
from ludwig.schema.features.bag_feature import BagInputFeatureConfig
from ludwig.schema.features.utils import register_input_feature
from ludwig.utils.misc_utils import set_default_value, set_default_values
from ludwig.utils.strings_utils import create_vocabulary

logger = logging.getLogger(__name__)


class BagFeatureMixin(BaseFeatureMixin):
    @staticmethod
    def type():
        return BAG

    @staticmethod
    def preprocessing_defaults():
        return BagInputFeatureConfig().preprocessing.to_dict()

    @staticmethod
    def cast_column(column, backend):
        return column.astype(str)

    @staticmethod
    def get_feature_meta(column, preprocessing_parameters, backend):
        idx2str, str2idx, str2freq, max_size, _, _, _, _ = create_vocabulary(
            column,
            preprocessing_parameters["tokenizer"],
            num_most_frequent=preprocessing_parameters["most_common"],
            lowercase=preprocessing_parameters["lowercase"],
            processor=backend.df_engine,
        )
        return {
            "idx2str": idx2str,
            "str2idx": str2idx,
            "str2freq": str2freq,
            "vocab_size": len(str2idx),
            "max_set_size": max_size,
        }

    @staticmethod
    def feature_data(column, metadata, preprocessing_parameters, backend):
        def to_vector(set_str):
            bag_vector = np.zeros((len(metadata["str2idx"]),), dtype=np.float32)
            col_counter = Counter(set_str_to_idx(set_str, metadata["str2idx"], preprocessing_parameters["tokenizer"]))

            bag_vector[list(col_counter.keys())] = list(col_counter.values())
            return bag_vector

        return backend.df_engine.map_objects(column, to_vector)

    @staticmethod
    def add_feature_data(
        feature_config, input_df, proc_df, metadata, preprocessing_parameters, backend, skip_save_processed_input
    ):
        proc_df[feature_config[PROC_COLUMN]] = BagFeatureMixin.feature_data(
            input_df[feature_config[COLUMN]],
            metadata[feature_config[NAME]],
            preprocessing_parameters,
            backend,
        )
        return proc_df


@register_input_feature(BAG)
class BagInputFeature(BagFeatureMixin, InputFeature):
    def __init__(self, input_feature_config: Union[BagInputFeatureConfig, Dict], encoder_obj=None, **kwargs):
        input_feature_config = self.load_config(input_feature_config)
        super().__init__(input_feature_config, **kwargs)

        if encoder_obj:
            self.encoder_obj = encoder_obj
        else:
            self.encoder_obj = self.initialize_encoder(input_feature_config.encoder)

    def forward(self, inputs):
        assert isinstance(inputs, torch.Tensor)
        # assert inputs.dtype == tf.bool # this fails

        encoder_output = self.encoder_obj(inputs)

        return {"encoder_output": encoder_output}

    @property
    def input_shape(self) -> torch.Size:
        return torch.Size([len(self.encoder_obj.config.vocab)])

    @property
    def output_shape(self) -> torch.Size:
        return self.encoder_obj.output_shape

    @staticmethod
    def update_config_with_metadata(input_feature, feature_metadata, *args, **kwargs):
        input_feature[ENCODER]["vocab"] = feature_metadata["idx2str"]

    @staticmethod
    def populate_defaults(input_feature):
        defaults = BagInputFeatureConfig()
        set_default_value(input_feature, TIED, defaults.tied)
        set_default_values(input_feature, {ENCODER: {TYPE: defaults.encoder.type}})

    @staticmethod
    def get_schema_cls():
        return BagInputFeatureConfig

    @staticmethod
    def create_preproc_module(metadata: Dict[str, Any]) -> torch.nn.Module:
        return _SetPreprocessing(metadata, is_bag=True)
