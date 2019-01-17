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

import tensorflow as tf

from ludwig.models.modules.convolutional_modules import ConvStack1D, \
    StackParallelConv1D, ParallelConv1D
from ludwig.models.modules.embedding_modules import EmbedSequence
from ludwig.models.modules.fully_connected_modules import FCStack
from ludwig.models.modules.recurrent_modules import RecurrentStack
from ludwig.models.modules.recurrent_modules import reduce_sequence


class EmbedEncoder:

    def __init__(
            self,
            vocab,
            representation='dense',
            embedding_size=256,
            embeddings_trainable=True,
            pretrained_embeddings=None,
            embeddings_on_cpu=False,
            dropout=False,
            initializer=None,
            regularize=True,
            reduce_output='sum',
            **kwargs
    ):
        """
            :param should_embed: If True the input sequence is expected
                   to be made of integers and will be mapped into embeddings
            :type should_embed: Boolean
            :param vocab: Vocabulary of the input feature to encode
            :type vocab: List
            :param representation: the possible values are `dense` and `sparse`.
                   `dense` means the mebeddings are initialized randomly,
                   `sparse` meanse they are initialized to be one-hot encodings.
            :type representation: Str (one of 'dense' or 'sparse')
            :param embedding_size: it is the maximum embedding size, the actual
                   size will be `min(vocaularyb_size, embedding_size)`
                   for `dense` representations and exacly `vocaularyb_size`
                   for the `sparse` encoding, where `vocabulary_size` is
                   the number of different strings appearing in the training set
                   in the column the feature is named after (plus 1 for `<UNK>`).
            :type embedding_size: Integer
            :param embeddings_trainable: If `True` embeddings are trained during
                   the training process, if `False` embeddings are fixed.
                   It may be useful when loading pretrained embeddings
                   for avoiding finetuning them. This parameter has effect only
                   for `representation` is `dense` as `sparse` one-hot encodings
                    are not trainable.
            :type embeddings_trainable: Boolean
            :param pretrained_embeddings: by default `dense` embeddings
                   are initialized randomly, but this parameter allow to specify
                   a path to a file containing embeddings in the GloVe format.
                   When the file containing the embeddings is loaded, only the
                   embeddings with labels present in the vocabulary are kept,
                   the others are discarded. If the vocabulary contains strings
                   that have no match in the embeddings file, their embeddings
                   are initialized with the average of all other embedding plus
                   some random noise to make them different from each other.
                   This parameter has effect only if `representation` is `dense`.
            :type pretrained_embeddings: str (filepath)
            :param embeddings_on_cpu: by default embedings matrices are stored
                   on GPU memory if a GPU is used, as it allows
                   for faster access, but in some cases the embedding matrix
                   may be really big and this parameter forces the placement
                   of the embedding matrix in regular memroy and the CPU is used
                   to resolve them, slightly slowing down the process
                   as a result of data transfer between CPU and GPU memory.
            :param dropout: determines if there should be a dropout layer before
                   returning the encoder output.
            :type dropout: Boolean
            :param initializer: the initializer to use. If `None`, the default
                   initialized of each variable is used (`glorot_uniform`
                   in most cases). Options are: `constant`, `identity`, `zeros`,
                    `ones`, `orthogonal`, `normal`, `uniform`,
                    `truncated_normal`, `variance_scaling`, `glorot_normal`,
                    `glorot_uniform`, `xavier_normal`, `xavier_uniform`,
                    `he_normal`, `he_uniform`, `lecun_normal`, `lecun_uniform`.
                    Alternatively it is possible to specify a dictionary with
                    a key `type` that identifies the type of initialzier and
                    other keys for its parameters, e.g.
                    `{type: normal, mean: 0, stddev: 0}`.
                    To know the parameters of each initializer, please refer to
                    TensorFlow's documentation.
            :type initializer: str
            :param regularize: if `True` the embedding wieghts are added to
                   the set of weights that get reularized by a regularization
                   loss (if the `regularization_lambda` in `training`
                   is greater than 0).
            :type regularize: Boolean
            :param reduce_output: defines how to reduce the output tensor along
                   the `s` sequence length dimention if the rank of the tensor
                   is greater than 2. Available values are: `sum`,
                   `mean` or `avg`, `max`, `concat` (concatenates along
                   the first dimension), `last` (returns the last vector of the
                   first dimension) and `None` or `null` (which does not reduce
                   and returns the full tensor).
            :type reduce_output: str
        """
        self.reduce_output = reduce_output

        self.embed_sequence = EmbedSequence(
            vocab,
            embedding_size,
            representation=representation,
            embeddings_trainable=embeddings_trainable,
            pretrained_embeddings=pretrained_embeddings,
            embeddings_on_cpu=embeddings_on_cpu,
            dropout=dropout,
            initializer=initializer,
            regularize=regularize
        )

    def __call__(
            self,
            input_sequence,
            regularizer,
            dropout_rate,
            is_training=True
    ):
        """
            :param input_sequence: The input sequence fed into the encoder.
                   Shape: [batch x sequence length], type tf.int32
            :type input_sequence: Tensor
            :param regularizer: The regularizer to use for the weights
                   of the encoder.
            :type regularizer:
            :param dropout_rate: Tensor (tf.float) of the probability of dropout
            :type dropout_rate: Tensor
            :param is_training: Tesnor (tf.bool) specifying if in training mode
                   (important for dropout)
            :type is_training: Tensor
        """
        # ================ Embeddings ================
        embedded_sequence, embedding_size = self.embed_sequence(
            input_sequence,
            regularizer,
            dropout_rate,
            is_training=True
        )

        hidden = reduce_sequence(embedded_sequence, self.reduce_output)

        return hidden, embedding_size


