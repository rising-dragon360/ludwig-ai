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

from ludwig.backend.base import Backend, LocalBackend
from ludwig.utils.horovod_utils import has_horovodrun


LOCAL_BACKEND = LocalBackend()

LOCAL = 'local'
HOROVOD = 'horovod'

ALL_BACKENDS = [LOCAL, HOROVOD]


def get_local_backend():
    return LOCAL_BACKEND


def create_horovod_backend():
    from ludwig.backend.horovod import HorovodBackend
    return HorovodBackend()


backend_registry = {
    LOCAL: get_local_backend,
    HOROVOD: create_horovod_backend,
    None: get_local_backend,
}


def create_backend(backend):
    if isinstance(backend, Backend):
        return backend

    if backend is None and has_horovodrun():
        backend = HOROVOD

    return backend_registry[backend]()


def initialize_backend(backend):
    backend = create_backend(backend)
    backend.initialize()
    return backend
