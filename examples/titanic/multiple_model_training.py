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


# clean out old results
try:
    shutil.rmtree('./results')
except:
    pass

try:
    shutil.rmtree('./visualizations')
except:
    pass

# list models to train
list_of_model_ids = ['model1', 'model2']
list_of_train_stats = []


# ## Train models
for model_id in list_of_model_ids:
    print('>>>> training: ', model_id)

    # set up Python dictionary to hold model training parameters
    model_definition = {...}

    # Define Ludwig model object that drive model training
    model = LudwigModel(model_definition,
                        model_definition_file='./' + model_id + '_definition.yaml',
                        logging_level=logging.WARN)

    # initiate model training
    train_stats = model.train(data_csv='./data/train.csv',
                             experiment_name='multiple_experiment',
                             model_name=model_id)

    # save training stats for later user
    list_of_train_stats.append(train_stats)

    print('>>>>>>> completed: ', model_id,'\n')

    model.close()

# generating learning curves from traiing
learning_curves(list_of_train_stats, 'Survived',
                output_directory='./visualizations',
                file_format='png')



