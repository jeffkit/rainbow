#!/usr/bin/env python

from setuptools import setup, find_packages
from rainbow import VERSION

url="https://github.com/jeffkit/rainbow"

long_description="Rainbow Server Reference Implementation. In Python"

setup(name="rainbow-server",
      version=VERSION,
      description=long_description,
      maintainer="jeff kit",
      maintainer_email="bbmyth@gmail.com",
      url = url,
      long_description=long_description,
      packages=find_packages('.'),
      install_requires=[
          'tornado',
          'websocket',
          ]
     )


