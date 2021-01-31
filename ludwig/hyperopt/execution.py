import copy
import multiprocessing
import signal
from abc import ABC, abstractmethod

from ludwig.api import LudwigModel
from ludwig.constants import *
from ludwig.hyperopt.sampling import HyperoptSampler, \
    logger
from ludwig.modules.metric_modules import get_best_function
from ludwig.utils.defaults import default_random_seed
from ludwig.utils.misc_utils import get_available_gpu_memory, get_from_registry
from ludwig.utils.tf_utils import get_available_gpus_cuda_string


class HyperoptExecutor(ABC):
    def __init__(self, hyperopt_sampler: HyperoptSampler,
                 output_feature: str, metric: str, split: str) -> None:
        self.hyperopt_sampler = hyperopt_sampler
        self.output_feature = output_feature
        self.metric = metric
        self.split = split

    def get_metric_score(self, train_stats, eval_stats) -> float:
        if (train_stats is not None and
                self.split in train_stats and
                VALIDATION in train_stats and  # needed otherwise can-t figure
                # out the best epoch
                self.output_feature in train_stats[self.split] and
                self.metric in train_stats[self.split][self.output_feature]):
            logger.info("Returning metric score from training statistics")
            return self.get_metric_score_from_train_stats(train_stats)
        else:
            logger.info("Returning metric score from eval statistics. "
                        "If skip_save_model is True, eval statistics "
                        "are calculated using the model at the last epoch "
                        "rather than the model at the epoch with "
                        "best validation performance")
            return self.get_metric_score_from_eval_stats(eval_stats)

    def get_metric_score_from_eval_stats(self, eval_stats) -> float:
        if '.' in self.metric:
            metric_parts = self.metric.split('.')
            stats = eval_stats[self.output_feature]
            for metric_part in metric_parts:
                if isinstance(stats, dict):
                    if metric_part in stats:
                        stats = stats[metric_part]
                    else:
                        raise ValueError(
                            f"Evaluation statistics do not contain "
                            f"the metric {self.metric}")
                else:
                    raise ValueError(f"Evaluation statistics do not contain "
                                     f"the metric {self.metric}")
            if not isinstance(stats, float):
                raise ValueError(f"The metric {self.metric} in "
                                 f"evaluation statistics is not "
                                 f"a numerical value: {stats}")
            return stats
        return eval_stats[self.output_feature][self.metric]

    def get_metric_score_from_train_stats(self, train_stats) -> float:
        # grab the results of the model with highest validation test performance
        train_valiset_stats = train_stats[VALIDATION]
        train_evalset_stats = train_stats[self.split]

        validation_field_result = train_valiset_stats[self.output_feature]
        best_function = get_best_function(self.metric)

        # results of the model with highest validation test performance
        epoch_best_vali_metric, best_vali_metric = best_function(
            enumerate(validation_field_result[self.metric]),
            key=lambda pair: pair[1]
        )
        best_vali_metric_epoch_eval_metric = train_evalset_stats[
            self.output_feature][self.metric][
            epoch_best_vali_metric]

        return best_vali_metric_epoch_eval_metric

    def sort_hyperopt_results(self, hyperopt_results):
        return sorted(
            hyperopt_results, key=lambda hp_res: hp_res["metric_score"],
            reverse=self.hyperopt_sampler.goal == MAXIMIZE
        )

    @abstractmethod
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
            model_load_path=None,
            model_resume_path=None,
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
            use_horovod=None,
            random_seed=default_random_seed,
            debug=False,
            **kwargs
    ):
        pass


class SerialExecutor(HyperoptExecutor):
    def __init__(
            self, hyperopt_sampler: HyperoptSampler,
            output_feature: str,
            metric: str, split: str, **kwargs
    ) -> None:
        HyperoptExecutor.__init__(self, hyperopt_sampler, output_feature,
                                  metric, split)

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
            # model_load_path=None,
            # model_resume_path=None,
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
            use_horovod=None,
            random_seed=default_random_seed,
            debug=False,
            **kwargs
    ):
        hyperopt_results = []
        trials = 0
        while not self.hyperopt_sampler.finished():
            sampled_parameters = self.hyperopt_sampler.sample_batch()
            metric_scores = []

            for i, parameters in enumerate(sampled_parameters):
                modified_config = substitute_parameters(
                    copy.deepcopy(config), parameters)

                trial_id = trials + i

                model = LudwigModel(
                    config=modified_config,
                    use_horovod=use_horovod,
                    gpus=gpus,
                    gpu_memory_limit=gpu_memory_limit,
                    allow_parallel_threads=allow_parallel_threads,
                )
                eval_stats, train_stats, _, _ = model.experiment(
                    dataset=dataset,
                    training_set=training_set,
                    validation_set=validation_set,
                    test_set=test_set,
                    training_set_metadata=training_set_metadata,
                    data_format=data_format,
                    experiment_name=f'{experiment_name}_{trial_id}',
                    model_name=model_name,
                    # model_load_path=model_load_path,
                    # model_resume_path=model_resume_path,
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
                    skip_collect_predictions=True,
                    skip_collect_overall_stats=False,
                    random_seed=random_seed,
                    debug=debug,
                )
                metric_score = self.get_metric_score(train_stats, eval_stats)
                metric_scores.append(metric_score)

                hyperopt_results.append(
                    {
                        "parameters": parameters,
                        "metric_score": metric_score,
                        "training_stats": train_stats,
                        "eval_stats": eval_stats,
                    }
                )
            trials += len(sampled_parameters)

            self.hyperopt_sampler.update_batch(
                zip(sampled_parameters, metric_scores))

        hyperopt_results = self.sort_hyperopt_results(hyperopt_results)

        return hyperopt_results


