import contextlib
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, TYPE_CHECKING

import horovod.torch as hvd
import ray
import torch
from horovod.torch.optimizer import _DistributedOptimizer
from packaging import version
from ray.train.backend import BackendConfig
from ray.train.data_parallel_trainer import DataParallelTrainer
from ray.train.horovod import HorovodTrainer
from torch import nn
from torch.optim import Optimizer

from ludwig.constants import AUTO
from ludwig.distributed.base import DistributedStrategy
from ludwig.modules.optimization_modules import create_optimizer
from ludwig.utils.horovod_utils import gather_all_tensors, is_distributed_available

if TYPE_CHECKING:
    from ludwig.schema.trainer import ECDTrainerConfig

_ray220 = version.parse(ray.__version__) >= version.parse("2.2.0")


class HorovodStrategy(DistributedStrategy):
    def __init__(self):
        hvd.init()
        logging.info("Using Horovod strategy")

    def prepare(
        self,
        model: nn.Module,
        trainer_config: "ECDTrainerConfig",
        base_learning_rate: float,
    ) -> Tuple[nn.Module, Optimizer]:
        optimizer = create_optimizer(model, trainer_config.optimizer, base_learning_rate)
        grad_accum_steps = (
            trainer_config.gradient_accumulation_steps if trainer_config.gradient_accumulation_steps != AUTO else 1
        )
        dist_optimizer = hvd.DistributedOptimizer(
            optimizer,
            named_parameters=model.named_parameters(),
            backward_passes_per_step=grad_accum_steps,
        )
        return model, dist_optimizer

    def size(self) -> int:
        return hvd.size()

    def rank(self) -> int:
        return hvd.rank()

    def local_size(self) -> int:
        return hvd.local_size()

    def local_rank(self) -> int:
        return hvd.local_rank()

    def barrier(self):
        return hvd.allreduce(torch.as_tensor([0], dtype=torch.int))

    def allreduce(self, t: torch.Tensor) -> torch.Tensor:
        return hvd.allreduce(t)

    def broadcast(self, t: torch.Tensor) -> torch.Tensor:
        return hvd.broadcast(t, root_rank=0)

    def sync_model(self, model: nn.Module):
        hvd.broadcast_parameters(model.state_dict(), root_rank=0)

    def sync_optimizer(self, optimizer: Optimizer):
        hvd.broadcast_optimizer_state(optimizer, root_rank=0)

    def broadcast_object(self, v: Any, name: Optional[str] = None) -> Any:
        return hvd.broadcast_object(v, name=name)

    def wait_optimizer_synced(self, optimizer: _DistributedOptimizer):
        optimizer.synchronize()

    @contextlib.contextmanager
    def prepare_model_update(self, model: nn.Module, should_step: bool):
        yield

    @contextlib.contextmanager
    def prepare_optimizer_update(self, optimizer: _DistributedOptimizer):
        with optimizer.skip_synchronize():
            yield

    @classmethod
    def is_available(cls) -> bool:
        return is_distributed_available()

    @classmethod
    def gather_all_tensors_fn(cls) -> Optional[Callable]:
        return gather_all_tensors

    @classmethod
    def get_ray_trainer_backend(cls, nics: Optional[List[str]] = None, **kwargs) -> Optional[Any]:
        from ray.train.horovod import HorovodConfig

        # Explicitly override network interfaces Horovod will attempt to use
        if nics is not None:
            nics = set(nics)
        return HorovodConfig(nics=nics)

    @classmethod
    def get_trainer_cls(cls, backend_config: BackendConfig) -> Tuple[Type[DataParallelTrainer], Dict[str, Any]]:
        if not _ray220:
            from ludwig.distributed._ray_210_compat import HorovodTrainerRay210

            return HorovodTrainerRay210, dict(horovod_config=backend_config)

        return HorovodTrainer, dict(horovod_config=backend_config)

    def shutdown(self):
        hvd.shutdown()
