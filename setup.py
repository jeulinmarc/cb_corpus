#!/usr/bin/env python
"""Setuptools configuration for cb_corpus."""
from setuptools import setup, find_packages

setup(
    name="cb_corpus",
    version="0.1.0",
    description="Official central-bank document corpus builder (BIS-63, scope A-F)",
    author="MyOpenFund",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "requests>=2.31",
        "beautifulsoup4>=4.12",
        "lxml>=5.0",
        "pandas>=2.0",
        "python-dateutil>=2.9",
    ],
    entry_points={
        "console_scripts": [
            "cb_corpus=cb_corpus.cli:main",
        ],
    },
)
