#! /usr/bin/env python
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
from typing import Any, Dict, List, Tuple, Union

import numpy as np
import torch

from ludwig.constants import (
    ACCURACY,
    BINARY,
    COLUMN,
    DECODER,
    DEPENDENCIES,
    ENCODER,
    HIDDEN,
    LOGITS,
    LOSS,
    NAME,
    PREDICTIONS,
    PROBABILITIES,
    PROBABILITY,
    PROC_COLUMN,
    REDUCE_DEPENDENCIES,
    REDUCE_INPUT,
    ROC_AUC,
    THRESHOLD,
    TIED,
    TYPE,
)
from ludwig.features.base_feature import BaseFeatureMixin, InputFeature, OutputFeature, PredictModule
from ludwig.schema.features.binary_feature import BinaryInputFeatureConfig, BinaryOutputFeatureConfig
from ludwig.schema.features.utils import register_input_feature, register_output_feature
from ludwig.utils import calibration, output_feature_utils, strings_utils
from ludwig.utils.eval_utils import (
    average_precision_score,
    ConfusionMatrix,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)
from ludwig.utils.misc_utils import set_default_value, set_default_values
from ludwig.utils.types import DataFrame, TorchscriptPreprocessingInput

logger = logging.getLogger(__name__)


class _BinaryPreprocessing(torch.nn.Module):
    def __init__(self, metadata: Dict[str, Any]):
        super().__init__()
        str2bool = metadata.get("str2bool")
        self.str2bool = str2bool or {v: True for v in strings_utils.BOOL_TRUE_STRS}
        self.should_lower = str2bool is None

    def forward(self, v: TorchscriptPreprocessingInput) -> torch.Tensor:
        if torch.jit.isinstance(v, List[Tuple[torch.Tensor, int]]):
            raise ValueError(f"Unsupported input: {v}")

        if torch.jit.isinstance(v, List[torch.Tensor]):
            v = torch.stack(v)

        if torch.jit.isinstance(v, torch.Tensor):
            return v.to(dtype=torch.float32)

        v = [s.strip() for s in v]
        if self.should_lower:
            v = [s.lower() for s in v]
        indices = [self.str2bool.get(s, False) for s in v]
        return torch.tensor(indices, dtype=torch.float32)


class _BinaryPostprocessing(torch.nn.Module):
    def __init__(self, metadata: Dict[str, Any]):
        super().__init__()
        bool2str = metadata.get("bool2str")
        self.bool2str = {i: v for i, v in enumerate(bool2str)} if bool2str is not None else None
        self.predictions_key = PREDICTIONS
        self.probabilities_key = PROBABILITIES

    def forward(self, preds: Dict[str, torch.Tensor], feature_name: str) -> Dict[str, Any]:
        predictions = output_feature_utils.get_output_feature_tensor(preds, feature_name, self.predictions_key)
        probabilities = output_feature_utils.get_output_feature_tensor(preds, feature_name, self.probabilities_key)

        if self.bool2str is not None:
            predictions = predictions.to(dtype=torch.int32)
            predictions = [self.bool2str.get(pred, self.bool2str[0]) for pred in predictions]

        probabilities = torch.stack([1 - probabilities, probabilities], dim=-1)

        return {
            self.predictions_key: predictions,
            self.probabilities_key: probabilities,
        }


class _BinaryPredict(PredictModule):
    def __init__(self, threshold, calibration_module=None):
        super().__init__()
        self.threshold = threshold
        self.calibration_module = calibration_module

    def forward(self, inputs: Dict[str, torch.Tensor], feature_name: str) -> Dict[str, torch.Tensor]:
        logits = output_feature_utils.get_output_feature_tensor(inputs, feature_name, self.logits_key)

        if self.calibration_module is not None:
            probabilities = self.calibration_module(logits)
        else:
            probabilities = torch.sigmoid(logits)

        predictions = probabilities >= self.threshold
        return {
            self.probabilities_key: probabilities,
            self.predictions_key: predictions,
            self.logits_key: logits,
        }


