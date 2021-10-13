#! /usr/bin/env python
# coding=utf-8
# Copyright (c) 2020 Uber Technologies, Inc.
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
from collections import defaultdict
from functools import partial
from typing import Any, Dict, List

import dask
import pandas as pd
import ray
from horovod.ray import RayExecutor
from ray.util.dask import ray_dask_get

from ludwig.backend.base import Backend, RemoteTrainingMixin
from ludwig.constants import NAME, PARQUET, TFRECORD, PREPROCESSING
from ludwig.data.dataframe.dask import DaskEngine
from ludwig.data.dataframe.pandas import PandasEngine
from ludwig.data.dataset.pandas import PandasDataset
from ludwig.data.dataset.ray import RayDataset
from ludwig.models.ecd import ECD
from ludwig.models.predictor import BasePredictor, Predictor, get_output_columns
from ludwig.models.trainer import BaseTrainer, RemoteTrainer
from ludwig.utils.tf_utils import initialize_tensorflow, save_weights_to_buffer, load_weights_from_buffer


logger = logging.getLogger(__name__)


def get_horovod_kwargs(use_gpu=None):
    # Our goal is to have a worker per resource used for training.
    # The priority is GPUs, but can fall back to CPUs if there are no
    # GPUs available.
    if use_gpu is None:
        use_gpu = int(ray.cluster_resources().get('GPU', 0)) > 0

    resource = 'GPU' if use_gpu else 'CPU'
    num_workers = int(ray.cluster_resources().get(resource, 0))

    return dict(
        num_workers=num_workers,
        use_gpu=use_gpu,
    )


_engine_registry = {
    'dask': DaskEngine,
    'pandas': PandasEngine,
}


def _get_df_engine(processor):
    logger.info(f"Ray processor params: {processor}")
    if processor is None:
        # TODO ray: find an informed way to set the parallelism, in practice
        #  it looks like Dask handles this well on its own most of the time
        return DaskEngine()

    processor_kwargs = processor.copy()

    dtype = processor_kwargs.pop('type', 'dask')
    engine_cls = _engine_registry.get(dtype)

    return engine_cls(**processor_kwargs)


class RayRemoteModel:
    def __init__(self, model: ECD):
        buf = save_weights_to_buffer(model)
        self.cls = type(model)
        self.args = model.get_args()
        self.state = ray.put(buf)

    def load(self):
        obj = self.cls(*self.args)
        buf = ray.get(self.state)
        load_weights_from_buffer(obj, buf)
        return obj


