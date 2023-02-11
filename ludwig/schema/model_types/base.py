import copy
from abc import ABC
from typing import Any, Dict, Optional

from marshmallow import ValidationError

from ludwig.api_annotations import DeveloperAPI
from ludwig.constants import BACKEND, ENCODER, INPUT_FEATURES, MODEL_ECD, PREPROCESSING, TYPE
from ludwig.globals import LUDWIG_VERSION
from ludwig.schema import utils as schema_utils
from ludwig.schema.defaults.defaults import DefaultsConfig
from ludwig.schema.features.base import BaseInputFeatureConfig, BaseOutputFeatureConfig, FeatureCollection
from ludwig.schema.hyperopt import HyperoptConfig
from ludwig.schema.model_types.utils import (
    merge_fixed_preprocessing_params,
    merge_with_defaults,
    set_derived_feature_columns_,
    set_hyperopt_defaults_,
    set_validation_parameters,
)
from ludwig.schema.preprocessing import PreprocessingConfig
from ludwig.schema.trainer import BaseTrainerConfig
from ludwig.schema.utils import ludwig_dataclass
from ludwig.utils.backward_compatibility import upgrade_config_dict_to_latest_version
from ludwig.utils.data_utils import load_yaml
from ludwig.utils.registry import Registry

model_type_schema_registry = Registry()


@DeveloperAPI
@ludwig_dataclass
class ModelConfig(schema_utils.BaseMarshmallowConfig, ABC):
    input_features: FeatureCollection[BaseInputFeatureConfig]
    output_features: FeatureCollection[BaseOutputFeatureConfig]

    model_type: str

    trainer: BaseTrainerConfig
    preprocessing: PreprocessingConfig
    defaults: DefaultsConfig
    hyperopt: Optional[HyperoptConfig] = None

    backend: Dict[str, Any] = schema_utils.Dict()
    ludwig_version: str = LUDWIG_VERSION

    @staticmethod
    def from_dict(config: Dict[str, Any]) -> "ModelConfig":
        config = copy.deepcopy(config)
        config = upgrade_config_dict_to_latest_version(config)
        config = merge_with_defaults(config)

        model_type = config.get("model_type", MODEL_ECD)
        if model_type not in model_type_schema_registry:
            raise ValidationError(
                f"Invalid model type: '{model_type}', expected one of: {list(model_type_schema_registry.keys())}"
            )

        # TODO(travis): move this into helper function
        # Update preprocessing parameters if encoders require fixed preprocessing parameters
        for feature_config in config.get(INPUT_FEATURES, []):
            if TYPE not in feature_config:
                continue

            preprocessing_parameters = feature_config.get(PREPROCESSING, {})
            preprocessing_parameters = merge_fixed_preprocessing_params(
                model_type, feature_config[TYPE], preprocessing_parameters, feature_config.get(ENCODER, {})
            )
            preprocessing_parameters = _merge_encoder_cache_params(
                preprocessing_parameters, feature_config.get(ENCODER, {})
            )
            feature_config[PREPROCESSING] = preprocessing_parameters

        # TODO(travis): handle this with helper function
        backend = config.get(BACKEND)
        if isinstance(backend, str):
            config[BACKEND] = {"type": backend}

        cls = model_type_schema_registry[model_type]
        schema = cls.get_class_schema()()
        config_obj: ModelConfig = schema.load(config)

        # TODO(travis): do this post-processing stuff at the dict level before we load
        set_validation_parameters(config_obj)
        set_hyperopt_defaults_(config_obj)

        # Derive proc_col for each feature from the feature's preprocessing parameters
        # after all preprocessing parameters have been set
        set_derived_feature_columns_(config_obj)

        return config_obj

    @staticmethod
    def from_yaml(config_path: str) -> "ModelConfig":
        return ModelConfig.from_dict(load_yaml(config_path))


@DeveloperAPI
def register_model_type(name: str):
    def wrap(model_type_config: ModelConfig) -> ModelConfig:
        model_type_schema_registry[name] = model_type_config
        return model_type_config

    return wrap


def _merge_encoder_cache_params(preprocessing_params: Dict[str, Any], encoder_params: Dict[str, Any]) -> Dict[str, Any]:
    if preprocessing_params.get("cache_encoder_embeddings"):
        preprocessing_params[ENCODER] = encoder_params
    return preprocessing_params
