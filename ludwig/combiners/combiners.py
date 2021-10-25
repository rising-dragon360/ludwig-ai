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
from typing import List, Dict, Optional, Union, Any

from marshmallow import INCLUDE
from marshmallow_dataclass import dataclass

import tensorflow as tf
from tensorflow.keras.layers import LayerNormalization
from tensorflow.keras.layers import Dense
from tensorflow.keras.layers import concatenate

import ludwig.utils.schema_utils as schema
from ludwig.constants import NUMERICAL, BINARY, TYPE, NAME
from ludwig.encoders.sequence_encoders import ParallelCNN
from ludwig.encoders.sequence_encoders import StackedCNN
from ludwig.encoders.sequence_encoders import StackedCNNRNN
from ludwig.encoders.sequence_encoders import StackedParallelCNN
from ludwig.encoders.sequence_encoders import StackedRNN
from ludwig.modules.attention_modules import TransformerStack
from ludwig.modules.embedding_modules import Embed
from ludwig.modules.fully_connected_modules import FCStack
from ludwig.modules.reduction_modules import SequenceReducer
from ludwig.modules.tabnet_modules import TabNet
from ludwig.utils.misc_utils import get_from_registry
from ludwig.utils.tf_utils import sequence_length_3D

logger = logging.getLogger(__name__)


sequence_encoder_registry = {
    'stacked_cnn': StackedCNN,
    'parallel_cnn': ParallelCNN,
    'stacked_parallel_cnn': StackedParallelCNN,
    'rnn': StackedRNN,
    'cnnrnn': StackedCNNRNN,
    # todo: add transformer
    # 'transformer': StackedTransformer,
}


@dataclass
class ConcatCombinerConfig:
    fc_layers: Optional[List[Dict[str, Any]]] = schema.DictList()
    num_fc_layers: int = schema.NonNegativeInteger(default=0)
    fc_size: int = schema.PositiveInteger(default=256)
    use_bias: bool = True
    weights_initializer: str = schema.InitializerOptions(default='glorot_uniform')
    bias_initializer: str = schema.InitializerOptions(default='zeros')
    weights_regularizer: Optional[str] = schema.RegularizerOptions()
    bias_regularizer: Optional[str] = schema.RegularizerOptions()
    activity_regularizer: Optional[str] = schema.RegularizerOptions()
    norm: Optional[str] = schema.StringOptions(['batch', 'layer'])
    norm_params: Optional[dict] = schema.Dict()
    activation: str = 'relu'
    dropout: float = schema.FloatRange(default=0.0, min=0, max=1)
    flatten_inputs: bool = False
    residual: bool = False

    class Meta:
        unknown = INCLUDE


class ConcatCombiner(tf.keras.Model):
    def __init__(
            self,
            input_features: Optional[List] = None,
            config: ConcatCombinerConfig = None,
            **kwargs
    ):
        super().__init__()
        logger.debug(' {}'.format(self.name))

        self.flatten_inputs = config.flatten_inputs
        self.fc_stack = None

        # todo future: this may be redundant, check
        if config.fc_layers is None and \
                config.num_fc_layers is not None:
            fc_layers = []
            for i in range(config.num_fc_layers):
                fc_layers.append({'fc_size': config.fc_size})

        if config.fc_layers is not None:
            logger.debug('  FCStack')
            self.fc_stack = FCStack(
                layers=config.fc_layers,
                num_layers=config.num_fc_layers,
                default_fc_size=config.fc_size,
                default_use_bias=config.use_bias,
                default_weights_initializer=config.weights_initializer,
                default_bias_initializer=config.bias_initializer,
                default_weights_regularizer=config.weights_regularizer,
                default_bias_regularizer=config.bias_regularizer,
                default_activity_regularizer=config.activity_regularizer,
                # default_weights_constraint=weights_constraint,
                # default_bias_constraint=bias_constraint,
                default_norm=config.norm,
                default_norm_params=config.norm_params,
                default_activation=config.activation,
                default_dropout=config.dropout,
                residual=config.residual,
            )

        if input_features and len(input_features) == 1 and config.fc_layers is None:
            self.supports_masking = True

    def call(
            self,
            inputs,  # encoder outputs
            training=None,
            mask=None,
            **kwargs
    ):
        encoder_outputs = [inputs[k]['encoder_output'] for k in inputs]

        # ================ Flatten ================
        if self.flatten_inputs:
            batch_size = tf.shape(encoder_outputs[0])[0]
            encoder_outputs = [
                tf.reshape(eo, [batch_size, -1]) for eo in encoder_outputs
            ]

        # ================ Concat ================
        if len(encoder_outputs) > 1:
            hidden = concatenate(encoder_outputs, -1)
        else:
            hidden = list(encoder_outputs)[0]

        # ================ Fully Connected ================
        if self.fc_stack is not None:
            hidden = self.fc_stack(
                hidden,
                training=training,
                mask=mask
            )

        return_data = {'combiner_output': hidden}

        if len(inputs) == 1:
            for key, value in [d for d in inputs.values()][0].items():
                if key != 'encoder_output':
                    return_data[key] = value

        return return_data

    @staticmethod
    def get_schema_cls():
        return ConcatCombinerConfig


