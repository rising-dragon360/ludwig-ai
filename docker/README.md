# Ludwig Docker Images

These images provide Ludwig, a toolbox that allows to train and evaluate deep
learning models without the need to write code. Ludwig Docker image with full
set of pre-requiste packages to support these capabilities

* text features
* image features
* audio features
* visualizations
* hyperparameter optimization
* distributed training
* model serving

## Repositories

These three repositories contain a version of Ludwig with full features built
from the project's `master` branch.

* `ludwigai/ludwig` Ludwig packaged with TF 2.x
* `ludwigai/ludwig-gpu` Ludwig packaged with gpu-enabled version of TF 2.x
* `ludwigai/ludwig-ray` Ludwig packaged with TF2.x
  and [nightly build of ray-project/ray](https://github.com/ray-project/ray)

## Image Tags

* `master` - built from Ludwig's `master` branch
* `nightly` - nightly build of Ludwig's software.
* `sha-<commit point>` - version of Ludwig software at designated git sha1
  7-character commit point.

## Running Containers

Following are examples using the `ludwigai/ludwig:master` image to run
the `ludwig cli` command or running a Python program using the Ludwig api.

For purposes of the examples assume this host directory structure

``` 
/top/level/directory/path/
    data/
        train.csv
    src/
        config.yaml
        ludwig_api_program.py
```

### Run Ludwig CLI

``` 
# set shell variable to parent directory
parent_path=/top/level/directory/path

# invoke docker run command to execute the ludwig cli
# map host directory ${parent_path}/data to container /data directory
# map host directory ${parent_path}/src to container /src directory
docker run -v ${parent_path}/data:/data  \
    -v ${parent_path}/src:/src \
    ludwigai/ludwig:master \
    experiment --config /src/config.yaml \
        --dataset /data/train.csv \
        --output_directory /src/results
```

Experiment results can be found in host
directory `/top/level/directory/path/src/results`

### Run Python program using Ludwig APIs

```
# set shell variable to parent directory
parent_path=/top/level/directory/path

# invoke docker run command to execute Python interpreter
# map host directory ${parent_path}/data to container /data directory
# map host directory ${parent_path}/src to container /src directory
# set current working directory to container /src directory
# change default entrypoint from ludwig to python
docker run  -v ${parent_path}/data:/data  \
    -v ${parent_path}/src:/src \
    -w /src \
    --entrypoint python \
    ludwigai/ludwig:master /src/ludwig_api_program.py
```

Ludwig results can be found in host
directory `/top/level/directory/path/src/results`