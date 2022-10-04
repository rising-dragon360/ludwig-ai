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
import contextlib
import math
import queue
import threading
from functools import lru_cache
from typing import Any, Dict, Iterator, Optional, Union

import numpy as np
import pandas as pd
import ray
from packaging import version
from pyarrow.fs import FSSpecHandler, PyFileSystem
from ray.data import read_parquet
from ray.data.dataset_pipeline import DatasetPipeline
from ray.data.extensions import TensorDtype

from ludwig.backend.base import Backend
from ludwig.constants import BINARY, CATEGORY, NAME, NUMBER, TYPE
from ludwig.data.batcher.base import Batcher
from ludwig.data.dataset.base import Dataset, DatasetManager
from ludwig.utils.data_utils import DATA_TRAIN_HDF5_FP, DATA_TRAIN_PARQUET_FP
from ludwig.utils.defaults import default_random_seed
from ludwig.utils.fs_utils import get_fs_and_path
from ludwig.utils.misc_utils import get_proc_features
from ludwig.utils.types import DataFrame, Series

_ray113 = version.parse(ray.__version__) == version.parse("1.13.0")
_ray_nightly = version.parse(ray.__version__) > version.parse("1.13")

_SCALAR_TYPES = {BINARY, CATEGORY, NUMBER}

# https://github.com/ray-project/ray/issues/27031
# TODO(geoffrey): remove this once Ray > 1.13 in our CI.
if _ray_nightly:
    from ray.data.extensions import TensorArray

    def cast_as_tensor_dtype(series: Series) -> Series:
        return TensorArray(series)

else:

    def cast_as_tensor_dtype(series: Series) -> Series:
        return series.astype(TensorDtype())


def read_remote_parquet(path: str):
    fs, path = get_fs_and_path(path)
    return read_parquet(path, filesystem=PyFileSystem(FSSpecHandler(fs)))


class RayDataset(Dataset):
    """Wrapper around ray.data.Dataset."""

    def __init__(
        self,
        df: Union[str, DataFrame],
        features: Dict[str, Dict],
        training_set_metadata: Dict[str, Any],
        backend: Backend,
    ):
        self.df_engine = backend.df_engine
        self.ds = self.df_engine.to_ray_dataset(df) if not isinstance(df, str) else read_remote_parquet(df)
        self.features = features
        self.training_set_metadata = training_set_metadata
        self.data_hdf5_fp = training_set_metadata.get(DATA_TRAIN_HDF5_FP)
        self.data_parquet_fp = training_set_metadata.get(DATA_TRAIN_PARQUET_FP)

        # TODO ray 1.8: convert to Tensors before shuffle
        # def to_tensors(df: pd.DataFrame) -> pd.DataFrame:
        #     for c in features.keys():
        #         df[c] = df[c].astype(TensorDtype())
        #     return df
        # self.ds = self.ds.map_batches(to_tensors, batch_format="pandas")

    def pipeline(
        self,
        shuffle: bool = True,
        fully_executed: bool = True,
        window_size_bytes: Optional[int] = None,
        shuffle_seed: int = default_random_seed,
    ) -> DatasetPipeline:
        """
        Args:
            shuffle: If true, the entire dataset is shuffled in memory before batching.
            fully_executed: If true, force full evaluation of the Ray Dataset by loading all blocks into memory.
            window_size_bytes: If not None, windowing is enabled and this parameter specifies the window size in bytes
                    for the dataset.
        """
        if fully_executed:
            if _ray113:
                # Workaround for: https://github.com/ray-project/ray/issues/25643
                # TODO(travis): remove after 1.13.1
                self.ds = self.ds.map_batches(lambda x: x, batch_size=None)

            # set instance state so calls to __len__ will also use the fully_executed version
            self.ds = self.ds.fully_executed()

        if window_size_bytes is None:
            pipe = self.ds.repeat()
        else:
            pipe = self.ds.window(bytes_per_window=window_size_bytes).repeat()
        if shuffle:
            pipe = pipe.random_shuffle_each_window(seed=shuffle_seed)
        return pipe

    @contextlib.contextmanager
    def initialize_batcher(self, batch_size=128, should_shuffle=True, seed=0, ignore_last=False, horovod=None):
        yield RayDatasetBatcher(
            self.ds.repeat().iter_datasets(),
            self.features,
            self.training_set_metadata,
            batch_size,
            self.size,
        )

    def __len__(self):
        return self.ds.count()

    @property
    def size(self):
        return len(self)

    @property
    def in_memory_size_bytes(self):
        """Memory size may be unknown, so return 0 incase size_bytes() returns None
        https://docs.ray.io/en/releases-1.12.1/_modules/ray/data/dataset.html#Dataset.size_bytes."""
        return self.ds.size_bytes() if self.ds is not None else 0

    def to_df(self):
        return self.df_engine.from_ray_dataset(self.ds)