class ParallelCNN(object):

    def __init__(
            self,
            should_embed=True,
            vocab=None,
            representation='dense',
            embedding_size=256,
            embeddings_trainable=True,
            pretrained_embeddings=None,
            embeddings_on_cpu=False,
            conv_layers=None,
            num_conv_layers=None,
            filter_size=3,
            num_filters=256,
            pool_size=None,
            fc_layers=None,
            num_fc_layers=None,
            fc_size=256,
            norm=None,
            activation='relu',
            dropout=False,
            initializer=None,
            regularize=True,
            reduce_output='max',
            **kwargs):
        """
            :param should_embed: If True the input sequence is expected
                   to be made of integers and will be mapped into embeddings
            :type should_embed: Boolean
            :param vocab: Vocabulary of the input feature to encode
            :type vocab: List
            :param representation: the possible values are `dense` and `sparse`.
                   `dense` means the mebeddings are initialized randomly,
                   `sparse` meanse they are initialized to be one-hot encodings.
            :type representation: Str (one of 'dense' or 'sparse')
            :param embedding_size: it is the maximum embedding size, the actual
                   size will be `min(vocaularyb_size, embedding_size)`
                   for `dense` representations and exacly `vocaularyb_size`
                   for the `sparse` encoding, where `vocabulary_size` is
                   the number of different strings appearing in the training set
                   in the column the feature is named after (plus 1 for `<UNK>`).
            :type embedding_size: Integer
            :param embeddings_trainable: If `True` embeddings are trained during
                   the training process, if `False` embeddings are fixed.
                   It may be useful when loading pretrained embeddings
                   for avoiding finetuning them. This parameter has effect only
                   for `representation` is `dense` as `sparse` one-hot encodings
                    are not trainable.
            :type embeddings_trainable: Boolean
            :param pretrained_embeddings: by default `dense` embeddings
                   are initialized randomly, but this parameter allow to specify
                   a path to a file containing embeddings in the GloVe format.
                   When the file containing the embeddings is loaded, only the
                   embeddings with labels present in the vocabulary are kept,
                   the others are discarded. If the vocabulary contains strings
                   that have no match in the embeddings file, their embeddings
                   are initialized with the average of all other embedding plus
                   some random noise to make them different from each other.
                   This parameter has effect only if `representation` is `dense`.
            :type pretrained_embeddings: str (filepath)
            :param embeddings_on_cpu: by default embedings matrices are stored
                   on GPU memory if a GPU is used, as it allows
                   for faster access, but in some cases the embedding matrix
                   may be really big and this parameter forces the placement
                   of the embedding matrix in regular memroy and the CPU is used
                   to resolve them, slightly slowing down the process
                   as a result of data transfer between CPU and GPU memory.
            :param conv_layers: it is a list of dictionaries containing
                   the parameters of all the convolutional layers. The length
                   of the list determines the number of parallel convolutional
                   layers and the content of each dictionary determines
                   the parameters for a specific layer. The available parameters
                   for each layer are: `filter_size`, `num_filters`, `pool`,
                   `norm`, `activation` and `regularize`. If any of those values
                   is missing from the dictionary, the default one specified
                   as a parameter of the encoder will be used instead. If both
                   `conv_layers` and `num_conv_layers` are `None`, a default
                   list will be assigned to `conv_layers` with the value
                   `[{filter_size: 2}, {filter_size: 3}, {filter_size: 4},
                   {filter_size: 5}]`.
            :type conv_layers: List
            :param num_conv_layers: if `conv_layers` is `None`, this is
                   the number of parallel convolutional layers.
            :type num_conv_layers: Integer
            :param filter_size:  if a `filter_size` is not already specified in
                   `conv_layers` this is the default `filter_size` that
                   will be used for each layer. It indicates how wide is
                   the 1d convolutional filter.
            :type filter_size: Integer
            :param num_filters: if a `num_filters` is not already specified in
                   `conv_layers` this is the default `num_filters` that
                   will be used for each layer. It indicates the number
                   of filters, and by consequence the output channels of
                   the 1d convolution.
            :type num_filters: Integer
            :param pool_size: if a `pool_size` is not already specified
                  in `conv_layers` this is the default `pool_size` that
                  will be used for each layer. It indicates the size of
                  the max pooling that will be performed along the `s` sequence
                  dimension after the convolution operation.
            :type pool_size: Integer
            :param fc_layers: it is a list of dictionaries containing
                   the parameters of all the fully connected layers. The length
                   of the list determines the number of stacked fully connected
                   layers and the content of each dictionary determines
                   the parameters for a specific layer. The available parameters
                   for each layer are: `fc_size`, `norm`, `activation` and
                   `regularize`. If any of those values is missing from
                   the dictionary, the default one specified as a parameter of
                   the encoder will be used instead. If both `fc_layers` and
                   `num_fc_layers` are `None`, a default list will be assigned
                   to `fc_layers` with the value
                   `[{fc_size: 512}, {fc_size: 256}]`.
                   (only applies if `reduce_output` is not `None`).
            :type fc_layers: List
            :param num_fc_layers: if `fc_layers` is `None`, this is the number
                   of stacked fully connected layers (only applies if
                   `reduce_output` is not `None`).
            :type num_fc_layers: Integer
            :param fc_size: if a `fc_size` is not already specified in
                   `fc_layers` this is the default `fc_size` that will be used
                   for each layer. It indicates the size of the output
                   of a fully connected layer.
            :type fc_size: Integer
            :param norm: if a `norm` is not already specified in `conv_layers`
                   or `fc_layers` this is the default `norm` that will be used
                   for each layer. It indicates the norm of the output.
            :type norm: str
            :param activation: Default activation function to use
            :type activation: Str
            :param dropout: determines if there should be a dropout layer before
                   returning the encoder output.
            :type dropout: Boolean
            :param initializer: the initializer to use. If `None` it uses
                   `glorot_uniform`. Options are: `constant`, `identity`,
                   `zeros`, `ones`, `orthogonal`, `normal`, `uniform`,
                   `truncated_normal`, `variance_scaling`, `glorot_normal`,
                   `glorot_uniform`, `xavier_normal`, `xavier_uniform`,
                   `he_normal`, `he_uniform`, `lecun_normal`, `lecun_uniform`.
                   Alternatively it is possible to specify a dictionary with
                   a key `type` that identifies the type of initialzier and
                   other keys for its parameters,
                   e.g. `{type: normal, mean: 0, stddev: 0}`.
                   To know the parameters of each initializer, please refer
                   to TensorFlow's documentation.
            :type initializer: str
            :param regularize: if a `regularize` is not already specified in
                   `conv_layers` or `fc_layers` this is the default `regularize`
                   that will be used for each layer. It indicates if
                   the layer weights should be considered when computing
                   a regularization loss.
            :type regularize:
            :param reduce_output: defines how to reduce the output tensor of
                   the convolutional layers along the `s` sequence length
                   dimention if the rank of the tensor is greater than 2.
                   Available values are: `sum`, `mean` or `avg`, `max`, `concat`
                   (concatenates along the first dimension), `last` (returns
                   the last vector of the first dimension) and `None` or `null`
                   (which does not reduce and returns the full tensor).
            :type reduce_output: str
            """

        self.should_embed = should_embed

        if conv_layers is not None and num_conv_layers is None:
            # use custom-defined layers
            self.conv_layers = conv_layers
            self.num_conv_layers = len(conv_layers)
        elif conv_layers is None and num_conv_layers is not None:
            # generate num_conv_layers with default parameters
            self.conv_layers = None
            self.num_conv_layers = num_conv_layers
        elif conv_layers is None and num_conv_layers is None:
            # use default layers with varying filter sizes
            self.conv_layers = [
                {'filter_size': 2},
                {'filter_size': 3},
                {'filter_size': 4},
                {'filter_size': 5}
            ]
            self.num_conv_layers = 4
        else:
            raise ValueError(
                'Invalid layer parametrization, use either conv_layers or num_conv_layers')

        if fc_layers is not None and num_fc_layers is None:
            # use custom-defined layers
            fc_layers = fc_layers
            num_fc_layers = len(fc_layers)
        elif fc_layers is None and num_fc_layers is not None:
            # generate num_fc_layers with default parameters
            fc_layers = None
            num_fc_layers = num_fc_layers
        elif fc_layers is None and num_fc_layers is None:
            # use default layers with varying filter sizes
            fc_layers = [
                {'fc_size': 512},
                {'fc_size': 256}
            ]
            num_fc_layers = 2
        else:
            raise ValueError(
                'Invalid layer parametrization, use either fc_layers or num_fc_layers')

        self.reduce_output = reduce_output

        self.embed_sequence = EmbedSequence(
            vocab,
            embedding_size,
            representation=representation,
            embeddings_trainable=embeddings_trainable,
            pretrained_embeddings=pretrained_embeddings,
            embeddings_on_cpu=embeddings_on_cpu,
            dropout=dropout,
            initializer=initializer,
            regularize=regularize
        )

        self.parallel_conv_1d = ParallelConv1D(
            layers=self.conv_layers,
            default_filter_size=filter_size,
            default_num_filters=num_filters,
            default_pool_size=pool_size,
            default_activation=activation,
            default_norm=norm,
            default_dropout=dropout,
            default_initializer=initializer,
            default_regularize=regularize
        )

        self.fc_stack = FCStack(
            layers=fc_layers,
            num_layers=num_fc_layers,
            default_fc_size=fc_size,
            default_activation=activation,
            default_norm=norm,
            default_dropout=dropout,
            default_regularize=regularize,
            default_initializer=initializer
        )

    def __call__(
            self,
            input_sequence,
            regularizer,
            dropout_rate,
            is_training=True
    ):
        """
            :param input_sequence: The input sequence fed into the encoder.
                   Shape: [batch x sequence length], type tf.int32
            :type input_sequence: Tensor
            :param regularizer: The regularizer to use for the weights
                   of the encoder.
            :type regularizer:
            :param dropout_rate: Tensor (tf.float) of the probability of dropout
            :type dropout_rate: Tensor
            :param is_training: Tesnor (tf.bool) specifying if in training mode
                   (important for dropout)
            :type is_training: Tensor
        """
        # ================ Embeddings ================
        if self.should_embed:
            embedded_input_sequence, embedding_size = self.embed_sequence(
                input_sequence,
                regularizer,
                dropout_rate,
                is_training=True
            )
        else:
            embedded_input_sequence = input_sequence
            while len(embedded_input_sequence.shape) < 3:
                embedded_input_sequence = tf.expand_dims(
                    embedded_input_sequence, -1)
            embedding_size = 1

        # shape=(?, sequence_length, embedding_size)
        hidden = embedded_input_sequence
        logging.debug('  hidden: {0}'.format(hidden))

        # ================ Conv Layers ================
        hidden = self.parallel_conv_1d(
            hidden,
            embedding_size,
            regularizer=regularizer,
            dropout_rate=dropout_rate,
            is_training=is_training
        )
        hidden_size = sum(
            [conv_layer['num_filters'] for conv_layer in self.conv_layers]
        )
        logging.debug('  hidden: {0}'.format(hidden))

        # ================ Sequence Reduction ================
        if self.reduce_output is not None:
            hidden = reduce_sequence(hidden, self.reduce_output)

            # ================ FC Layers ================
            hidden_size = hidden.shape.as_list()[-1]
            logging.debug('  flatten hidden: {0}'.format(hidden))

            hidden = self.fc_stack(
                hidden,
                hidden_size,
                regularizer=regularizer,
                dropout_rate=dropout_rate,
                is_training=is_training
            )
            hidden_size = hidden.shape.as_list()[-1]

        return hidden, hidden_size