@dataclass
class SequenceConcatCombinerConfig:
    main_sequence_feature: Optional[str] = None
    reduce_output: Optional[str] = schema.ReductionOptions()

    class Meta:
        unknown = INCLUDE


class SequenceConcatCombiner(tf.keras.Model):
    def __init__(
            self,
            config: SequenceConcatCombinerConfig = None,
            **kwargs
    ):
        super().__init__()
        logger.debug(' {}'.format(self.name))

        self.reduce_output = config.reduce_output
        self.reduce_sequence = SequenceReducer(reduce_mode=config.reduce_output)
        if self.reduce_output is None:
            self.supports_masking = True
        self.main_sequence_feature = config.main_sequence_feature

    def __call__(
            self,
            inputs,  # encoder outputs
            training=None,
            mask=None,
            **kwargs
    ):
        if (self.main_sequence_feature is None or
                self.main_sequence_feature not in inputs):
            for if_name, if_outputs in inputs.items():
                # todo: when https://github.com/ludwig-ai/ludwig/issues/810 is closed
                #       convert following test from using shape to use explicit
                #       if_outputs[TYPE] values for sequence features
                if len(if_outputs['encoder_output'].shape) == 3:
                    self.main_sequence_feature = if_name
                    break

        if self.main_sequence_feature is None:
            raise Exception(
                'No sequence feature available for sequence combiner'
            )

        main_sequence_feature_encoding = inputs[self.main_sequence_feature]

        representation = main_sequence_feature_encoding['encoder_output']
        representations = [representation]

        sequence_max_length = representation.shape[1]
        sequence_length = sequence_length_3D(representation)

        # ================ Concat ================
        for if_name, if_outputs in inputs.items():
            if if_name != self.main_sequence_feature:
                if_representation = if_outputs['encoder_output']
                if len(if_representation.shape) == 3:
                    # The following check makes sense when
                    # both representations have a specified
                    # sequence length dimension. If they do not,
                    # then this check is simply checking if None == None
                    # and will not catch discrepancies in the different
                    # feature length dimension. Those errors will show up
                    # at training time. Possible solutions to this is
                    # to enforce a length second dimension in
                    # sequential feature placeholders, but that
                    # does not work with BucketedBatcher that requires
                    # the second dimension to be undefined in order to be
                    # able to trim the data points and speed up computation.
                    # So for now we are keeping things like this, make sure
                    # to write in the documentation that training time
                    # dimensions mismatch may occur if the sequential
                    # features have different lengths for some data points.
                    if if_representation.shape[1] != representation.shape[1]:
                        raise ValueError(
                            'The sequence length of the input feature {} '
                            'is {} and is different from the sequence '
                            'length of the main sequence feature {} which '
                            'is {}.\n Shape of {}: {}, shape of {}: {}.\n'
                            'Sequence lengths of all sequential features '
                            'must be the same  in order to be concatenated '
                            'by the sequence concat combiner. '
                            'Try to impose the same max sequence length '
                            'as a preprocessing parameter to both features '
                            'or to reduce the output of {}.'.format(
                                if_name,
                                if_representation.shape[1],
                                self.main_sequence_feature,
                                representation.shape[1],
                                if_name,
                                if_representation.shape,
                                if_name,
                                representation.shape,
                                if_name
                            )
                        )
                    # this assumes all sequence representations have the
                    # same sequence length, 2nd dimension
                    representations.append(if_representation)

                elif len(if_representation.shape) == 2:
                    multipliers = tf.constant([1, sequence_max_length, 1])
                    tiled_representation = tf.tile(
                        tf.expand_dims(if_representation, 1),
                        multipliers
                    )
                    representations.append(tiled_representation)

                else:
                    raise ValueError(
                        'The representation of {} has rank {} and cannot be'
                        ' concatenated by a sequence concat combiner. '
                        'Only rank 2 and rank 3 tensors are supported.'.format(
                            if_outputs['name'],
                            len(if_representation.shape)
                        )
                    )

        hidden = tf.concat(representations, 2)
        logger.debug('  concat_hidden: {0}'.format(hidden))

        # ================ Mask ================
        # todo future: maybe modify this with TF2 mask mechanics
        sequence_mask = tf.sequence_mask(
            sequence_length,
            sequence_max_length
        )
        hidden = tf.multiply(
            hidden,
            tf.cast(tf.expand_dims(sequence_mask, -1), dtype=tf.float32)
        )

        # ================ Reduce ================
        hidden = self.reduce_sequence(hidden)

        return_data = {'combiner_output': hidden}

        if len(inputs) == 1:
            for key, value in [d for d in inputs.values()][0].items():
                if key != 'encoder_output':
                    return_data[key] = value

        return return_data

    @staticmethod
    def get_schema_cls():
        return SequenceConcatCombinerConfig


