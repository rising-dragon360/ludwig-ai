FROM tensorflow/tensorflow:latest-py3

RUN apt-get install -y --no-install-recommends \
        git libgmp3-dev
RUN git clone --depth=1 https://github.com/uber/ludwig.git \
    && cd ludwig/ \
    && pip install -r requirements.txt -r requirements_text.txt \
    && python -m spacy download en \
    && python setup.py install

WORKDIR /data

ENTRYPOINT ["ludwig"]
