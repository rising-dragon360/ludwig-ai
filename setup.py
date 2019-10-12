# coding=utf-8
'''Ludwig: a deep learning experimentation toolbox
'''
from codecs import open
from os import path

from setuptools import setup, find_packages

here = path.abspath(path.dirname(__file__))

# Get the long description from the README.md file
with open(path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

with open(path.join(here, 'requirements.txt'), encoding='utf-8') as f:
    requirements = [line.strip() for line in f if line]

extra_requirements = {}

with open(path.join(here, 'requirements_audio.txt'), encoding='utf-8') as f:
    extra_requirements['audio'] = [line.strip() for line in f if line]

with open(path.join(here, 'requirements_image.txt'), encoding='utf-8') as f:
    extra_requirements['image'] = [line.strip() for line in f if line]

with open(path.join(here, 'requirements_serve.txt'), encoding='utf-8') as f:
    extra_requirements['serve'] = [line.strip() for line in f if line]

with open(path.join(here, 'requirements_text.txt'), encoding='utf-8') as f:
    extra_requirements['text'] = [line.strip() for line in f if line]

with open(path.join(here, 'requirements_viz.txt'), encoding='utf-8') as f:
    extra_requirements['viz'] = [line.strip() for line in f if line]

extra_requirements['full'] = [item for sublist in extra_requirements.values()
                              for item in sublist]

with open(path.join(here, 'requirements_test.txt'), encoding='utf-8') as f:
    extra_requirements['test'] = (
            extra_requirements['full'] + [line.strip() for line in f if line]
    )

tensorflow_gpu = 'tensorflow-gpu'
for req in requirements:
    if req.startswith('tensorflow'):
        tensorflow_gpu = req.replace('tensorflow', 'tensorflow-gpu')
extra_requirements['gpu'] = [tensorflow_gpu]

setup(
    name='ludwig',

    version='0.2.1',

    description='A deep learning experimentation toolbox',
    long_description=long_description,
    long_description_content_type='text/markdown',

    url='https://ludwig.ai',

    author='Piero Molino',
    author_email='piero.molino@gmail.com',

    license='Apache 2.0',

    keywords='ludwig deep learning deep_learning machine machine_learning natural language processing computer vision',

    packages=find_packages(exclude=['contrib', 'docs', 'tests']),

    python_requires='>=3',

    include_package_data=True,
    package_data={'ludwig': ['etc/*', 'examples/*.py']},

    install_requires=requirements,
    extras_require=extra_requirements,

    entry_points={
        'console_scripts': [
            'ludwig=ludwig.cli:main'
        ]
    }
)