@dataclass
class SequenceCombinerConfig:
    main_sequence_feature: Optional[str] = None
    reduce_output: Optional[str] = schema.ReductionOptions()
    encoder: Optional[str] = schema.StringOptions(list(sequence_encoder_registry.keys()))

    class Meta:
        unknown = INCLUDE


class SequenceCombiner(tf.keras.Model):
    def __init__(
            self,
            config: SequenceCombinerConfig = None,
            **kwargs
    ):
        super().__init__()
        logger.debug(' {}'.format(self.name))
        self.combiner = SequenceConcatCombiner(config)

        self.encoder_obj = get_from_registry(
            config.encoder, sequence_encoder_registry)(
            should_embed=False,
            reduce_output=config.reduce_output,
            **kwargs
        )

        if (hasattr(self.encoder_obj, 'supports_masking') and
                self.encoder_obj.supports_masking):
            self.supports_masking = True

    def __call__(
            self,
            inputs,  # encoder outputs
            training=None,
            mask=None,
            **kwargs
    ):
        # ================ Concat ================
        hidden = self.combiner(
            inputs,  # encoder outputs
            training=training,
            **kwargs
        )

        # ================ Sequence encoding ================
        hidden = self.encoder_obj(
            hidden['combiner_output'],
            training=training,
            **kwargs
        )

        return_data = {'combiner_output': hidden['encoder_output']}
        for key, value in hidden.items():
            if key != 'encoder_output':
                return_data[key] = value

        return return_data
    
    @staticmethod
    def get_schema_cls():
        return SequenceCombinerConfig


@dataclass
class TabNetCombinerConfig:
    size: int = schema.PositiveInteger(default=32)
    output_size: int = schema.PositiveInteger(default=32)
    num_steps: int = schema.NonNegativeInteger(default=1)
    num_total_blocks: int = schema.NonNegativeInteger(default=4)
    num_shared_blocks: int = schema.NonNegativeInteger(default=2)
    relaxation_factor: float = 1.5
    bn_epsilon: float = 1e-3
    bn_momentum: float = 0.7
    bn_virtual_bs: Optional[int] = schema.PositiveInteger()
    sparsity: float = 1e-5
    dropout: float = schema.FloatRange(default=0.0, min=0, max=1)

    class Meta:
        unknown = INCLUDE