class StackedCNN:

    def __init__(
            self,
            should_embed=True,
            vocab=None,
            representation='dense',
            embedding_size=256,
            embeddings_trainable=True,
            pretrained_embeddings=None,
            embeddings_on_cpu=False,
            conv_layers=None,
            num_conv_layers=None,
            filter_size=5,
            num_filters=256,
            pool_size=None,
            fc_layers=None,
            num_fc_layers=None,
            fc_size=256,
            norm=None,
            activation='relu',
            dropout=False,
            initializer=None,
            regularize=True,
            reduce_output='max',
            **kwargs
    ):
        """
            :param input_sequence: The input sequence fed into the stacked cnn
            :type input_sequence:
            :param regularizer: The method of regularization that is being used
            :type regularizer:
            :param dropout_rate: Probability of dropping a neuron in a layer
            :type dropout_rate: Float
            :param vocab: Vocabulary in the dataset
            :type vocab: List
            :param representation: Either dense or sparse representations
            :type representation: Str (one of 'dense' or 'sparse')
            :param embedding_size: The dimension of the embedding that has been chosen
            :type embedding_size: Integer
            :param filter_sizes: Size of the filter used in the convolutions
            :type filter_sizes: Tuple (Integer)
            :param num_filters: Number of filters to apply on the input for a given filter size
            :type num_filters: Tuple (Integer)
            :param pool_sizes: Use the pooling of features in Convlutional Neural Nets TODO
            :type pool_sizes: Integer or None
            :param fc_sizes: Fully connected dimensions at the end of the convolution layers
            :param activations: Type of activation function being used in the model
            :type activations: Str
            :param regularize: TODO
            :type regularize:
            :type fc_sizes: Tuple (Integer)
            :param norms: TODO
            :type norms:
            :param should_embed: Represents a boolean value determining if there is a need to embed the input sequence
            :type should_embed: Boolean
            :param embeddings_trainable: Argument that determines if the embeddings in the model are trainable end to end
            :type embeddings_trainable: Boolean
            :param pretrained_embeddings: Represents whether the embedd
            :type pretrained_embeddings: Boolean
            :param embeddings_on_cpu: TODO: clarify (Whether the embeddings should be trained on the CPU)
            :type embeddings_on_cpu: Boolean
            :param is_training: Whether this is training or not
            :type is_training: Boolean
            :returns: hidden, hidden_size - the hidden layer and hidden size
        """

        if conv_layers is not None and num_conv_layers is None:
            # use custom-defined layers
            self.conv_layers = conv_layers
            self.num_conv_layers = len(conv_layers)
        elif conv_layers is None and num_conv_layers is not None:
            # generate num_conv_layers with default parameters
            self.conv_layers = None
            self.num_conv_layers = num_conv_layers
        elif conv_layers is None and num_conv_layers is None:
            # use default layers with varying filter sizes
            self.conv_layers = [
                {
                    'filter_size': 7,
                    'pool_size': 3,
                    'regularize': False
                },
                {
                    'filter_size': 7,
                    'pool_size': 3,
                    'regularize': False
                },
                {
                    'filter_size': 3,
                    'pool_size': None,
                    'regularize': False
                },
                {
                    'filter_size': 3,
                    'pool_size': None,
                    'regularize': False
                },
                {
                    'filter_size': 3,
                    'pool_size': None,
                    'regularize': True
                },
                {
                    'filter_size': 3,
                    'pool_size': 3,
                    'regularize': True
                }
            ]
            self.num_conv_layers = 6
        else:
            raise ValueError(
                'Invalid layer parametrization, use either conv_layers or num_conv_layers')

        if fc_layers is not None and num_fc_layers is None:
            # use custom-defined layers
            fc_layers = fc_layers
            num_fc_layers = len(fc_layers)
        elif fc_layers is None and num_fc_layers is not None:
            # generate num_fc_layers with default parameters
            fc_layers = None
            num_fc_layers = num_fc_layers
        elif fc_layers is None and num_fc_layers is None:
            # use default layers with varying filter sizes
            fc_layers = [
                {'fc_size': 512},
                {'fc_size': 256}
            ]
            num_fc_layers = 2
        else:
            raise ValueError(
                'Invalid layer parametrization, use either fc_layers or num_fc_layers')

        self.should_embed = should_embed
        self.reduce_output = reduce_output

        self.embed_sequence = EmbedSequence(
            vocab,
            embedding_size,
            representation=representation,
            embeddings_trainable=embeddings_trainable,
            pretrained_embeddings=pretrained_embeddings,
            embeddings_on_cpu=embeddings_on_cpu,
            dropout=dropout,
            initializer=initializer,
            regularize=regularize
        )

        self.conv_stack_1d = ConvStack1D(
            layers=self.conv_layers,
            default_filter_size=filter_size,
            default_num_filters=num_filters,
            default_activation=activation,
            default_norm=norm,
            default_pool_size=pool_size,
            default_dropout=dropout,
            default_initializer=initializer,
            default_regularize=regularize
        )

        self.fc_stack = FCStack(
            layers=fc_layers,
            num_layers=num_fc_layers,
            default_fc_size=fc_size,
            default_activation=activation,
            default_norm=norm,
            default_dropout=dropout,
            default_regularize=regularize,
            default_initializer=initializer
        )

    def __call__(
            self,
            input_sequence,
            regularizer,
            dropout_rate,
            is_training=True
    ):
        """
            :param input_sequence: The input sequence fed into the encoder.
                   Shape: [batch x sequence length], type tf.int32
            :type input_sequence: Tensor
            :param regularizer: The regularizer to use for the weights
                   of the encoder.
            :type regularizer:
            :param dropout_rate: Tensor (tf.float) of the probability of dropout
            :type dropout_rate: Tensor
            :param is_training: Tesnor (tf.bool) specifying if in training mode
                   (important for dropout)
            :type is_training: Tensor
        """
        # ================ Embeddings ================
        if self.should_embed:
            embedded_input_sequence, self.embedding_size = self.embed_sequence(
                input_sequence,
                regularizer,
                dropout_rate,
                is_training=True
            )
        else:
            embedded_input_sequence = input_sequence
            while len(embedded_input_sequence.shape) < 3:
                embedded_input_sequence = tf.expand_dims(
                    embedded_input_sequence, -1)
            self.embedding_size = embedded_input_sequence.shape[-1]

        hidden = embedded_input_sequence
        logging.debug('  hidden: {0}'.format(hidden))

        # ================ Conv Layers ================
        with tf.variable_scope('stack_conv'):
            hidden = self.conv_stack_1d(
                hidden,
                self.embedding_size,
                regularizer=regularizer,
                dropout_rate=dropout_rate,
                is_training=is_training
            )
        hidden_size = self.conv_layers[-1]['num_filters']
        logging.debug('  hidden: {0}'.format(hidden))

        # ================ Sequence Reduction ================
        if self.reduce_output is not None:
            hidden = reduce_sequence(hidden, self.reduce_output)

            # ================ FC Layers ================
            hidden_size = hidden.shape.as_list()[-1]
            logging.debug('  flatten hidden: {0}'.format(hidden))

            hidden = self.fc_stack(
                hidden,
                hidden_size,
                regularizer=regularizer,
                dropout_rate=dropout_rate,
                is_training=is_training
            )
            hidden_size = hidden.shape.as_list()[-1]

        return hidden, hidden_size


