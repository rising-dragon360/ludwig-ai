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
"""This module contains the class and auxiliary methods of a model."""
import contextlib
import gc
import logging
import math
import os
import os.path
import signal
import statistics
import sys
import threading
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

import numpy as np
import psutil
import torch
from tabulate import tabulate
from torch.utils.tensorboard import SummaryWriter

from ludwig.constants import COMBINED, LOSS, MINIMIZE, MODEL_ECD, TEST, TRAINING, VALIDATION
from ludwig.data.dataset.base import Dataset
from ludwig.globals import (
    is_progressbar_disabled,
    MODEL_HYPERPARAMETERS_FILE_NAME,
    TRAINING_CHECKPOINTS_DIR_PATH,
    TRAINING_PROGRESS_TRACKER_FILE_NAME,
)
from ludwig.models.ecd import ECD
from ludwig.models.predictor import Predictor
from ludwig.modules.metric_modules import get_improved_fn, get_initial_validation_value
from ludwig.modules.metric_registry import metric_registry
from ludwig.modules.optimization_modules import create_clipper, create_optimizer
from ludwig.progress_bar import LudwigProgressBar
from ludwig.schema.trainer import ECDTrainerConfig
from ludwig.trainers.base import BaseTrainer
from ludwig.trainers.registry import register_trainer
from ludwig.types import ModelConfigDict
from ludwig.utils import time_utils
from ludwig.utils.checkpoint_utils import Checkpoint, CheckpointManager
from ludwig.utils.data_utils import load_json
from ludwig.utils.defaults import default_random_seed
from ludwig.utils.fs_utils import path_exists
from ludwig.utils.horovod_utils import return_first
from ludwig.utils.math_utils import exponential_decay, learning_rate_warmup, learning_rate_warmup_distributed
from ludwig.utils.metric_utils import get_metric_names, TrainerMetric
from ludwig.utils.misc_utils import set_random_seed
from ludwig.utils.torch_utils import get_torch_device
from ludwig.utils.trainer_utils import (
    append_metrics,
    get_final_steps_per_checkpoint,
    get_latest_metrics_dict,
    get_new_progress_tracker,
    get_total_steps,
    ProgressTracker,
)

logger = logging.getLogger(__name__)


