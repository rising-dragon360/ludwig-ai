This section contains several examples of how to build models with Ludwig for a variety of tasks.
For each task we show an example dataset and a sample model definition that can be used to train a model from that data.


Text Classification
===

| text                                                                                             | class       |
|--------------------------------------------------------------------------------------------------|-------------|
| Toronto  Feb 26 - Standard Trustco said it expects earnings in 1987 to increase at least 15..   | earnings    |
| New York  Feb 26 - American Express Co remained silent on market rumors..                       | acquisition |
| BANGKOK  March 25 - Vietnam will resettle 300000 people on state farms known as new economic.. | coffee      |

```
ludwig experiment \
  --data_csv reuters-allcats.csv \
  --model_definition_file model_definition.yaml
```

With `model_definition.yaml`:

```yaml
input_features:
    -
        name: text
        type: text
        encoder: parallel_cnn
        level: word

output_features:
    -
        name: class
        type: category
```

Named Entity Recognition Tagging
===

| utterance                                         | tag                                            |
|---------------------------------------------------|------------------------------------------------|
| John Smith was born in New York on July 21st 1982 | Person Person O O O City City O Date Date Date |
| Jane Smith was born in Boston on May 1st 1973     | Person Person O O O City City O Date Date Date |
| My friend Carlos was born in San Jose             | O O Person O O O City City                     |

```
ludwig experiment \
  --data_csv sequence_tags.csv \
  --model_definition_file model_definition.yaml
```

With `model_definition.yaml`:

```yaml
input_features:
    -
        name: utterance
        type: sequence
        encoder: rnn
        cell_type: lstm
        reduce_output: null

output_features:
    -
        name: tag
        type: sequence
        decoder: tagger
```


Natural Language Understanding
===

| utterance                      | intent      | slots                             |
|--------------------------------|-------------|-----------------------------------|
| I want a pizza                 | order_food  | O O O B-Food_type                 |
| Book a flight to Boston        | book_flight | O O O O B-City                    |
| Book a flight at 7pm to London | book_flight | O O O O B-Departure_time O B-City |

```
ludwig experiment \
  --data_csv nlu.csv \
  --model_definition_file model_definition.yaml
```

With `model_definition.yaml`:

```yaml
input_features:
    -
        name: utterance
        type: sequence
        encoder: rnn
        cell_type: lstm
        bidirectional: true
        num_layers: 2
        reduce_output: null

output_features:
    -
        name: intent
        type: category
        reduce_input: sum
        num_fc_layers: 1
        fc_size: 64
    -
        name: slots
        type: sequence
        decoder: tagger
```


Machine Translation
===

| english                   | italian                   |
|---------------------------|---------------------------|
| Hello! How are you doing? | Ciao, come stai?          |
| I got promoted today      | Oggi sono stato promosso! |
| Not doing well today      | Oggi non mi sento bene    |

```
ludwig experiment \
  --data_csv translation.csv \
  --model_definition_file model_definition.yaml
```

With `model_definition.yaml`:

```yaml
input_features:
    -
        name: english
        type: text
        encoder: rnn
        cell_type: lstm
        reduce_output: null

output_features:
    -
        name: italian
        type: text
        decoder: generator
        cell_type: lstm
        attention: bahdanau
        loss:
            type: sampled_softmax_cross_entropy

training:
    batch_size: 96
```


Chit-Chat Dialogue Modeling through Sequence2Sequence
===

| user1                     | user2                                      |
|---------------------------|--------------------------------------------|
| Hello! How are you doing? | Doing well, thanks!                        |
| I got promoted today      | Congratulations!                           |
| Not doing well today      | I’m sorry, can I do something to help you? |

```
ludwig experiment \
  --data_csv chitchat.csv \
  --model_definition_file model_definition.yaml
```

With `model_definition.yaml`:

```yaml
input_features:
    -
        name: user1
        type: text
        encoder: rnn
        cell_type: lstm
        reduce_output: null

output_features:
    -
        name: user2
        type: text
        decoder: generator
        cell_type: lstm
        attention: bahdanau
        loss:
            type: sampled_softmax_cross_entropy

training:
    batch_size: 96
```


Sentiment Analysis
===

| review                          | sentiment |
|---------------------------------|-----------|
| The movie was fantastic!        | positive  |
| Great acting and cinematography | positive  |
| The acting was terrible!        | negative  |

```
ludwig experiment \
  --data_csv reuters-allcats.csv \
  --model_definition_file model_definition.yaml
```

With `model_definition.yaml`:

```yaml
input_features:
    -
        name: review
        type: text
        encoder: parallel_cnn
        level: word

output_features:
    -
        name: sentiment
        type: category
```


Image Classification
===

| image_path                | class |
|---------------------------|-------|
| imagenet/image_000001.jpg | car   |
| imagenet/image_000002.jpg | dog   |
| imagenet/image_000003.jpg | boat  |