class StackedParallelCNN:

    def __init__(
            self,
            should_embed=True,
            vocab=None,
            representation='dense',
            embedding_size=256,
            embeddings_trainable=True,
            pretrained_embeddings=None,
            embeddings_on_cpu=False,
            stacked_layers=None,
            num_stacked_layers=None,
            filter_size=3,
            num_filters=256,
            stride=1,
            pool_size=None,
            pool_stride=1,
            fc_layers=None,
            num_fc_layers=None,
            fc_size=256,
            norm=None,
            activation='relu',
            dropout=False,
            initializer=None,
            regularize=True,
            reduce_output='max',
            **kwargs
    ):
        """
            :param input_sequence: The input sequence fed into the stacked parallel cnn
            :type input_sequence:
            :param regularizer: The method of regularization that is being
            :type regularizer:
            :param dropout: Probability of dropping a neuron in a layer
            :type dropout: Float
            :param vocab: Vocabulary in the dataset
            :type vocab: List
            :param representation: Either dense or sparse representations
            :type representation: Str (one of 'dense' or 'sparse')
            :param embedding_size: The dimension of the embedding that has been chosen
            :type embedding_size: Integer
            :param filter_sizes: Size of the filter used in the convolutions
            :type filter_sizes: Tuple(Tuple(Integer))
            :param num_filters: Number of filters to apply on the input for a given filter size
            :type num_filters: Tuple(Tuple(Integer))
            :param fc_sizes: Fully connected dimensions at the end of the convolution layers
            :type fc_sizes: Tuple (Integer)
            :param activations: Type of activation function being used in the model
            :type activations: Str
            :param regularize: TODO
            :type regularize:
            :param norms: TODO
            :type norms:
            :param should_embed: Represents a boolean value determining if there is a need to embed the input sequence
            :type should_embed: Boolean
            :param embeddings_trainable: Argument that determines if the embeddings in the model are trainable end to end
            :type embeddings_trainable: Boolean
            :param pretrained_embeddings: Represents whether the embedd
            :type pretrained_embeddings: Boolean
            :param embeddings_on_cpu: TODO: clarify (Whether the embeddings should be trained on the CPU)
            :type embeddings_on_cpu: Boolean
            :param is_training: Whether this is training or not
            :type is_training: Boolean
            :returns: hidden, hidden_size - the hidden layer and hidden size
        """
        if stacked_layers is not None and num_stacked_layers is None:
            # use custom-defined layers
            self.stacked_layers = stacked_layers
            self.num_stacked_layers = len(stacked_layers)
        elif stacked_layers is None and num_stacked_layers is not None:
            # generate num_conv_layers with default parameters
            self.stacked_layers = None
            self.num_stacked_layers = num_stacked_layers
        elif stacked_layers is None and num_stacked_layers is None:
            # use default layers with varying filter sizes
            self.stacked_layers = [
                [
                    {'filter_size': 2},
                    {'filter_size': 3},
                    {'filter_size': 4},
                    {'filter_size': 5}
                ],
                [
                    {'filter_size': 2},
                    {'filter_size': 3},
                    {'filter_size': 4},
                    {'filter_size': 5}
                ],
                [
                    {'filter_size': 2},
                    {'filter_size': 3},
                    {'filter_size': 4},
                    {'filter_size': 5}
                ]
            ]
            self.num_stacked_layers = 6
        else:
            raise ValueError(
                'Invalid layer parametrization, use either stacked_layers or num_stacked_layers')

        if fc_layers is not None and num_fc_layers is None:
            # use custom-defined layers
            fc_layers = fc_layers
            num_fc_layers = len(fc_layers)
        elif fc_layers is None and num_fc_layers is not None:
            # generate num_fc_layers with default parameters
            fc_layers = None
            num_fc_layers = num_fc_layers
        elif fc_layers is None and num_fc_layers is None:
            # use default layers with varying filter sizes
            fc_layers = [
                {'fc_size': 512},
                {'fc_size': 256}
            ]
            num_fc_layers = 2
        else:
            raise ValueError(
                'Invalid layer parametrization, use either fc_layers or num_fc_layers')

        self.should_embed = should_embed
        self.reduce_output = reduce_output

        self.embed_sequence = EmbedSequence(
            vocab,
            embedding_size,
            representation=representation,
            embeddings_trainable=embeddings_trainable,
            pretrained_embeddings=pretrained_embeddings,
            embeddings_on_cpu=embeddings_on_cpu,
            dropout=dropout,
            initializer=initializer,
            regularize=regularize
        )

        self.stack_parallel_conv_1d = StackParallelConv1D(
            stacked_layers=self.stacked_layers,
            default_filter_size=filter_size,
            default_num_filters=num_filters,
            default_pool_size=pool_size,
            default_activation=activation,
            default_norm=norm,
            default_stride=stride,
            default_pool_stride=pool_stride,
            default_dropout=dropout,
            default_initializer=initializer,
            default_regularize=regularize
        )

        self.fc_stack = FCStack(
            layers=fc_layers,
            num_layers=num_fc_layers,
            default_fc_size=fc_size,
            default_activation=activation,
            default_norm=norm,
            default_dropout=dropout,
            default_regularize=regularize,
            default_initializer=initializer
        )

    def __call__(
            self,
            input_sequence,
            regularizer,
            dropout_rate,
            is_training=True
    ):
        """
            :param input_sequence: The input sequence fed into the encoder.
                   Shape: [batch x sequence length], type tf.int32
            :type input_sequence: Tensor
            :param regularizer: The regularizer to use for the weights
                   of the encoder.
            :type regularizer:
            :param dropout_rate: Tensor (tf.float) of the probability of dropout
            :type dropout_rate: Tensor
            :param is_training: Tesnor (tf.bool) specifying if in training mode
                   (important for dropout)
            :type is_training: Tensor
        """
        # ================ Embeddings ================
        if self.should_embed:
            embedded_input_sequence, self.embedding_size = self.embed_sequence(
                input_sequence,
                regularizer,
                dropout_rate,
                is_training=True
            )
        else:
            embedded_input_sequence = input_sequence
            while len(embedded_input_sequence.shape) < 3:
                embedded_input_sequence = tf.expand_dims(
                    embedded_input_sequence,
                    -1
                )
            self.embedding_size = embedded_input_sequence.shape[-1]

        hidden = embedded_input_sequence
        logging.debug('  hidden: {0}'.format(hidden))

        # ================ Conv Layers ================
        with tf.variable_scope('stack_parallel_conv'):
            hidden = self.stack_parallel_conv_1d(
                hidden,
                self.embedding_size,
                regularizer=regularizer,
                dropout_rate=dropout_rate,
                is_training=is_training
            )
        hidden_size = 0
        for stack in self.stacked_layers:
            hidden_size += stack[-1]['num_filters']
        logging.debug('  hidden: {0}'.format(hidden))

        # ================ Sequence Reduction ================
        if self.reduce_output is not None:
            hidden = reduce_sequence(hidden, self.reduce_output)

            # ================ FC Layers ================
            hidden_size = hidden.shape.as_list()[-1]
            logging.debug('  flatten hidden: {0}'.format(hidden))

            hidden = self.fc_stack(
                hidden,
                hidden_size,
                regularizer=regularizer,
                dropout_rate=dropout_rate,
                is_training=is_training
            )
            hidden_size = hidden.shape.as_list()[-1]

        return hidden, hidden_size


