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

import numpy as np
import tensorflow as tf
from tensorflow.keras.layers import Dropout
from tensorflow.keras.layers import Layer

from ludwig.models.modules.initializer_modules import get_initializer
from ludwig.utils.data_utils import load_pretrained_embeddings

logger = logging.getLogger(__name__)


def embedding_matrix(
        vocab,
        embedding_size,
        representation='dense',
        embeddings_trainable=True,
        pretrained_embeddings=None,
        force_embedding_size=False,
        initializer=None,
):
    vocab_size = len(vocab)
    if representation == 'dense':
        if pretrained_embeddings is not None and pretrained_embeddings is not False:
            embeddings_matrix = load_pretrained_embeddings(
                pretrained_embeddings, vocab
            )
            if embeddings_matrix.shape[-1] != embedding_size:
                raise ValueError(
                    'The size of the pretrained embeddings is {}, '
                    'but the specified embedding_size is {}. '
                    'Please change the embedding_size accordingly.'.format(
                        embeddings_matrix.shape[-1],
                        embedding_size
                    ))
            initializer_obj = tf.constant(embeddings_matrix, dtype=tf.float32)

        else:
            if vocab_size < embedding_size and not force_embedding_size:
                logger.info(
                    '  embedding_size ({}) is greater than vocab_size ({}). '
                    'Setting embedding size to be equal to vocab_size.'.format(
                        embedding_size, vocab_size
                    ))
                embedding_size = vocab_size

            if initializer is not None:
                initializer_obj_ref = get_initializer(initializer)
            else:
                initializer_obj_ref = get_initializer(
                    {'type': 'uniform', 'minval': -1.0, 'maxval': 1.0})
            initializer_obj = initializer_obj_ref([vocab_size, embedding_size])

        embeddings = tf.Variable(
            initializer_obj,
            trainable=embeddings_trainable,
            name='embeddings'
        )

    elif representation == 'sparse':
        embedding_size = vocab_size
        embeddings = tf.Variable(
            get_initializer('identity')([vocab_size, embedding_size]),
            trainable=False,
            name='embeddings'
        )

    else:
        raise Exception(
            'Embedding representation {} not supported.'.format(representation))

    return embeddings, embedding_size


class Embed(Layer):
    def __init__(
            self,
            vocab,
            embedding_size,
            representation='dense',
            embeddings_trainable=True,
            pretrained_embeddings=None,
            force_embedding_size=False,
            embeddings_on_cpu=False,
            dropout_rate=0.0,
            initializer=None,
            regularizer=None
    ):
        super(Embed, self).__init__()

        if embeddings_on_cpu:
            with tf.device('/cpu:0'):
                self.embeddings, self.embedding_size = embedding_matrix(
                    vocab,
                    embedding_size,
                    representation=representation,
                    embeddings_trainable=embeddings_trainable,
                    pretrained_embeddings=pretrained_embeddings,
                    force_embedding_size=force_embedding_size,
                    initializer=initializer,
                )
        else:
            self.embeddings, self.embedding_size = embedding_matrix(
                vocab,
                embedding_size,
                representation=representation,
                embeddings_trainable=embeddings_trainable,
                pretrained_embeddings=pretrained_embeddings,
                force_embedding_size=force_embedding_size,
                initializer=initializer,
            )

        if regularizer:
            regularizer_obj = tf.keras.regularizers.get(regularizer)
            self.add_loss(lambda: regularizer_obj(self.embeddings))

        if dropout_rate > 0:
            self.dropout = Dropout(dropout_rate)
        else:
            self.dropout = None

    def call(self, inputs, training=None, mask=None):
        embedded = tf.nn.embedding_lookup(
            self.embeddings, inputs, name='embeddings_lookup'
        )

        if self.dropout:
            embedded = self.dropout(embedded, training=training)

        return embedded


class EmbedWeighted(Layer):
    def __init__(
            self,
            vocab,
            embedding_size,
            representation='dense',
            embeddings_trainable=True,
            pretrained_embeddings=None,
            force_embedding_size=False,
            embeddings_on_cpu=False,
            dropout_rate=0.0,
            initializer=None,
            regularizer=None
    ):
        super(EmbedWeighted, self).__init__()

        if embeddings_on_cpu:
            with tf.device('/cpu:0'):
                self.embeddings, self.embedding_size = embedding_matrix(
                    vocab,
                    embedding_size,
                    representation=representation,
                    embeddings_trainable=embeddings_trainable,
                    pretrained_embeddings=pretrained_embeddings,
                    force_embedding_size=force_embedding_size,
                    initializer=initializer,
                )
        else:
            self.embeddings, self.embedding_size = embedding_matrix(
                vocab,
                embedding_size,
                representation=representation,
                embeddings_trainable=embeddings_trainable,
                pretrained_embeddings=pretrained_embeddings,
                force_embedding_size=force_embedding_size,
                initializer=initializer,
            )

        if regularizer:
            regularizer_obj = tf.keras.regularizers.get(regularizer)()
            self.add_loss(regularizer_obj(self.embeddings))

        if dropout_rate > 0:
            self.dropout = Dropout(dropout_rate)
        else:
            self.dropout = None

    def call(self, inputs, training=None, mask=None):
        signed_input = tf.cast(tf.sign(tf.abs(inputs)), tf.int32)
        multiple_hot_indexes = tf.multiply(
            signed_input,
            tf.constant(np.array([range(len(self.vocab))], dtype=np.int32))
        )
        embedded = tf.nn.embedding_lookup(
            self.embeddings, multiple_hot_indexes, name='embeddings_lookup'
        )

        # Get the multipliers to embeddings
        weights_mask = tf.expand_dims(inputs, -1)
        weighted_embedded = tf.multiply(embedded, weights_mask)

        embedded_reduced = tf.reduce_sum(weighted_embedded, 1)

        if self.dropout:
            embedded_reduced = self.dropout(embedded_reduced, training=training)

        return embedded_reduced