class RayDatasetManager(DatasetManager):
    def __init__(self, backend):
        self.backend = backend

    def create(self, dataset: Union[str, DataFrame], config: Dict[str, Any], training_set_metadata: Dict[str, Any]):
        return RayDataset(dataset, get_proc_features(config), training_set_metadata, self.backend)

    def save(
        self,
        cache_path: str,
        dataset: DataFrame,
        config: Dict[str, Any],
        training_set_metadata: Dict[str, Any],
        tag: str,
    ):
        self.backend.df_engine.to_parquet(dataset, cache_path)
        return cache_path

    def can_cache(self, skip_save_processed_input):
        return not skip_save_processed_input

    @property
    def data_format(self):
        return "parquet"


class RayDatasetShard(Dataset):
    def __init__(
        self,
        dataset_shard: DatasetPipeline,
        features: Dict[str, Dict],
        training_set_metadata: Dict[str, Any],
    ):
        self.dataset_shard = dataset_shard
        self.features = features
        self.training_set_metadata = training_set_metadata
        self.epoch_iter = dataset_shard.iter_epochs()

    @contextlib.contextmanager
    def initialize_batcher(self, batch_size=128, should_shuffle=True, seed=0, ignore_last=False, horovod=None):
        yield RayDatasetBatcher(
            self.epoch_iter,
            self.features,
            self.training_set_metadata,
            batch_size,
            self.size,
        )

    @lru_cache(1)
    def __len__(self):
        # TODO(travis): find way to avoid calling this, as it's expensive
        return next(self.epoch_iter).count()

    @property
    def size(self):
        return len(self)