@register_trainer(MODEL_ECD, default=True)
class Trainer(BaseTrainer):
    """Trainer is a class that trains a model."""

    @staticmethod
    def get_schema_cls():
        return ECDTrainerConfig

    def __init__(
        self,
        config: ECDTrainerConfig,
        model: ECD,
        resume: float = False,
        skip_save_model: bool = False,
        skip_save_progress: bool = False,
        skip_save_log: bool = False,
        callbacks: List = None,
        report_tqdm_to_ray=False,
        random_seed: float = default_random_seed,
        horovod: Optional[Dict] = None,
        device: Optional[str] = None,
        **kwargs,
    ):
        """Trains a model with a set of options and hyperparameters listed below. Customizable.

        :param model: Underlying Ludwig model
        :type model: `ludwig.models.ecd.ECD`
        :param resume: Resume training a model that was being trained. (default: False).
        :type resume: Boolean
        :param skip_save_model: Disables saving model weights and hyperparameters each time the model improves. By
                default Ludwig saves model weights after each round of evaluation the validation metric (improves, but
                if the model is really big that can be time consuming. If you do not want to keep the weights and just
                find out what performance a model can get with a set of hyperparameters, use this parameter to skip it,
                but the model will not be loadable later on. (default: False).
        :type skip_save_model: Boolean
        :param skip_save_progress: Disables saving progress each round of evaluation. By default Ludwig saves weights
                and stats after each round of evaluation for enabling resuming of training, but if the model is really
                big that can be time consuming and will uses twice as much space, use this parameter to skip it, but
                training cannot be resumed later on. (default: False).
        :type skip_save_progress: Boolean
        :param skip_save_log: Disables saving TensorBoard logs. By default Ludwig saves logs for the TensorBoard, but if
                it is not needed turning it off can slightly increase the overall speed. (default: False).
        :type skip_save_log: Boolean
        :param callbacks: List of `ludwig.callbacks.Callback` objects that provide hooks into the Ludwig pipeline.
                (default: None).
        :type callbacks: list
        :param report_tqdm_to_ray: Enables using the ray based tqdm Callback for progress bar reporting
        :param random_seed: Default initialization for the random seeds (default: 42).
        :type random_seed: Float
        :param horovod: Horovod parameters (default: None).
        :type horovod: dict
        :param device: Device to load the model on from a saved checkpoint (default: None).
        :type device: str
        :param config: `ludwig.schema.trainer.BaseTrainerConfig` instance that specifies training hyperparameters
                (default: `ludwig.schema.trainer.ECDTrainerConfig()`).
        """

        self.epochs = config.epochs
        self.train_steps = config.train_steps
        self.total_steps = 0  # Computed during training, after batcher has been initialized.

        self.regularization_lambda = config.regularization_lambda
        self.regularization_type = config.regularization_type
        self.learning_rate = config.learning_rate
        try:
            base_learning_rate = float(config.learning_rate)
        except ValueError:
            # TODO (ASN): Circle back on how we want to set default placeholder value
            base_learning_rate = 0.001  # Default initial learning rate for autoML.

        self.base_learning_rate = base_learning_rate
        self.decay = config.decay
        self.decay_rate = config.decay_rate
        self.decay_steps = config.decay_steps
        self.staircase = config.staircase
        self.batch_size = config.batch_size
        self.max_batch_size = config.max_batch_size
        self.eval_batch_size = config.batch_size if config.eval_batch_size is None else config.eval_batch_size
        self.should_shuffle = config.should_shuffle
        self._validation_field = config.validation_field
        self._validation_metric = config.validation_metric
        self.early_stop = config.early_stop
        self.steps_per_checkpoint = config.steps_per_checkpoint
        self.checkpoints_per_epoch = config.checkpoints_per_epoch
        self.evaluate_training_set = config.evaluate_training_set
        self.reduce_learning_rate_on_plateau = config.reduce_learning_rate_on_plateau
        self.reduce_learning_rate_on_plateau_patience = config.reduce_learning_rate_on_plateau_patience
        self.reduce_learning_rate_on_plateau_rate = config.reduce_learning_rate_on_plateau_rate
        self.reduce_learning_rate_eval_metric = config.reduce_learning_rate_eval_metric
        self.reduce_learning_rate_eval_split = config.reduce_learning_rate_eval_split
        self.increase_batch_size_on_plateau = config.increase_batch_size_on_plateau
        self.increase_batch_size_on_plateau_patience = config.increase_batch_size_on_plateau_patience
        self.increase_batch_size_on_plateau_rate = config.increase_batch_size_on_plateau_rate
        self.increase_batch_size_eval_metric = config.increase_batch_size_eval_metric
        self.increase_batch_size_eval_split = config.increase_batch_size_eval_split
        self.learning_rate_warmup_epochs = config.learning_rate_warmup_epochs
        self.resume = resume
        self.skip_save_model = skip_save_model
        self.skip_save_progress = skip_save_progress
        self.skip_save_log = skip_save_log
        self.random_seed = random_seed
        self.horovod = horovod
        self.received_sigint = False
        self.report_tqdm_to_ray = report_tqdm_to_ray
        self.callbacks = callbacks or []
        self.device = device
        if self.device is None:
            self.device = get_torch_device()

        self.model = model
        self.model = self.model.to(self.device)

        # ================ Optimizer tuning ================
        optimizer_config = config.optimizer
        # Most optimizers require 'lr' parameter.  set_optimizer_learning_rate will update this during training:
        optimizer_config.lr = base_learning_rate
        self.gradient_clipping_config = create_clipper(config.gradient_clipping)
        self.optimizer = create_optimizer(model, horovod=horovod, optimizer_config=optimizer_config)
        self.lr_scale_fn = learning_rate_scale_fns[config.learning_rate_scaling]

        # Setup for automatic mixed precision (AMP)
        self.use_amp = config.use_mixed_precision
        self.scaler = torch.cuda.amp.GradScaler() if self.use_amp else None
        if self.use_amp:
            logger.info("Enabling automatic mixed precision (AMP)")

        # when training starts the sigint handler will be replaced with
        # set_steps_to_1_or_quit so this is needed to remember
        # the original sigint to restore at the end of training
        # and before set_steps_to_1_or_quit returns
        self.original_sigint_handler = None

        # TODO(Justin): Move to config validation when that's ready.
        if config.checkpoints_per_epoch != 0 and config.steps_per_checkpoint != 0:
            raise ValueError(
                "It is invalid to specify both trainer.checkpoints_per_epoch AND "
                "trainer.steps_per_checkpoint. Please specify one or the other, or specify neither to checkpoint/eval "
                "the model every epoch."
            )

    def train_step(
        self, inputs: Dict[str, torch.Tensor], targets: Dict[str, torch.Tensor]
    ) -> Tuple[torch.Tensor, Dict[str, torch.Tensor]]:
        """Performs a single training step.

        Params:
            inputs: A dictionary of input data, from feature name to tensor.
            targets: A dictionary of target data, from feature name to tensor.

        Returns:
            A tuple of the loss tensor and a dictionary of loss for every output feature.
        """
        if isinstance(self.optimizer, torch.optim.LBFGS):
            # NOTE: Horovod is not supported for L-BFGS.
            # NOTE: AMP is not supported for L-BFGS yet.

            def closure():
                # Allows L-BFGS to reevaluate the loss function
                self.optimizer.zero_grad()
                model_outputs = self.model((inputs, targets))
                loss, all_losses = self.model.train_loss(
                    targets, model_outputs, self.regularization_type, self.regularization_lambda
                )
                loss.backward()
                return loss

            self.optimizer.step(closure)

            # Obtain model predictions and loss
            model_outputs = self.model((inputs, targets))
            loss, all_losses = self.model.train_loss(
                targets, model_outputs, self.regularization_type, self.regularization_lambda
            )

            if not self.evaluate_training_set:
                # Update evaluation metrics with current model params:
                # noisy but fast way to get metrics on the training set
                predictions = self.model.outputs_to_predictions(model_outputs)
                self.model.update_metrics(targets, predictions)

            return loss, all_losses

        self.optimizer.zero_grad()

        with torch.cuda.amp.autocast() if self.use_amp else contextlib.nullcontext():
            # Obtain model predictions and loss
            model_outputs = self.model((inputs, targets))
            loss, all_losses = self.model.train_loss(
                targets, model_outputs, self.regularization_type, self.regularization_lambda
            )

        # Begin the backward pass
        variables = self.model.parameters()
        if self.use_amp:
            self.scaler.scale(loss).backward()
        else:
            loss.backward()

        if self.horovod:
            # Wait for gradient aggregation to complete before clipping the gradients
            # When using AMP, we need to do this before unscaling.
            # See: https://github.com/horovod/horovod/blob/master/examples/pytorch/pytorch_mnist.py
            self.optimizer.synchronize()

        if self.use_amp:
            # In-place unscaling of all gradients before weights update
            # Do this before gradient clipping per docs:
            # https://pytorch.org/docs/master/notes/amp_examples.html#gradient-clipping
            self.scaler.unscale_(self.optimizer)

        # Clip gradients
        self.clip_grads(variables)

        # Apply gradient updates
        with self.optimizer.skip_synchronize() if self.horovod else contextlib.nullcontext():
            # Because we already synchronized above, we skip doing so here
            if self.use_amp:
                self.scaler.step(self.optimizer)
            else:
                self.optimizer.step()

        if self.use_amp:
            # Update scaler in case of overflow/underflow
            self.scaler.update()

        if not self.evaluate_training_set:
            # Update evaluation metrics with current model params:
            # noisy but fast way to get metrics on the training set
            predictions = self.model.outputs_to_predictions(model_outputs)
            self.model.update_metrics(targets, predictions)

        return loss, all_losses

    def clip_grads(self, variables):
        """Applies gradient clipping."""
        if self.gradient_clipping_config.clipglobalnorm:
            torch.nn.utils.clip_grad_norm_(variables, self.gradient_clipping_config.clipglobalnorm)
        if self.gradient_clipping_config.clipnorm:
            torch.nn.utils.clip_grad_norm_(variables, self.gradient_clipping_config.clipglobalnorm)
        if self.gradient_clipping_config.clipvalue:
            torch.nn.utils.clip_grad_value_(variables, self.gradient_clipping_config.clipvalue)

    def set_base_learning_rate(self, base_learning_rate):
        """Sets the target learning rate, and updates the optimizer learning rate."""
        if self.horovod:
            base_learning_rate *= self.lr_scale_fn(self.horovod.size())
        self.base_learning_rate = base_learning_rate  # The LR target for warmup and initial value for decay.
        self.set_optimizer_learning_rate(base_learning_rate)

    def set_optimizer_learning_rate(self, learning_rate):
        """Sets the learning rate of the optimizer."""
        for g in self.optimizer.param_groups:
            g["lr"] = learning_rate

    @classmethod
    def write_eval_summary(
        cls,
        summary_writer,
        metrics,
        step,
    ):
        if not summary_writer:
            return

        for feature_name, output_feature in metrics.items():
            for metric_name, metrics in output_feature.items():
                if metrics:
                    metric_tag = f"{feature_name}/epoch_{metric_name}"
                    metric_val = metrics[-1][-1]
                    summary_writer.add_scalar(metric_tag, metric_val, global_step=step)
        summary_writer.flush()

    @classmethod
    def write_step_summary(cls, train_summary_writer, combined_loss, all_losses, step, learning_rate=None):
        if not train_summary_writer:
            return

        # combined loss
        train_summary_writer.add_scalar("combined/step_training_loss", combined_loss, global_step=step)

        # all other losses
        for feature_name, loss in all_losses.items():
            loss_tag = f"{feature_name}/step_training_loss"
            train_summary_writer.add_scalar(loss_tag, loss, global_step=step)

        if learning_rate:
            train_summary_writer.add_scalar("combined/step_learning_rate", learning_rate, global_step=step)

        train_summary_writer.flush()

    def train_for_tuning(
        self,
        batch_size: int,
        total_steps: int = 5,
    ) -> float:
        """Function to be used by tune_batch_size.

        Return:
            Median throughput in samples / sec.
        """
        self.model.train()  # Sets model training mode.
        durations = []
        for _ in range(total_steps):
            self.model.reset_metrics()
            start_ts = time.time()
            inputs = {
                input_feature_name: input_feature.create_sample_input(batch_size=batch_size).to(self.device)
                for input_feature_name, input_feature in self.model.input_features.items()
            }
            targets = {
                output_feature_name: output_feature.create_sample_output(batch_size=batch_size).to(self.device)
                for output_feature_name, output_feature in self.model.output_features.items()
            }
            self.train_step(inputs, targets)
            durations.append(time.time() - start_ts)
        med_duration_s = statistics.median(durations)
        return batch_size / med_duration_s

    def is_cpu_training(self):
        return torch.device(self.device) == torch.device("cpu")

    def tune_batch_size(
        self,
        config: ModelConfigDict,
        training_set: Dataset,
        random_seed: int = default_random_seed,
        max_trials: int = 20,
        halving_limit: int = 3,
    ) -> int:
        logger.info("Tuning batch size...")

        def _is_valid_batch_size(batch_size):
            # make sure that batch size is valid (e.g. less than size of ds)
            is_smaller_than_training_set = batch_size < len(training_set)
            is_under_max_batch_size = batch_size <= self.max_batch_size
            is_valid = is_smaller_than_training_set and is_under_max_batch_size
            if not is_valid:
                logger.info(
                    f"Batch size {batch_size} is invalid, must be smaller than training set size "
                    f"{len(training_set)} and less than or equal to max batch size {self.max_batch_size}"
                )
            return is_valid

        # TODO (ASN) : Circle back on how we want to set default placeholder value
        # Currently, since self.batch_size is originally set to auto, we provide a
        # placeholder starting value
        batch_size = 2
        skip_save_model = self.skip_save_model
        skip_save_progress = self.skip_save_progress
        skip_save_log = self.skip_save_log

        # Set temporary values
        self.skip_save_model = True
        self.skip_save_progress = True
        self.skip_save_log = True

        best_samples_per_sec = 0
        best_batch_size = None
        try:
            count = 0
            while count < max_trials and _is_valid_batch_size(batch_size):
                logger.info(f"Exploring batch_size={batch_size}")
                gc.collect()

                try:
                    samples_per_sec = self.train_for_tuning(batch_size, total_steps=5)
                    logger.info(f"Throughput at batch_size={batch_size}: {samples_per_sec:.5f} samples/s")
                    if samples_per_sec < best_samples_per_sec:
                        # We assume that once the throughput starts degrading, it won't go up again
                        logger.info(f"Throughput decrease at batch_size={batch_size}")
                        break

                    best_samples_per_sec = samples_per_sec
                    best_batch_size = batch_size
                    count += 1

                    # double batch size
                    batch_size *= 2
                except RuntimeError as e:
                    # PyTorch only generates Runtime errors for CUDA OOM.
                    gc.collect()
                    if "CUDA out of memory" in str(e):
                        logger.info(f"OOM at batch_size={batch_size}")
                    else:
                        # Not a CUDA error
                        raise
                    break
        finally:
            # Restore original parameters to defaults
            self.skip_save_model = skip_save_model
            self.skip_save_progress = skip_save_progress
            self.skip_save_log = skip_save_log

        logger.info(f"Selected batch_size={best_batch_size}")
        return best_batch_size

    def run_evaluation(
        self,
        training_set,
        validation_set,
        test_set,
        progress_tracker,
        train_summary_writer,
        validation_summary_writer,
        test_summary_writer,
        model_hyperparameters_path,
        output_features,
        metrics_names,
        save_path,
        loss: torch.Tensor,
        all_losses: Dict[str, torch.Tensor],
        early_stopping_steps: int,
    ) -> bool:
        """Runs evaluation over training, validation, and test sets.

        Also:
        - Prints results, saves results to the progress tracker.
        - Saves the model if the validation score is the best so far
        - If there is no validation set, the model is always saved.

        Returns whether the trainer should early stop, based on validation metrics history.
        """
        start_time = time.time()
        self.callback(lambda c: c.on_eval_start(self, progress_tracker, save_path))

        progress_tracker.checkpoint_number += 1
        if self.is_coordinator():
            logger.info(f"\nRunning evaluation for step: {progress_tracker.steps}, epoch: {progress_tracker.epoch}")

        # ================ Eval ================
        # init tables
        tables = OrderedDict()
        for output_feature_name, output_feature in output_features.items():
            tables[output_feature_name] = [[output_feature_name] + metrics_names[output_feature_name]]
        tables[COMBINED] = [[COMBINED, LOSS]]

        # eval metrics on train
        self.eval_batch_size = max(self.eval_batch_size, progress_tracker.batch_size)

        if self.evaluate_training_set:
            # Run a separate pass over the training data to compute metrics
            self.evaluation(
                training_set, "train", progress_tracker.train_metrics, tables, self.eval_batch_size, progress_tracker
            )
        else:
            # Use metrics accumulated during training
            metrics = self.model.get_metrics()
            append_metrics(self.model, "train", metrics, progress_tracker.train_metrics, tables, progress_tracker)
            self.model.reset_metrics()

        self.write_eval_summary(
            summary_writer=train_summary_writer,
            metrics=progress_tracker.train_metrics,
            step=progress_tracker.steps,
        )

        if validation_set is not None:
            self.callback(lambda c: c.on_validation_start(self, progress_tracker, save_path))

            # eval metrics on validation set
            self.evaluation(
                validation_set,
                VALIDATION,
                progress_tracker.validation_metrics,
                tables,
                self.eval_batch_size,
                progress_tracker,
            )

            self.write_eval_summary(
                summary_writer=validation_summary_writer,
                metrics=progress_tracker.validation_metrics,
                step=progress_tracker.steps,
            )

            self.callback(lambda c: c.on_validation_end(self, progress_tracker, save_path))

        if test_set is not None:
            self.callback(lambda c: c.on_test_start(self, progress_tracker, save_path))

            # eval metrics on test set
            self.evaluation(
                test_set, TEST, progress_tracker.test_metrics, tables, self.eval_batch_size, progress_tracker
            )

            self.write_eval_summary(
                summary_writer=test_summary_writer,
                metrics=progress_tracker.test_metrics,
                step=progress_tracker.steps,
            )

            self.callback(lambda c: c.on_test_end(self, progress_tracker, save_path))

        elapsed_time = (time.time() - start_time) * 1000.0

        if self.is_coordinator():
            logger.info(f"Evaluation took {time_utils.strdelta(elapsed_time)}\n")
            for output_feature, table in tables.items():
                logger.info(tabulate(table, headers="firstrow", tablefmt="fancy_grid", floatfmt=".4f"))

        # ================ Validation Logic ================
        should_break = False
        if validation_set is not None and validation_set.size > 0:
            should_break = self.check_progress_on_validation(
                progress_tracker,
                self.validation_field,
                self.validation_metric,
                save_path,
                model_hyperparameters_path,
                self.reduce_learning_rate_on_plateau,
                self.reduce_learning_rate_on_plateau_patience,
                self.reduce_learning_rate_on_plateau_rate,
                self.reduce_learning_rate_eval_metric,
                self.reduce_learning_rate_eval_split,
                self.increase_batch_size_on_plateau,
                self.increase_batch_size_on_plateau_patience,
                self.increase_batch_size_on_plateau_rate,
                self.max_batch_size,
                self.increase_batch_size_eval_metric,
                self.increase_batch_size_eval_split,
                early_stopping_steps,
                self.skip_save_model,
            )
        else:
            # There's no validation, so we save the model.
            if self.is_coordinator() and not self.skip_save_model:
                self.model.save(save_path)

        # Trigger eval end callback after any model weights save for complete checkpoint
        self.callback(lambda c: c.on_eval_end(self, progress_tracker, save_path))

        return should_break

    def train(self, training_set, validation_set=None, test_set=None, save_path="model", **kwargs):
        """Trains a model with a set of hyperparameters listed below. Customizable.

        :param training_set: The training set
        :param validation_set: The validation dataset
        :param test_set: The test dataset
        """
        # ====== General setup =======
        output_features = self.model.output_features

        # Only use signals when on the main thread to avoid issues with CherryPy
        # https://github.com/ludwig-ai/ludwig/issues/286
        if threading.current_thread() == threading.main_thread():
            # set the original sigint signal handler
            # as we want to restore it at the end of training
            self.original_sigint_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self.set_steps_to_1_or_quit)

        metrics_names = get_metric_names(output_features)

        # check if validation_field is valid
        valid_validation_field = False
        if self.validation_field == "combined":
            valid_validation_field = True
            if self.validation_metric is not LOSS and len(output_features) == 1:
                only_of = next(iter(output_features))
                if self.validation_metric in metrics_names[only_of]:
                    self._validation_field = only_of
                    logger.warning(
                        "Replacing 'combined' validation field "
                        "with '{}' as the specified validation "
                        "metric {} is invalid for 'combined' "
                        "but is valid for '{}'.".format(only_of, self.validation_metric, only_of)
                    )
        else:
            for output_feature in output_features:
                if self.validation_field == output_feature:
                    valid_validation_field = True

        if not valid_validation_field:
            raise ValueError(
                "The specified validation_field {} is not valid."
                "Available ones are: {}".format(self.validation_field, list(output_features.keys()) + ["combined"])
            )

        # check if validation_metric is valid
        valid_validation_metric = self.validation_metric in metrics_names[self.validation_field]
        if not valid_validation_metric:
            raise ValueError(
                "The specified metric {} is not valid. "
                "Available metrics for {} output feature are: {}".format(
                    self.validation_metric, self.validation_field, metrics_names[self.validation_field]
                )
            )

        # ====== Setup file names =======
        model_hyperparameters_path = None
        training_checkpoints_path = training_progress_tracker_path = None
        tensorboard_log_dir = None
        if self.is_coordinator():
            os.makedirs(save_path, exist_ok=True)
            model_hyperparameters_path = os.path.join(save_path, MODEL_HYPERPARAMETERS_FILE_NAME)
            training_checkpoints_path = os.path.join(save_path, TRAINING_CHECKPOINTS_DIR_PATH)
            tensorboard_log_dir = os.path.join(save_path, "logs")
        if save_path:
            training_progress_tracker_path = os.path.join(save_path, TRAINING_PROGRESS_TRACKER_FILE_NAME)

        self.callback(
            lambda c: c.on_trainer_train_setup(self, save_path, self.is_coordinator()), coordinator_only=False
        )

        # ====== Setup session =======
        checkpoint = checkpoint_manager = None
        if self.is_coordinator() and not self.skip_save_progress:
            checkpoint = Checkpoint(model=self.model, optimizer=self.optimizer)
            checkpoint_manager = CheckpointManager(checkpoint, training_checkpoints_path, device=self.device)

        # ====== Setup Tensorboard writers =======
        train_summary_writer = None
        validation_summary_writer = None
        test_summary_writer = None
        if self.is_coordinator() and not self.skip_save_log and tensorboard_log_dir:
            train_summary_writer = SummaryWriter(os.path.join(tensorboard_log_dir, TRAINING))
            if validation_set is not None and validation_set.size > 0:
                validation_summary_writer = SummaryWriter(os.path.join(tensorboard_log_dir, VALIDATION))
            if test_set is not None and test_set.size > 0:
                test_summary_writer = SummaryWriter(os.path.join(tensorboard_log_dir, TEST))

        # ================ Resume logic ================
        if self.resume and self.resume_files_exist(training_progress_tracker_path, training_checkpoints_path):
            logger.info("Resuming training from previous run.")
            progress_tracker = self.resume_training_progress_tracker(training_progress_tracker_path)
            if self.is_coordinator():
                self.resume_weights_and_optimizer(training_checkpoints_path, checkpoint)
        else:
            logger.info("Creating fresh model training run.")
            progress_tracker = get_new_progress_tracker(
                batch_size=self.batch_size,
                learning_rate=self.base_learning_rate,
                best_eval_metric_value=get_initial_validation_value(self.validation_metric),
                best_reduce_learning_rate_eval_metric=get_initial_validation_value(
                    self.reduce_learning_rate_eval_metric
                ),
                best_increase_batch_size_eval_metric=get_initial_validation_value(self.increase_batch_size_eval_metric),
                output_features=output_features,
            )

        if self.horovod:
            # Horovod: broadcast initial variable states from rank 0 to all other processes.
            # This is necessary to ensure consistent initialization of all workers when
            # training is started with random weights or restored from a checkpoint.
            self.horovod.broadcast_parameters(self.model.state_dict(), root_rank=0)
            self.horovod.broadcast_optimizer_state(self.optimizer, root_rank=0)

        set_random_seed(self.random_seed)

        try:
            with training_set.initialize_batcher(
                batch_size=self.batch_size,
                should_shuffle=self.should_shuffle,
                seed=self.random_seed,
                horovod=self.horovod,
                ignore_last=True,
            ) as batcher:
                # ================ Training Loop ================
                self.total_steps = get_total_steps(self.epochs, batcher.steps_per_epoch, self.train_steps)

                # Get the terminal steps per checkpoint.
                final_steps_per_checkpoint = get_final_steps_per_checkpoint(
                    batcher.steps_per_epoch,
                    self.steps_per_checkpoint,
                    self.checkpoints_per_epoch,
                    self.is_coordinator(),
                )
                final_steps_per_checkpoint = min(final_steps_per_checkpoint, self.total_steps)

                early_stopping_steps = final_steps_per_checkpoint * self.early_stop

                if self.is_coordinator():
                    logger.info(
                        f"Training for {self.total_steps} step(s), approximately "
                        f"{int(self.total_steps / batcher.steps_per_epoch)} epoch(s)."
                    )
                    if self.early_stop < 0:
                        logger.info("Early stopping policy: None")
                    else:
                        logger.info(
                            f"Early stopping policy: {self.early_stop} round(s) of evaluation, or "
                            f"{early_stopping_steps} step(s), approximately "
                            f"{int(early_stopping_steps / batcher.steps_per_epoch)} epoch(s).\n"
                        )
                    logger.info(f"Starting with step {progress_tracker.steps}, epoch: {progress_tracker.epoch}")

                progress_bar_config = {
                    "desc": "Training",
                    "total": self.total_steps,
                    "disable": is_progressbar_disabled(),
                    "file": sys.stdout,
                }
                progress_bar = LudwigProgressBar(self.report_tqdm_to_ray, progress_bar_config, self.is_coordinator())

                while progress_tracker.steps < self.total_steps:
                    # note that batch size may change over epochs
                    batcher.set_epoch(progress_tracker.epoch, progress_tracker.batch_size)

                    # epoch init
                    start_time = time.time()

                    # Reset the metrics at the start of the next epoch
                    self.model.train()  # Sets model to training mode.
                    self.model.reset_metrics()

                    self.callback(lambda c: c.on_epoch_start(self, progress_tracker, save_path))

                    # Trains over a full epoch of data.
                    should_break = self._train_loop(
                        batcher,
                        progress_tracker,
                        save_path,
                        train_summary_writer,
                        progress_bar,
                        training_set,
                        validation_set,
                        test_set,
                        start_time,
                        validation_summary_writer,
                        test_summary_writer,
                        model_hyperparameters_path,
                        output_features,
                        metrics_names,
                        checkpoint_manager,
                        final_steps_per_checkpoint,
                        early_stopping_steps,
                    )

                    # ================ Post Training Epoch ================
                    progress_tracker.epoch += 1
                    self.callback(lambda c: c.on_epoch_end(self, progress_tracker, save_path))

                    if self.is_coordinator():
                        # ========== Save training progress ==========
                        logger.debug(
                            f"Epoch {progress_tracker.epoch} took: "
                            f"{time_utils.strdelta((time.time() - start_time) * 1000.0)}."
                        )
                        if not self.skip_save_progress:
                            checkpoint_manager.save(progress_tracker.steps)
                            progress_tracker.save(os.path.join(save_path, TRAINING_PROGRESS_TRACKER_FILE_NAME))

                    # Early stop if needed.
                    if should_break:
                        break
        finally:
            # ================ Finished Training ================
            self.callback(
                lambda c: c.on_trainer_train_teardown(self, progress_tracker, save_path, self.is_coordinator()),
                coordinator_only=False,
            )

            if train_summary_writer is not None:
                train_summary_writer.close()
            if validation_summary_writer is not None:
                validation_summary_writer.close()
            if test_summary_writer is not None:
                test_summary_writer.close()

            if self.is_coordinator() and not self.skip_save_progress:
                checkpoint_manager.close()

        # Load the best weights from saved checkpoint
        if self.is_coordinator() and not self.skip_save_model:
            self.model.load(save_path)

        # restore original sigint signal handler
        if self.original_sigint_handler and threading.current_thread() == threading.main_thread():
            signal.signal(signal.SIGINT, self.original_sigint_handler)

        return (
            self.model,
            progress_tracker.train_metrics,
            progress_tracker.validation_metrics,
            progress_tracker.test_metrics,
        )

    def _train_loop(
        self,
        batcher,
        progress_tracker,
        save_path,
        train_summary_writer,
        progress_bar,
        training_set,
        validation_set,
        test_set,
        start_time,
        validation_summary_writer,
        test_summary_writer,
        model_hyperparameters_path,
        output_features,
        metrics_names,
        checkpoint_manager,
        final_steps_per_checkpoint: int,
        early_stopping_steps: int,
    ) -> bool:
        """Completes up to one epoch through the data."""
        while not batcher.last_batch() and progress_tracker.steps < self.total_steps:
            self.callback(lambda c: c.on_batch_start(self, progress_tracker, save_path))

            # Set learning rate for this batch
            current_learning_rate = progress_tracker.learning_rate

            if self.decay:
                current_learning_rate = exponential_decay(
                    current_learning_rate,
                    self.decay_rate,
                    self.decay_steps,
                    progress_tracker.steps,
                    self.staircase,
                )

            if self.horovod:
                current_learning_rate = learning_rate_warmup_distributed(
                    current_learning_rate,
                    progress_tracker.epoch,
                    self.learning_rate_warmup_epochs,
                    self.horovod.size(),
                    batcher.step,
                    batcher.steps_per_epoch,
                ) * self.lr_scale_fn(self.horovod.size())
            else:
                current_learning_rate = learning_rate_warmup(
                    current_learning_rate,
                    progress_tracker.epoch,
                    self.learning_rate_warmup_epochs,
                    batcher.step,
                    batcher.steps_per_epoch,
                )
            self.set_optimizer_learning_rate(current_learning_rate)

            # obtain batch
            batch = batcher.next_batch()

            # Move tensors to cuda here.
            inputs = {
                i_feat.feature_name: torch.from_numpy(np.array(batch[i_feat.proc_column], copy=True)).to(self.device)
                for i_feat in self.model.input_features.values()
            }
            targets = {
                o_feat.feature_name: torch.from_numpy(np.array(batch[o_feat.proc_column], copy=True)).to(self.device)
                for o_feat in self.model.output_features.values()
            }

            loss, all_losses = self.train_step(
                inputs,
                targets,
            )

            if self.is_coordinator() and not self.skip_save_log:
                self.write_step_summary(
                    train_summary_writer=train_summary_writer,
                    combined_loss=loss,
                    all_losses=all_losses,
                    step=progress_tracker.steps,
                    learning_rate=current_learning_rate,
                )

            progress_tracker.steps += 1
            progress_bar.update(1)
            if self.is_coordinator():
                logger.debug(
                    f"training: completed batch {progress_bar.total_steps} "
                    f"memory used: "
                    f"{psutil.Process(os.getpid()).memory_info()[0] / 1e6:0.2f}MB"
                )

            if progress_tracker.steps % final_steps_per_checkpoint == 0:
                # Checkpoint the model.
                if self.is_coordinator() and not self.skip_save_progress:
                    checkpoint_manager.save(progress_tracker.steps)
                    progress_tracker.save(os.path.join(save_path, TRAINING_PROGRESS_TRACKER_FILE_NAME))

                should_break = self.run_evaluation(
                    training_set,
                    validation_set,
                    test_set,
                    progress_tracker,
                    train_summary_writer,
                    validation_summary_writer,
                    test_summary_writer,
                    model_hyperparameters_path,
                    output_features,
                    metrics_names,
                    save_path,
                    loss,
                    all_losses,
                    early_stopping_steps,
                )
                if should_break:
                    return should_break

            self.callback(lambda c: c.on_batch_end(self, progress_tracker, save_path))

        return False

    def train_online(self, dataset):
        self.model.train()  # Sets model training mode.
        with dataset.initialize_batcher(
            batch_size=self.batch_size, should_shuffle=self.should_shuffle, horovod=self.horovod, ignore_last=True
        ) as batcher:
            # training step loop
            progress_bar_config = {
                "desc": "Training online",
                "total": batcher.steps_per_epoch,
                "file": sys.stdout,
                "disable": is_progressbar_disabled(),
            }
            progress_bar = LudwigProgressBar(self.report_tqdm_to_ray, progress_bar_config, self.is_coordinator())

            while not batcher.last_batch():
                batch = batcher.next_batch()
                inputs = {
                    i_feat.feature_name: torch.from_numpy(np.array(batch[i_feat.proc_column], copy=True)).to(
                        self.device
                    )
                    for i_feat in self.model.input_features.values()
                }
                targets = {
                    o_feat.feature_name: torch.from_numpy(np.array(batch[o_feat.proc_column], copy=True)).to(
                        self.device
                    )
                    for o_feat in self.model.output_features.values()
                }

                self.train_step(
                    inputs,
                    targets,
                )

                progress_bar.update(1)

            progress_bar.close()
        return self.model

    @property
    def validation_field(self):
        return self._validation_field

    @property
    def validation_metric(self):
        return self._validation_metric

    def evaluation(self, dataset, dataset_name, metrics_log, tables, batch_size, progress_tracker):
        predictor = Predictor(
            self.model, batch_size=batch_size, horovod=self.horovod, report_tqdm_to_ray=self.report_tqdm_to_ray
        )
        metrics, _ = predictor.batch_evaluation(dataset, collect_predictions=False, dataset_name=dataset_name)

        append_metrics(self.model, dataset_name, metrics, metrics_log, tables, progress_tracker)

        return metrics_log, tables

    def check_progress_on_validation(
        self,
        progress_tracker,
        validation_output_feature_name,
        validation_metric: str,
        save_path,
        model_hyperparameters_path,
        reduce_learning_rate_on_plateau,
        reduce_learning_rate_on_plateau_patience,
        reduce_learning_rate_on_plateau_rate,
        reduce_learning_rate_eval_metric,
        reduce_learning_rate_eval_split,
        increase_batch_size_on_plateau,
        increase_batch_size_on_plateau_patience,
        increase_batch_size_on_plateau_rate,
        increase_batch_size_on_plateau_max,
        increase_batch_size_eval_metric,
        increase_batch_size_eval_split,
        early_stopping_steps: int,
        skip_save_model,
    ) -> bool:
        """Checks the history of validation scores.

        Uses history of validation scores to reduce learning rate, increase batch size, and decide whether training
        should stop.

        Saves the model if scores have improved.

        Returns whether the model should stop training.
        """
        should_break = False
        improved_fn = get_improved_fn(validation_metric)

        all_validation_metrics = progress_tracker.validation_metrics[validation_output_feature_name]
        # The most recent validation_metric metric.
        eval_metric: TrainerMetric = all_validation_metrics[validation_metric][-1]
        eval_metric_value = eval_metric[-1]

        if eval_metric_value != eval_metric_value:
            # Fallback to 0 if the validation metric value is a NaN.
            # This is potentially relevant for small datasets like those used in testing where if there's only a
            # single output label, some metrics like ROC may turn out to be NaN.
            # However, we want to guarantee that the model will be saved at least once over a full
            # training-checkpoint-eval-loop.
            eval_metric_value = 0

        if improved_fn(eval_metric_value, progress_tracker.best_eval_metric_value):
            previous_best_eval_metric_value = progress_tracker.best_eval_metric_value

            # Save the value, steps, epoch, and checkpoint number.
            progress_tracker.best_eval_metric_value = eval_metric_value
            progress_tracker.best_eval_metric_steps = progress_tracker.steps
            progress_tracker.best_eval_metric_epoch = progress_tracker.epoch
            progress_tracker.best_eval_metric_checkpoint_number = progress_tracker.checkpoint_number

            # Save best metrics for all data subsets.
            progress_tracker.best_eval_train_metrics = get_latest_metrics_dict(progress_tracker.train_metrics)
            progress_tracker.best_eval_validation_metrics = get_latest_metrics_dict(progress_tracker.validation_metrics)
            progress_tracker.best_eval_test_metrics = get_latest_metrics_dict(progress_tracker.test_metrics)

            if self.is_coordinator():
                logger.info(
                    f"Evaluation validation metric: '{validation_output_feature_name}' '{validation_metric}' improved."
                )
                absolute_eval_metric_value_change = round(
                    abs(previous_best_eval_metric_value - progress_tracker.best_eval_metric_value), 3
                )
                if metric_registry[validation_metric].get_objective() == MINIMIZE:
                    logger.info(
                        f"'{validation_output_feature_name}' '{validation_metric}' decreased by "
                        f"{absolute_eval_metric_value_change}."
                    )
                else:
                    logger.info(
                        f"'{validation_output_feature_name}' '{validation_metric}' increased by "
                        f"{absolute_eval_metric_value_change}."
                    )
                # Save the model.
                if not skip_save_model:
                    logger.info("New best model saved.\n")
                    self.model.save(save_path)

        last_improvement_in_steps = progress_tracker.steps - progress_tracker.best_eval_metric_steps
        progress_tracker.last_improvement_steps = last_improvement_in_steps

        if last_improvement_in_steps != 0 and self.is_coordinator():
            logger.info(
                f"Last improvement of {validation_output_feature_name} validation {validation_metric} happened "
                + f"{last_improvement_in_steps} step(s) ago.\n"
            )

        # ========== Reduce Learning Rate Plateau logic ========
        if reduce_learning_rate_on_plateau > 0:
            self.reduce_learning_rate(
                progress_tracker,
                validation_output_feature_name,
                reduce_learning_rate_on_plateau,
                reduce_learning_rate_on_plateau_patience,
                reduce_learning_rate_on_plateau_rate,
                reduce_learning_rate_eval_metric,
                reduce_learning_rate_eval_split,
            )
            progress_tracker.last_learning_rate_reduction = (
                progress_tracker.steps - progress_tracker.last_learning_rate_reduction_steps
            )
            if (
                progress_tracker.last_learning_rate_reduction > 0
                and progress_tracker.last_reduce_learning_rate_eval_metric_improvement > 0
                and not progress_tracker.num_reductions_learning_rate >= reduce_learning_rate_on_plateau
            ):
                logger.info(
                    f"Last learning rate reduction happened {progress_tracker.last_learning_rate_reduction} step(s) "
                    f"ago, improvement of {validation_output_feature_name} {reduce_learning_rate_eval_split} "
                    f"{reduce_learning_rate_eval_metric} happened "
                    f"{progress_tracker.last_reduce_learning_rate_eval_metric_improvement} step(s) ago."
                )

        # ========== Increase Batch Size Plateau logic =========
        if increase_batch_size_on_plateau > 0:
            self.increase_batch_size(
                progress_tracker,
                validation_output_feature_name,
                increase_batch_size_on_plateau,
                increase_batch_size_on_plateau_patience,
                increase_batch_size_on_plateau_rate,
                increase_batch_size_on_plateau_max,
                increase_batch_size_eval_metric,
                increase_batch_size_eval_split,
            )
            progress_tracker.last_increase_batch_size = (
                progress_tracker.steps - progress_tracker.last_increase_batch_size_steps
            )
            if (
                progress_tracker.last_increase_batch_size > 0
                and progress_tracker.last_increase_batch_size_eval_metric_improvement > 0
                and not progress_tracker.num_increases_batch_size >= increase_batch_size_on_plateau
                and not progress_tracker.batch_size >= increase_batch_size_on_plateau_max
            ):
                logger.info(
                    "Last batch size increase "
                    f"happened {progress_tracker.last_increase_batch_size} step(s) ago, "
                    f"improvement of {validation_output_feature_name} {increase_batch_size_eval_split} "
                    f"{increase_batch_size_eval_metric} happened "
                    f"{progress_tracker.last_increase_batch_size_eval_metric_improvement} step(s) ago."
                )

        # ========== Early Stop logic ==========
        # If any early stopping condition is satisfied, either lack of improvement for many steps, or via callbacks on
        # any worker, then trigger early stopping.
        early_stop_bool = 0 < early_stopping_steps <= last_improvement_in_steps
        if not early_stop_bool:
            for callback in self.callbacks:
                if callback.should_early_stop(self, progress_tracker, self.is_coordinator()):
                    early_stop_bool = True
                    break

        should_early_stop = torch.as_tensor([early_stop_bool], dtype=torch.int)
        if self.horovod:
            should_early_stop = self.horovod.allreduce(should_early_stop)
        if should_early_stop.item():
            if self.is_coordinator():
                logger.info(
                    f"\nEARLY STOPPING due to lack of validation improvement. It has been {last_improvement_in_steps} "
                    "step(s) since last validation improvement."
                )
            should_break = True
        return should_break

    def set_steps_to_1_or_quit(self, signum, frame):
        """Custom SIGINT handler used to elegantly exit training.

        A single SIGINT will stop training after the next training step. A second SIGINT will stop training immediately.
        """
        if not self.received_sigint:
            self.total_steps = 1
            self.received_sigint = True
            logger.critical("\nReceived SIGINT, will finish this training step and then conclude training.")
            logger.critical("Send another SIGINT to immediately interrupt the process.")
        else:
            logger.critical("\nReceived a second SIGINT, will now quit")
            if self.original_sigint_handler:
                signal.signal(signal.SIGINT, self.original_sigint_handler)
            sys.exit(1)

    def resume_files_exist(
        self,
        training_progress_tracker_path: str,
        training_checkpoint_path: str,
    ) -> bool:
        missing_files = []
        if self.is_coordinator():
            # training_progress.json
            if not path_exists(training_progress_tracker_path):
                missing_files.append(training_progress_tracker_path)
            # latest.ckpt in training_checkpoints/
            latest_ckpt = os.path.join(training_checkpoint_path, "latest.ckpt")
            if not path_exists(latest_ckpt):
                missing_files.append(latest_ckpt)
        if missing_files:
            logger.warning(f"Could not find {missing_files} while trying to resume model training.")
            return False
        return True

    def resume_training_progress_tracker(self, training_progress_tracker_path):
        progress_tracker_dict = None
        if self.is_coordinator():
            logger.info(f"Loading progress tracker for model: {training_progress_tracker_path}")
            progress_tracker_dict = load_json(training_progress_tracker_path)
        if self.horovod:
            logger.debug("Broadcasting model progress tracker dict to all workers")
            progress_tracker_dict = self.horovod.broadcast_object(
                progress_tracker_dict, name="broadcast_progress_tracker"
            )
        progress_tracker = ProgressTracker.load(progress_tracker_dict)
        return progress_tracker

    def resume_weights_and_optimizer(
        self,
        model_weights_progress_path: str,
        checkpoint: Checkpoint,
    ):
        CheckpointManager.load_latest_checkpoint(checkpoint, model_weights_progress_path, self.device)

    def reduce_learning_rate(
        self,
        progress_tracker: ProgressTracker,
        validation_output_feature_name: str,
        reduce_learning_rate_on_plateau: int,
        reduce_learning_rate_on_plateau_patience: int,
        reduce_learning_rate_on_plateau_rate: float,
        reduce_learning_rate_eval_metric: str = LOSS,
        reduce_learning_rate_eval_split: str = TRAINING,
    ):
        """Uses the progress tracker to determine if the learning rate should be reduced."""
        if not (progress_tracker.num_reductions_learning_rate >= reduce_learning_rate_on_plateau):

            if reduce_learning_rate_eval_split == TRAINING:
                split_metrics = progress_tracker.train_metrics
            elif reduce_learning_rate_eval_split == VALIDATION:
                split_metrics = progress_tracker.validation_metrics
            else:  # if reduce_learning_rate_eval_split == TEST:
                split_metrics = progress_tracker.test_metrics

            validation_metric = reduce_learning_rate_eval_metric
            last_metric: TrainerMetric = split_metrics[validation_output_feature_name][validation_metric][-1]
            last_metric_value = last_metric[-1]

            improved_fn = get_improved_fn(validation_metric)
            is_improved = improved_fn(last_metric_value, progress_tracker.best_reduce_learning_rate_eval_metric)
            if is_improved:
                # we update the best metric value and set it to the current one
                # and reset last improvement step count
                progress_tracker.best_reduce_learning_rate_eval_metric = last_metric_value
                progress_tracker.last_reduce_learning_rate_eval_metric_improvement = 0
            else:
                progress_tracker.last_reduce_learning_rate_eval_metric_improvement += 1
                if not is_improved and (
                    # learning rate reduction happened more than N steps ago
                    progress_tracker.last_learning_rate_reduction >= reduce_learning_rate_on_plateau_patience
                    # No improvement of the evaluation metric since more than N steps ago
                    and progress_tracker.last_reduce_learning_rate_eval_metric_improvement
                    >= reduce_learning_rate_on_plateau_patience
                ):
                    progress_tracker.learning_rate *= reduce_learning_rate_on_plateau_rate

                    if self.is_coordinator():
                        logger.info(
                            f"PLATEAU REACHED, reducing learning rate to {progress_tracker.learning_rate} due to lack "
                            f"of improvement of {validation_output_feature_name} {reduce_learning_rate_eval_split} "
                            f"{validation_metric}."
                        )

                    progress_tracker.last_learning_rate_reduction_steps = progress_tracker.steps
                    progress_tracker.last_learning_rate_reduction = 0
                    progress_tracker.num_reductions_learning_rate += 1

                    if progress_tracker.num_reductions_learning_rate >= reduce_learning_rate_on_plateau:
                        if self.is_coordinator():
                            logger.info(
                                f"Learning rate was already reduced {progress_tracker.num_reductions_learning_rate} "
                                "time(s), not reducing it anymore."
                            )

    def increase_batch_size(
        self,
        progress_tracker: ProgressTracker,
        validation_output_feature_name: str,
        increase_batch_size_on_plateau: int,
        increase_batch_size_on_plateau_patience: int,
        increase_batch_size_on_plateau_rate: float,
        increase_batch_size_on_plateau_max: int,
        increase_batch_size_eval_metric: str = LOSS,
        increase_batch_size_eval_split: str = TRAINING,
    ):
        """Uses the progress tracker to determine if the batch size should be increased."""
        if (
            not progress_tracker.num_increases_batch_size >= increase_batch_size_on_plateau
            and not progress_tracker.batch_size == increase_batch_size_on_plateau_max
        ):

            if increase_batch_size_eval_split == TRAINING:
                split_metrics = progress_tracker.train_metrics
            elif increase_batch_size_eval_split == VALIDATION:
                split_metrics = progress_tracker.validation_metrics
            else:  # if increase_batch_size_eval_split == TEST:
                split_metrics = progress_tracker.test_metrics

            validation_metric = increase_batch_size_eval_metric
            last_metric = split_metrics[validation_output_feature_name][validation_metric][-1]
            last_metric_value = last_metric[-1]

            improved_fn = get_improved_fn(validation_metric)
            is_improved = improved_fn(last_metric_value, progress_tracker.best_increase_batch_size_eval_metric)
            if is_improved:
                # We update the best metric value and set it to the current one, and reset last
                # improvement step count
                progress_tracker.best_increase_batch_size_eval_metric = last_metric_value
                progress_tracker.last_increase_batch_size_eval_metric_improvement = 0
            else:
                progress_tracker.last_increase_batch_size_eval_metric_improvement += 1
                if not is_improved and (
                    # Batch size increase happened more than N steps ago
                    progress_tracker.last_increase_batch_size >= increase_batch_size_on_plateau_patience
                    and (
                        # No improvement of the evaluation metric since more than N steps ago
                        progress_tracker.last_increase_batch_size_eval_metric_improvement
                        >= increase_batch_size_on_plateau_patience
                    )
                ):
                    progress_tracker.batch_size = min(
                        int(increase_batch_size_on_plateau_rate * progress_tracker.batch_size),
                        increase_batch_size_on_plateau_max,
                    )

                    if self.is_coordinator():
                        logger.info(
                            f"PLATEAU REACHED, increasing batch size to {progress_tracker.batch_size} due to lack of "
                            f"improvement of {validation_output_feature_name} {increase_batch_size_eval_split} "
                            f"{validation_metric}."
                        )

                    progress_tracker.last_increase_batch_size_steps = progress_tracker.steps
                    progress_tracker.last_increase_batch_size = 0
                    progress_tracker.num_increases_batch_size += 1

                    if progress_tracker.num_increases_batch_size >= increase_batch_size_on_plateau:
                        if self.is_coordinator():
                            logger.info(
                                f"Batch size was already increased {progress_tracker.num_increases_batch_size} times, "
                                "not increasing it anymore."
                            )
                    elif progress_tracker.batch_size >= increase_batch_size_on_plateau_max:
                        if self.is_coordinator():
                            logger.info(
                                f"Batch size was already increased {progress_tracker.num_increases_batch_size} times, "
                                f"currently it is {progress_tracker.batch_size}, the maximum allowed."
                            )

    def is_coordinator(self):
        if not self.horovod:
            return True
        return self.horovod.rank() == 0

    @property
    def local_rank(self) -> int:
        if not self.horovod:
            return 0
        return self.horovod.local_rank()

    def barrier(self):
        if not self.horovod:
            return
        self.horovod.allreduce(torch.as_tensor([0], dtype=torch.int))

    def callback(self, fn, coordinator_only=True):
        if not coordinator_only or self.is_coordinator():
            for callback in self.callbacks:
                fn(callback)


class RemoteTrainer(Trainer):
    def __init__(self, gpus=None, gpu_memory_limit=None, allow_parallel_threads=True, **kwargs):
        super().__init__(**kwargs)

        # Only return results from rank 0 to reduce network overhead
        self.train = return_first(self.train)
        self.train_online = return_first(self.train_online)


learning_rate_scale_fns = {
    "linear": lambda n: n,
    "sqrt": lambda n: math.sqrt(n),
    "constant": lambda n: 1,
}