class EmbedSparse(Layer):
    def __init__(
            self,
            vocab,
            embedding_size,
            representation='dense',
            embeddings_trainable=True,
            pretrained_embeddings=None,
            force_embedding_size=False,
            embeddings_on_cpu=False,
            dropout_rate=0.0,
            initializer=None,
            regularizer=None
    ):
        super(EmbedSparse, self).__init__()

        if embeddings_on_cpu:
            with tf.device('/cpu:0'):
                self.embeddings, self.embedding_size = embedding_matrix(
                    vocab,
                    embedding_size,
                    representation=representation,
                    embeddings_trainable=embeddings_trainable,
                    pretrained_embeddings=pretrained_embeddings,
                    force_embedding_size=force_embedding_size,
                    initializer=initializer,
                )
        else:
            self.embeddings, self.embedding_size = embedding_matrix(
                vocab,
                embedding_size,
                representation=representation,
                embeddings_trainable=embeddings_trainable,
                pretrained_embeddings=pretrained_embeddings,
                force_embedding_size=force_embedding_size,
                initializer=initializer,
            )

        if regularizer:
            regularizer_obj = tf.keras.regularizers.get(regularizer)()
            self.add_loss(regularizer_obj(self.embeddings))

        if dropout_rate > 0:
            self.dropout = Dropout(dropout_rate)
        else:
            self.dropout = None

    def call(self, inputs, training=None, mask=None):
        multiple_hot_indexes = tf.multiply(
            inputs,
            tf.constant(np.array([range(len(self.vocab))], dtype=np.int32))
        )

        idx = tf.where(tf.not_equal(multiple_hot_indexes, 0))

        sparse_multiple_hot_indexes = tf.SparseTensor(
            idx,
            tf.gather_nd(multiple_hot_indexes, idx),
            tf.shape(multiple_hot_indexes, out_type=tf.int64)
        )

        embedded_reduced = tf.nn.embedding_lookup_sparse(
            self.embeddings,
            sparse_multiple_hot_indexes,
            sp_weights=None,
            combiner=self.reduce_output
        )

        if self.dropout:
            embedded_reduced = self.dropout(embedded_reduced, training=training)

        return embedded_reduced


class EmbedSequence(Layer):
    def __init__(
            self,
            vocab,
            embedding_size,
            representation='dense',
            embeddings_trainable=True,
            pretrained_embeddings=None,
            force_embedding_size=False,
            embeddings_on_cpu=False,
            dropout_rate=0.0,
            initializer=None,
            regularizer=None
    ):
        super(EmbedSequence, self).__init__()

        if embeddings_on_cpu:
            with tf.device('/cpu:0'):
                self.embeddings, self.embedding_size = embedding_matrix(
                    vocab,
                    embedding_size,
                    representation=representation,
                    embeddings_trainable=embeddings_trainable,
                    pretrained_embeddings=pretrained_embeddings,
                    force_embedding_size=force_embedding_size,
                    initializer=initializer,
                )
        else:
            self.embeddings, self.embedding_size = embedding_matrix(
                vocab,
                embedding_size,
                representation=representation,
                embeddings_trainable=embeddings_trainable,
                pretrained_embeddings=pretrained_embeddings,
                force_embedding_size=force_embedding_size,
                initializer=initializer,
            )

        if regularizer:
            regularizer_obj = tf.keras.regularizers.get(regularizer)()
            self.add_loss(regularizer_obj(self.embeddings))

        if dropout_rate > 0:
            self.dropout = Dropout(dropout_rate)
        else:
            self.dropout = None

    def call(self, inputs, training=None, mask=None):
        embedded = self.embed(
            inputs, training=None, mask=None
        )

        # TODO use tf2 mechanism for masking
        if mask:
            mask_matrix = tf.cast(
                tf.expand_dims(tf.sign(tf.abs(inputs)), -1),
                dtype=tf.float32
            )
            embedded = tf.multiply(embedded, mask_matrix)

        if self.dropout:
            embedded = self.dropout(embedded, training=training)

        return embedded
