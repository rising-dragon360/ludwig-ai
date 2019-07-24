## I'm experiencing problems installing `gmpy`, how do I solve them?

If you encounter problems installing `gmpy` please install `libgmp` or `gmp`.
On Debian based Linux distributions: `sudo apt-get install libgmp3-dev`.
On MacOS : `brew install gmp`.

## Do you support \[feature | encoder | decoder\] in Ludwig?

The list of encoders for each feature type is specified in the [User Guide](user_guide.md).
We plan to add additional feature types and additional encoders and decoders for all feature types.
Refer to [this question](#what-additional-features-are-you-working-on) for more details.
If you want to help us implementing your favourite feature or model please take a look at the [Developer Guide](developer_guide.md) to see how to contribute.


## Do all datasets need to be loaded in memory?

At the moment it depends on the type of feature: image features can be dynamically loaded from disk from an opened hdf5 file, while other types of features (that usually take need less memory than image ones) are loaded entirely in memory for speed. We plan to add an option to load also other features from disk in future releases and to also support more input file types and more scalable solutions like [Petastorm](https://github.com/uber/Petastorm).


## My data is on \[ GCS | S3 | Azure \], how can I load it?

Ludwig uses Pandas for loading data at the moment (this may change when we move to Petastorm).
This means that if your service provides a mechanism for loading data with a name handler, you can load it.

These name handlers already work:
- [Google Cloud Storage](https://cloud.google.com/storage/): `gs://`. You just have to install `gcsfs` with `pip install gcsfs>=0.2.1` and you will be able to prive paths to Ludwig with the `gs://` name handler.
- [Amazon S3](https://aws.amazon.com/s3/): `s3://`. You just have to install `boto` with `pip install boto` and you will be able to prive paths to Ludwig with the `s3://` name handler.


## What additional features are you working on?

We will prioritize new features depending on the feedback of the community, but we are already planning to add:

- additional text and sequence encoders (attention, co-attention, hierarchical attention, [Transformer](https://arxiv.org/abs/1706.03762), [ELMo](https://arxiv.org/abs/1802.05365) and [BERT](https://arxiv.org/abs/1810.04805)).
- additional image encoders ([DenseNet](https://arxiv.org/abs/1608.06993) and [FractalNet](https://arxiv.org/abs/1605.07648)).
- image decoding (both image generation by deconvolution and pixel-wise classification for image segmentation).
- time series decoding.
- additional features types (vectors, point clouds, nested lists, multi-sentence documents, graphs).
- additional measures and losses.
- additional data formatters and dataset-specific preprocessing scripts.

We also want to address some of the current limitations:

- currently the full dataset needs to be loaded in memory in order to train a model. Image features already have a way to dynamically read batches of datapoints from disk, and we want to extend this capability to other datatypes.
- add a command to start a rest service maybe with a simple user interface in order to provide a live demo capability.
- document lower level functions.
- optimize the data I/O to TensorFlow.
- increase the number of supported data formats beyond just CSV and integrating with [Petastorm](https://github.com/uber/Petastorm).

All these are opportunities to get involved in the community and contribute.
Feel free to reach out to us and ask as there are tasks for all levels of experience.


## Who are the authors of Ludwig?

- [Piero Molino](http://w4nderlu.st) is the main architect and maintainer
- Yaroslav Dudin is a key contributor
- Sai Sumanth Miryala contributed all the testing, logging and helped polishing.


## Who else helped developing Ludwig?

- Yi Shi who implemented the time series encoding
- Ankit Jain who implemented the bag feature encoding
- Pranav Subramani who contributed documentation
- Alex Sergeev and Felipe Petroski Such who helped with distributed training
- Emidio Torre helped with the initial design of the landing page