class ParallelExecutor(HyperoptExecutor):
    num_workers = 2
    epsilon = 0.01
    epsilon_memory = 100
    TF_REQUIRED_MEMORY_PER_WORKER = 100

    def __init__(
            self,
            hyperopt_sampler: HyperoptSampler,
            output_feature: str,
            metric: str,
            split: str,
            num_workers: int = 2,
            epsilon: float = 0.01,
            **kwargs
    ) -> None:
        HyperoptExecutor.__init__(self, hyperopt_sampler, output_feature,
                                  metric, split)
        self.num_workers = num_workers
        self.epsilon = epsilon
        self.queue = None

    @staticmethod
    def init_worker():
        signal.signal(signal.SIGINT, signal.SIG_IGN)

    def _run_experiment(self, hyperopt_dict):
        parameters = hyperopt_dict["parameters"]
        train_stats, eval_stats = run_experiment(**hyperopt_dict)
        metric_score = self.get_metric_score(train_stats, eval_stats)

        return {
            "parameters": parameters,
            "metric_score": metric_score,
            "training_stats": train_stats,
            "eval_stats": eval_stats,
        }

    def _run_experiment_gpu(self, hyperopt_dict):
        gpu_id_meta = self.queue.get()
        try:
            parameters = hyperopt_dict['parameters']
            hyperopt_dict["gpus"] = gpu_id_meta["gpu_id"]
            hyperopt_dict["gpu_memory_limit"] = gpu_id_meta["gpu_memory_limit"]
            train_stats, eval_stats = run_experiment(**hyperopt_dict)
            metric_score = self.get_metric_score(train_stats, eval_stats)
        finally:
            self.queue.put(gpu_id_meta)
        return {
            "parameters": parameters,
            "metric_score": metric_score,
            "training_stats": train_stats,
            "eval_stats": eval_stats,
        }

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
            # model_load_path=None,
            # model_resume_path=None,
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
            use_horovod=None,
            random_seed=default_random_seed,
            debug=False,
            **kwargs
    ):
        ctx = multiprocessing.get_context('spawn')

        if gpus is None:
            gpus = get_available_gpus_cuda_string()

        if gpus is not None:

            num_available_cpus = ctx.cpu_count()

            if self.num_workers > num_available_cpus:
                logger.warning(
                    "WARNING: num_workers={}, num_available_cpus={}. "
                    "To avoid bottlenecks setting num workers to be less "
                    "or equal to number of available cpus is suggested".format(
                        self.num_workers, num_available_cpus
                    )
                )

            if isinstance(gpus, int):
                gpus = str(gpus)
            gpus = gpus.strip()
            gpu_ids = gpus.split(",")
            num_gpus = len(gpu_ids)

            available_gpu_memory_list = get_available_gpu_memory()
            gpu_ids_meta = {}

            if num_gpus < self.num_workers:
                fraction = (num_gpus / self.num_workers) - self.epsilon
                for gpu_id in gpu_ids:
                    available_gpu_memory = available_gpu_memory_list[
                        int(gpu_id)]
                    required_gpu_memory = fraction * available_gpu_memory

                    if gpu_memory_limit is None:
                        logger.warning(
                            'WARNING: Setting gpu_memory_limit to {} '
                            'as there available gpus are {} '
                            'and the num of workers is {} '
                            'and the available gpu memory for gpu_id '
                            '{} is {}'.format(
                                required_gpu_memory, num_gpus,
                                self.num_workers,
                                gpu_id, available_gpu_memory)
                        )
                        new_gpu_memory_limit = required_gpu_memory - \
                                               (
                                                       self.TF_REQUIRED_MEMORY_PER_WORKER * self.num_workers)
                    else:
                        new_gpu_memory_limit = gpu_memory_limit
                        if new_gpu_memory_limit > available_gpu_memory:
                            logger.warning(
                                'WARNING: Setting gpu_memory_limit to available gpu '
                                'memory {} minus an epsilon as the value specified is greater than '
                                'available gpu memory.'.format(
                                    available_gpu_memory)
                            )
                            new_gpu_memory_limit = available_gpu_memory - self.epsilon_memory

                        if required_gpu_memory < new_gpu_memory_limit:
                            if required_gpu_memory > 0.5 * available_gpu_memory:
                                if available_gpu_memory != new_gpu_memory_limit:
                                    logger.warning(
                                        'WARNING: Setting gpu_memory_limit to available gpu '
                                        'memory {} minus an epsilon as the gpus would be underutilized for '
                                        'the parallel processes otherwise'.format(
                                            available_gpu_memory)
                                    )
                                    new_gpu_memory_limit = available_gpu_memory - self.epsilon_memory
                            else:
                                logger.warning(
                                    'WARNING: Setting gpu_memory_limit to {} '
                                    'as the available gpus are {} and the num of workers '
                                    'are {} and the available gpu memory for gpu_id '
                                    '{} is {}'.format(
                                        required_gpu_memory, num_gpus,
                                        self.num_workers,
                                        gpu_id, available_gpu_memory)
                                )
                                new_gpu_memory_limit = required_gpu_memory
                        else:
                            logger.warning(
                                'WARNING: gpu_memory_limit could be increased to {} '
                                'as the available gpus are {} and the num of workers '
                                'are {} and the available gpu memory for gpu_id '
                                '{} is {}'.format(
                                    required_gpu_memory, num_gpus,
                                    self.num_workers,
                                    gpu_id, available_gpu_memory)
                            )

                    process_per_gpu = int(
                        available_gpu_memory / new_gpu_memory_limit)
                    gpu_ids_meta[gpu_id] = {
                        "gpu_memory_limit": new_gpu_memory_limit,
                        "process_per_gpu": process_per_gpu}
            else:
                for gpu_id in gpu_ids:
                    gpu_ids_meta[gpu_id] = {
                        "gpu_memory_limit": gpu_memory_limit,
                        "process_per_gpu": 1}

            manager = ctx.Manager()
            self.queue = manager.Queue()

            for gpu_id in gpu_ids:
                process_per_gpu = gpu_ids_meta[gpu_id]["process_per_gpu"]
                gpu_memory_limit = gpu_ids_meta[gpu_id]["gpu_memory_limit"]
                for _ in range(process_per_gpu):
                    gpu_id_meta = {"gpu_id": gpu_id,
                                   "gpu_memory_limit": gpu_memory_limit}
                    self.queue.put(gpu_id_meta)

        pool = ctx.Pool(self.num_workers,
                        ParallelExecutor.init_worker)
        try:
            hyperopt_results = []
            trials = 0
            while not self.hyperopt_sampler.finished():
                sampled_parameters = self.hyperopt_sampler.sample_batch()

                hyperopt_parameters = []
                for i, parameters in enumerate(sampled_parameters):
                    modified_config = substitute_parameters(
                        copy.deepcopy(config), parameters)

                    trial_id = trials + i
                    hyperopt_parameters.append(
                        dict(
                            parameters=parameters,
                            config=modified_config,
                            eval_split=self.split,
                            dataset=dataset,
                            training_set=training_set,
                            validation_set=validation_set,
                            test_set=test_set,
                            training_set_metadata=training_set_metadata,
                            data_format=data_format,
                            experiment_name=f'{experiment_name}_{trial_id}',
                            model_name=model_name,
                            # model_load_path=model_load_path,
                            # model_resume_path=model_resume_path,
                            skip_save_training_description=skip_save_training_description,
                            skip_save_training_statistics=skip_save_training_statistics,
                            skip_save_model=skip_save_model,
                            skip_save_progress=skip_save_progress,
                            skip_save_log=skip_save_log,
                            # needed because of concurrent HDF5 writes
                            skip_save_processed_input=True,
                            skip_save_unprocessed_output=skip_save_unprocessed_output,
                            skip_save_predictions=skip_save_predictions,
                            skip_save_eval_stats=skip_save_eval_stats,
                            output_directory=output_directory,
                            gpus=gpus,
                            gpu_memory_limit=gpu_memory_limit,
                            allow_parallel_threads=allow_parallel_threads,
                            use_horovod=use_horovod,
                            random_seed=random_seed,
                            debug=debug,
                        )
                    )
                trials += len(sampled_parameters)

                if gpus is not None:
                    batch_results = pool.map(self._run_experiment_gpu,
                                             hyperopt_parameters)
                else:
                    batch_results = pool.map(self._run_experiment,
                                             hyperopt_parameters)

                self.hyperopt_sampler.update_batch(
                    (result["parameters"], result["metric_score"]) for result
                    in
                    batch_results
                )

                hyperopt_results.extend(batch_results)
        finally:
            pool.close()
            pool.join()

        hyperopt_results = self.sort_hyperopt_results(hyperopt_results)
        return hyperopt_results


