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
import logging
from abc import ABC

import torch

from ludwig.encoders.base import Encoder
from ludwig.utils.registry import Registry, register_default
from ludwig.encoders.generic_encoders import DenseEncoder


logger = logging.getLogger(__name__)


ENCODER_REGISTRY = Registry({
    'dense': DenseEncoder
})


class BinaryEncoder(Encoder, ABC):
    @classmethod
    def register(cls, name):
        ENCODER_REGISTRY[name] = cls


@register_default(name='passthrough')
class BinaryPassthroughEncoder(BinaryEncoder):

    def __init__(
            self,
            **kwargs
    ):
        super().__init__()
        logger.debug(' {}'.format(self.name))

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        """
            :param inputs: The inputs fed into the encoder.
                   Shape: [batch x 1], type torch.float32
        """
        if inputs.dtype == torch.bool:
            inputs = inputs.to(torch.float32)

        return inputs

    @property
    def output_shape(self) -> torch.Size:
        return torch.Size([1])

    @property
    def input_shape(self) -> torch.Size:
        return torch.Size([1])
