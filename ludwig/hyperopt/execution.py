import copy
import datetime
import glob
import json
import logging
import os
import shutil
import threading
import time
import traceback
import uuid
from functools import lru_cache
from inspect import signature
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from packaging import version

from ludwig.api import LudwigModel
from ludwig.backend import initialize_backend, RAY
from ludwig.callbacks import Callback
from ludwig.constants import COLUMN, MAXIMIZE, TEST, TRAINER, TRAINING, TYPE, VALIDATION
from ludwig.hyperopt.results import RayTuneResults, TrialResults
from ludwig.hyperopt.search_algos import get_search_algorithm
from ludwig.hyperopt.utils import load_json_values
from ludwig.modules.metric_modules import get_best_function
from ludwig.utils import metric_utils
from ludwig.utils.data_utils import hash_dict, NumpyEncoder
from ludwig.utils.defaults import default_random_seed
from ludwig.utils.fs_utils import has_remote_protocol
from ludwig.utils.misc_utils import get_from_registry

logger = logging.getLogger(__name__)

try:
    import ray
    from ray import tune
    from ray.tune import register_trainable, Stopper
    from ray.tune.schedulers.resource_changing_scheduler import DistributeResources, ResourceChangingScheduler
    from ray.tune.suggest import BasicVariantGenerator, ConcurrencyLimiter

    _ray_114 = version.parse(ray.__version__) >= version.parse("1.14")
    if _ray_114:
        from ray.tune.search import SEARCH_ALG_IMPORT
        from ray.tune.syncer import get_node_to_storage_syncer, SyncConfig
    else:
        from ray.tune.syncer import get_cloud_sync_client
        from ray.tune.suggest import SEARCH_ALG_IMPORT

    from ray.tune.utils import wait_for_gpu
    from ray.tune.utils.placement_groups import PlacementGroupFactory
    from ray.util.queue import Queue as RayQueue

except ImportError as e:
    logger.warning(f"ImportError (execution.py) failed to import ray with error: \n\t{e}")
    ray = None
    Stopper = object
    get_horovod_kwargs = None


try:
    from ludwig.backend.ray import RayBackend

    # TODO: refactor this into an interface
    def _is_ray_backend(backend) -> bool:
        if isinstance(backend, str):
            return backend == RAY
        return isinstance(backend, RayBackend)

except ImportError as e:
    logger.warning(
        f"ImportError (execution.py) failed to import RayBackend with error: \n\t{e}. "
        "The LocalBackend will be used instead. If you want to use the RayBackend, please install ludwig[distributed]."
    )
    get_horovod_kwargs = None

    class RayBackend:
        pass

    def _is_ray_backend(backend) -> bool:
        return False


def identity(x):
    return x


def _get_relative_checkpoints_dir_parts(path: Path):
    return path.parts[-2:]


# Follwing disabled at the moment, expect to be re-enabled pending https://github.com/ludwig-ai/ludwig/issues/2039
def ray_resource_allocation_function(
    trial_runner: "trial_runner.TrialRunner",  # noqa
    trial: "Trial",  # noqa
    result: Dict[str, Any],
    scheduler: "ResourceChangingScheduler",
):
    """Determine resources to allocate to running trials."""
    pgf = DistributeResources(trial_runner, trial, result, scheduler)
    # restore original base trial resources

    # create bundles
    if scheduler.base_trial_resources.required_resources.get("GPU", 0):
        bundles = [{"CPU": 1, "GPU": 1}] * int(pgf.required_resources["GPU"])
    else:
        bundles = [{"CPU": 1}] * (int(pgf.required_resources["CPU"] - 0.001))
    # we can't set Trial actor's CPUs to 0 so we just go very low
    bundles = [{"CPU": 0.001}] + bundles
    pgf = PlacementGroupFactory(bundles)
    return pgf


def checkpoint(progress_tracker, save_path):
    def ignore_dot_files(src, files):
        return [f for f in files if f.startswith(".")]

    with tune.checkpoint_dir(step=progress_tracker.tune_checkpoint_num) as checkpoint_dir:
        checkpoint_model = os.path.join(checkpoint_dir, "model")
        # Atomic copying of the checkpoints
        if not os.path.isdir(checkpoint_model):
            copy_id = uuid.uuid4()
            tmp_dst = f"{checkpoint_model}.{copy_id}.tmp"
            assert os.path.exists(save_path)
            shutil.copytree(save_path, tmp_dst, ignore=ignore_dot_files)
            try:
                os.rename(tmp_dst, checkpoint_model)
            except Exception:
                shutil.rmtree(tmp_dst)


