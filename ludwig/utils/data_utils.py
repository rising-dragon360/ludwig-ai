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
import collections
import csv
import functools
import json
import logging
import os.path
import pickle
import random
import re
from itertools import islice
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import yaml
from pandas.errors import ParserError
from sklearn.model_selection import KFold

from ludwig.utils.fs_utils import download_h5, open_file, upload_h5
from ludwig.utils.misc_utils import get_from_registry

try:
    import dask.dataframe as dd

    DASK_DF_FORMATS = {dd.core.DataFrame}
except ImportError:
    DASK_DF_FORMATS = set()
    dd = None

from ludwig.constants import PREPROCESSING, PROC_COLUMN, SPLIT
from ludwig.globals import MODEL_HYPERPARAMETERS_FILE_NAME, MODEL_WEIGHTS_FILE_NAME, TRAIN_SET_METADATA_FILE_NAME

logger = logging.getLogger(__name__)

DATASET_SPLIT_URL = "dataset_{}_fp"
DATA_PROCESSED_CACHE_DIR = "data_processed_cache_dir"
DATA_TRAIN_HDF5_FP = "data_train_hdf5_fp"
HDF5_COLUMNS_KEY = "columns"
DICT_FORMATS = {"dict", "dictionary", dict}
DATAFRAME_FORMATS = {"dataframe", "df", pd.DataFrame} | DASK_DF_FORMATS
CSV_FORMATS = {"csv"}
TSV_FORMATS = {"tsv"}
JSON_FORMATS = {"json"}
JSONL_FORMATS = {"jsonl"}
EXCEL_FORMATS = {"excel"}
PARQUET_FORMATS = {"parquet"}
PICKLE_FORMATS = {"pickle"}
FEATHER_FORMATS = {"feather"}
FWF_FORMATS = {"fwf"}
HTML_FORMATS = {"html"}
ORC_FORMATS = {"orc"}
SAS_FORMATS = {"sas"}
SPSS_FORMATS = {"spss"}
STATA_FORMATS = {"stata"}
HDF5_FORMATS = {"hdf5", "h5"}
CACHEABLE_FORMATS = set.union(
    *(
        CSV_FORMATS,
        TSV_FORMATS,
        JSON_FORMATS,
        JSONL_FORMATS,
        EXCEL_FORMATS,
        PARQUET_FORMATS,
        PICKLE_FORMATS,
        FEATHER_FORMATS,
        FWF_FORMATS,
        HTML_FORMATS,
        ORC_FORMATS,
        SAS_FORMATS,
        SPSS_FORMATS,
        STATA_FORMATS,
    )
)

PANDAS_DF = pd


def get_split_path(dataset_fp):
    return os.path.splitext(dataset_fp)[0] + ".split.csv"


def get_abs_path(data_csv_path, file_path):
    if data_csv_path is not None:
        return os.path.join(data_csv_path, file_path)
    else:
        return file_path


def load_csv(data_fp):
    with open_file(data_fp, "rb") as f:
        data = list(csv.reader(f))
    return data


def read_xsv(data_fp, df_lib=PANDAS_DF, separator=",", header=0, nrows=None, skiprows=None):
    """Helper method to read a csv file. Wraps around pd.read_csv to handle some exceptions. Can extend to cover
    cases as necessary.

    :param data_fp: path to the xsv file
    :param df_lib: DataFrame library used to read in the CSV
    :param separator: defaults separator to use for splitting
    :param header: header argument for pandas to read the csv
    :param nrows: number of rows to read from the csv, None means all
    :param skiprows: number of rows to skip from the csv, None means no skips
    :return: Pandas dataframe with the data
    """
    with open_file(data_fp, "r", encoding="utf8") as csvfile:
        try:
            dialect = csv.Sniffer().sniff(csvfile.read(1024 * 100), delimiters=[",", "\t", "|"])
            separator = dialect.delimiter
        except csv.Error:
            # Could not conclude the delimiter, defaulting to user provided
            pass

    kwargs = dict(sep=separator, header=header, skiprows=skiprows)

    if nrows is not None:
        kwargs["nrows"] = nrows

    try:
        df = df_lib.read_csv(data_fp, **kwargs)
    except ParserError:
        logger.warning("Failed to parse the CSV with pandas default way," " trying \\ as escape character.")
        df = df_lib.read_csv(data_fp, escapechar="\\", **kwargs)

    return df