class BinaryFeatureMixin(BaseFeatureMixin):
    @staticmethod
    def type():
        return BINARY

    @staticmethod
    def preprocessing_defaults() -> Dict[str, Any]:
        return BinaryInputFeatureConfig().preprocessing.to_dict()

    @staticmethod
    def cast_column(column, backend):
        """Cast column of dtype object to bool.

        Unchecked casting to boolean when given a column of dtype object converts all non-empty cells to True. We check
        the values of the column directly and manually determine the best dtype to use.
        """
        values = backend.df_engine.compute(column.drop_duplicates())

        if strings_utils.values_are_pandas_numbers(values):
            # If numbers, convert to float so it can be converted to bool
            column = column.astype(float).astype(bool)
        elif strings_utils.values_are_pandas_bools(values):
            # If booleans, manually assign boolean values
            column = backend.df_engine.map_objects(
                column, lambda x: x.lower() in strings_utils.PANDAS_TRUE_STRS
            ).astype(bool)
        else:
            # If neither numbers or booleans, they are strings (objects)
            column = column.astype(object)
        return column

    @staticmethod
    def get_feature_meta(column: DataFrame, preprocessing_parameters: Dict[str, Any], backend) -> Dict[str, Any]:
        if column.dtype != object:
            return {}

        distinct_values = backend.df_engine.compute(column.drop_duplicates())
        if len(distinct_values) > 2:
            raise ValueError(
                f"Binary feature column {column.name} expects 2 distinct values, "
                f"found: {distinct_values.values.tolist()}"
            )
        if preprocessing_parameters["fallback_true_label"]:
            fallback_true_label = preprocessing_parameters["fallback_true_label"]
        else:
            fallback_true_label = sorted(distinct_values)[0]
            preprocessing_parameters["fallback_true_label"] = fallback_true_label

        try:
            str2bool = {v: strings_utils.str2bool(v) for v in distinct_values}
        except Exception as e:
            logger.warning(
                f"Binary feature {column.name} has at least 1 unconventional boolean value: {e}. "
                f"We will now interpret {fallback_true_label} as 1 and the other values as 0. "
                f"If this is incorrect, please use the category feature type or "
                f"manually specify the true value with `preprocessing.fallback_true_label`."
            )
            str2bool = {v: strings_utils.str2bool(v, fallback_true_label) for v in distinct_values}

        bool2str = [k for k, v in sorted(str2bool.items(), key=lambda item: item[1])]
        return {"str2bool": str2bool, "bool2str": bool2str, "fallback_true_label": fallback_true_label}

    @staticmethod
    def add_feature_data(
        feature_config: Dict[str, Any],
        input_df: DataFrame,
        proc_df: Dict[str, DataFrame],
        metadata: Dict[str, Any],
        preprocessing_parameters: Dict[str, Any],
        backend,
        skip_save_processed_input: bool,
    ) -> None:
        column = input_df[feature_config[COLUMN]]

        if column.dtype == object:
            metadata = metadata[feature_config[NAME]]
            if "str2bool" in metadata:
                column = backend.df_engine.map_objects(column, lambda x: metadata["str2bool"][str(x)])
            else:
                # No predefined mapping from string to bool, so compute it directly
                column = backend.df_engine.map_objects(column, strings_utils.str2bool)

        proc_df[feature_config[PROC_COLUMN]] = column.astype(np.bool_)
        return proc_df