class RayDatasetBatcher(Batcher):
    def __init__(
        self,
        dataset_epoch_iterator: Iterator[DatasetPipeline],
        features: Dict[str, Dict],
        training_set_metadata: Dict[str, Any],
        batch_size: int,
        samples_per_epoch: int,
    ):
        self.dataset_epoch_iterator = dataset_epoch_iterator
        self.batch_size = batch_size
        self.samples_per_epoch = samples_per_epoch
        self.training_set_metadata = training_set_metadata

        self.features = features
        self.columns = list(features.keys())
        self.reshape_map = {
            proc_column: training_set_metadata[feature[NAME]].get("reshape")
            for proc_column, feature in features.items()
        }

        self.dataset_batch_iter = None
        self._epoch = 0
        self._next_batch = None
        self._last_batch = False
        self._step = 0
        self._fetch_next_epoch()

    def next_batch(self):
        if self.last_batch():
            raise StopIteration()

        batch = self._next_batch
        self._fetch_next_batch()
        self._step += 1
        return batch

    def last_batch(self):
        return self._last_batch

    def set_epoch(self, epoch, batch_size):
        self.batch_size = batch_size
        if epoch != self._epoch:
            self._fetch_next_epoch()
            self._epoch = epoch

    @property
    def step(self):
        return self._step

    @property
    def steps_per_epoch(self):
        return math.ceil(self.samples_per_epoch / self.batch_size)

    def _fetch_next_epoch(self):
        pipeline = next(self.dataset_epoch_iterator)

        read_parallelism = 1
        if read_parallelism == 1:
            self.dataset_batch_iter = self._create_async_reader(pipeline)
        elif read_parallelism > 1:
            # TODO: consider removing this. doesn't work currently and read performance seems generally
            #  very good with 1 parallelism
            self.dataset_batch_iter = self._create_async_parallel_reader(pipeline, read_parallelism)
        else:
            self.dataset_batch_iter = self._create_sync_reader(pipeline)

        self._step = 0
        self._fetch_next_batch()

    def _fetch_next_batch(self):
        if self.dataset_batch_iter is None:
            self._last_batch = True
            return

        self._last_batch = False
        try:
            self._next_batch = next(self.dataset_batch_iter)
        except StopIteration:
            self._last_batch = True

    def _to_tensors_fn(self):
        columns = self.columns
        features = self.features

        def to_tensors(df: pd.DataFrame) -> pd.DataFrame:
            for c in columns:
                # do not convert scalar columns: https://github.com/ray-project/ray/issues/20825
                if features[c][TYPE] not in _SCALAR_TYPES:
                    df[c] = cast_as_tensor_dtype(df[c])
                elif features[c][TYPE] == BINARY:
                    # TODO(travis): figure out why Ray is converting these into object types by default
                    df[c] = df[c].astype(np.bool_)
            return df

        return to_tensors

    def _prepare_batch(self, batch: pd.DataFrame) -> Dict[str, np.ndarray]:
        res = {}
        for c in self.columns:
            if self.features[c][TYPE] not in _SCALAR_TYPES:
                # Ensure columns stacked instead of turned into np.array([np.array, ...], dtype=object) objects
                res[c] = np.stack(batch[c].values)
            else:
                res[c] = batch[c].to_numpy()

        for c in self.columns:
            reshape = self.reshape_map.get(c)
            if reshape is not None:
                res[c] = res[c].reshape((-1, *reshape))
        return res

    def _create_sync_reader(self, pipeline: DatasetPipeline):
        to_tensors = self._to_tensors_fn()

        def sync_read():
            for batch in pipeline.map_batches(to_tensors, batch_format="pandas").iter_batches(
                prefetch_blocks=0, batch_size=self.batch_size, batch_format="pandas"
            ):
                yield self._prepare_batch(batch)

        return sync_read()

    def _create_async_reader(self, pipeline: DatasetPipeline):
        q = queue.Queue(maxsize=100)

        batch_size = self.batch_size

        to_tensors = self._to_tensors_fn()

        def producer():
            for batch in pipeline.map_batches(to_tensors, batch_format="pandas").iter_batches(
                prefetch_blocks=0, batch_size=batch_size, batch_format="pandas"
            ):
                res = self._prepare_batch(batch)
                q.put(res)
            q.put(None)

        def async_read():
            t = threading.Thread(target=producer)
            t.start()
            while True:
                batch = q.get(block=True)
                if batch is None:
                    break
                yield batch
            t.join()

        return async_read()

    def _create_async_parallel_reader(self, pipeline: DatasetPipeline, num_threads: int):
        q = queue.Queue(maxsize=100)

        batch_size = self.batch_size

        to_tensors = self._to_tensors_fn()
        splits = pipeline.split(n=num_threads)

        def producer(i):
            for batch in (
                splits[i]
                .map_batches(to_tensors, batch_format="pandas")
                .iter_batches(prefetch_blocks=0, batch_size=batch_size, batch_format="pandas")
            ):
                res = self._prepare_batch(batch)
                q.put(res)
            q.put(None)

        def async_parallel_read():
            threads = [threading.Thread(target=producer, args=(i,)) for i in range(num_threads)]
            for t in threads:
                t.start()

            active_threads = num_threads
            while True:
                batch = q.get(block=True)
                if batch is None:
                    active_threads -= 1
                    if active_threads == 0:
                        break
                yield batch

            for t in threads:
                t.join()

        return async_parallel_read()