```
ludwig experiment \
  --data_csv reuters-allcats.csv \
  --model_definition_file model_definition.yaml
```

With `model_definition.yaml`:

```yaml
input_features:
    -
        name: image_path
        type: image
        encoder: stacked_cnn

output_features:
    -
        name: class
        type: category
```

Image Classification (MNIST)
===
This is a complete example of training an image classification model on the MNIST 
dataset.

## Download the MNIST dataset.
```
git clone https://github.com/myleott/mnist_png.git
cd mnist_png/
tar -xf mnist_png.tar.gz
cd mnist_png/
```

## Create train and test CSVs.
Open python shell in the same directory and run this:
```
import os
for name in ['training', 'testing']:
    with open('mnist_dataset_{}.csv'.format(name), 'w') as output_file:
        print('=== creating {} dataset ==='.format(name))
        output_file.write('image_path,label\n')
        for i in range(10):
            path = '{}/{}'.format(name, i)
            for file in os.listdir(path):
                if file.endswith(".png"):
                    output_file.write('{},{}\n'.format(os.path.join(path, file), str(i)))

```
Now you should have `mnist_dataset_training.csv` and `mnist_dataset_testing.csv`
containing 60000 and 10000 examples correspondingly and having the following format

| image_path           | label |
|----------------------|-------|
| training/0/16585.png |  0    |
| training/0/24537.png |  0    |
| training/0/25629.png |  0    |

## Train a model.

From the directory where you have virtual environment with ludwig installed:
```
ludwig train \
  --data_train_csv <full_path_to_mnist_dataset_training_csv> \
  --data_test_csv <full path to mnist_dataset_test.csv> \
  --model_definition_file model_definition.yaml
```

With `model_definition.yaml`:

```yaml
input_features:
    -
        name: image_path
        type: image
        encoder: stacked_cnn
        conv_layers:
            -
                num_filters: 32
                filter_size: 3
                pool_size: 2
                pool_stride: 2
            -
                num_filters: 64
                filter_size: 3
                pool_size: 2
                pool_stride: 2
                dropout: true
        fc_layers:
            -
                fc_size: 128
                dropout: true

output_features:
    -
        name: label
        type: category

training:
    dropout_rate: 0.4
```

Image Captioning
===

| image_path                | caption                   |
|---------------------------|---------------------------|
| imagenet/image_000001.jpg | car driving on the street |
| imagenet/image_000002.jpg | dog barking at a cat      |
| imagenet/image_000003.jpg | boat sailing in the ocean |

```
ludwig experiment \
--data_csv reuters-allcats.csv \
  --model_definition_file model_definition.yaml
```

With `model_definition.yaml`:

```yaml
input_features:
    -
        name: image_path
        type: image
        encoder: stacked_cnn

output_features:
    -
        name: caption
        type: text
        decoder: generator
        cell_type: lstm
```


One-shot Learning with Siamese Networks
===