@register_input_feature(BINARY)
class BinaryInputFeature(BinaryFeatureMixin, InputFeature):
    def __init__(self, input_feature_config: Union[BinaryInputFeatureConfig, Dict], encoder_obj=None, **kwargs):
        input_feature_config = self.load_config(input_feature_config)
        super().__init__(input_feature_config, **kwargs)

        if encoder_obj:
            self.encoder_obj = encoder_obj
        else:
            self.encoder_obj = self.initialize_encoder(input_feature_config.encoder)

    def forward(self, inputs):
        assert isinstance(inputs, torch.Tensor)
        assert inputs.dtype in [torch.bool, torch.int64, torch.float32]
        assert len(inputs.shape) == 1 or (len(inputs.shape) == 2 and inputs.shape[1] == 1)

        if len(inputs.shape) == 1:
            inputs = inputs[:, None]
        encoder_outputs = self.encoder_obj(inputs)
        return {"encoder_output": encoder_outputs}

    @property
    def input_dtype(self):
        return torch.bool

    @property
    def input_shape(self) -> torch.Size:
        return torch.Size([1])

    @property
    def output_shape(self) -> torch.Size:
        return self.encoder_obj.output_shape

    @staticmethod
    def update_config_with_metadata(input_feature, feature_metadata, *args, **kwargs):
        pass

    @staticmethod
    def populate_defaults(input_feature):
        defaults = BinaryInputFeatureConfig()
        set_default_value(input_feature, TIED, defaults.tied)
        set_default_values(input_feature, {ENCODER: {TYPE: defaults.encoder.type}})

    @staticmethod
    def get_schema_cls():
        return BinaryInputFeatureConfig

    def create_sample_input(self):
        return torch.Tensor([True, False])

    @classmethod
    def get_preproc_input_dtype(cls, metadata: Dict[str, Any]) -> str:
        return "string" if metadata.get("str2bool") else "int32"

    @staticmethod
    def create_preproc_module(metadata: Dict[str, Any]) -> torch.nn.Module:
        return _BinaryPreprocessing(metadata)