class TabNetCombiner(tf.keras.Model):
    def __init__(
            self,
            config: TabNetCombinerConfig = None,
            **kwargs
    ):
        super().__init__()
        logger.debug(' {}'.format(self.name))

        self.tabnet = TabNet(
            size=config.size,
            output_size=config.output_size,
            num_steps=config.num_steps,
            num_total_blocks=config.num_total_blocks,
            num_shared_blocks=config.num_shared_blocks,
            relaxation_factor=config.relaxation_factor,
            bn_epsilon=config.bn_epsilon,
            bn_momentum=config.bn_momentum,
            bn_virtual_bs=config.bn_virtual_bs,
            sparsity=config.sparsity
        )

        if config.dropout > 0:
            self.dropout = tf.keras.layers.Dropout(config.dropout)
        else:
            self.dropout = None

    def build(self, input_shape):
        self.flatten_layers = {
            k: tf.keras.layers.Flatten()
            for k in input_shape.keys()
        }

    def call(
            self,
            inputs,  # encoder outputs
            training=None,
            mask=None,
            **kwargs
    ):
        encoder_output_map = {
            k: inputs[k]['encoder_output'] for k in inputs
        }

        # ================ Flatten ================
        encoder_outputs = [
            self.flatten_layers[k](eo)
            for k, eo in encoder_output_map.items()
        ]

        # ================ Concat ================
        if len(encoder_outputs) > 1:
            hidden = concatenate(encoder_outputs, 1)
        else:
            hidden = list(encoder_outputs)[0]

        # ================ TabNet ================
        hidden, aggregated_mask, masks = self.tabnet(
            hidden,
            training=training,
        )
        if self.dropout:
            hidden = self.dropout(hidden, training=training)

        return_data = {'combiner_output': hidden,
                       'aggregated_attention_masks': aggregated_mask,
                       'attention_masks': masks}

        if len(inputs) == 1:
            for key, value in [d for d in inputs.values()][0].items():
                if key != 'encoder_output':
                    return_data[key] = value

        return return_data

    @staticmethod
    def get_schema_cls():
        return TabNetCombinerConfig


@dataclass
class TransformerCombinerConfig:
    num_layers: int = schema.PositiveInteger(default=1)
    hidden_size: int = schema.NonNegativeInteger(default=256)
    num_heads: int = schema.NonNegativeInteger(default=8)
    transformer_fc_size: int = schema.NonNegativeInteger(default=256)
    dropout: float = schema.FloatRange(default=0.1, min=0, max=1)
    fc_layers: Optional[List[Dict[str, Any]]] = schema.DictList()
    num_fc_layers: int = schema.NonNegativeInteger(default=0)
    fc_size: int = schema.PositiveInteger(default=256)
    use_bias: bool = True
    weights_initializer: str = schema.InitializerOptions(default='glorot_uniform')
    bias_initializer: str = schema.InitializerOptions(default='zeros')
    weights_regularizer: Optional[str] = schema.RegularizerOptions()
    bias_regularizer: Optional[str] = schema.RegularizerOptions()
    activity_regularizer: Optional[str] = schema.RegularizerOptions()
    norm: Optional[str] = schema.StringOptions(['batch', 'layer'])
    norm_params: Optional[dict] = schema.Dict()
    fc_activation: str = 'relu'
    fc_dropout: float = schema.FloatRange(default=0.0, min=0, max=1)
    fc_residual: bool = False
    reduce_output: Optional[str] = schema.ReductionOptions(default='mean')

    class Meta:
        unknown = INCLUDE