class RNN:

    def __init__(
            self,
            should_embed=True,
            vocab=None,
            representation='dense',
            embedding_size=256,
            embeddings_trainable=True,
            pretrained_embeddings=None,
            embeddings_on_cpu=False,
            num_layers=1,
            state_size=256,
            cell_type='rnn',
            bidirectional=False,
            dropout=False,
            initializer=None,
            regularize=True,
            reduce_output='last',
            **kwargs
    ):
        """
            :param input_sequence: The input sequence fed into the rnn
            :type input_sequence:
            :param dropout_rate: Probability of dropping a neuron in a layer
            :type dropout_rate: Float
            :param vocab: Vocabulary in the dataset
            :type vocab: List
            :param representation: Either dense or sparse representations
            :type representation: Str (one of 'dense' or 'sparse')
            :param embedding_size: The dimension of the embedding that has been chosen
            :type embedding_size: Integer
            :param state_size: Size of the hidden state TODO: Confirm
            :type state_size: Integer
            :param cell_type: The type of cell being used (e.g. 'rnn')
            :type: Str
            :param num_layers: Number of recurrent layers
            :type num_layers: Integer
            :param bidirectional: Using Bidirectional RNN's
            :type bidirectional: Boolean
            :param reduce_output: TODO
            :type reduce_output:
            :param should_embed: Represents a boolean value determining if there is a need to embed the input sequence
            :type should_embed: Boolean
            :param embeddings_trainable: Argument that determines if the embeddings in the model are trainable end to end
            :type embeddings_trainable: Boolean
            :param pretrained_embeddings: Represents whether the embedd
            :type pretrained_embeddings: Boolean
            :param embeddings_on_cpu: TODO: clarify (Whether the embeddings should be trained on the CPU)
            :type embeddings_on_cpu: Boolean
            :param is_training: Whether this is training or not
            :type is_training: Boolean
            :returns: hidden, hidden_size - the hidden layer and hidden size
        """

        self.should_embed = should_embed

        self.embed_sequence = EmbedSequence(
            vocab,
            embedding_size,
            representation=representation,
            embeddings_trainable=embeddings_trainable,
            pretrained_embeddings=pretrained_embeddings,
            embeddings_on_cpu=embeddings_on_cpu,
            dropout=dropout,
            initializer=initializer,
            regularize=regularize
        )

        self.recurrent_stack = RecurrentStack(
            state_size=state_size,
            cell_type=cell_type,
            num_layers=num_layers,
            bidirectional=bidirectional,
            dropout=dropout,
            regularize=regularize,
            reduce_output=reduce_output
        )

    def __call__(
            self,
            input_sequence,
            regularizer,
            dropout_rate,
            is_training=True
    ):
        """
            :param input_sequence: The input sequence fed into the encoder.
                   Shape: [batch x sequence length], type tf.int32
            :type input_sequence: Tensor
            :param regularizer: The regularizer to use for the weights
                   of the encoder.
            :type regularizer:
            :param dropout_rate: Tensor (tf.float) of the probability of dropout
            :type dropout_rate: Tensor
            :param is_training: Tesnor (tf.bool) specifying if in training mode
                   (important for dropout)
            :type is_training: Tensor
        """
        # ================ Embeddings ================
        if self.should_embed:
            embedded_input_sequence, self.embedding_size = self.embed_sequence(
                input_sequence,
                regularizer,
                dropout_rate,
                is_training=True
            )
        else:
            embedded_input_sequence = input_sequence
            while len(embedded_input_sequence.shape) < 3:
                embedded_input_sequence = tf.expand_dims(
                    embedded_input_sequence,
                    -1
                )
            self.embedding_size = embedded_input_sequence.shape[-1]
        logging.debug('  hidden: {0}'.format(embedded_input_sequence))

        # ================ RNN ================
        hidden, hidden_size = self.recurrent_stack(
            embedded_input_sequence,
            regularizer=regularizer,
            dropout_rate=dropout_rate,
            is_training=is_training
        )

        return hidden, hidden_size


