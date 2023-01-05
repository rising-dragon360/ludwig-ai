import logging
import math
from typing import Any, Dict

from torch.optim import Optimizer
from torch.optim.lr_scheduler import LambdaLR, ReduceLROnPlateau

from ludwig.constants import MINIMIZE, TRAINING, VALIDATION
from ludwig.modules.metric_modules import get_metric_cls
from ludwig.schema.lr_scheduler import LRSchedulerConfig
from ludwig.utils.metric_utils import TrainerMetric
from ludwig.utils.trainer_utils import ProgressTracker


class ReduceLROnPLateauCappedDecreases(ReduceLROnPlateau):
    def __init__(self, optimizer: Optimizer, mode: str, reduce_limit: int, factor: float, patience: int):
        super().__init__(optimizer, mode=mode, factor=factor, patience=patience)
        self.reduce_limit = reduce_limit
        self._num_reduce_lr = 0

    def step(self, metrics):
        if self._num_reduce_lr >= self.reduce_limit:
            # Already reduced the LR as many times as we will allow
            return

        return super().step(metrics)

    def _reduce_lr(self, epoch):
        super()._reduce_lr(epoch)
        self._num_reduce_lr += 1


class LRScheduler:
    def __init__(
        self,
        config: LRSchedulerConfig,
        optimizer: Optimizer,
        steps_per_checkpoint: int = 1000,
        total_steps: int = 10000,
    ):
        self.config = config
        self.optimizer = optimizer
        self.validation_metric = get_metric_cls(self.config.reduce_eval_metric)

        # Scheduler updated each training step
        self.step_info = StepInfo(steps_per_checkpoint, total_steps, self.config)
        self._train_scheduler = get_schedule_with_warmup(self.config, self.optimizer, self.step_info)

        # Scheduler updated each eval step
        self._eval_scheduler = None
        if self.config.reduce_on_plateau > 0:
            mode = "min" if self.validation_metric.get_objective() == MINIMIZE else "max"
            self._eval_scheduler = ReduceLROnPLateauCappedDecreases(
                optimizer=self.optimizer,
                mode=mode,
                reduce_limit=self.config.reduce_on_plateau,
                factor=self.config.reduce_on_plateau_rate,
                patience=self.config.reduce_on_plateau_patience,
            )

        self.reset(steps_per_checkpoint, total_steps)

    def reset(self, steps_per_checkpoint: int, total_steps: int):
        # Retain state but update number of steps for training
        self.step_info.reset(steps_per_checkpoint, total_steps)

    def step(self):
        self._train_scheduler.step()

    def eval_step(self, progress_tracker: ProgressTracker, validation_field: str):
        if self._eval_scheduler is None:
            # No reduce on plateau
            return

        if self.config.reduce_eval_split == TRAINING:
            split_metrics = progress_tracker.train_metrics
        elif self.config.reduce_eval_split == VALIDATION:
            split_metrics = progress_tracker.validation_metrics
        else:  # if self.config.reduce_eval_split == TEST:
            split_metrics = progress_tracker.test_metrics

        validation_metric = self.config.reduce_eval_metric
        last_metric: TrainerMetric = split_metrics[validation_field][validation_metric][-1]
        last_metric_value = last_metric[-1]

        self._eval_scheduler.step(last_metric_value)

    def state_dict(self) -> Dict[str, Any]:
        return {
            "train_scheduler_state": self._train_scheduler.state_dict(),
            "eval_scheduler_state": self._eval_scheduler.state_dict() if self._eval_scheduler is not None else {},
        }

    def load_state_dict(self, d: Dict[str, Any]):
        self._train_scheduler.load_state_dict(d["train_scheduler_state"])
        if self._eval_scheduler is not None:
            self._eval_scheduler.load_state_dict(d["eval_scheduler_state"])


class StepInfo:
    """Stores the steps_per_checkpoint and total_steps used during the current training run.

    This class is needed by LambdaLR to allow us to update the steps on training init without resetting the entire
    LRScheduler from scratch (which would result in resetting the optimizer learning rate).
    """

    def __init__(self, steps_per_checkpoint: int, total_steps: int, config: LRSchedulerConfig):
        self.config = config
        self.reset(steps_per_checkpoint, total_steps)

    def reset(self, steps_per_checkpoint: int, total_steps: int):
        self.steps_per_checkpoint = steps_per_checkpoint
        self.num_training_steps = total_steps

        if self.config.warmup_fraction > 0 and self.config.warmup_evaluations > 0:
            logging.info(
                "Both `learning_rate_scheduler.warmup_fraction` and `learning_rate_scheduler.warmup_evaluations` "
                "provided. The larger of the two (as a function of the total training steps) will be used."
            )

        num_warmup_steps = 0
        if self.config.warmup_fraction > 0:
            num_warmup_steps = max(self.config.warmup_fraction * self.num_training_steps, num_warmup_steps)
        if self.config.warmup_evaluations > 0:
            num_warmup_steps = max(self.config.warmup_evaluations * self.steps_per_checkpoint, num_warmup_steps)
        self.num_warmup_steps = num_warmup_steps


def get_schedule_with_warmup(
    config: LRSchedulerConfig,
    optimizer: Optimizer,
    step_info: StepInfo,
) -> LambdaLR:
    """Creates a learning rate scheduler that updates each training step."""
    decay_fn = decay_registry[config.decay]

    def lr_lambda(current_step: int):
        if current_step < step_info.num_warmup_steps:
            return float(current_step) / float(max(1, step_info.num_warmup_steps))
        return decay_fn(current_step, step_info.num_training_steps, step_info.num_warmup_steps, config)

    return LambdaLR(optimizer, lr_lambda, last_epoch=-1)


def no_decay(current_step: int, num_training_steps: int, num_warmup_steps: int, config: LRSchedulerConfig):
    return 1.0


def linear_decay(current_step: int, num_training_steps: int, num_warmup_steps: int, config: LRSchedulerConfig):
    return max(0.0, float(num_training_steps - current_step) / float(max(1, num_training_steps - num_warmup_steps)))


def exponential_decay(current_step: int, num_training_steps: int, num_warmup_steps: int, config: LRSchedulerConfig):
    decay_rate = float(config.decay_rate)
    decay_steps = float(config.decay_steps)
    step = float(current_step)
    exponent = 1 + step / decay_steps
    if config.staircase:
        exponent = math.ceil(exponent)
    return math.pow(decay_rate, exponent)


decay_registry = {
    None: no_decay,
    "linear": linear_decay,
    "exponential": exponential_decay,
}