read_csv = functools.partial(read_xsv, separator=",")
read_tsv = functools.partial(read_xsv, separator="\t")


def read_json(data_fp, df_lib, normalize=False):
    if normalize:
        return df_lib.json_normalize(load_json(data_fp))
    else:
        return df_lib.read_json(data_fp)


def read_jsonl(data_fp, df_lib):
    return df_lib.read_json(data_fp, lines=True)


def read_excel(data_fp, df_lib):
    fp_split = os.path.splitext(data_fp)
    if fp_split[1] == ".xls":
        excel_engine = "xlrd"
    else:
        excel_engine = "openpyxl"
    return df_lib.read_excel(data_fp, engine=excel_engine)


def read_parquet(data_fp, df_lib):
    return df_lib.read_parquet(data_fp)


def read_pickle(data_fp, df_lib):
    return df_lib.read_pickle(data_fp)


def read_fwf(data_fp, df_lib):
    return df_lib.read_fwf(data_fp)


def read_feather(data_fp, df_lib):
    return df_lib.read_feather(data_fp)


def read_html(data_fp, df_lib):
    return df_lib.read_html(data_fp)[0]


def read_orc(data_fp, df_lib):
    return df_lib.read_orc(data_fp)


def read_sas(data_fp, df_lib):
    return df_lib.read_sas(data_fp)


def read_spss(data_fp, df_lib):
    return df_lib.read_spss(data_fp)


def read_stata(data_fp, df_lib):
    return df_lib.read_stata(data_fp)


def read_hdf5(data_fp, **kwargs):
    return load_hdf5(data_fp, clean_cols=True)