class RayTuneExecutor:
    def __init__(
        self,
        parameters: dict,
        output_feature: str,
        metric: str,
        goal: str,
        split: str,
        search_alg: Optional[Dict] = None,
        cpu_resources_per_trial: int = None,
        gpu_resources_per_trial: int = None,
        kubernetes_namespace: str = None,
        time_budget_s: Union[int, float, datetime.timedelta] = None,
        max_concurrent_trials: Optional[int] = None,
        num_samples: int = 1,
        scheduler: Optional[Dict] = None,
        **kwargs,
    ) -> None:
        if ray is None:
            raise ImportError("ray module is not installed. To install it, try running pip install ray")
        self.output_feature = output_feature
        self.metric = metric
        self.split = split
        if not ray.is_initialized():
            try:
                ray.init("auto", ignore_reinit_error=True)
            except ConnectionError:
                logger.info("Initializing new Ray cluster...")
                ray.init(ignore_reinit_error=True)
        self.search_space, self.decode_ctx = self._get_search_space(parameters)
        self.num_samples = num_samples
        self.goal = goal
        self.search_algorithm = get_search_algorithm(search_alg)
        self.scheduler = None if scheduler is None else tune.create_scheduler(scheduler[TYPE], **scheduler)
        self.output_feature = output_feature
        self.metric = metric
        self.split = split
        self.trial_id = 0
        self.cpu_resources_per_trial = cpu_resources_per_trial
        self.gpu_resources_per_trial = gpu_resources_per_trial
        self.kubernetes_namespace = kubernetes_namespace
        self.time_budget_s = time_budget_s
        self.max_concurrent_trials = max_concurrent_trials
        self.sync_config = None
        self.sync_client = None
        # Head node is the node to which all checkpoints are synced if running on a K8s cluster.
        self.head_node_ip = ray.util.get_node_ip_address()

    def _get_search_space(self, parameters: Dict) -> Tuple[Dict, Dict]:
        """Encode search space parameters as JSON with context for decoding."""
        config = {}
        ctx = {}
        for param, values in parameters.items():
            # Encode list and dict types as JSON encoded strings to
            # workaround type limitations of the underlying frameworks
            values = self.encode_values(param, values, ctx)

            param_search_type = values["space"].lower()
            if hasattr(tune, param_search_type):
                param_search_space = getattr(tune, param_search_type)
            else:
                raise ValueError(f"'{param_search_type}' is not a supported Ray Tune search space")

            param_search_input_args = {}
            param_search_space_sig = signature(param_search_space)
            for arg in param_search_space_sig.parameters.values():
                if arg.name in values:
                    param_search_input_args[arg.name] = values[arg.name]
                else:
                    if arg.default is arg.empty:
                        raise ValueError(f"Parameter '{arg}' not defined for {param}")
            config[param] = param_search_space(**param_search_input_args)
        return config, ctx

    @staticmethod
    def encode_values(param: str, values: Dict, ctx: Dict) -> Dict:
        """JSON encodes any search spaces whose values are lists / dicts.

        Only applies to grid search and choice options.  See here for details:

        https://docs.ray.io/en/master/tune/api_docs/search_space.html#random-distributions-api
        """
        values = values.copy()
        for key in ["values", "categories"]:
            if key in values and not isinstance(values[key][0], (int, float)):
                values[key] = [json.dumps(v) for v in values[key]]
                ctx[param] = json.loads
        return values

    @staticmethod
    def decode_values(config: Dict, ctx: Dict) -> Dict:
        """Decode config values with the decode function in the context.

        Uses the identity function if no encoding is needed.
        """
        return {key: ctx.get(key, identity)(value) for key, value in config.items()}

    def _has_metric(self, stats, split):
        if not stats:
            return False

        if split is not None:
            if split not in stats:
                return False
            stats = stats[split]

        if self.output_feature not in stats:
            return False
        stats = stats[self.output_feature]

        if self.metric not in stats:
            return False
        stats = stats[self.metric]
        return len(stats) > 0

    def _has_eval_metric(self, stats):
        if stats is None:
            return False

        if self.output_feature not in stats:
            return False
        stats = stats[self.output_feature]

        for metric_part in self.metric.split("."):
            if not isinstance(stats, dict) or metric_part not in stats:
                return False
            stats = stats[metric_part]
        return isinstance(stats, float)

    def get_metric_score(self, train_stats) -> float:
        if self._has_metric(train_stats, VALIDATION):
            logger.info("Returning metric score from training (validation) statistics")
            return self.get_metric_score_from_train_stats(train_stats, VALIDATION)
        elif self._has_metric(train_stats, TRAINING):
            logger.info("Returning metric score from training split statistics, " "as no validation was given")
            return self.get_metric_score_from_train_stats(train_stats, TRAINING)
        else:
            raise RuntimeError("Unable to obtain metric score from missing training (validation) statistics")

    def get_metric_score_from_eval_stats(self, eval_stats) -> Union[float, list]:
        stats = eval_stats[self.output_feature]
        for metric_part in self.metric.split("."):
            if isinstance(stats, dict):
                if metric_part in stats:
                    stats = stats[metric_part]
                else:
                    raise ValueError(f"Evaluation statistics do not contain the metric {self.metric}")
            else:
                raise ValueError(f"Evaluation statistics do not contain the metric {self.metric}")

        if not isinstance(stats, float):
            raise ValueError(f"The metric {self.metric} in evaluation statistics is not a numerical value: {stats}")
        return stats

    def get_metric_score_from_train_stats(self, train_stats, select_split=None) -> float:
        select_split = select_split or VALIDATION

        # grab the results of the model with highest validation test performance
        train_valiset_stats = train_stats[select_split]

        validation_field_result = train_valiset_stats[self.output_feature]
        best_function = get_best_function(self.metric)

        # results of the model with highest validation test performance
        epoch_best_validation_metric, best_validation_metric = best_function(
            enumerate(validation_field_result[self.metric]), key=lambda pair: pair[1]
        )

        return best_validation_metric

    def sort_hyperopt_results(self, hyperopt_results):
        return sorted(
            hyperopt_results, key=lambda hp_res: hp_res.metric_score, reverse=self.hyperopt_sampler.goal == MAXIMIZE
        )

    @property
    def _cpu_resources_per_trial_non_none(self):
        return self.cpu_resources_per_trial if self.cpu_resources_per_trial is not None else 1

    @property
    def _gpu_resources_per_trial_non_none(self):
        return self.gpu_resources_per_trial if self.gpu_resources_per_trial is not None else 0

    def _get_remote_checkpoint_dir(self, trial_dir: Path) -> Optional[Union[str, Tuple[str, str]]]:
        """Get the path to remote checkpoint directory."""
        if self.sync_config is None:
            return None

        if self.sync_config.upload_dir is not None:
            # Cloud storage sync config
            remote_checkpoint_dir = os.path.join(
                self.sync_config.upload_dir, *_get_relative_checkpoints_dir_parts(trial_dir)
            )
            return remote_checkpoint_dir
        elif self.kubernetes_namespace is not None:
            # Kubernetes sync config. Returns driver node name and path.
            # When running on kubernetes, each trial is rsynced to the node running the main process.
            node_name = self._get_kubernetes_node_address_by_ip()(self.head_node_ip)
            return (node_name, trial_dir)
        else:
            logger.warning(
                "Checkpoint syncing disabled as syncing is only supported to remote cloud storage or on Kubernetes "
                "clusters is supported. To use syncing, set the kubernetes_namespace in the config or use a cloud URI "
                "as the output directory."
            )
            return None

    @lru_cache(maxsize=1)
    def _get_kubernetes_node_address_by_ip(self) -> Callable:
        """Returns a method to get the node name by IP address within a K8s cluster."""
        assert self.kubernetes_namespace is not None
        from ray.tune.integration.kubernetes import KubernetesSyncer

        # Initialized with null local and remote directories as we only need to use get_node_address_by_ip.
        kubernetes_syncer = KubernetesSyncer(None, None)

        return kubernetes_syncer.get_node_address_by_ip

    # For specified [stopped] trial, remove checkpoint marker on any partial checkpoints
    @staticmethod
    def _remove_partial_checkpoints(trial_path: str):
        marker_paths = glob.glob(os.path.join(glob.escape(trial_path), "checkpoint_*/.is_checkpoint"))
        for marker_path in marker_paths:
            chkpt_dir = os.path.dirname(marker_path)
            metadata_file = glob.glob(os.path.join(glob.escape(chkpt_dir), "*.tune_metadata"))
            # glob.glob: filenames starting with a dot are special cases
            # that are not matched by '*' and '?' patterns.
            metadata_file += glob.glob(os.path.join(glob.escape(chkpt_dir), ".tune_metadata"))
            metadata_file = list(set(metadata_file))  # avoid duplication
            if len(metadata_file) < 1:
                # Remove checkpoint marker on incomplete directory
                os.remove(marker_path)

    def _get_best_model_path(self, trial_path, analysis):
        remote_checkpoint_dir = self._get_remote_checkpoint_dir(Path(trial_path))
        if remote_checkpoint_dir is not None:
            self.sync_client.sync_down(remote_checkpoint_dir, trial_path)
            self.sync_client.wait_or_retry()
        self._remove_partial_checkpoints(trial_path)  # needed by get_best_checkpoint
        mod_path = None
        try:
            mod_path = analysis.get_best_checkpoint(trial_path.rstrip("/"))
        except Exception:
            logger.warning(
                f"Cannot get best model path for {trial_path} due to exception below:\n{traceback.format_exc()}"
            )
        return mod_path

    @staticmethod
    def _evaluate_best_model(
        trial,
        trial_path,
        best_model_path,
        dataset,
        data_format,
        skip_save_unprocessed_output,
        skip_save_predictions,
        skip_save_eval_stats,
        gpus,
        gpu_memory_limit,
        allow_parallel_threads,
        backend,
        debug,
    ):
        best_model = LudwigModel.load(
            os.path.join(best_model_path, "model"),
            backend=backend,
            gpus=gpus,
            gpu_memory_limit=gpu_memory_limit,
            allow_parallel_threads=allow_parallel_threads,
        )
        if best_model.config[TRAINER]["eval_batch_size"]:
            batch_size = best_model.config[TRAINER]["eval_batch_size"]
        else:
            batch_size = best_model.config[TRAINER]["batch_size"]
        try:
            eval_stats, _, _ = best_model.evaluate(
                dataset=dataset,
                data_format=data_format,
                batch_size=batch_size,
                output_directory=trial_path,
                skip_save_unprocessed_output=skip_save_unprocessed_output,
                skip_save_predictions=skip_save_predictions,
                skip_save_eval_stats=skip_save_eval_stats,
                collect_predictions=False,
                collect_overall_stats=True,
                return_type="dict",
                debug=debug,
            )
            trial["eval_stats"] = json.dumps(eval_stats, cls=NumpyEncoder)
        except NotImplementedError:
            logger.warning(
                "Skipping evaluation as the necessary methods are not "
                "supported. Full exception below:\n"
                f"{traceback.format_exc()}"
            )

    def _run_experiment(self, config, checkpoint_dir, hyperopt_dict, decode_ctx, is_using_ray_backend=False):
        for gpu_id in ray.get_gpu_ids():
            # Previous trial may not have freed its memory yet, so wait to avoid OOM
            wait_for_gpu(gpu_id)
        # Some config values may be JSON encoded as strings, so decode them here
        config = self.decode_values(config, decode_ctx)

        trial_id = tune.get_trial_id()
        modified_config = substitute_parameters(copy.deepcopy(hyperopt_dict["config"]), config)

        trial_dir = Path(tune.get_trial_dir())
        driver_trial_location = ray.util.get_node_ip_address()

        hyperopt_dict["config"] = modified_config
        hyperopt_dict["experiment_name "] = f'{hyperopt_dict["experiment_name"]}_{trial_id}'
        hyperopt_dict["output_directory"] = str(trial_dir)

        tune_executor = self
        if is_using_ray_backend:
            ray_queue = RayQueue(actor_options={"num_cpus": 0})
        else:
            ray_queue = None

        def report(progress_tracker):
            # The progress tracker's metrics are nested dictionaries of TrainerMetrics: feature_name -> metric_name ->
            # List[TrainerMetric], with one entry per training checkpoint, according to steps_per_checkpoint.
            # We reduce the dictionary of TrainerMetrics to a simple list of floats for interfacing with Ray Tune.
            train_stats = {
                TRAINING: metric_utils.reduce_trainer_metrics_dict(progress_tracker.train_metrics),
                VALIDATION: metric_utils.reduce_trainer_metrics_dict(progress_tracker.validation_metrics),
                TEST: metric_utils.reduce_trainer_metrics_dict(progress_tracker.test_metrics),
            }

            metric_score = tune_executor.get_metric_score(train_stats)
            tune.report(
                parameters=json.dumps(config, cls=NumpyEncoder),
                metric_score=metric_score,
                training_stats=json.dumps(train_stats, cls=NumpyEncoder),
                eval_stats="{}",
                trial_id=tune.get_trial_id(),
                trial_dir=tune.get_trial_dir(),
            )

        class RayTuneReportCallback(Callback):
            def __init__(self):
                super().__init__()
                self.last_steps = 0

            def _get_remote_checkpoint_dir(self) -> Optional[Union[str, Tuple[str, str]]]:
                # sync client has to be recreated to avoid issues with serialization
                return tune_executor._get_remote_checkpoint_dir(trial_dir)

            def _checkpoint_progress(self, trainer, progress_tracker, save_path) -> None:
                """Checkpoints the progress tracker."""
                if is_using_ray_backend:
                    save_path = Path(save_path)
                    remote_checkpoint_dir = self._get_remote_checkpoint_dir()
                    if remote_checkpoint_dir is not None:
                        sync_client = tune_executor.sync_client
                        sync_client.sync_up(str(save_path.parent.parent.absolute()), remote_checkpoint_dir)
                        sync_client.wait_or_retry()
                    ray_queue.put((progress_tracker, str(save_path)))
                    return
                checkpoint(progress_tracker, save_path)

            def on_trainer_train_setup(self, trainer, save_path, is_coordinator):
                if is_using_ray_backend and checkpoint_dir and driver_trial_location != ray.util.get_node_ip_address():
                    save_path = Path(save_path)

                    for path in trial_dir.glob("checkpoint*"):
                        if path not in (save_path.parent, checkpoint_dir):
                            shutil.rmtree(path, ignore_errors=True)

                    remote_checkpoint_dir = self._get_remote_checkpoint_dir()
                    if remote_checkpoint_dir is not None:
                        sync_client = tune_executor.sync_client
                        sync_client.sync_down(remote_checkpoint_dir, str(trial_dir.absolute()))
                        sync_client.wait_or_retry()

            def on_eval_end(self, trainer, progress_tracker, save_path):
                progress_tracker.tune_checkpoint_num += 1
                self.last_steps = progress_tracker.steps
                self._checkpoint_progress(trainer, progress_tracker, save_path)
                if not is_using_ray_backend:
                    report(progress_tracker)

            def on_trainer_train_teardown(self, trainer, progress_tracker, save_path, is_coordinator):
                if is_coordinator and progress_tracker.steps > self.last_steps:
                    # Note: Calling tune.report in both on_eval_end() and here can cause multiprocessing issues
                    # for some ray samplers if not steps have happened since the last eval.
                    self._checkpoint_progress(trainer, progress_tracker, save_path)
                    if not is_using_ray_backend:
                        report(progress_tracker)

        callbacks = hyperopt_dict.get("callbacks") or []
        hyperopt_dict["callbacks"] = callbacks + [RayTuneReportCallback()]

        # set tune resources
        if is_using_ray_backend:
            resources = tune.get_trial_resources()
            # check if we are using at least 1 gpu per trial
            use_gpu = bool(self._gpu_resources_per_trial_non_none)
            # get the resources assigned to the current trial
            num_gpus = resources.required_resources.get("GPU", 0)
            num_cpus = resources.required_resources.get("CPU", 1) if num_gpus == 0 else 0

            hvd_kwargs = {
                "num_workers": int(num_gpus) if use_gpu else 1,
                "use_gpu": use_gpu,
                "resources_per_worker": {
                    "CPU": num_cpus,
                    "GPU": 1 if use_gpu else 0,
                },
            }
            hyperopt_dict["backend"].set_distributed_kwargs(**hvd_kwargs)

            logger.debug(f"Trial horovod kwargs: {hvd_kwargs}")

        stats = []

        def _run():
            train_stats, eval_stats = run_experiment(
                **hyperopt_dict,
                model_resume_path=checkpoint_dir,
                parameters=config,
            )
            stats.append((train_stats, eval_stats))

        if is_using_ray_backend:
            # We have to pull the results to the trial actor
            # from worker actors, as the Tune session is running
            # only on the trial actor
            thread = threading.Thread(target=_run)
            thread.daemon = True
            thread.start()

            if self.sync_config is not None:
                remote_checkpoint_dir = self._get_remote_checkpoint_dir(trial_dir)

            def check_queue():
                qsize = ray_queue.qsize()
                if qsize:
                    results = ray_queue.get_nowait_batch(qsize)
                    if self.sync_client is not None:
                        self.sync_client.sync_down(remote_checkpoint_dir, str(trial_dir.absolute()))
                        self.sync_client.wait()
                    for progress_tracker, save_path in results:
                        checkpoint(progress_tracker, str(trial_dir.joinpath(Path(save_path))))
                        report(progress_tracker)

            while thread.is_alive():
                thread.join(timeout=0)
                check_queue()
                time.sleep(0.1)
            thread.join()
            check_queue()
        else:
            # remove threading overhead
            _run()

        if not stats:
            raise RuntimeError("Experiment did not complete.")
        train_stats, eval_stats = stats.pop()

        metric_score = self.get_metric_score(train_stats)
        tune.report(
            parameters=json.dumps(config, cls=NumpyEncoder),
            metric_score=metric_score,
            training_stats=json.dumps(train_stats, cls=NumpyEncoder),
            eval_stats=json.dumps(eval_stats, cls=NumpyEncoder),
            trial_id=tune.get_trial_id(),
            trial_dir=tune.get_trial_dir(),
        )

    def execute(
        self,
        config,
        dataset=None,
        training_set=None,
        validation_set=None,
        test_set=None,
        training_set_metadata=None,
        data_format=None,
        experiment_name="hyperopt",
        model_name="run",
        resume=None,
        skip_save_training_description=False,
        skip_save_training_statistics=False,
        skip_save_model=False,
        skip_save_progress=False,
        skip_save_log=False,
        skip_save_processed_input=True,
        skip_save_unprocessed_output=False,
        skip_save_predictions=False,
        skip_save_eval_stats=False,
        output_directory="results",
        gpus=None,
        gpu_memory_limit=None,
        allow_parallel_threads=True,
        callbacks=None,
        backend=None,
        random_seed=default_random_seed,
        debug=False,
        hyperopt_log_verbosity=3,
        **kwargs,
    ) -> RayTuneResults:
        if isinstance(dataset, str) and not has_remote_protocol(dataset) and not os.path.isabs(dataset):
            dataset = os.path.abspath(dataset)

        if isinstance(backend, str):
            backend = initialize_backend(backend)

        if gpus is not None:
            raise ValueError(
                "Parameter `gpus` is not supported when using Ray Tune. "
                "Configure GPU resources with Ray and set `gpu_resources_per_trial` in your "
                "hyperopt config."
            )

        if gpu_memory_limit is None and 0 < self._gpu_resources_per_trial_non_none < 1:
            # Enforce fractional GPU utilization
            gpu_memory_limit = self.gpu_resources_per_trial

        hyperopt_dict = dict(
            config=config,
            dataset=dataset,
            training_set=training_set,
            validation_set=validation_set,
            test_set=test_set,
            training_set_metadata=training_set_metadata,
            data_format=data_format,
            experiment_name=experiment_name,
            model_name=model_name,
            eval_split=self.split,
            skip_save_training_description=skip_save_training_description,
            skip_save_training_statistics=skip_save_training_statistics,
            skip_save_model=skip_save_model,
            skip_save_progress=skip_save_progress,
            skip_save_log=skip_save_log,
            skip_save_processed_input=skip_save_processed_input,
            skip_save_unprocessed_output=skip_save_unprocessed_output,
            skip_save_predictions=skip_save_predictions,
            skip_save_eval_stats=skip_save_eval_stats,
            output_directory=output_directory,
            gpus=gpus,
            gpu_memory_limit=gpu_memory_limit,
            allow_parallel_threads=allow_parallel_threads,
            callbacks=callbacks,
            backend=backend,
            random_seed=random_seed,
            debug=debug,
        )

        mode = "min" if self.goal != MAXIMIZE else "max"
        metric = "metric_score"
        # if random seed not set, use Ludwig seed
        self.search_algorithm.check_for_random_seed(random_seed)
        if self.search_algorithm.search_alg_dict is not None:
            if TYPE not in self.search_algorithm.search_alg_dict:
                candiate_search_algs = [search_alg for search_alg in SEARCH_ALG_IMPORT.keys()]
                logger.warning(
                    "WARNING: search_alg type parameter missing, using 'variant_generator' as default. "
                    f"These are possible values for the type parameter: {candiate_search_algs}."
                )
                search_alg = None
            else:
                search_alg_type = self.search_algorithm.search_alg_dict[TYPE]
                search_alg = tune.create_searcher(
                    search_alg_type, metric=metric, mode=mode, **self.search_algorithm.search_alg_dict
                )
        else:
            search_alg = None

        if self.max_concurrent_trials:
            assert (
                self.max_concurrent_trials > 0
            ), f"`max_concurrent_trials` must be greater than 0, got {self.max_concurrent_trials}"
            if isinstance(search_alg, BasicVariantGenerator) or search_alg is None:
                search_alg = BasicVariantGenerator(max_concurrent=self.max_concurrent_trials)
            elif isinstance(search_alg, ConcurrencyLimiter):
                raise ValueError(
                    "You have specified `max_concurrent_trials`, but the search "
                    "algorithm is already a `ConcurrencyLimiter`. FIX THIS "
                    "by setting `max_concurrent_trials=None`."
                )
            else:
                search_alg = ConcurrencyLimiter(search_alg, max_concurrent=self.max_concurrent_trials)

        resources_per_trial = {
            "cpu": self._cpu_resources_per_trial_non_none,
            "gpu": self._gpu_resources_per_trial_non_none,
        }

        def run_experiment_trial(config, local_hyperopt_dict, checkpoint_dir=None):
            return self._run_experiment(
                config, checkpoint_dir, local_hyperopt_dict, self.decode_ctx, _is_ray_backend(backend)
            )

        tune_config = {}
        tune_callbacks = []
        for callback in callbacks or []:
            run_experiment_trial, tune_config = callback.prepare_ray_tune(
                run_experiment_trial,
                tune_config,
                tune_callbacks,
            )

        if _is_ray_backend(backend):
            # for now, we do not do distributed training on cpu (until spread scheduling is implemented for Ray Train)
            # but we do want to enable it when GPUs are specified
            resources_per_trial = PlacementGroupFactory(
                [{}] + ([{"CPU": 0, "GPU": 1}] * self._gpu_resources_per_trial_non_none)
                if self._gpu_resources_per_trial_non_none
                else [{}] + [{"CPU": self._cpu_resources_per_trial_non_none}]
            )

        if has_remote_protocol(output_directory):
            run_experiment_trial = tune.durable(run_experiment_trial)
            self.sync_config = tune.SyncConfig(sync_to_driver=False, upload_dir=output_directory)
            if _ray_114:
                self.sync_client = get_node_to_storage_syncer(SyncConfig(upload_dir=output_directory))
            else:
                self.sync_client = get_cloud_sync_client(output_directory)
            output_directory = None
        elif self.kubernetes_namespace:
            from ray.tune.integration.kubernetes import KubernetesSyncClient, NamespacedKubernetesSyncer

            self.sync_config = tune.SyncConfig(sync_to_driver=NamespacedKubernetesSyncer(self.kubernetes_namespace))
            self.sync_client = KubernetesSyncClient(self.kubernetes_namespace)

        run_experiment_trial_params = tune.with_parameters(run_experiment_trial, local_hyperopt_dict=hyperopt_dict)
        register_trainable(f"trainable_func_f{hash_dict(config).decode('ascii')}", run_experiment_trial_params)

        # Note that resume="AUTO" will attempt to resume the experiment if possible, and
        # otherwise will start a new experiment:
        # https://docs.ray.io/en/latest/tune/tutorials/tune-stopping.html
        should_resume = "AUTO" if resume is None else resume

        try:
            analysis = tune.run(
                f"trainable_func_f{hash_dict(config).decode('ascii')}",
                name=experiment_name,
                config={
                    **self.search_space,
                    **tune_config,
                },
                scheduler=self.scheduler,
                search_alg=search_alg,
                num_samples=self.num_samples,
                keep_checkpoints_num=1,
                max_failures=1,  # retry a trial failure once
                resources_per_trial=resources_per_trial,
                time_budget_s=self.time_budget_s,
                sync_config=self.sync_config,
                local_dir=output_directory,
                metric=metric,
                mode=mode,
                trial_name_creator=lambda trial: f"trial_{trial.trial_id}",
                trial_dirname_creator=lambda trial: f"trial_{trial.trial_id}",
                callbacks=tune_callbacks,
                stop=CallbackStopper(callbacks),
                verbose=hyperopt_log_verbosity,
                resume=should_resume,
                log_to_file=True,
            )
        except Exception as e:
            # Explicitly raise a RuntimeError if an error is encountered during a Ray trial.
            # NOTE: Cascading the exception with "raise _ from e" still results in hanging.
            raise RuntimeError(f"Encountered Ray Tune error: {e}")

        if "metric_score" in analysis.results_df.columns:
            ordered_trials = analysis.results_df.sort_values("metric_score", ascending=self.goal != MAXIMIZE)

            # Catch nans in edge case where the trial doesn't complete
            temp_ordered_trials = []
            for kwargs in ordered_trials.to_dict(orient="records"):
                for key in ["parameters", "training_stats", "eval_stats"]:
                    if isinstance(kwargs[key], float):
                        kwargs[key] = {}
                temp_ordered_trials.append(kwargs)

            # Trials w/empty eval_stats fields & non-empty training_stats fields ran intermediate
            # tune.report call(s) but were terminated before reporting eval_stats from post-train
            # evaluation (e.g., trial stopped due to time budget or relatively poor performance.)
            # For any such trials, run model evaluation for the best model in that trial & record
            # results in ordered_trials which is returned & is persisted in hyperopt_statistics.json.
            for trial in temp_ordered_trials:
                if trial["eval_stats"] == "{}" and trial["training_stats"] != "{}":
                    # Evaluate the best model on the eval_split, which is validation_set
                    if validation_set is not None and validation_set.size > 0:
                        trial_path = trial["trial_dir"]
                        best_model_path = self._get_best_model_path(trial_path, analysis)
                        if best_model_path is not None:
                            self._evaluate_best_model(
                                trial,
                                trial_path,
                                best_model_path,
                                validation_set,
                                data_format,
                                skip_save_unprocessed_output,
                                skip_save_predictions,
                                skip_save_eval_stats,
                                gpus,
                                gpu_memory_limit,
                                allow_parallel_threads,
                                backend,
                                debug,
                            )
                        else:
                            logger.warning("Skipping evaluation as no model checkpoints were available")
                    else:
                        logger.warning("Skipping evaluation as no validation set was provided")

            ordered_trials = [TrialResults.from_dict(load_json_values(kwargs)) for kwargs in temp_ordered_trials]
        else:
            logger.warning("No trials reported results; check if time budget lower than epoch latency")
            ordered_trials = []

        return RayTuneResults(ordered_trials=ordered_trials, experiment_analysis=analysis)