class TransformerCombiner(tf.keras.Model):
    def __init__(
            self,
            input_features: Optional[List] = None,
            config: TransformerCombinerConfig = None,
            **kwargs
    ):
        super().__init__()
        logger.debug(' {}'.format(self.name))

        self.reduce_output = config.reduce_output
        self.reduce_sequence = SequenceReducer(reduce_mode=config.reduce_output)
        if self.reduce_output is None:
            self.supports_masking = True

        logger.debug('  Projectors')
        self.projectors = [Dense(config.hidden_size) for _ in input_features]

        logger.debug('  TransformerStack')
        self.transformer_stack = TransformerStack(
            hidden_size=config.hidden_size,
            num_heads=config.num_heads,
            fc_size=config.transformer_fc_size,
            num_layers=config.num_layers,
            dropout=config.dropout
        )

        if self.reduce_output is not None:
            logger.debug('  FCStack')
            self.fc_stack = FCStack(
                layers=config.fc_layers,
                num_layers=config.num_fc_layers,
                default_fc_size=config.fc_size,
                default_use_bias=config.use_bias,
                default_weights_initializer=config.weights_initializer,
                default_bias_initializer=config.bias_initializer,
                default_weights_regularizer=config.weights_regularizer,
                default_bias_regularizer=config.bias_regularizer,
                default_activity_regularizer=config.activity_regularizer,
                # default_weights_constraint=weights_constraint,
                # default_bias_constraint=bias_constraint,
                default_norm=config.norm,
                default_norm_params=config.norm_params,
                default_activation=config.fc_activation,
                default_dropout=config.fc_dropout,
                fc_residual=config.fc_residual,
            )

    def call(
            self,
            inputs,  # encoder outputs
            training=None,
            mask=None,
            **kwargs
    ):
        encoder_outputs = [inputs[k]['encoder_output'] for k in inputs]

        # ================ Flatten ================
        batch_size = tf.shape(encoder_outputs[0])[0]
        encoder_outputs = [
            tf.reshape(eo, [batch_size, -1]) for eo in encoder_outputs
        ]

        # ================ Project & Concat ================
        projected = [
            self.projectors[i](eo)
            for i, eo in enumerate(encoder_outputs)
        ]
        hidden = tf.stack(projected)  # num_eo, bs, h
        hidden = tf.transpose(hidden, perm=[1, 0, 2])  # bs, num_eo, h

        # ================ Transformer Layers ================
        hidden = self.transformer_stack(
            hidden,
            training=training,
            mask=mask
        )

        # ================ Sequence Reduction ================
        if self.reduce_output is not None:
            hidden = self.reduce_sequence(hidden)

            # ================ FC Layers ================
            hidden = self.fc_stack(
                hidden,
                training=training,
                mask=mask
            )

        return_data = {'combiner_output': hidden}

        if len(inputs) == 1:
            for key, value in [d for d in inputs.values()][0].items():
                if key != 'encoder_output':
                    return_data[key] = value

        return return_data

    @staticmethod
    def get_schema_cls():
        return TransformerCombinerConfig


@dataclass
class TabTransformerCombinerConfig:
    embed_input_feature_name: Optional[Union[str, int]] = schema.Embed()
    num_layers: int = schema.PositiveInteger(default=1)
    hidden_size: int = schema.NonNegativeInteger(default=256)
    num_heads: int = schema.NonNegativeInteger(default=8)
    transformer_fc_size: int = schema.NonNegativeInteger(default=256)
    dropout: float = schema.FloatRange(default=0.1, min=0, max=1)
    fc_layers: Optional[List[Dict[str, Any]]] = schema.DictList()
    num_fc_layers: int = schema.NonNegativeInteger(default=0)
    fc_size: int = schema.PositiveInteger(default=256)
    use_bias: bool = True
    weights_initializer: str = schema.InitializerOptions(default='glorot_uniform')
    bias_initializer: str = schema.InitializerOptions(default='zeros')
    weights_regularizer: Optional[str] = schema.RegularizerOptions()
    bias_regularizer: Optional[str] = schema.RegularizerOptions()
    activity_regularizer: Optional[str] = schema.RegularizerOptions()
    norm: Optional[str] = schema.StringOptions(['batch', 'layer'])
    norm_params: Optional[dict] = schema.Dict()
    fc_activation: str = 'relu'
    fc_dropout: float = schema.FloatRange(default=0.0, min=0, max=1)
    fc_residual: bool = False
    reduce_output: str = schema.ReductionOptions(default='concat')

    class Meta:
        unknown = INCLUDE