class FiberExecutor(HyperoptExecutor):
    num_workers = 2
    fiber_backend = "local"

    def __init__(
            self,
            hyperopt_sampler: HyperoptSampler,
            output_feature: str,
            metric: str,
            split: str,
            num_workers: int = 2,
            num_cpus_per_worker: int = -1,
            num_gpus_per_worker: int = -1,
            fiber_backend: str = "local",
            **kwargs
    ) -> None:
        import fiber

        HyperoptExecutor.__init__(self, hyperopt_sampler, output_feature,
                                  metric, split)

        fiber.init(backend=fiber_backend)
        self.fiber_meta = fiber.meta

        self.num_cpus_per_worker = num_cpus_per_worker
        self.num_gpus_per_worker = num_gpus_per_worker

        self.resource_limits = {}
        if num_cpus_per_worker != -1:
            self.resource_limits["cpu"] = num_cpus_per_worker

        if num_gpus_per_worker != -1:
            self.resource_limits["gpu"] = num_gpus_per_worker

        self.num_workers = num_workers
        self.pool = fiber.Pool(num_workers)

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
            # model_load_path=None,
            # model_resume_path=None,
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
            use_horovod=None,
            random_seed=default_random_seed,
            debug=False,
            **kwargs
    ):
        experiment_kwargs = dict(
            dataset=dataset,
            training_set=training_set,
            validation_set=validation_set,
            test_set=test_set,
            training_set_metadata=training_set_metadata,
            data_format=data_format,
            model_name=model_name,
            # model_load_path=model_load_path,
            # model_resume_path=model_resume_path,
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
            use_horovod=use_horovod,
            random_seed=random_seed,
            debug=debug,
        )

        experiemnt_fn = _run_experiment_unary
        if self.resource_limits:
            experiemnt_fn = self.fiber_meta(**self.resource_limits)(
                experiemnt_fn)

        hyperopt_results = []
        trials = 0
        while not self.hyperopt_sampler.finished():
            sampled_parameters = self.hyperopt_sampler.sample_batch()
            metric_scores = []

            stats_batch = self.pool.map(
                experiemnt_fn,
                [
                    {
                        'config': substitute_parameters(
                            copy.deepcopy(config), parameters),
                        'experiment_name': f'{experiment_name}_{trials + i}',
                        **experiment_kwargs
                    }
                    for i, parameters in enumerate(sampled_parameters)
                ],
            )
            trials += len(sampled_parameters)

            for stats, parameters in zip(stats_batch, sampled_parameters):
                train_stats, eval_stats = stats
                metric_score = self.get_metric_score(train_stats, eval_stats)
                metric_scores.append(metric_score)

                hyperopt_results.append(
                    {
                        "parameters": parameters,
                        "metric_score": metric_score,
                        "training_stats": train_stats,
                        "eval_stats": eval_stats,
                    }
                )

            self.hyperopt_sampler.update_batch(
                zip(sampled_parameters, metric_scores))

        hyperopt_results = self.sort_hyperopt_results(hyperopt_results)

        return hyperopt_results


