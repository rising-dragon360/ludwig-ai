#! /usr/bin/env python
# coding=utf-8
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

import contextlib
from abc import ABC, abstractmethod


class Dataset(ABC):
    @abstractmethod
    def __len__(self):
        raise NotImplementedError()

    # TODO(travis): will not need shuffle_buffer_size after removing Petastorm
    @contextlib.contextmanager
    @abstractmethod
    def initialize_batcher(self, batch_size=128,
                           should_shuffle=True,
                           shuffle_buffer_size=None,
                           seed=0,
                           ignore_last=False,
                           horovod=None):
        raise NotImplementedError()