def save_csv(data_fp, data):
    with open_file(data_fp, "w", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        for row in data:
            if not isinstance(row, collections.Iterable) or isinstance(row, str):
                row = [row]
            writer.writerow(row)


def csv_contains_column(data_fp, column_name):
    return column_name in read_csv(data_fp, nrows=0)  # only loads header


def load_yaml(yaml_fp):
    with open_file(yaml_fp, "r") as f:
        return yaml.safe_load(f)


def load_config_from_str(config):
    """Load the config as either a serialized string or a path to a YAML file."""
    config = yaml.safe_load(config)
    if isinstance(config, str):
        # Assume the caller provided a path name
        with open(config) as f:
            config = yaml.safe_load(f)
    return config


def load_json(data_fp):
    with open_file(data_fp, "r") as input_file:
        data = json.load(input_file)
    return data


def save_json(data_fp, data, sort_keys=True, indent=4):
    with open_file(data_fp, "w") as output_file:
        json.dump(data, output_file, cls=NumpyEncoder, sort_keys=sort_keys, indent=indent)


def to_json_dict(d):
    """Converts Python dict to pure JSON ready format."""
    return json.loads(json.dumps(d, cls=NumpyEncoder))


def chunk_dict(data, chunk_size=100):
    """Split large dictionary into chunks.

    Source: https://stackoverflow.com/a/22878842
    """
    it = iter(data)
    for i in range(0, len(data), chunk_size):
        yield {k: data[k] for k in islice(it, chunk_size)}


def flatten_dict(d, parent_key="", sep="."):
    """Based on https://www.geeksforgeeks.org/python-convert-nested-dictionary-into-flattened-dictionary/"""
    items = []
    for k, v in d.items():
        new_key = parent_key + sep + k if parent_key else k

        if isinstance(v, collections.MutableMapping):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        elif isinstance(v, list):
            list_mapping = {str(i): item for i, item in enumerate(v)}
            items.extend(flatten_dict(list_mapping, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def flatten_df(df, backend):
    # Workaround for: https://issues.apache.org/jira/browse/ARROW-5645
    column_shapes = {}
    for c in df.columns:
        df = backend.df_engine.persist(df)
        shape = backend.df_engine.compute(
            backend.df_engine.map_objects(
                df[c],
                lambda x: np.array(x).shape,
            ).max()
        )

        if len(shape) > 1:
            column_shapes[c] = shape
            df[c] = backend.df_engine.map_objects(df[c], lambda x: np.array(x).reshape(-1))
    return df, column_shapes


def unflatten_df(df, column_shapes, backend):
    for c in df.columns:
        shape = column_shapes.get(c)
        if shape:
            df[c] = backend.df_engine.map_objects(df[c], lambda x: np.array(x).reshape(shape))
    return df


def to_numpy_dataset(df):
    dataset = {}
    for col in df.columns:
        dataset[col] = np.stack(df[col].to_numpy())
    return dataset


def from_numpy_dataset(dataset):
    col_mapping = {}
    for k, v in dataset.items():
        if len(v.shape) > 1:
            # unstacking, needed for ndarrays of dimension 2 and more
            (*vals,) = v
        else:
            # not unstacking. Needed because otherwise pandas casts types
            # the way it wants, like converting a list of float32 scalats
            # to a column of float64
            vals = v
        col_mapping[k] = vals
    return pd.DataFrame.from_dict(col_mapping)


def save_hdf5(data_fp, data):
    numpy_dataset = to_numpy_dataset(data)
    with upload_h5(data_fp) as h5_file:
        h5_file.create_dataset(HDF5_COLUMNS_KEY, data=np.array(data.columns.values, dtype="S"))
        for column in data.columns:
            h5_file.create_dataset(column, data=numpy_dataset[column])


def load_hdf5(data_fp, clean_cols: bool = False):
    with download_h5(data_fp) as hdf5_data:
        columns = [s.decode("utf-8") for s in hdf5_data[HDF5_COLUMNS_KEY][()].tolist()]

        numpy_dataset = {}
        for column in columns:
            # Column names from training hdf5 will be in the form 'Survived_a2fv4'
            np_col = column.rsplit("_", 1)[0] if clean_cols else column
            numpy_dataset[np_col] = hdf5_data[column][()]

    return from_numpy_dataset(numpy_dataset)


def load_object(object_fp):
    with open_file(object_fp, "rb") as f:
        return pickle.load(f)


def save_object(object_fp, obj):
    with open_file(object_fp, "wb") as f:
        pickle.dump(obj, f)


def load_array(data_fp, dtype=float):
    list_num = []
    with open_file(data_fp, "r") as input_file:
        for x in input_file:
            list_num.append(dtype(x.strip()))
    return np.array(list_num)


def load_matrix(data_fp, dtype=float):
    list_num = []
    with open_file(data_fp, "r") as input_file:
        for row in input_file:
            list_num.append([dtype(elem) for elem in row.strip().split()])
    return np.squeeze(np.array(list_num))


def save_array(data_fp, array):
    with open_file(data_fp, "w") as output_file:
        for x in np.nditer(array):
            output_file.write(str(x) + "\n")


# TODO(shreya): Confirm types of args
def load_pretrained_embeddings(embeddings_path: str, vocab: List[str]) -> np.ndarray:
    """Create an embedding matrix of all words in vocab."""
    embeddings, embeddings_size = load_glove(embeddings_path, return_embedding_size=True)

    # calculate an average embedding, to use for initializing missing words
    avg_embedding = [embeddings[w] for w in vocab if w in embeddings]
    avg_embedding = sum(avg_embedding) / len(avg_embedding)

    # create the embedding matrix
    embeddings_vectors = []
    for word in vocab:
        if word in embeddings:
            embeddings_vectors.append(embeddings[word])
        else:
            embeddings_vectors.append(avg_embedding + np.random.uniform(-0.01, 0.01, embeddings_size))
    embeddings_matrix = np.stack(embeddings_vectors)

    # let's help the garbage collector free some memory
    embeddings = None

    return embeddings_matrix


@functools.lru_cache(1)
def load_glove(file_path: str, return_embedding_size: bool = False) -> Dict[str, np.ndarray]:
    """Loads Glove embeddings for each word.

    Returns:
        Mapping between word and numpy array of size embedding_size as set by
        first line of file.
    """
    logger.info(f"  Loading Glove format file {file_path}")
    embeddings = {}
    embedding_size = 0

    # collect embeddings size assuming the first line is correct
    with open_file(file_path, "r", encoding="utf-8") as f:
        found_line = False
        while not found_line:
            line = f.readline()
            if line:
                embedding_size = len(line.split()) - 1
                found_line = True

    # collect embeddings
    with open_file(file_path, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f):
            if line:
                try:
                    split = line.split()
                    if len(split) != embedding_size + 1:
                        raise ValueError(
                            f"Line {line_number} is of length {len(split)}, "
                            f"while expected length is {embedding_size + 1}."
                        )
                    word = split[0]
                    embedding = np.array([float(val) for val in split[-embedding_size:]])
                    embeddings[word] = embedding
                except ValueError:
                    logger.warning(
                        "Line {} in the GloVe file {} is malformed, " "skipping it".format(line_number, file_path)
                    )
    logger.info(f"  {len(embeddings)} embeddings loaded")

    if return_embedding_size:
        return embeddings, embedding_size
    return embeddings


def split_data(split: float, data: List) -> Tuple[List, List]:
    split_length = int(round(split * len(data)))
    random.shuffle(data)
    return data[:split_length], data[split_length:]


def shuffle_unison_inplace(list_of_lists, random_state=None):
    if list_of_lists:
        assert all(len(single_list) == len(list_of_lists[0]) for single_list in list_of_lists)
        if random_state is not None:
            p = random_state.permutation(len(list_of_lists[0]))
        else:
            p = np.random.permutation(len(list_of_lists[0]))
        return [single_list[p] for single_list in list_of_lists]
    return None


def shuffle_dict_unison_inplace(np_dict, random_state=None):
    keys = list(np_dict.keys())
    list_of_lists = list(np_dict.values())

    # shuffle up the list of lists according to previous fct
    shuffled_list = shuffle_unison_inplace(list_of_lists, random_state)

    recon = {}
    for ii in range(len(keys)):
        dkey = keys[ii]
        recon[dkey] = shuffled_list[ii]

    # we've shuffled the dictionary in place!
    return recon


def split_dataset_ttv(dataset, split):
    # Obtain distinct splits from the split column. If
    # a split is not present in this set, then we can skip generating
    # the dataframe for that split.
    distinct_values = dataset[split].drop_duplicates()
    if hasattr(distinct_values, "compute"):
        distinct_values = distinct_values.compute()
    distinct_values = set(distinct_values.values.tolist())

    training_set = split_dataset(dataset, split, 0) if 0 in distinct_values else None
    validation_set = split_dataset(dataset, split, 1) if 1 in distinct_values else None
    test_set = split_dataset(dataset, split, 2) if 2 in distinct_values else None
    return training_set, test_set, validation_set


def split_dataset(dataset, split, value_to_split=0):
    split_df = dataset[dataset[split] == value_to_split]
    return split_df.reset_index()


def collapse_rare_labels(labels, labels_limit):
    if labels_limit > 0:
        labels[labels >= labels_limit] = labels_limit
    return labels


def class_counts(dataset, labels_field):
    return np.bincount(dataset[labels_field].flatten()).tolist()


def text_feature_data_field(text_feature):
    return text_feature[PROC_COLUMN] + "_" + text_feature["level"]


def load_from_file(file_name, field=None, dtype=int, ground_truth_split=2):
    """Load experiment data from supported file formats.

    Experiment data can be test/train statistics, model predictions,
    probability, ground truth,  ground truth metadata.
    :param file_name: Path to file to be loaded
    :param field: Target Prediction field.
    :param dtype:
    :param ground_truth_split: Ground truth split filter where 0 is train 1 is
    validation and 2 is test split. By default test split is used when loading
    ground truth from hdf5.
    :return: Experiment data as array
    """
    if file_name.endswith(".hdf5") and field is not None:
        dataset = pd.read_hdf(file_name, key=HDF5_COLUMNS_KEY)
        column = dataset[field]
        array = column[dataset[SPLIT] == ground_truth_split].values  # ground truth
    elif file_name.endswith(".npy"):
        array = np.load(file_name)
    elif file_name.endswith(".csv"):
        array = read_csv(file_name, header=None).values
    else:
        array = load_matrix(file_name, dtype)
    return array


def replace_file_extension(file_path, extension):
    """Return a file path for a file with same name but different format. a.csv, json -> a.json a.csv, hdf5 ->
    a.hdf5.

    :param file_path: original file path
    :param extension: file extension
    :return: file path with same name but different format
    """
    if file_path is None:
        return None
    extension = extension.strip()
    if extension.startswith("."):
        # Handle the case if the user calls with '.hdf5' instead of 'hdf5'
        extension = extension[1:]

    return os.path.splitext(file_path)[0] + "." + extension


def file_exists_with_diff_extension(file_path, extension):
    return file_path is None or os.path.isfile(replace_file_extension(file_path, extension))


def add_sequence_feature_column(df, col_name, seq_length):
    """Adds a new column to the dataframe computed from an existing column. Values in the new column are space-
    delimited strings composed of preceding values of the same column up to seq_length. For example values of the
    i-th row of the new column will be a space-delimited string of df[col_name][i-seq_length].

     :param df: input dataframe
    :param col_name: column name containing sequential data
    :param seq_length: length of an array of preceeding column values to use
    """
    if col_name not in df.columns.values:
        logger.error(f"{col_name} column does not exist")
        return

    new_col_name = col_name + "_feature"
    if new_col_name in df.columns.values:
        logger.warning(f"{new_col_name} column already exists, values will be overridden")

    new_data = [None] * seq_length
    old_data = np.array(df[col_name])

    for i in range(seq_length, len(df)):
        new_data.append(" ".join(str(j) for j in old_data[i - seq_length : i]))

    df[new_col_name] = new_data
    df[new_col_name] = df[new_col_name].fillna(method="backfill")


def override_in_memory_flag(input_features, override_value):
    num_overrides = 0
    for feature in input_features:
        if PREPROCESSING in feature:
            if "in_memory" in feature[PREPROCESSING]:
                feature[PREPROCESSING]["in_memory"] = override_value
                num_overrides += 1
    return num_overrides


def normalize_numpy(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return normalize_numpy(obj.tolist())
    elif isinstance(obj, list):
        return [normalize_numpy(v) for v in obj]
    else:
        return obj


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (set, tuple)):
            return list(obj)
        elif isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        else:
            return json.JSONEncoder.default(self, obj)


def generate_kfold_splits(data_df, num_folds, random_state):
    kf = KFold(n_splits=num_folds, shuffle=True, random_state=random_state)
    fold_num = 0
    for train_indices, test_indices in kf.split(data_df):
        fold_num += 1
        yield train_indices, test_indices, fold_num


def get_path_size(start_path, regex_accept=None, regex_reject=None):
    total_size = 0
    pattern_accept = re.compile(regex_accept) if regex_accept else None
    pattern_reject = re.compile(regex_reject) if regex_reject else None

    for dirpath, dirnames, filenames in os.walk(start_path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            if not os.path.islink(filepath):
                accepted = True
                if pattern_accept:
                    accepted = accepted and pattern_accept.match(filename)
                if pattern_reject:
                    accepted = accepted and not pattern_reject.match(filename)
                if accepted:
                    total_size += os.path.getsize(filepath)

    return total_size


def clear_data_cache():
    """Clears any cached data objects (e.g., embeddings)"""
    load_glove.cache_clear()


def figure_data_format_dataset(dataset):
    if isinstance(dataset, pd.DataFrame):
        return pd.DataFrame
    elif dd and isinstance(dataset, dd.core.DataFrame):
        return dd.core.DataFrame
    elif isinstance(dataset, dict):
        return dict
    elif isinstance(dataset, str):
        dataset = dataset.lower()
        if dataset.endswith(".csv"):
            return "csv"
        elif dataset.endswith(".tsv"):
            return "tsv"
        elif dataset.endswith(".json"):
            return "json"
        elif dataset.endswith(".jsonl"):
            return "jsonl"
        elif (
            dataset.endswith(".xls")
            or dataset.endswith(".xlsx")
            or dataset.endswith(".xlsm")
            or dataset.endswith(".xlsb")
            or dataset.endswith(".odf")
            or dataset.endswith(".ods")
            or dataset.endswith(".odt")
        ):
            return "excel"
        elif dataset.endswith(".parquet"):
            return "parquet"
        elif dataset.endswith(".pickle") or dataset.endswith(".p"):
            return "pickle"
        elif dataset.endswith(".feather"):
            return "feather"
        elif dataset.endswith(".fwf"):
            return "fwf"
        elif dataset.endswith(".html"):
            return "html"
        elif dataset.endswith(".orc"):
            return "orc"
        elif dataset.endswith(".sas"):
            return "sas"
        elif dataset.endswith(".spss"):
            return "spss"
        elif dataset.endswith(".dta") or dataset.endswith(".stata"):
            return "stata"
        elif dataset.endswith(".h5") or dataset.endswith(".hdf5"):
            return "hdf5"
        else:
            raise ValueError("Dataset path string {} " "does not contain a valid extension".format(dataset))
    else:
        raise ValueError(f"Cannot figure out the format of dataset {dataset}")


def figure_data_format(dataset=None, training_set=None, validation_set=None, test_set=None):
    if dataset is not None:
        data_format = figure_data_format_dataset(dataset)
    elif training_set is not None:
        data_formats = [figure_data_format_dataset(training_set)]
        if validation_set is not None:
            data_formats.append(figure_data_format_dataset(validation_set))
        if test_set is not None:
            data_formats.append(figure_data_format_dataset(test_set))
        data_formats_set = set(data_formats)
        if len(data_formats_set) > 1:
            error_message = "Datasets have different formats. Training: "
            error_message += str(data_formats[0])
            if validation_set:
                error_message = ", Validation: "
                error_message += str(data_formats[1])
            if test_set:
                error_message = ", Test: "
                error_message += str(data_formats[-1])
            raise ValueError(error_message)
        else:
            data_format = next(iter(data_formats_set))
    else:
        raise ValueError("At least one between dataset and training_set must be not None")
    return data_format


def is_model_dir(path: str) -> bool:
    hyperparameters_fn = os.path.join(path, MODEL_HYPERPARAMETERS_FILE_NAME)
    ts_metadata_fn = os.path.join(path, TRAIN_SET_METADATA_FILE_NAME)
    is_model_dir = False
    if os.path.isdir(path) and os.path.isfile(hyperparameters_fn) and os.path.isfile(ts_metadata_fn):
        weights_files_count = 0
        for file_name in os.listdir(path):
            if file_name.startswith(MODEL_WEIGHTS_FILE_NAME):
                weights_files_count += 1
        if weights_files_count >= 2:
            is_model_dir = True
    return is_model_dir


def ndarray2string(parm_array):
    # convert numpy.ndarray to ludwig custom string format
    if isinstance(parm_array, np.ndarray):
        return "__ndarray__" + json.dumps(parm_array.tolist())
    else:
        raise ValueError("Argument must be numpy.ndarray.  Instead argument found to be " "{}".format(type(parm_array)))


def string2ndarray(parm_string):
    # convert ludwig custom ndarray string to numpy.ndarray
    if isinstance(parm_string, str) and parm_string[:11] == "__ndarray__":
        return np.array(json.loads(parm_string[11:]))
    else:
        raise ValueError("Argument must be Ludwig custom string format for numpy.ndarray")


def is_ludwig_ndarray_string(parm_string):
    # tests if parameter is a Ludwig custom ndarray string
    return isinstance(parm_string, str) and parm_string[:11] == "__ndarray__"


external_data_reader_registry = {
    **{fmt: read_csv for fmt in CSV_FORMATS},
    **{fmt: read_tsv for fmt in TSV_FORMATS},
    **{fmt: read_json for fmt in JSON_FORMATS},
    **{fmt: read_jsonl for fmt in JSONL_FORMATS},
    **{fmt: read_excel for fmt in EXCEL_FORMATS},
    **{fmt: read_parquet for fmt in PARQUET_FORMATS},
    **{fmt: read_pickle for fmt in PICKLE_FORMATS},
    **{fmt: read_fwf for fmt in FWF_FORMATS},
    **{fmt: read_feather for fmt in FEATHER_FORMATS},
    **{fmt: read_html for fmt in HTML_FORMATS},
    **{fmt: read_orc for fmt in ORC_FORMATS},
    **{fmt: read_sas for fmt in SAS_FORMATS},
    **{fmt: read_spss for fmt in SPSS_FORMATS},
    **{fmt: read_stata for fmt in STATA_FORMATS},
    **{fmt: read_hdf5 for fmt in HDF5_FORMATS},
}


def load_dataset(dataset, data_format=None, df_lib=PANDAS_DF):
    if not data_format or data_format == "auto":
        data_format = figure_data_format(dataset)

    # use appropriate reader to create dataframe
    if data_format in DATAFRAME_FORMATS:
        return dataset
    elif data_format in DICT_FORMATS:
        return pd.DataFrame(dataset)
    elif data_format in CACHEABLE_FORMATS:
        data_reader = get_from_registry(data_format, external_data_reader_registry)
        return data_reader(dataset, df_lib)
    else:
        ValueError(f"{data_format} format is not supported")
