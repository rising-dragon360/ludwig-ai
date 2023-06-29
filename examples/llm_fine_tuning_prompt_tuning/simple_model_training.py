#!/usr/bin/env python

# # Simple Model Training Example
#
# This is a simple example of how to use the LLM model type with fine-tuning
# using prompt tuning. It uses the facebook/opt-1.3b model as the base LLM model.

# Import required libraries
import logging
import shutil

import pandas as pd
import yaml

from ludwig.api import LudwigModel

# clean out prior results
shutil.rmtree("./results", ignore_errors=True)

review_label_pairs = [
    {"review": "I loved this movie!", "label": "positive"},
    {"review": "The food was okay, but the service was terrible.", "label": "negative"},
    {"review": "I can't believe how rude the staff was.", "label": "negative"},
    {"review": "This book was a real page-turner.", "label": "positive"},
    {"review": "The hotel room was dirty and smelled bad.", "label": "negative"},
    {"review": "I had a great experience at this restaurant.", "label": "positive"},
    {"review": "The concert was amazing!", "label": "positive"},
    {"review": "The traffic was terrible on my way to work this morning.", "label": "negative"},
    {"review": "The customer service was excellent.", "label": "positive"},
    {"review": "I was disappointed with the quality of the product.", "label": "negative"},
    {"review": "The scenery on the hike was breathtaking.", "label": "positive"},
    {"review": "I had a terrible experience at this hotel.", "label": "negative"},
    {"review": "The coffee at this cafe was delicious.", "label": "positive"},
    {"review": "The weather was perfect for a day at the beach.", "label": "positive"},
    {"review": "I would definitely recommend this product.", "label": "positive"},
    {"review": "The wait time at the doctor's office was ridiculous.", "label": "negative"},
    {"review": "The museum was a bit underwhelming.", "label": "neutral"},
    {"review": "I had a fantastic time at the amusement park.", "label": "positive"},
    {"review": "The staff at this store was extremely helpful.", "label": "positive"},
    {"review": "The airline lost my luggage and was very unhelpful.", "label": "negative"},
    {"review": "This album is a must-listen for any music fan.", "label": "positive"},
    {"review": "The food at this restaurant was just okay.", "label": "neutral"},
    {"review": "I was pleasantly surprised by how great this movie was.", "label": "positive"},
    {"review": "The car rental process was quick and easy.", "label": "positive"},
    {"review": "The service at this hotel was top-notch.", "label": "positive"},
]

df = pd.DataFrame(review_label_pairs)

config = yaml.safe_load(
    """
        input_features:
            - name: review
              type: text
        output_features:
            - name: label
              type: category
              decoder:
                type: classifier
        model_type: llm
        base_model: facebook/opt-1.3b
        adapter:
            type: prompt_tuning
            num_virtual_tokens: 16
            prompt_tuning_init_text: "Classify the review sentiment as one positive, negative, neutral: "
        trainer:
            type: finetune
            batch_size: 2
            epochs: 10
    """
)

# Define Ludwig model object that drive model training
model = LudwigModel(config=config, logging_level=logging.INFO)

# initiate model training
(
    train_stats,  # dictionary containing training statistics
    preprocessed_data,  # tuple Ludwig Dataset objects of pre-processed training data
    output_directory,  # location of training results stored on disk
) = model.train(
    dataset=df, experiment_name="simple_experiment", model_name="simple_model", skip_save_processed_input=True
)

training_set, val_set, test_set, _ = preprocessed_data

# batch prediction
preds, _ = model.predict(test_set, skip_save_predictions=False)
print(preds)
