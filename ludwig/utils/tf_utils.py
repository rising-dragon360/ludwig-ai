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
import multiprocessing
import warnings

import tensorflow as tf
from tensorflow.python.client import device_lib

_TF_INIT_PARAMS = None


def sequence_length_3D(sequence):
    used = tf.sign(tf.reduce_max(tf.abs(sequence), 2))
    length = tf.reduce_sum(used, 1)
    length = tf.cast(length, tf.int32)
    return length


def sequence_length_2D(sequence):
    used = tf.sign(tf.abs(sequence))
    length = tf.reduce_sum(used, 1)
    length = tf.cast(length, tf.int32)
    return length


# Convert a dense matrix into a sparse matrix (for e.g. edit_distance)
def to_sparse(tensor, lengths, max_length):
    mask = tf.sequence_mask(lengths, max_length)
    indices = tf.cast(tf.where(tf.equal(mask, True)), tf.int64)
    values = tf.cast(tf.boolean_mask(tensor, mask), tf.int32)
    shape = tf.cast(tf.shape(tensor), tf.int64)
    return tf.SparseTensor(indices, values, shape)


def initialize_tensorflow(gpus=None,
                          gpu_memory_limit=None,
                          allow_parallel_threads=True,
                          horovod=None):
    global _TF_INIT_PARAMS

    use_horovod = horovod is not None
    param_tuple = (gpus, gpu_memory_limit, allow_parallel_threads, use_horovod)
    if _TF_INIT_PARAMS is not None:
        if _TF_INIT_PARAMS != param_tuple:
            warnings.warn(
                'TensorFlow has already been initialized. Changes to `gpus`, '
                '`gpu_memory_limit`, and `allow_parallel_threads` will be ignored. '
                'Start a new Python process to modify these values.')
        return

    # For reproducivility / determinism, set parallel threads to 1.
    # For performance, set to 0 to allow TensorFlow to select the best value automatically.
    tf.config.threading.set_intra_op_parallelism_threads(
        0 if allow_parallel_threads else 1)
    tf.config.threading.set_inter_op_parallelism_threads(
        0 if allow_parallel_threads else 1)

    if horovod is not None and gpus is None:
        gpus = [horovod.local_rank()]

    if isinstance(gpus, int):
        gpus = [gpus]
    elif isinstance(gpus, str):
        gpus = gpus.strip()
        gpus = [int(g) for g in gpus.split(",")]

    if gpus is not None:
        gpu_devices = tf.config.list_physical_devices('GPU')
        for gpu in gpu_devices:
            tf.config.experimental.set_memory_growth(gpu, True)
            if gpu_memory_limit is not None:
                tf.config.set_logical_device_configuration(
                    gpu,
                    [tf.config.LogicalDeviceConfiguration(
                        memory_limit=gpu_memory_limit)])
        if gpu_devices:
            local_devices = [gpu_devices[g] for g in gpus]
            tf.config.set_visible_devices(local_devices, 'GPU')

    _TF_INIT_PARAMS = param_tuple


def get_available_gpus_child_process(gpus_list_queue):
    local_device_protos = device_lib.list_local_devices()
    gpus_list = [x.name[-1]
                 for x in local_device_protos if x.device_type == 'GPU']
    gpus_list_queue.put(gpus_list)


def get_available_gpus():
    ctx = multiprocessing.get_context('spawn')
    gpus_list_queue = ctx.Queue()
    proc_get_gpus = ctx.Process(
        target=get_available_gpus_child_process, args=(gpus_list_queue,))
    proc_get_gpus.start()
    proc_get_gpus.join()
    gpus_list = gpus_list_queue.get()
    return gpus_list
