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

import torch
from torch.nn import BatchNorm1d, BatchNorm2d, Dropout, LayerNorm, Linear, ModuleList

from ludwig.utils.torch_utils import activations, initializer_registry, LudwigModule

logger = logging.getLogger(__name__)


class FCLayer(LudwigModule):
    @property
    def input_shape(self) -> torch.Size:
        return torch.Size([self.input_size])

    @property
    def output_shape(self) -> torch.Size:
        return torch.Size([self.output_size])

    def __init__(
        self,
        input_size,
        input_rank=2,
        output_size=256,
        use_bias=True,
        weights_initializer="xavier_uniform",
        bias_initializer="zeros",
        norm=None,
        norm_params=None,
        activation="relu",
        dropout=0,
    ):
        super().__init__()

        self.layers = ModuleList()
        self.input_size = input_size
        self.output_size = output_size

        fc = Linear(in_features=input_size, out_features=output_size, bias=use_bias)

        self.layers.append(fc)

        weights_initializer = initializer_registry[weights_initializer]
        weights_initializer(fc.weight)

        if use_bias:
            bias_initializer = initializer_registry[bias_initializer]
            bias_initializer(fc.bias)

        if norm and norm_params is None:
            norm_params = {}
        if norm == "batch":
            # might need if statement for 1d vs 2d? like images
            if input_rank == 2:
                self.layers.append(BatchNorm1d(output_size, **norm_params))
            elif input_rank == 3:
                self.layers.append(BatchNorm2d(output_size, **norm_params))
            else:
                ValueError(
                    f"input_rank parameter expected to be either 2 or 3, " f"however valued found to be {input_rank}."
                )
        elif norm == "layer":
            self.layers.append(LayerNorm(output_size, **norm_params))

        # Dict for activation objects in pytorch?
        self.layers.append(activations[activation]())

        if dropout > 0:
            self.layers.append(Dropout(dropout))

    def forward(self, inputs, mask=None):
        hidden = inputs

        for layer in self.layers:
            hidden = layer(hidden)

        return hidden


class FCStack(LudwigModule):
    def __init__(
        self,
        first_layer_input_size,
        layers=None,
        num_layers=1,
        default_input_rank=2,
        default_output_size=256,
        default_use_bias=True,
        default_weights_initializer="xavier_uniform",
        default_bias_initializer="zeros",
        default_norm=None,
        default_norm_params=None,
        default_activation="relu",
        default_dropout=0,
        residual=False,
        **kwargs,
    ):
        super().__init__()
        self.input_size = first_layer_input_size

        if layers is None:
            self.layers = []
            for i in range(num_layers):
                self.layers.append({})
        else:
            self.layers = layers

        if len(self.layers) > 0 and "input_size" not in self.layers[0]:
            self.layers[0]["input_size"] = first_layer_input_size
        for i, layer in enumerate(self.layers):
            if i != 0:
                layer["input_size"] = self.layers[i - 1]["output_size"]
            if "input_rank" not in layer:
                layer["input_rank"] = default_input_rank
            if "output_size" not in layer:
                layer["output_size"] = default_output_size
            if "use_bias" not in layer:
                layer["use_bias"] = default_use_bias
            if "weights_initializer" not in layer:
                layer["weights_initializer"] = default_weights_initializer
            if "bias_initializer" not in layer:
                layer["bias_initializer"] = default_bias_initializer
            if "norm" not in layer:
                layer["norm"] = default_norm
            if "norm_params" not in layer:
                layer["norm_params"] = default_norm_params
            if "activation" not in layer:
                layer["activation"] = default_activation
            if "dropout" not in layer:
                layer["dropout"] = default_dropout

        self.stack = ModuleList()

        for i, layer in enumerate(self.layers):
            self.stack.append(
                FCLayer(
                    input_size=layer["input_size"],
                    input_rank=layer["input_rank"],
                    output_size=layer["output_size"],
                    use_bias=layer["use_bias"],
                    weights_initializer=layer["weights_initializer"],
                    bias_initializer=layer["bias_initializer"],
                    norm=layer["norm"],
                    norm_params=layer["norm_params"],
                    activation=layer["activation"],
                    dropout=layer["dropout"],
                )
            )
        self.residual = residual

    def forward(self, inputs, mask=None):
        hidden = inputs
        prev_fc_layer_size = self.input_size
        for layer in self.stack:
            out = layer(hidden)
            if self.residual and layer.output_size == prev_fc_layer_size:
                hidden = hidden + out
            else:
                hidden = out
            prev_fc_layer_size = layer.layers[0].out_features
        return hidden

    @property
    def input_shape(self) -> torch.Size:
        return torch.Size([self.input_size])

    @property
    def output_shape(self) -> torch.Size:
        if len(self.stack) > 0:
            return self.stack[-1].output_shape
        return torch.Size([self.input_size])
