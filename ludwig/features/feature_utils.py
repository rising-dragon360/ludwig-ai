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
import numpy as np

from ludwig.constants import SEQUENCE
from ludwig.constants import TEXT
from ludwig.constants import TIMESERIES
from ludwig.utils.strings_utils import UNKNOWN_SYMBOL
from ludwig.utils.strings_utils import tokenizer_registry

SEQUENCE_TYPES = {SEQUENCE, TEXT, TIMESERIES}


def should_regularize(regularize_layers):
    regularize = False
    if isinstance(regularize_layers, bool) and regularize_layers:
        regularize = True
    elif (isinstance(regularize_layers, (list, tuple))
          and regularize_layers and regularize_layers[-1]):
        regularize = True
    return regularize


def set_str_to_idx(set_string, feature_dict, format_func):
    try:
        tokenizer = tokenizer_registry[format_func]()
    except ValueError:
        raise Exception('Format {} not supported'.format(format_func))

    out = [feature_dict.get(item, feature_dict[UNKNOWN_SYMBOL]) for item in
           tokenizer(set_string)]

    return np.array(out, dtype=np.int32)
