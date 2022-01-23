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
import copy
import logging
from abc import ABC, abstractmethod, abstractstaticmethod
from typing import Any, Dict, Optional

import torch
from torch import Tensor

from ludwig.constants import COLUMN, HIDDEN, LENGTHS, LOGITS, LOSS, NAME, PREDICTIONS, PROBABILITIES, PROC_COLUMN, TYPE
from ludwig.decoders.registry import get_decoder_cls
from ludwig.encoders.registry import get_encoder_cls
from ludwig.features.feature_utils import compute_feature_hash, get_input_size_with_dependencies
from ludwig.modules.fully_connected_modules import FCStack
from ludwig.modules.loss_modules import get_loss_cls
from ludwig.modules.metric_registry import get_metric_classes, get_metric_cls
from ludwig.modules.reduction_modules import SequenceReducer
from ludwig.utils import output_feature_utils
from ludwig.utils.metric_utils import get_scalar_from_ludwig_metric
from ludwig.utils.misc_utils import merge_dict
from ludwig.utils.torch_utils import LudwigModule
from ludwig.utils.types import DataFrame

logger = logging.getLogger(__name__)


class BaseFeatureMixin(ABC):
    """Parent class for feature mixins.

    Feature mixins support preprocessing functionality shared across input and output features.
    """

    @abstractstaticmethod
    def type() -> str:
        """Returns the type of feature this mixin supports."""
        raise NotImplementedError

    @abstractstaticmethod
    def preprocessing_defaults() -> Dict[str, Any]:
        """Returns dict of preprocessing defaults."""
        raise NotImplementedError

    @abstractstaticmethod
    def preprocessing_schema() -> Dict[str, Any]:
        """Returns schema for the preprocessing configuration."""
        raise NotImplementedError

    @abstractstaticmethod
    def cast_column(column: DataFrame, backend) -> DataFrame:
        """Returns a copy of the dataset column for the given feature, potentially after a type cast.

        Args:
            column: Pandas column of values.
            backend: (Union[Backend, str]) Backend to use for feature data processing.
        """
        raise NotImplementedError

    @abstractstaticmethod
    def get_feature_meta(column: DataFrame, preprocessing_parameters: Dict[str, Any], backend) -> Dict[str, Any]:
        """Returns a dictionary of feature metadata.

        Args:
            column: Pandas column of values.
            preprocessing_parameters: Preprocessing configuration for this feature.
            backend: (Union[Backend, str]) Backend to use for feature data processing.
        """
        raise NotImplementedError

    @abstractstaticmethod
    def add_feature_data(
        feature_config: Dict[str, Any],
        input_df: DataFrame,
        proc_df: Dict[str, DataFrame],
        metadata: Dict[str, Any],
        preprocessing_parameters: Dict[str, Any],
        backend,  # Union[Backend, str]
        skip_save_processed_input: bool,
    ) -> None:
        """Runs preprocessing on the input_df and stores results in the proc_df and metadata dictionaries.

        Args:
            feature_config: Feature configuration.
            input_df: Pandas column of values.
            proc_df: Dict of processed columns of data. Feature data is added to this.
            metadata: Metadata returned by get_feature_meta(). Additional information may be added to this.
            preprocessing_parameters: Preprocessing configuration for this feature.
            backend: (Union[Backend, str]) Backend to use for feature data processing.
            skip_save_processed_input: Whether to skip saving the processed input.
        """
        raise NotImplementedError


class PredictModule(torch.nn.Module):
    """Base class for all modules that convert model outputs to predictions.

    Explicit member variables needed here for scripting, as Torchscript will not be able to recognize global variables
    during scripting.
    """

    def __init__(self):
        super().__init__()
        self.predictions_key = PREDICTIONS
        self.probabilities_key = PROBABILITIES
        self.logits_key = LOGITS


class BaseFeature:
    """Base class for all features.

    Note that this class is not-cooperative (does not forward kwargs), so when constructing feature class hierarchies,
    there should be only one parent class that derives from base feature.  Other functionality should be put into mixin
    classes to avoid the diamond pattern.
    """

    def __init__(self, feature, *args, **kwargs):
        super().__init__()

        if NAME not in feature:
            raise ValueError("Missing feature name")
        self.feature_name = feature[NAME]

        if COLUMN not in feature:
            feature[COLUMN] = self.feature_name
        self.column = feature[COLUMN]

        if PROC_COLUMN not in feature:
            feature[PROC_COLUMN] = compute_feature_hash(feature)
        self.proc_column = feature[PROC_COLUMN]

    def overwrite_defaults(self, feature):
        attributes = set(self.__dict__.keys())
        attributes.update(self.__class__.__dict__.keys())

        for k in feature.keys():
            if k in attributes:
                if isinstance(feature[k], dict) and hasattr(self, k) and isinstance(getattr(self, k), dict):
                    setattr(self, k, merge_dict(getattr(self, k), feature[k]))
                else:
                    setattr(self, k, feature[k])


