import logging
import os

from ludwig.constants import STRATEGY, EXECUTOR, VALIDATION, COMBINED, LOSS, \
    MINIMIZE, TYPE
from ludwig.hyperopt.execution import executor_registry
from ludwig.hyperopt.sampling import sampler_registry
from ludwig.utils.data_utils import save_json
from ludwig.utils.misc_utils import set_default_value, set_default_values, \
    get_from_registry, get_class_attributes
from ludwig.utils.print_utils import print_boxed

logger = logging.getLogger(__name__)


def update_hyperopt_params_with_defaults(hyperopt_params):
    set_default_value(hyperopt_params, STRATEGY, {})
    set_default_value(hyperopt_params, EXECUTOR, {})
    set_default_value(hyperopt_params, "split", VALIDATION)
    set_default_value(hyperopt_params, "output_feature", COMBINED)
    set_default_value(hyperopt_params, "metric", LOSS)
    set_default_value(hyperopt_params, "goal", MINIMIZE)

    set_default_values(hyperopt_params[STRATEGY], {TYPE: "random"})

    strategy = get_from_registry(hyperopt_params[STRATEGY][TYPE],
                                 sampler_registry)
    strategy_defaults = {k: v for k, v in strategy.__dict__.items() if
                         k in get_class_attributes(strategy)}
    set_default_values(
        hyperopt_params[STRATEGY], strategy_defaults,
    )

    set_default_values(hyperopt_params[EXECUTOR], {TYPE: "serial"})

    executor = get_from_registry(hyperopt_params[EXECUTOR][TYPE],
                                 executor_registry)
    executor_defaults = {k: v for k, v in executor.__dict__.items() if
                         k in get_class_attributes(executor)}
    set_default_values(
        hyperopt_params[EXECUTOR], executor_defaults,
    )


def print_hyperopt_results(hyperopt_results):
    print_boxed('HYPEROPT RESULTS', print_fun=logger.info)
    for hyperopt_result in hyperopt_results:
        logger.info('score: {:.6f} | parameters: {}'.format(
            hyperopt_result['metric_score'], hyperopt_result['parameters']
        ))
    logger.info("")


def save_hyperopt_stats(hyperopt_stats, hyperopt_dir_name):
    hyperopt_stats_fn = os.path.join(
        hyperopt_dir_name,
        'hyperopt_statistics.json'
    )
    save_json(hyperopt_stats_fn, hyperopt_stats)