def get_build_hyperopt_executor(executor_type):
    return get_from_registry(executor_type, executor_registry)


executor_registry = {
    "serial": SerialExecutor,
    "parallel": ParallelExecutor,
    "fiber": FiberExecutor,
}


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
    set_values(config["training"], "training", parameters_dict)
    set_values(config["preprocessing"], "preprocessing",
               parameters_dict)
    return config


def run_experiment(
        config,
        dataset=None,
        training_set=None,
        validation_set=None,
        test_set=None,
        training_set_metadata=None,
        data_format=None,
        experiment_name="hyperopt",
        model_name="run",
        # model_load_path=None,
        # model_resume_path=None,
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
        use_horovod=None,
        random_seed=default_random_seed,
        debug=False,
        **kwargs
):
    # Collect training and validation losses and metrics
    # & append it to `results`
    model = LudwigModel(
        config=config,
        use_horovod=use_horovod,
        gpus=gpus,
        gpu_memory_limit=gpu_memory_limit,
        allow_parallel_threads=allow_parallel_threads,
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
        # model_load_path=model_load_path,
        # model_resume_path=model_resume_path,
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
    return train_stats, eval_stats


def _run_experiment_unary(kwargs):
    """Unary function is needed by Fiber to map a list of args."""
    return run_experiment(**kwargs)
