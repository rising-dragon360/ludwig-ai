#!/usr/bin/env python
# coding: utf-8

# # Multiple Model Training Example
# 
# This example trains multiple models and extracts training statistics

# ## Import required libraries
from ludwig.api import LudwigModel
from ludwig.visualize import learning_curves
import logging
import shutil
import yaml
from collections import namedtuple

# clean out old results
try:
    shutil.rmtree('./results')
except:
    pass

try:
    shutil.rmtree('./visualizations')
except:
    pass

# read in base model definition
with open('./model_definition.yaml', 'r') as f:
    base_model = yaml.safe_load(f.read())

# Specify named tuple to keep track of training results
TrainingResult = namedtuple('TrainingResult', ['name', 'train_stats'])

# specify alternative architectures to test
FullyConnectedLayers = namedtuple('FullyConnectedLayers',['name', 'fc_layers'])

list_of_fc_layers = [
    FullyConnectedLayers(name='Option1', fc_layers=[{'fc_size':64, 'dropout': 'true'}]),

    FullyConnectedLayers(name='Option2', fc_layers=[{'fc_size':128, 'dropout':'true'},
                                                    {'fc_size':64, 'dropout': 'true'}]),

    FullyConnectedLayers(name='Option3', fc_layers=[{'fc_size':128, 'dropout':'true'}])
]

#
list_of_train_stats = []

# ## Train models
for model_option in list_of_fc_layers:
    print('>>>> training: ', model_option.name)

    # set up Python dictionary to hold model training parameters
    model_definition = base_model.copy()
    model_definition['input_features'][0]['fc_layers'] = model_option.fc_layers
    model_definition['training']['epochs'] = 8

    # Define Ludwig model object that drive model training
    model = LudwigModel(model_definition,
                        logging_level=logging.WARN)

    # initiate model training
    train_stats = model.train(data_csv='./data/mnist_dataset_training.csv',
                             experiment_name='multiple_experiment',
                             model_name=model_option.name)

    # save training stats for later use
    list_of_train_stats.append(TrainingResult(name=model_option.name, train_stats=train_stats))

    print('>>>>>>> completed: ', model_option.name, '\n')

    model.close()

# generating learning curves from training
option_names = [trs.name for trs in list_of_train_stats]
train_stats = [trs.train_stats for trs in list_of_train_stats]
learning_curves(train_stats, 'Survived',
                model_names=option_names,
                output_directory='./visualizations',
                file_format='png')



