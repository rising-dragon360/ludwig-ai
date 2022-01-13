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
import re
from typing import Dict, List, Tuple

import numpy as np
import torch

from ludwig.constants import NAME, PREPROCESSING, SEQUENCE, TEXT, TIMESERIES
from ludwig.utils.misc_utils import hash_dict
from ludwig.utils.strings_utils import tokenizer_registry, UNKNOWN_SYMBOL

SEQUENCE_TYPES = {SEQUENCE, TEXT, TIMESERIES}
FEATURE_NAME_SUFFIX = "__ludwig"
FEATURE_NAME_SUFFIX_LENGTH = len(FEATURE_NAME_SUFFIX)


def should_regularize(regularize_layers):
    regularize = False
    if isinstance(regularize_layers, bool) and regularize_layers:
        regularize = True
    elif isinstance(regularize_layers, (list, tuple)) and regularize_layers and regularize_layers[-1]:
        regularize = True
    return regularize


def set_str_to_idx(set_string, feature_dict, tokenizer_name):
    try:
        tokenizer = tokenizer_registry[tokenizer_name]()
    except ValueError:
        raise Exception(f"Tokenizer {tokenizer_name} not supported")

    out = [feature_dict.get(item, feature_dict[UNKNOWN_SYMBOL]) for item in tokenizer(set_string)]

    return np.array(out, dtype=np.int32)


def sanitize(name):
    """Replaces invalid id characters."""
    return re.sub("\\W|^(?=\\d)", "_", name)


def compute_feature_hash(feature: dict) -> str:
    preproc_hash = hash_dict(feature.get(PREPROCESSING, {}))
    return sanitize(feature[NAME]) + "_" + preproc_hash.decode("ascii")


def get_input_size_with_dependencies(
    combiner_output_size: int, dependencies: List[str], other_output_features  # Dict[str, "OutputFeature"]
):
    """Returns the input size for the first layer of this output feature's FC stack, accounting for dependencies on
    other output features.

    In the forward pass, the hidden states of any dependent output features get concatenated with the combiner's output.
    If this output feature depends on other output features, then the input size for this feature's FCStack is the sum
    of the output sizes of other output features + the combiner's output size.
    """
    input_size_with_dependencies = combiner_output_size
    for feature_name in dependencies:
        if other_output_features[feature_name].num_fc_layers:
            input_size_with_dependencies += other_output_features[feature_name].fc_stack.output_shape[-1]
        else:
            # 0-layer FCStack. Use the output feature's input size.
            input_size_with_dependencies += other_output_features[feature_name].input_size
    return input_size_with_dependencies


class LudwigFeatureDict(torch.nn.Module):
    """Torch ModuleDict wrapper that permits keys with any name.

    Torch's ModuleDict implementation doesn't allow certain keys to be used if they conflict with existing class
    attributes, e.g.

    > torch.nn.ModuleDict({'type': torch.nn.Module()})  # Raises KeyError.

    This class is a simple wrapper around torch's ModuleDict that mitigates possible conflicts by using a key-suffixing
    protocol.

    This is also tracked in Pytorch: https://github.com/pytorch/pytorch/issues/71203.
    """

    def __init__(self):
        super().__init__()
        self.module_dict = torch.nn.ModuleDict()

    def __getitem__(self, key) -> torch.nn.Module:
        return self.module_dict[key + FEATURE_NAME_SUFFIX]

    def __setitem__(self, key: str, module: torch.nn.Module) -> None:
        self.module_dict[key + FEATURE_NAME_SUFFIX] = module

    def __len__(self) -> int:
        return len(self.module_dict)

    def __next__(self) -> None:
        return next(iter(self))

    def __iter__(self) -> None:
        return iter(self.keys())

    def keys(self) -> List[str]:
        return [feature_name[:-FEATURE_NAME_SUFFIX_LENGTH] for feature_name in self.module_dict.keys()]

    def values(self) -> List[torch.nn.Module]:
        return [module for _, module in self.module_dict.items()]

    def items(self) -> List[Tuple[str, torch.nn.Module]]:
        return [
            (feature_name[:-FEATURE_NAME_SUFFIX_LENGTH], module) for feature_name, module in self.module_dict.items()
        ]

    def update(self, modules: Dict[str, torch.nn.Module]) -> None:
        for feature_name, module in modules.items():
            self[feature_name] = module