class CallbackStopper(Stopper):
    """Ray Tune Stopper that triggers the entire job to stop if one callback returns True."""

    def __init__(self, callbacks: Optional[List[Callback]]):
        self.callbacks = callbacks or []

    def __call__(self, trial_id, result):
        return False

    def stop_all(self):
        for callback in self.callbacks:
            if callback.should_stop_hyperopt():
                return True
        return False


def get_build_hyperopt_executor(executor_type):
    return get_from_registry(executor_type, executor_registry)


executor_registry = {"ray": RayTuneExecutor}


def set_values(model_dict, name, parameters_dict):
    if name in parameters_dict:
        params = parameters_dict[name]
        for key, value in params.items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    model_dict[key][sub_key] = sub_value
            else:
                model_dict[key] = value


def get_parameters_dict(parameters):
    parameters_dict = {}
    for name, value in parameters.items():
        curr_dict = parameters_dict
        name_list = name.split(".")
        for i, name_elem in enumerate(name_list):
            if i == len(name_list) - 1:
                curr_dict[name_elem] = value
            else:
                name_dict = curr_dict.get(name_elem, {})
                curr_dict[name_elem] = name_dict
                curr_dict = name_dict
    return parameters_dict


def substitute_parameters(config, parameters):
    parameters_dict = get_parameters_dict(parameters)
    for input_feature in config["input_features"]:
        set_values(input_feature, input_feature[COLUMN], parameters_dict)
    for output_feature in config["output_features"]:
        set_values(output_feature, output_feature[COLUMN], parameters_dict)
    set_values(config["combiner"], "combiner", parameters_dict)
    set_values(config[TRAINER], TRAINER, parameters_dict)
    set_values(config["preprocessing"], "preprocessing", parameters_dict)
    return config