class InputFeature(BaseFeature, LudwigModule, ABC):
    """Parent class for all input features."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def create_sample_input(self):
        # Used by get_model_inputs(), which is used for tracing-based torchscript generation.
        return torch.rand([2, *self.input_shape]).to(self.input_dtype)

    @staticmethod
    @abstractmethod
    def update_config_with_metadata(input_feature, feature_metadata, *args, **kwargs):
        pass

    @staticmethod
    @abstractmethod
    def populate_defaults(input_feature):
        pass

    def initialize_encoder(self, encoder_parameters):
        return get_encoder_cls(self.type(), self.encoder)(**encoder_parameters)

    @staticmethod
    def create_preproc_module(metadata: Dict[str, Any]) -> torch.nn.Module:
        raise NotImplementedError("Torchscript tracing not supported for feature")


class OutputFeature(BaseFeature, LudwigModule, ABC):
    """Parent class for all output features."""

    def __init__(self, feature: Dict[str, Any], other_output_features: Dict[str, "OutputFeature"], *args, **kwargs):
        """Defines defaults, overwrites them based on the feature dictionary, and sets up dependencies.

        Any output feature can depend on one or more other output features. The `other_output_features` input dictionary
        should contain entries for any dependent output features, which is accomplished by constructing output features
        in topographically sorted order. Attributes of any dependent output features are used to properly initialize
        this feature's sizes.
        """
        super().__init__(*args, feature=feature, **kwargs)

        self.reduce_input = None
        self.reduce_dependencies = None

        # List of feature names that this output feature is depdendent on.
        self.dependencies = []

        self.fc_layers = None
        self.num_fc_layers = 0
        self.output_size = 256
        self.use_bias = True
        self.weights_initializer = "xavier_uniform"
        self.bias_initializer = "zeros"
        self.norm = None
        self.norm_params = None
        self.activation = "relu"
        self.dropout = 0
        self.input_size = None

        self.overwrite_defaults(feature)

        logger.debug(" output feature fully connected layers")
        logger.debug("  FCStack")

        self.input_size = get_input_size_with_dependencies(self.input_size, self.dependencies, other_output_features)
        feature["input_size"] = self.input_size  # needed for future overrides

        self.fc_stack = FCStack(
            first_layer_input_size=self.input_size,
            layers=self.fc_layers,
            num_layers=self.num_fc_layers,
            default_output_size=self.output_size,
            default_use_bias=self.use_bias,
            default_weights_initializer=self.weights_initializer,
            default_bias_initializer=self.bias_initializer,
            default_norm=self.norm,
            default_norm_params=self.norm_params,
            default_activation=self.activation,
            default_dropout=self.dropout,
        )
        self._prediction_module = self.create_predict_module()

        # set up two sequence reducers, one for inputs and other for dependencies
        self.reduce_sequence_input = SequenceReducer(reduce_mode=self.reduce_input)
        if self.dependencies:
            self.dependency_reducers = torch.nn.ModuleDict()
            # todo: re-evaluate need for separate handling of `attention` reducer
            #       currently this code does not support `attention`
            for dependency in self.dependencies:
                self.dependency_reducers[dependency] = SequenceReducer(reduce_mode=self.reduce_dependencies)

    def create_sample_output(self):
        return torch.rand(self.output_shape, dtype=self.get_output_dtype())

    @abstractmethod
    def get_prediction_set(self):
        """Returns the set of prediction keys returned by this feature."""
        raise NotImplementedError("OutputFeature is missing implementation for get_prediction_set.")

    @classmethod
    @abstractmethod
    def get_output_dtype(cls):
        """Returns the Tensor data type feature outputs."""
        pass

    @property
    @abstractmethod
    def metric_functions(self) -> Dict:
        pass

    def initialize_decoder(self, decoder_parameters):
        decoder_parameters_copy = copy.copy(decoder_parameters)
        # Input to the decoder is the output feature's FC hidden layer.
        decoder_parameters_copy["input_size"] = self.fc_stack.output_shape[-1]
        if "decoder" in decoder_parameters:
            decoder = decoder_parameters["decoder"]
        else:
            decoder = self.decoder
        return get_decoder_cls(self.type(), decoder)(**decoder_parameters_copy)

    def train_loss(self, targets: Tensor, predictions: Dict[str, Tensor], feature_name):
        loss_class = type(self.train_loss_function)
        prediction_key = output_feature_utils.get_feature_concat_name(feature_name, loss_class.get_loss_inputs())
        return self.train_loss_function(predictions[prediction_key], targets)

    def eval_loss(self, targets: Tensor, predictions: Dict[str, Tensor]):
        loss_class = type(self.train_loss_function)
        prediction_key = loss_class.get_loss_inputs()
        return self.eval_loss_function(predictions[prediction_key].detach(), targets)

    def _setup_loss(self):
        loss_kwargs = self.loss_kwargs()
        self.train_loss_function = get_loss_cls(self.type(), self.loss[TYPE])(**loss_kwargs)
        self.eval_loss_function = get_metric_cls(self.type(), self.loss[TYPE])(**loss_kwargs)

    def _setup_metrics(self):
        # needed to shadow class variable
        self.metric_functions = {
            LOSS: self.eval_loss_function,
            **{
                name: cls(**self.loss_kwargs(), **self.metric_kwargs())
                for name, cls in get_metric_classes(self.type()).items()
                if cls.can_report(self)
            },
        }

    @abstractmethod
    def create_predict_module(self) -> PredictModule:
        """Creates and returns a `nn.Module` that converts raw model outputs (logits) to predictions.

        Thos module is needed when generating the Torchscript model using scripting.
        """
        raise NotImplementedError()

    @property
    def prediction_module(self) -> PredictModule:
        """Returns the PredictModule used to convert model outputs to predictions."""
        return self._prediction_module

    def predictions(self, all_decoder_outputs: Dict[str, torch.Tensor], feature_name: str) -> Dict[str, torch.Tensor]:
        """Computes actual predictions from the outputs of feature decoders.

        TODO(Justin): Consider refactoring this to accept feature-specific decoder outputs.

        Args:
            all_decoder_outputs: A dictionary of {feature name}::{tensor_name} -> output tensor.
        Returns:
            Dictionary of tensors with predictions as well as any additional tensors that may be
            necessary for computing evaluation metrics.
        """
        return self.prediction_module(all_decoder_outputs, feature_name)

    @abstractmethod
    def logits(self, combiner_outputs: Dict[str, torch.Tensor], target=None, **kwargs) -> Dict[str, torch.Tensor]:
        """Unpacks and feeds combiner_outputs to the decoder. Invoked as part of the output feature's forward pass.

        If target is not None, then we are in training.

        Args:
            combiner_outputs: Dictionary of tensors from the combiner's forward pass.
        Returns:
            Dictionary of decoder's output tensors (non-normalized), as well as any additional
            tensors that may be necessary for computing predictions or evaluation metrics.
        """
        raise NotImplementedError("OutputFeature is missing logits() implementation.")

    def loss_kwargs(self) -> Dict[str, Any]:
        """Returns arguments that are used to instantiate an instance of the loss class."""
        return {}

    def metric_kwargs(self) -> Dict[str, Any]:
        """Returns arguments that are used to instantiate an instance of each metric class."""
        return {}

    def update_metrics(self, targets: Tensor, predictions: Dict[str, Tensor]) -> None:
        """Updates metrics with the given targets and predictions.

        Args:
            targets: Tensor with target values for this output feature.
            predictions: Dict of tensors returned by predictions().
        """
        for _, metric_fn in self.metric_functions.items():
            metric_class = type(metric_fn)
            prediction_key = metric_class.get_inputs()
            # TODO(shreya): Metrics should ideally just move to the correct device
            #  and not require the user to do this. This is a temporary fix. See
            #  if this can be removed before merging the PR.
            metric_fn = metric_fn.to(predictions[prediction_key].device)
            metric_fn.update(predictions[prediction_key].detach(), targets)

    def get_metrics(self):
        metric_vals = {}
        for metric_name, metric_fn in self.metric_functions.items():
            try:
                metric_vals[metric_name] = get_scalar_from_ludwig_metric(metric_fn)
            except Exception as e:
                logger.error(f"Caught exception computing metric: {metric_name}. Exception: {e}")
        return metric_vals

    def reset_metrics(self):
        for _, metric_fn in self.metric_functions.items():
            if metric_fn is not None:
                metric_fn.reset()

    def forward(
        self,
        combiner_outputs: Dict[str, torch.Tensor],
        other_output_feature_outputs: Dict[str, torch.Tensor],
        mask: Optional[torch.Tensor] = None,
        target: Optional[torch.Tensor] = None,
    ) -> Dict[str, torch.Tensor]:
        """Forward pass that takes in output from the combiner, and passes it through to the decoder.

        Args:
            combiner_outputs: Dict of outputs from the combiner.
            other_output_feature_outputs: Dict of tensors from other output features. Used for resolving dependencies.
            mask: (Unused). Tensor for masking.
            target: Tensor with targets. During training, targets != None. During prediction, targets = None.

        Returns:
            Dict of output tensors, with at least 'last_hidden' and 'logits' as keys, as well as any additional tensor
            results from the decoder.
        """
        # extract the combined hidden layer
        combiner_hidden = combiner_outputs["combiner_output"]
        hidden = self.prepare_decoder_inputs(combiner_hidden, other_output_feature_outputs, mask=mask)

        # ================ Predictions ================
        logits_input = {HIDDEN: hidden}
        # pass supplemental data from encoders to decoder
        if "encoder_output_state" in combiner_outputs:
            logits_input["encoder_output_state"] = combiner_outputs["encoder_output_state"]
        if LENGTHS in combiner_outputs:
            logits_input[LENGTHS] = combiner_outputs[LENGTHS]

        logits = self.logits(logits_input, target=target)

        # For binary and numerical features, self.logits() is a tensor.
        # There are two special cases where self.logits() is a dict:
        #   categorical
        #       keys: logits, projection_input
        #   sequence
        #       keys: logits
        # TODO(Justin): Clean this up.
        if isinstance(logits, Tensor):
            logits = {"logits": logits}

        # For multi-class features, we must choose a consistent tuple subset.
        return {
            # last_hidden used for dependencies processing
            "last_hidden": hidden,
            **logits,
        }

    def overall_statistics_metadata(self):
        """Additional metadata used to extend `training_set_metadata`.

        Used when calculating the overall statistics.
        """
        return {}

    @property
    @abstractmethod
    def default_validation_metric(self):
        pass

    @abstractmethod
    def postprocess_predictions(
        self,
        result: Dict[str, Tensor],
        metadata: Dict[str, Any],
        output_directory: str,
        backend,
    ):
        raise NotImplementedError

    @staticmethod
    def create_postproc_module(metadata: Dict[str, Any]) -> torch.nn.Module:
        raise NotImplementedError("Torchscript tracing not supported for feature")

    @staticmethod
    @abstractmethod
    def update_config_with_metadata(output_feature, feature_metadata, *args, **kwargs):
        pass

    @staticmethod
    @abstractmethod
    def calculate_overall_stats(predictions, targets, train_set_metadata):
        pass

    @staticmethod
    @abstractmethod
    def populate_defaults(input_feature):
        pass

    def output_specific_fully_connected(self, inputs, mask=None):
        feature_hidden = inputs
        original_feature_hidden = inputs

        # flatten inputs
        if len(original_feature_hidden.shape) > 2:
            feature_hidden = torch.reshape(feature_hidden, (-1, list(feature_hidden.shape)[-1]))

        # pass it through fc_stack
        feature_hidden = self.fc_stack(feature_hidden, mask=mask)
        feature_hidden_size = feature_hidden.shape[-1]

        # reshape back to original first and second dimension
        if len(original_feature_hidden.shape) > 2:
            sequence_length = original_feature_hidden.shape[1]
            feature_hidden = torch.reshape(feature_hidden, (-1, sequence_length, feature_hidden_size))

        return feature_hidden

    def prepare_decoder_inputs(
        self, combiner_hidden: Tensor, other_output_features: Dict[str, Tensor], mask=None
    ) -> Tensor:
        """Takes the combiner output and the outputs of other outputs features computed so far and performs:

        - reduction of combiner outputs (if needed)
        - concatenating the outputs of dependent features (if needed)
        - output_specific fully connected layers (if needed)

        Args:
            combiner_hidden: hidden state of the combiner
            other_output_features: output tensors from other output features
        """
        # ================ Reduce Inputs ================
        feature_hidden = combiner_hidden
        if self.reduce_input is not None and len(combiner_hidden.shape) > 2:
            feature_hidden = self.reduce_sequence_input(combiner_hidden)

        # ================ Concat Dependencies ================
        if self.dependencies:
            feature_hidden = output_feature_utils.concat_dependencies(
                self.column, self.dependencies, self.dependency_reducers, feature_hidden, other_output_features
            )

        # ================ Output-wise Fully Connected ================
        feature_hidden = self.output_specific_fully_connected(feature_hidden, mask=mask)

        return feature_hidden

    def flatten(self, df: DataFrame) -> DataFrame:
        """Converts the output of batch_predict to a 1D array."""
        return df

    def unflatten(self, df: DataFrame) -> DataFrame:
        """Reshapes a flattened 1D array into its original shape."""
        return df