This example can be considered a simple baseline for one-shot learning on the [Omniglot](https://github.com/brendenlake/omniglot) dataset. The task is, given two images of two handwritten characters, recognize if they are two instances of the same character or not.

| image_path_1                     |   image_path_2                   | similarity |
|----------------------------------|----------------------------------|------------|
| balinese/character01/0108_13.png | balinese/character01/0108_18.png | 1          |
| balinese/character01/0108_13.png | balinese/character08/0115_12.png | 0          |
| balinese/character01/0108_04.png | balinese/character01/0108_08.png | 1          |
| balinese/character01/0108_11.png | balinese/character05/0112_02.png | 0          |

```
ludwig experiment \
--data_csv balinese_characters.csv \
  --model_definition_file model_definition.yaml
```

With `model_definition.yaml`:

```yaml
input_features:
    -
        name: image_path_1
        type: image
        encoder: stacked_cnn
        resize_image: true
        width: 28
        height: 28
    -
        name: image_path_2
        type: image
        encoder: stacked_cnn
        resize_image: true
        width: 28
        height: 28
        tied_weights: image_path_1

combiner:
    type: concat
    num_fc_layers: 2
    fc_size: 256

output_features:
    -
        name: similarity
        type: binary
```

Visual Question Answering
===

| image_path              |   question                                | answer |
|-------------------------|-------------------------------------------|--------|
| imdata/image_000001.jpg | Is there snow on the mountains?           | yes    |
| imdata/image_000002.jpg | What color are the wheels                 | blue   |
| imdata/image_000003.jpg | What kind of utensil is in the glass bowl | knife  |


```yaml
input_features:
    -
        name: image_path
        type: image
        encoder: stacked_cnn
    -
        name: question
        type: text
        encoder: parallel_cnn
        level: word

output_features:
    -
        name: answer
        type: text
        decoder: generator
        cell_type: lstm
        loss:
            type: sampled_softmax_cross_entropy
```



Kaggle's Titanic: Predicting survivors
===

This example describes how to use Ludwig to train a model for the 
[kaggle competition](https://www.kaggle.com/c/titanic/), on predicting a passenger's probability of surviving the Titanic
disaster. Here's a sample of the data:

| Pclass | Sex    | Age | SibSp | Parch | Fare    | Survived | Embarked |
|--------|--------|-----|-------|-------|---------|----------|----------|
| 3      | male   | 22  | 1     | 0     |  7.2500 | 0        | S        |
| 1      | female | 38  | 1     | 0     | 71.2833 | 1        | C        |
| 3      | female | 26  | 0     | 0     |  7.9250 | 0        | S        |
| 3      | male   | 35  | 0     | 0     |  8.0500 | 0        | S        |

The full data and the column descriptions can be found [here](https://www.kaggle.com/c/titanic/data). 

After downloading the data, to train a model on this dataset using Ludwig,
```
ludwig experiment \
  --data_csv PATH_TO_TITANIC_TRAIN.CSV \
  --model_definition_file model_definition.yaml
```

With `model_definition.yaml`:

```yaml
input_features:
    -
        name: Pclass
        type: category
    -
        name: Sex
        type: category
    -
        name: Age
        type: numerical
        missing_value_strategy: fill_with_mean
    -
        name: SibSp
        type: numerical
    -
        name: Parch
        type: numerical
    -
        name: Fare
        type: numerical
        missing_value_strategy: fill_with_mean
    -
        name: Embarked
        type: category

output_features:
    -
        name: Survived
        type: binary
```

Better results can be obtained with morerefined feature transformations and preprocessing, but this example has the only aim to show how this type do tasks and data can be used in Ludwig.


Time series forecasting
===

While direct timeseries prediction is a work in progress Ludwig can ingest timeseries input feature data and make numerical predictions. Below is an example of a model trained to forecast timeseries at five different horizons.

| timeseries_data       |   y1  |   y2  |   y3  |   y4  |   y5  |
|-----------------------|-------|-------|-------|-------|-------|
| 15.07 14.89 14.45 ... | 16.92 | 16.67 | 16.48 | 17.00 | 17.02 |
| 14.89 14.45 14.30 ... | 16.67 | 16.48 | 17.00 | 17.02 | 16.48 |
| 14.45 14.3 14.94 ...  | 16.48 | 17.00 | 17.02 | 16.48 | 15.82 |

```
ludwig experiment \
--data_csv timeseries_data.csv \
  --model_definition_file model_definition.yaml
```

With `model_definition.yaml`:

```yaml
input_features:
    -
        name: timeseries_data
        type: timeseries

output_features:
    -
        name: y1
        type: numerical
    -
        name: y2
        type: numerical
    -
        name: y3
        type: numerical
    -
        name: y4
        type: numerical
    -
        name: y5
        type: numerical
```


Movie rating prediction
===

| year | duration  | nominations |  categories        | rating |
|------|-----------|-------------|--------------------|--------|
| 1921 |   3240    |     0       | comedy drama       |  8.4   |
| 1925 |   5700    |     1       | adventure comedy   |  8.3   |
| 1927 |   9180    |     4       | drama comedy scifi |  8.4   |

```yaml
input_features:
    -
        name: year
        type: numerical
    -
        name: duration
        type: numerical
    -
        name: nominations
        type: numerical
    -
        name: categories
        type: set
        
output_features:
    -
        name: rating
        type: numerical
```


Multi-label classification
===

| image_path                | tags          |
|---------------------------|---------------|
| imagenet/image_000001.jpg | car man       |
| imagenet/image_000002.jpg | happy dog tie |
| imagenet/image_000003.jpg | boat water    |

```yaml
input_features:
    -
        name: image_path
        type: image
        encoder: stacked_cnn

output_features:
    -
        name: tags
        type: set
```


Multi-Task Learning
===

This example is inspired by the classic paper [Natural Language Processing (Almost) from Scratch](https://arxiv.org/abs/1103.0398) by Collobert et al..

| sentence                    | chunks                       | part_of_speech    | named_entities      |
|-----------------------------|------------------------------|-------------------|---------------------|
| San Francisco is very foggy | B-NP I-NP B-VP B-ADJP I-ADJP | NNP NNP VBZ RB JJ | B-Loc I-Loc O O O   |
| My dog likes eating sausage | B-NP I-NP B-VP B-VP B-NP     | PRP NN VBZ VBG NN | O O O O O           |
| Brutus Killed Julius Caesar | B-NP B-VP B-NP I-NP          | NNP VBD NNP NNP   | B-Per O B-Per I-Per |

```yaml
input_features:
    -
        name: sentence
        type: sequence
        encoder: rnn
        cell: lstm
        bidirectional: true
        reduce_output: null

output_features:
    -
        name: chunks
        type: sequence
        decoder: tagger
    -
        name: part_of_speech
        type: sequence
        decoder: tagger
    -
        name: named_entities
        type: sequence
        decoder: tagger
```