class CNNRNN:

    def __init__(
            self,
            should_embed=True,
            vocab=None,
            representation='dense',
            embedding_size=256,
            embeddings_trainable=True,
            pretrained_embeddings=None,
            embeddings_on_cpu=False,
            conv_layers=None,
            num_conv_layers=None,
            filter_size=5,
            num_filters=256,
            norm=None,
            activation='relu',
            pool_size=None,
            num_rec_layers=1,
            state_size=256,
            cell_type='rnn',
            bidirectional=False,
            dropout=False,
            initializer=None,
            regularize=True,
            reduce_output='last',
            **kwargs
    ):
        """
            :param should_embed: If True the input sequence is expected
                   to be made of integers and will be mapped into embeddings
            :type should_embed: Boolean
        """
        if conv_layers is not None and num_conv_layers is None:
            # use custom-defined layers
            self.conv_layers = conv_layers
            self.num_conv_layers = len(conv_layers)
        elif conv_layers is None and num_conv_layers is not None:
            # generate num_conv_layers with default parameters
            self.conv_layers = None
            self.num_conv_layers = num_conv_layers
        elif conv_layers is None and num_conv_layers is None:
            # use default layers with varying filter sizes
            self.conv_layers = [
                {'pool_size': 3},
                {'pool_size': None}
            ]
            self.num_conv_layers = 2
        else:
            raise ValueError(
                'Invalid layer parametrization, use either conv_layers or num_conv_layers')

        self.should_embed = should_embed

        self.embed_sequence = EmbedSequence(
            vocab,
            embedding_size,
            representation=representation,
            embeddings_trainable=embeddings_trainable,
            pretrained_embeddings=pretrained_embeddings,
            embeddings_on_cpu=embeddings_on_cpu,
            dropout=dropout,
            initializer=initializer,
            regularize=regularize
        )

        self.conv_stack_1d = ConvStack1D(
            layers=self.conv_layers,
            default_filter_size=filter_size,
            default_num_filters=num_filters,
            default_activation=activation,
            default_norm=norm,
            default_pool_size=pool_size,
            default_dropout=dropout,
            default_initializer=initializer,
            default_regularize=regularize
        )

        self.recurrent_stack = RecurrentStack(
            state_size=state_size,
            cell_type=cell_type,
            num_layers=num_rec_layers,
            bidirectional=bidirectional,
            dropout=dropout,
            regularize=regularize,
            reduce_output=reduce_output
        )

    def __call__(
            self,
            input_sequence,
            regularizer,
            dropout_rate,
            is_training=True
    ):
        """
            :param input_sequence: The input sequence fed into the encoder.
                   Shape: [batch x sequence length], type tf.int32
            :type input_sequence: Tensor
            :param regularizer: The regularizer to use for the weights
                   of the encoder.
            :type regularizer:
            :param dropout_rate: Tensor (tf.float) of the probability of dropout
            :type dropout_rate: Tensor
            :param is_training: Tesnor (tf.bool) specifying if in training mode
                   (important for dropout)
            :type is_training: Tensor
        """
        # ================ Embeddings ================
        if self.should_embed:
            embedded_input_sequence, self.embedding_size = self.embed_sequence(
                input_sequence,
                regularizer,
                dropout_rate,
                is_training=True
            )
        else:
            embedded_input_sequence = input_sequence
            while len(embedded_input_sequence.shape) < 3:
                embedded_input_sequence = tf.expand_dims(
                    embedded_input_sequence,
                    -1
                )
            self.embedding_size = embedded_input_sequence.shape[-1]

        hidden = embedded_input_sequence
        # shape=(?, sequence_length, embedding_size)
        logging.debug('  hidden: {0}'.format(hidden))

        # ================ CNN ================
        hidden = self.conv_stack_1d(
            hidden,
            self.embedding_size,
            regularizer=regularizer,
            dropout_rate=dropout_rate,
            is_training=is_training
        )
        logging.debug('  hidden: {0}'.format(hidden))

        # ================ RNN ================
        hidden, hidden_size = self.recurrent_stack(
            hidden,
            regularizer=regularizer,
            dropout_rate=dropout_rate,
            is_training=is_training
        )

        return hidden, hidden_size