@register_output_feature(BINARY)
class BinaryOutputFeature(BinaryFeatureMixin, OutputFeature):
    metric_functions = {LOSS: None, ACCURACY: None, ROC_AUC: None}
    default_validation_metric = ROC_AUC

    def __init__(
        self,
        output_feature_config: Union[BinaryOutputFeatureConfig, Dict],
        output_features: Dict[str, OutputFeature],
        **kwargs,
    ):
        output_feature_config = self.load_config(output_feature_config)
        self.threshold = output_feature_config.threshold
        super().__init__(output_feature_config, output_features, **kwargs)
        self.decoder_obj = self.initialize_decoder(output_feature_config.decoder)
        self._setup_loss()
        self._setup_metrics()

    def logits(self, inputs, **kwargs):
        hidden = inputs[HIDDEN]
        return self.decoder_obj(hidden)

    def loss_kwargs(self):
        return dict(
            positive_class_weight=self.loss.positive_class_weight,
            robust_lambda=self.loss.robust_lambda,
            confidence_penalty=self.loss.confidence_penalty,
        )

    def create_calibration_module(self, feature: BinaryOutputFeatureConfig) -> torch.nn.Module:
        """Creates the appropriate calibration module based on the feature config.

        Today, only one type of calibration ("temperature_scaling") is available, but more options may be supported in
        the future.
        """
        if feature.calibration:
            calibration_cls = calibration.get_calibration_cls(BINARY, "temperature_scaling")
            return calibration_cls(binary=True)
        return None

    def create_predict_module(self) -> PredictModule:
        # A lot of code assumes output features have a prediction module, but if we are using GBM then passthrough
        # decoder is specified here which has no threshold.
        threshold = getattr(self, "threshold", 0.5)
        return _BinaryPredict(threshold, calibration_module=self.calibration_module)

    def get_prediction_set(self):
        return {PREDICTIONS, PROBABILITIES, LOGITS}

    @classmethod
    def get_output_dtype(cls):
        return torch.bool

    @property
    def output_shape(self) -> torch.Size:
        return torch.Size([1])

    @property
    def input_shape(self) -> torch.Size:
        return torch.Size([1])

    @staticmethod
    def update_config_with_metadata(input_feature, feature_metadata, *args, **kwargs):
        pass

    @staticmethod
    def calculate_overall_stats(predictions, targets, train_set_metadata):
        overall_stats = {}
        confusion_matrix = ConfusionMatrix(targets, predictions[PREDICTIONS], labels=["False", "True"])
        overall_stats["confusion_matrix"] = confusion_matrix.cm.tolist()
        overall_stats["overall_stats"] = confusion_matrix.stats()
        overall_stats["per_class_stats"] = confusion_matrix.per_class_stats()
        fpr, tpr, thresholds = roc_curve(targets, predictions[PROBABILITIES])
        overall_stats["roc_curve"] = {
            "false_positive_rate": fpr.tolist(),
            "true_positive_rate": tpr.tolist(),
        }
        overall_stats["roc_auc_macro"] = roc_auc_score(targets, predictions[PROBABILITIES], average="macro")
        overall_stats["roc_auc_micro"] = roc_auc_score(targets, predictions[PROBABILITIES], average="micro")
        ps, rs, thresholds = precision_recall_curve(targets, predictions[PROBABILITIES])
        overall_stats["precision_recall_curve"] = {
            "precisions": ps.tolist(),
            "recalls": rs.tolist(),
        }
        overall_stats["average_precision_macro"] = average_precision_score(
            targets, predictions[PROBABILITIES], average="macro"
        )
        overall_stats["average_precision_micro"] = average_precision_score(
            targets, predictions[PROBABILITIES], average="micro"
        )
        overall_stats["average_precision_samples"] = average_precision_score(
            targets, predictions[PROBABILITIES], average="samples"
        )

        return overall_stats

    def postprocess_predictions(
        self,
        result,
        metadata,
    ):
        class_names = ["False", "True"]
        if "bool2str" in metadata:
            class_names = metadata["bool2str"]

        predictions_col = f"{self.feature_name}_{PREDICTIONS}"
        if predictions_col in result:
            if "bool2str" in metadata:
                result[predictions_col] = result[predictions_col].map(
                    lambda pred: metadata["bool2str"][pred],
                )

        probabilities_col = f"{self.feature_name}_{PROBABILITIES}"
        if probabilities_col in result:
            false_col = f"{probabilities_col}_{class_names[0]}"
            true_col = f"{probabilities_col}_{class_names[1]}"
            prob_col = f"{self.feature_name}_{PROBABILITY}"

            result = result.assign(
                **{
                    false_col: lambda x: 1 - x[probabilities_col],
                    true_col: lambda x: x[probabilities_col],
                    prob_col: np.where(
                        result[probabilities_col] > 0.5, result[probabilities_col], 1 - result[probabilities_col]
                    ),
                    probabilities_col: result.apply(lambda x: [1 - x[probabilities_col], x[probabilities_col]], axis=1),
                }
            )

        return result

    @staticmethod
    def populate_defaults(output_feature):
        defaults = BinaryOutputFeatureConfig()

        # If Loss is not defined, set an empty dictionary
        set_default_value(output_feature, LOSS, {})
        set_default_values(output_feature[LOSS], defaults.loss.Schema().dump(defaults.loss))

        set_default_values(
            output_feature,
            {
                DECODER: {
                    TYPE: defaults.decoder.type,
                },
                DEPENDENCIES: defaults.dependencies,
                REDUCE_INPUT: defaults.reduce_input,
                REDUCE_DEPENDENCIES: defaults.reduce_dependencies,
                THRESHOLD: defaults.threshold,
            },
        )

    @staticmethod
    def get_schema_cls():
        return BinaryOutputFeatureConfig

    @classmethod
    def get_postproc_output_dtype(cls, metadata: Dict[str, Any]) -> str:
        return "string" if metadata.get("bool2str") else "int32"

    @staticmethod
    def create_postproc_module(metadata: Dict[str, Any]) -> torch.nn.Module:
        return _BinaryPostprocessing(metadata)
