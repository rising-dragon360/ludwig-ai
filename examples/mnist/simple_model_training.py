#!/usr/bin/env python

# # Simple Model Training Example
#
# This example is the API example for this Ludwig command line example
# (https://ludwig-ai.github.io/ludwig-docs/latest/examples/mnist/).
import logging
import shutil

import yaml

from ludwig.api import LudwigModel
from ludwig.datasets import mnist

# clean out prior results
shutil.rmtree("./results", ignore_errors=True)

# set up Python dictionary to hold model training parameters
with open("./config.yaml") as f:
    config = yaml.safe_load(f.read())

# Define Ludwig model object that drive model training
model = LudwigModel(config, logging_level=logging.INFO)

# load and split MNIST dataset
training_set, test_set, _ = mnist.load(split=True)

# initiate model training
(train_stats, _, output_directory) = model.train(  # training statistics  # location for training results saved to disk
    training_set=training_set,
    test_set=test_set,
    experiment_name="simple_image_experiment",
    model_name="single_model",
    skip_save_processed_input=True,
)
