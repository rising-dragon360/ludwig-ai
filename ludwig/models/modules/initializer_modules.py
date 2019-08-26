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
import tensorflow as tf

from ludwig.utils.misc import get_from_registry

initializers_registry = {
    'constant': tf.initializers.constant,
    'identity': tf.initializers.identity,
    'zeros': tf.initializers.zeros,
    'ones': tf.initializers.ones,
    'orthogonal': tf.initializers.orthogonal,
    'normal': tf.initializers.random_normal,
    'uniform': tf.initializers.random_uniform,
    'truncated_normal': tf.initializers.truncated_normal,
    'variance_scaling': tf.initializers.variance_scaling,
    'glorot_normal': tf.initializers.glorot_normal,
    'glorot_uniform': tf.initializers.glorot_uniform,
    'xavier_normal': tf.initializers.glorot_normal,
    'xavier_uniform': tf.initializers.glorot_uniform,
    'he_normal': tf.compat.v1.initializers.he_normal,
    'he_uniform': tf.compat.v1.initializers.he_uniform,
    'lecun_normal': tf.compat.v1.initializers.lecun_normal,
    'lecun_uniform': tf.compat.v1.initializers.lecun_uniform,
    None: tf.initializers.glorot_uniform
}


def get_initializer(parameters):
    if parameters is None:
        return initializers_registry[parameters]()
    elif isinstance(parameters, str):
        initializer_fun = get_from_registry(
            parameters, initializers_registry)
        return initializer_fun()
    elif isinstance(parameters, dict):
        initializer_fun = get_from_registry(
            parameters['type'], initializers_registry)
        arguments = parameters.copy()
        del arguments['type']
        return initializer_fun(**arguments)
    else:
        raise ValueError(
            'Initializers parameters should be either strings or dictionaries, '
            'but the provided parameters are a {}. '
            'Parameters values: {}'.format(
                type(parameters), parameters
            ))