class TabTransformerCombiner(tf.keras.Model):
    def __init__(
            self,
            input_features: Optional[List] = None,
            config: TabTransformerCombinerConfig = None,
            **kwargs
    ):
        super().__init__()
        logger.debug(' {}'.format(self.name))

        if config.reduce_output is None:
            raise ValueError("TabTransformer requires the `resude_output` "
                             "parametr")
        self.reduce_output = config.reduce_output
        self.reduce_sequence = SequenceReducer(reduce_mode=config.reduce_output)
        self.supports_masking = True
        self.layer_norm = LayerNormalization()

        self.embed_input_feature_name = config.embed_input_feature_name
        if self.embed_input_feature_name:
            vocab = [i_f for i_f in input_features
                     if i_f[TYPE] != NUMERICAL or i_f[TYPE] != BINARY]
            if self.embed_input_feature_name == 'add':
                self.embed_i_f_name_layer = Embed(vocab, config.hidden_size,
                                                  force_embedding_size=True)
                projector_size = config.hidden_size
            elif isinstance(self.embed_input_feature_name, int):
                if self.embed_input_feature_name > config.hidden_size:
                    raise ValueError(
                        "TabTransformer parameter "
                        "`embed_input_feature_name` "
                        "specified integer value ({}) "
                        "needs to be smaller than "
                        "`hidden_size` ({}).".format(
                            self.embed_input_feature_name, config.hidden_size
                        ))
                self.embed_i_f_name_layer = Embed(
                    vocab,
                    self.embed_input_feature_name,
                    force_embedding_size=True,
                )
                projector_size = config.hidden_size - self.embed_input_feature_name
            else:
                raise ValueError("TabTransformer parameter "
                                 "`embed_input_feature_name` "
                                 "should be either None, an integer or `add`, "
                                 "the current value is "
                                 "{}".format(self.embed_input_feature_name))
        else:
            projector_size = config.hidden_size

        logger.debug('  Projectors')
        self.projectors = [Dense(projector_size) for i_f in input_features
                           if i_f[TYPE] != NUMERICAL and i_f[TYPE] != BINARY]
        self.skip_features = [i_f[NAME] for i_f in input_features
                              if i_f[TYPE] == NUMERICAL or i_f[TYPE] == BINARY]

        logger.debug('  TransformerStack')
        self.transformer_stack = TransformerStack(
            hidden_size=config.hidden_size,
            num_heads=config.num_heads,
            fc_size=config.transformer_fc_size,
            num_layers=config.num_layers,
            dropout=config.dropout
        )

        logger.debug('  FCStack')

        self.fc_stack = FCStack(
            layers=config.fc_layers,
            num_layers=config.num_fc_layers,
            default_fc_size=config.fc_size,
            default_use_bias=config.use_bias,
            default_weights_initializer=config.weights_initializer,
            default_bias_initializer=config.bias_initializer,
            default_weights_regularizer=config.weights_regularizer,
            default_bias_regularizer=config.bias_regularizer,
            default_activity_regularizer=config.activity_regularizer,
            # default_weights_constraint=weights_constraint,
            # default_bias_constraint=bias_constraint,
            default_norm=config.norm,
            default_norm_params=config.norm_params,
            default_activation=config.fc_activation,
            default_dropout=config.fc_dropout,
            fc_residual=config.fc_residual,
        )

    def call(
            self,
            inputs,  # encoder outputs
            training=None,
            mask=None,
            **kwargs
    ):
        skip_encoder_outputs = [inputs[k]['encoder_output'] for k in inputs
                                if k in self.skip_features]
        other_encoder_outputs = [inputs[k]['encoder_output'] for k in inputs
                                 if k not in self.skip_features]

        # ================ Flatten ================
        batch_size = tf.shape(other_encoder_outputs[0])[0]
        other_encoder_outputs = [
            tf.reshape(eo, [batch_size, -1]) for eo in other_encoder_outputs
        ]
        skip_encoder_outputs = [
            tf.reshape(eo, [batch_size, -1]) for eo in skip_encoder_outputs
        ]

        # ================ Project & Concat others ================
        projected = [
            self.projectors[i](eo)
            for i, eo in enumerate(other_encoder_outputs)
        ]
        hidden = tf.stack(projected)  # num_eo, bs, h
        hidden = tf.transpose(hidden, perm=[1, 0, 2])  # bs, num_eo, h

        if self.embed_input_feature_name:
            i_f_names_idcs = tf.range(0, len(other_encoder_outputs))
            embedded_i_f_names = self.embed_i_f_name_layer(i_f_names_idcs)
            embedded_i_f_names = tf.expand_dims(embedded_i_f_names, axis=0)
            embedded_i_f_names = tf.tile(embedded_i_f_names, [batch_size, 1, 1])
            if self.embed_input_feature_name == 'add':
               hidden = hidden + embedded_i_f_names
            else:
                hidden = tf.concat([hidden, embedded_i_f_names], axis=-1)

        # ================ Transformer Layers ================
        hidden = self.transformer_stack(
            hidden,
            training=training,
            mask=mask
        )

        # ================ Sequence Reduction ================
        hidden = self.reduce_sequence(hidden)

        # ================ Concat Skipped ================
        if len(skip_encoder_outputs) > 1:
            skip_hidden = concatenate(skip_encoder_outputs, -1)
        else:
            skip_hidden = list(skip_encoder_outputs)[0]
        skip_hidden = self.layer_norm(skip_hidden)

        # ================ Concat Skipped and Others ================
        hidden = concatenate([hidden, skip_hidden], -1)

        # ================ FC Layers ================
        hidden = self.fc_stack(
            hidden,
            training=training,
            mask=mask
        )

        return_data = {'combiner_output': hidden}

        if len(inputs) == 1:
            for key, value in [d for d in inputs.values()][0].items():
                if key != 'encoder_output':
                    return_data[key] = value

        return return_data
    
    @staticmethod
    def get_schema_cls():
        return TabTransformerCombinerConfig


