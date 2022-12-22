import os
from contextlib import contextmanager
from typing import Dict, Optional, Tuple, Union

import lightgbm as lgb
import numpy as np
import torch
import torchmetrics
from hummingbird.ml import convert
from hummingbird.ml.operator_converters import constants as hb_constants

from ludwig.constants import BINARY, CATEGORY, LOGITS, MODEL_GBM, NAME, NUMBER
from ludwig.features.base_feature import OutputFeature
from ludwig.globals import MODEL_WEIGHTS_FILE_NAME
from ludwig.models.base import BaseModel
from ludwig.schema.model_config import ModelConfig, OutputFeaturesContainer
from ludwig.utils import output_feature_utils
from ludwig.utils.torch_utils import get_torch_device
from ludwig.utils.types import TorchDevice


class GBM(BaseModel):
    @staticmethod
    def type() -> str:
        return MODEL_GBM

    def __init__(
        self,
        config_obj: ModelConfig,
        random_seed: int = None,
        **_kwargs,
    ):
        self.config_obj = config_obj
        self._random_seed = random_seed

        super().__init__(random_seed=self._random_seed)

        # ================ Inputs ================
        try:
            self.input_features.update(self.build_inputs(input_feature_configs=self.config_obj.input_features))
        except KeyError as e:
            raise KeyError(
                f"An input feature has a name that conflicts with a class attribute of torch's ModuleDict: {e}"
            )

        # ================ Outputs ================
        self.output_features.update(
            self.build_outputs(output_feature_configs=self.config_obj.output_features, input_size=self.input_shape[-1])
        )

        # ================ Combined loss metric ================
        self.eval_loss_metric = torchmetrics.MeanMetric()
        self.eval_additional_losses_metrics = torchmetrics.MeanMetric()

        self.lgbm_model: lgb.LGBMModel = None
        self.compiled_model: torch.nn.Module = None

    @classmethod
    def build_outputs(
        cls, output_feature_configs: OutputFeaturesContainer, input_size: int
    ) -> Dict[str, OutputFeature]:
        """Builds and returns output feature."""
        # TODO: only single task currently
        if len(output_feature_configs.to_dict()) > 1:
            raise ValueError("Only single task currently supported")

        output_feature_def = output_feature_configs.to_list()[0]
        output_features = {}

        setattr(getattr(output_feature_configs, output_feature_def[NAME]), "input_size", input_size)
        output_feature = cls.build_single_output(
            getattr(output_feature_configs, output_feature_def[NAME]), output_features
        )
        output_features[output_feature_def[NAME]] = output_feature

        return output_features

    @contextmanager
    def compile(self):
        """Convert the LightGBM model to a PyTorch model and store internally."""
        if self.lgbm_model is None:
            raise ValueError("Model has not been trained yet.")

        # explicitly use sigmoid for classification, so we can invert to logits at inference time
        extra_config = (
            {hb_constants.POST_TRANSFORM: hb_constants.SIGMOID}
            if isinstance(self.lgbm_model, lgb.LGBMClassifier)
            else {}
        )
        try:
            self.compiled_model = convert(self.lgbm_model, "torch", extra_config=extra_config)
            yield
        finally:
            self.compiled_model = None

    def forward(
        self,
        inputs: Union[
            Dict[str, torch.Tensor], Dict[str, np.ndarray], Tuple[Dict[str, torch.Tensor], Dict[str, torch.Tensor]]
        ],
        mask=None,
    ) -> Dict[str, torch.Tensor]:
        # Invoke output features.
        output_logits = {}
        output_feature_name = self.output_features.keys()[0]
        output_feature = self.output_features[output_feature_name]

        # If the model has not been compiled, predict using the LightGBM sklearn iterface. Otherwise, use torch with
        # the Hummingbird compiled model. Notably, when compiling the model to torchscript, compiling with Hummingbird
        # first should preserve the torch predictions code path.
        if self.compiled_model is None:
            # If `inputs` is a tuple, it should contain `(inputs, targets)``. When using the LGBM sklearn interface,
            # `targets` is not needed, so it is discarded here.
            if isinstance(inputs, tuple):
                inputs, _ = inputs

            assert list(inputs.keys()) == self.input_features.keys()

            # The LGBM sklearn interface works with array-likes, so we place the inputs into a 2D numpy array.
            in_array = np.stack(list(inputs.values()), axis=0).T

            # Predict on the input batch and convert the predictions to torch tensors so that they are compatible with
            # the existing metrics modules.
            # Input: 2D eval_batch_size x n_features array
            # Output: 1D eval_batch_size array if regression, else 2D eval_batch_size x n_classes array
            logits = torch.from_numpy(self.lgbm_model.predict(in_array, raw_score=True))

            if output_feature.type() == CATEGORY and logits.ndim == 1:
                # add logits for the negative class (LightGBM classifier only returns logits for the positive class)
                logits = logits.view(-1, 1)
                logits = torch.cat([-logits, logits], dim=1)

        else:
            if isinstance(inputs, tuple):
                inputs, targets = inputs
                # Convert targets to tensors.
                for target_feature_name, target_value in targets.items():
                    if not isinstance(target_value, torch.Tensor):
                        targets[target_feature_name] = torch.from_numpy(target_value)
                    else:
                        targets[target_feature_name] = target_value
            else:
                targets = None

            assert list(inputs.keys()) == self.input_features.keys()

            # Convert inputs to tensors of type float as expected by hummingbird GEMMTreeImpl.
            for input_feature_name, input_values in inputs.items():
                if not isinstance(input_values, torch.Tensor):
                    inputs[input_feature_name] = torch.from_numpy(input_values).float()
                else:
                    inputs[input_feature_name] = input_values.view(-1, 1).float()

            # TODO(travis): include encoder and decoder steps during inference
            # encoder_outputs = {}
            # for input_feature_name, input_values in inputs.items():
            #     encoder = self.input_features[input_feature_name]
            #     encoder_output = encoder(input_values)
            #     encoder_outputs[input_feature_name] = encoder_output

            # concatenate inputs
            inputs = torch.cat(list(inputs.values()), dim=1)

            assert (
                type(inputs) is torch.Tensor
                and inputs.dtype == torch.float32
                and inputs.ndim == 2
                and inputs.shape[1] == len(self.input_features)
            ), (
                f"Expected inputs to be a 2D tensor of shape (batch_size, {len(self.input_features)}) of type float32, "
                f"but got {inputs.shape} of type {inputs.dtype}"
            )
            # Predict using PyTorch module, so it is included when converting to TorchScript.
            preds = self.compiled_model.model(inputs)

            if output_feature.type() == NUMBER:
                # regression
                logits = preds.view(-1)
            else:
                # classification
                _, probs = preds

                if output_feature.type() == BINARY:
                    # keep positive class only for binary feature
                    probs = probs[:, 1]  # shape (batch_size,)
                elif output_feature.num_classes > 2:
                    probs = probs.view(-1, 2, output_feature.num_classes)  # shape (batch_size, 2, num_classes)
                    probs = probs.transpose(2, 1)  # shape (batch_size, num_classes, 2)

                    # sigmoid probabilities for belonging to each class
                    probs = probs[:, :, 1]  # shape (batch_size, num_classes)

                # invert probabilities to get back logits and use Ludwig's output feature prediction functionality
                logits = torch.logit(probs)

        output_feature_utils.set_output_feature_tensor(output_logits, output_feature_name, LOGITS, logits)

        return output_logits

    def save(self, save_path):
        """Saves the model to the given path."""
        if self.lgbm_model is None:
            raise ValueError("Model has not been trained yet.")

        import joblib

        weights_save_path = os.path.join(save_path, MODEL_WEIGHTS_FILE_NAME)
        joblib.dump(self.lgbm_model, weights_save_path)

    def load(self, save_path):
        """Loads the model from the given path."""
        import joblib

        weights_save_path = os.path.join(save_path, MODEL_WEIGHTS_FILE_NAME)
        self.lgbm_model = joblib.load(weights_save_path)

    def to_torchscript(self, device: Optional[TorchDevice] = None):
        """Converts the ECD model as a TorchScript model."""
        with self.compile():
            # Disable gradient calculation for hummingbird Parameter nodes.
            device = torch.device(get_torch_device())
            self.compiled_model.to(device)
            self.compiled_model.model.requires_grad_(False)
            trace = super().to_torchscript(device)
        return trace

    def get_args(self):
        """Returns init arguments for constructing this model."""
        return self.config_obj.input_features.to_list(), self.config_obj.output_features.to_list(), self._random_seed