def run_experiment(
    config,
    parameters=None,
    dataset=None,
    training_set=None,
    validation_set=None,
    test_set=None,
    training_set_metadata=None,
    data_format=None,
    experiment_name="hyperopt",
    model_name="run",
    model_resume_path=None,
    eval_split=VALIDATION,
    skip_save_training_description=False,
    skip_save_training_statistics=False,
    skip_save_model=False,
    skip_save_progress=False,
    skip_save_log=False,
    skip_save_processed_input=False,
    skip_save_unprocessed_output=False,
    skip_save_predictions=False,
    skip_save_eval_stats=False,
    output_directory="results",
    gpus=None,
    gpu_memory_limit=None,
    allow_parallel_threads=True,
    callbacks=None,
    backend=None,
    random_seed=default_random_seed,
    debug=False,
    **kwargs,
):
    for callback in callbacks or []:
        callback.on_hyperopt_trial_start(parameters)

    # Collect training and validation losses and metrics
    # & append it to `results`
    model = LudwigModel(
        config=config,
        backend=backend,
        gpus=gpus,
        gpu_memory_limit=gpu_memory_limit,
        allow_parallel_threads=allow_parallel_threads,
        callbacks=callbacks,
    )
    eval_stats, train_stats, _, _ = model.experiment(
        dataset=dataset,
        training_set=training_set,
        validation_set=validation_set,
        test_set=test_set,
        training_set_metadata=training_set_metadata,
        data_format=data_format,
        experiment_name=experiment_name,
        model_name=model_name,
        model_resume_path=model_resume_path,
        eval_split=eval_split,
        skip_save_training_description=skip_save_training_description,
        skip_save_training_statistics=skip_save_training_statistics,
        skip_save_model=skip_save_model,
        skip_save_progress=skip_save_progress,
        skip_save_log=skip_save_log,
        skip_save_processed_input=skip_save_processed_input,
        skip_save_unprocessed_output=skip_save_unprocessed_output,
        skip_save_predictions=skip_save_predictions,
        skip_save_eval_stats=skip_save_eval_stats,
        output_directory=output_directory,
        skip_collect_predictions=True,
        skip_collect_overall_stats=False,
        random_seed=random_seed,
        debug=debug,
    )

    for callback in callbacks or []:
        callback.on_hyperopt_trial_end(parameters)

    return train_stats, eval_stats


def _run_experiment_unary(kwargs):
    """Unary function is needed by Fiber to map a list of args."""
    return run_experiment(**kwargs)
