# Kaggle Titanic Survivor Prediction

This API example is based on [Ludwig's Kaggle Titanic example](https://ludwig-ai.github.io/ludwig-docs/examples/#kaggles-titanic-predicting-survivors) for predicting probability of surviving. 

### Preparatory Steps
* Create `data` directory
* Download [Kaggle competition dataset](https://www.kaggle.com/c/titanic/data) into the `data` directory.  Directory should
appear as follows:
```
titanic/
    data/
        train.csv
        test.csv
```

### Examples
|File|Description|
|----|-----------|
|simple_model_training.py|Demonstrates using Ludwig api for training a model.|
|multiple_model_training.py|Trains two models and generates a visualization for results of training.|
|model_training_results.ipynb|Example for extracting training statistics and generate custom visualizations.|

Enter `python simple_model_training.py` will train a single model.  Results of model training will be stored in this location.
```
./results/
    simple_experiment_simple_model/
```

Enter `python multiple_model_training.py` will train two models and generate standard Ludwig visualizations comparing the 
two models.  Results will in the following directories:
``` 
./results/
    multiple_model_model1/
    multiple_model_model2/
./visualizations/
    learning_curves_Survived_accuracy.png
    learning_curves_Survived_loss.png
```
 
 This is the standard Ludwig learning curve plot from training the two models
 ![](../images/learning_curves_Survived_accuracy.png)
 
 This is the custom visualization created by the Jupyter notebook `model_training_results.ipynb`. 
 ![](../images/custom_learning_curve.png)