class RayRemoteTrainer(RemoteTrainer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def train(self, *args, **kwargs):
        results = super().train(*args, **kwargs)
        if results is not None:
            model, *stats = results
            results = (save_weights_to_buffer(model), *stats)
        return results

    def train_online(self, *args, **kwargs):
        results = super().train_online(*args, **kwargs)
        if results is not None:
            results = save_weights_to_buffer(results)
        return results


class RayTrainer(BaseTrainer):
    def __init__(self, horovod_kwargs, trainer_kwargs):
        # TODO ray: make this more configurable by allowing YAML overrides of timeout_s, etc.
        setting = RayExecutor.create_settings(timeout_s=30)
        self.executor = RayExecutor(
            setting, **{**get_horovod_kwargs(), **horovod_kwargs})
        self.executor.start(executable_cls=RayRemoteTrainer,
                            executable_kwargs=trainer_kwargs)

    def train(self, model, *args, **kwargs):
        remote_model = RayRemoteModel(model)
        results = self.executor.execute(
            lambda trainer: trainer.train(remote_model.load(), *args, **kwargs)
        )

        weights, *stats = results[0]
        load_weights_from_buffer(model, weights)
        return (model, *stats)

    def train_online(self, model, *args, **kwargs):
        remote_model = RayRemoteModel(model)
        results = self.executor.execute(
            lambda trainer: trainer.train_online(
                remote_model.load(), *args, **kwargs)
        )

        weights = results[0]
        load_weights_from_buffer(model, weights)
        return model

    @property
    def validation_field(self):
        return self.executor.execute_single(lambda trainer: trainer.validation_field)

    @property
    def validation_metric(self):
        return self.executor.execute_single(lambda trainer: trainer.validation_metric)

    def shutdown(self):
        self.executor.shutdown()


class RayPredictor(BasePredictor):
    def __init__(self, horovod_kwargs, predictor_kwargs):
        # TODO ray: use horovod_kwargs to allocate GPU model replicas
        self.predictor_kwargs = predictor_kwargs
        self.actor_handles = []

    def batch_predict(self, model: ECD, dataset: RayDataset, *args, **kwargs):
        self._check_dataset(dataset)

        remote_model = RayRemoteModel(model)
        predictor_kwargs = self.predictor_kwargs
        output_columns = get_output_columns(model.output_features)
        batch_predictor = self.get_batch_infer_model(
            remote_model, predictor_kwargs, output_columns, dataset.features,
            dataset.data_hdf5_fp, *args, **kwargs
        )

        num_gpus = int(ray.cluster_resources().get('GPU', 0) > 0)
        dask_dataset = dataset.ds.map_batches(
            batch_predictor, 
            compute='actors',
            batch_format='pandas',
            num_gpus=num_gpus
        ).to_dask()

        for of_feature in model.output_features.values():
            dask_dataset = of_feature.unflatten(dask_dataset)

        return dask_dataset

    def batch_evaluation(self, model, dataset, collect_predictions=False, **kwargs):
        raise NotImplementedError(
            'Ray backend does not support batch evaluation at this time.'
        )

    def batch_collect_activations(self, model, *args, **kwargs):
        raise NotImplementedError(
            'Ray backend does not support collecting activations at this time.'
        )

    def _check_dataset(self, dataset):
        if not isinstance(dataset, RayDataset):
            raise RuntimeError(
                f'Ray backend requires RayDataset for inference, '
                f'found: {type(dataset)}'
            )

    def shutdown(self):
        for handle in self.actor_handles:
            ray.kill(handle)
        self.actor_handles.clear()

    def get_batch_infer_model(
            self,
            remote_model: RayRemoteModel,
            predictor_kwargs: Dict[str, Any],
            output_columns: List[str],
            features: List[Dict],
            data_hdf5_fp: str,
            *args, **kwargs
    ):
        class BatchInferModel:
            def __init__(self):
                self.model = remote_model.load()
                self.output_columns = output_columns
                self.features = features
                self.data_hdf5_fp = data_hdf5_fp
                predictor = Predictor(**predictor_kwargs)
                self.batch_predict = partial(predictor.batch_predict, *args, **kwargs)

            def __call__(self, df: pd.DataFrame) -> pd.DataFrame:
                pd_ds = PandasDataset(df, self.features, self.data_hdf5_fp)
                predictions = self.batch_predict(model=self.model, dataset=pd_ds)

                for output_feature in self.model.output_features.values():
                    predictions = output_feature.flatten(predictions)
                ordered_predictions = predictions[self.output_columns]
                return ordered_predictions

        return BatchInferModel


class RayBackend(RemoteTrainingMixin, Backend):
    def __init__(self, processor=None, trainer=None, cache_format=PARQUET, **kwargs):
        super().__init__(cache_format=cache_format, **kwargs)
        self._df_engine = _get_df_engine(processor)
        self._horovod_kwargs = trainer or {}
        self._tensorflow_kwargs = {}
        if cache_format not in [PARQUET, TFRECORD]:
            raise ValueError(
                f'Data format {cache_format} is not supported when using the Ray backend. '
                f'Try setting to `parquet`.'
            )

    def initialize(self):
        if not ray.is_initialized():
            try:
                ray.init('auto', ignore_reinit_error=True)
            except ConnectionError:
                logger.info('Initializing new Ray cluster...')
                ray.init(ignore_reinit_error=True)

        dask.config.set(scheduler=ray_dask_get)

    def initialize_tensorflow(self, **kwargs):
        # Make sure we don't claim any GPU resources on the head node
        initialize_tensorflow(gpus=-1)
        self._tensorflow_kwargs = kwargs

    def create_trainer(self, **kwargs):
        executable_kwargs = {**kwargs, **self._tensorflow_kwargs}
        return RayTrainer(self._horovod_kwargs, executable_kwargs)

    def create_predictor(self, **kwargs):
        executable_kwargs = {**kwargs, **self._tensorflow_kwargs}
        return RayPredictor(self._horovod_kwargs, executable_kwargs)

    def set_distributed_kwargs(self, **kwargs):
        self._horovod_kwargs = kwargs

    @property
    def df_engine(self):
        return self._df_engine

    @property
    def supports_multiprocessing(self):
        return False

    def check_lazy_load_supported(self, feature):
        if not feature[PREPROCESSING]['in_memory']:
            raise ValueError(f'RayBackend does not support lazy loading of data files at train time. '
                             f'Set preprocessing config `in_memory: True` for feature {feature[NAME]}')