@dataclass
class ComparatorCombinerConfig:
    entity_1: List[str]
    entity_2: List[str]
    num_fc_layers: int = schema.NonNegativeInteger(default=1)
    fc_size: int = schema.PositiveInteger(default=256)
    use_bias: bool = True
    weights_initializer: str = schema.InitializerOptions(default='glorot_uniform')
    bias_initializer: str = schema.InitializerOptions(default='zeros')
    weights_regularizer: Optional[str] = schema.RegularizerOptions()
    bias_regularizer: Optional[str] = schema.RegularizerOptions()
    activity_regularizer: Optional[str] = schema.RegularizerOptions()
    norm: Optional[str] = schema.StringOptions(['batch', 'layer'])
    norm_params: Optional[dict] = schema.Dict()
    activation: str = 'relu'
    dropout: float = schema.FloatRange(default=0.0, min=0, max=1)

    class Meta:
        unknown = INCLUDE


class ComparatorCombiner(tf.keras.Model):
    def __init__(
            self,
            config: ComparatorCombinerConfig = None,
            **kwargs,
    ):
        super().__init__()
        logger.debug(" {}".format(self.name))

        self.fc_stack = None

        # todo future: this may be redundant, check
        # if fc_layers is None and num_fc_layers is not None:
        fc_layers = []
        for i in range(config.num_fc_layers):
            fc_layers.append({"fc_size": config.fc_size})

        if fc_layers is not None:
            logger.debug("  FCStack")
            self.e1_fc_stack = FCStack(
                layers=fc_layers,
                num_layers=config.num_fc_layers,
                default_fc_size=config.fc_size,
                default_use_bias=config.use_bias,
                default_weights_initializer=config.weights_initializer,
                default_bias_initializer=config.bias_initializer,
                default_weights_regularizer=config.weights_regularizer,
                default_bias_regularizer=config.bias_regularizer,
                default_activity_regularizer=config.activity_regularizer,
                # default_weights_constraint=weights_constraint,
                # default_bias_constraint=bias_constraint,
                default_norm=config.norm,
                default_norm_params=config.norm_params,
                default_activation=config.activation,
                default_dropout=config.dropout,
            )
            self.e2_fc_stack = FCStack(
                layers=fc_layers,
                num_layers=config.num_fc_layers,
                default_fc_size=config.fc_size,
                default_use_bias=config.use_bias,
                default_weights_initializer=config.weights_initializer,
                default_bias_initializer=config.bias_initializer,
                default_weights_regularizer=config.weights_regularizer,
                default_bias_regularizer=config.bias_regularizer,
                default_activity_regularizer=config.activity_regularizer,
                # default_weights_constraint=weights_constraint,
                # default_bias_constraint=bias_constraint,
                default_norm=config.norm,
                default_norm_params=config.norm_params,
                default_activation=config.activation,
                default_dropout=config.dropout,
            )

        # todo: this should actually be the size of the last fc layer,
        #  not just fc_size
        # todo: set initializer and regularization
        self.bilinear_weights = tf.random.normal([config.fc_size, config.fc_size],
                                                 dtype=tf.float32)

        self.entity_1 = config.entity_1
        self.entity_2 = config.entity_2
        self.required_inputs = set(config.entity_1 + config.entity_2)
        self.fc_size = config.fc_size

    def call(self, inputs, training=None, mask=None,
             **kwargs):  # encoder outputs
        assert (
                inputs.keys() == self.required_inputs
        ), f"Missing inputs {self.required_inputs - set(inputs.keys())}"

        ############
        # Entity 1 #
        ############
        e1_enc_outputs = [inputs[k]["encoder_output"] for k in self.entity_1]

        # ================ Flatten ================
        batch_size = tf.shape(e1_enc_outputs[0])[0]
        e1_enc_outputs = [
            tf.reshape(eo, [batch_size, -1]) for eo in e1_enc_outputs
        ]

        # ================ Concat ================
        if len(e1_enc_outputs) > 1:
            e1_hidden = concatenate(e1_enc_outputs, 1)
        else:
            e1_hidden = list(e1_enc_outputs)[0]

        # ================ Fully Connected ================
        e1_hidden = self.e1_fc_stack(e1_hidden, training=training, mask=mask)

        ############
        # Entity 2 #
        ############
        e2_enc_outputs = [inputs[k]["encoder_output"] for k in self.entity_2]

        # ================ Flatten ================
        batch_size = tf.shape(e2_enc_outputs[0])[0]
        e2_enc_outputs = [
            tf.reshape(eo, [batch_size, -1]) for eo in e2_enc_outputs
        ]

        # ================ Concat ================
        if len(e2_enc_outputs) > 1:
            e2_hidden = concatenate(e2_enc_outputs, 1)
        else:
            e2_hidden = list(e2_enc_outputs)[0]

        # ================ Fully Connected ================
        e2_hidden = self.e2_fc_stack(e2_hidden, training=training, mask=mask)

        ###########
        # Compare #
        ###########
        if e1_hidden.shape != e2_hidden.shape:
            raise ValueError(
                f"Mismatching shapes among dimensions! "
                f"entity1 shape: {e1_hidden.shape.as_list()} "
                f"entity2 shape: {e2_hidden.shape.as_list()}"
            )

        dot_product = tf.matmul(e1_hidden, tf.transpose(e2_hidden))
        element_wise_mul = tf.math.multiply(e1_hidden, e2_hidden)
        abs_diff = tf.abs(e1_hidden - e2_hidden)
        bilinear_prod = tf.matmul(
            e1_hidden,
            tf.matmul(self.bilinear_weights, tf.transpose(e2_hidden))
        )
        hidden = concatenate(
            [dot_product, element_wise_mul, abs_diff, bilinear_prod], 1
        )

        return {"combiner_output": hidden}

    @staticmethod
    def get_schema_cls():
        return ComparatorCombinerConfig


def get_combiner_class(combiner_type):
    return get_from_registry(
        combiner_type,
        combiner_registry
    )


combiner_registry = {
    'concat': ConcatCombiner,
    'sequence_concat': SequenceConcatCombiner,
    'sequence': SequenceCombiner,
    'tabnet': TabNetCombiner,
    'comparator': ComparatorCombiner,
    "transformer": TransformerCombiner,
    "tabtransformer": TabTransformerCombiner,
}
