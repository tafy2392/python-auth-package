import os

from setuptools import find_packages, setup

HERE = os.path.abspath(os.path.dirname(__file__))


def read(*parts):
    with open(os.path.join(HERE, *parts)) as f:
        return f.read()


setup(
    name="marathon-acme-trio",
    version="0.0.1.dev0",
    license="MIT",
    description="Replacement for python-marathon-acme",
    author="Praekelt.org SRE team",
    author_email="sre@praekelt.org",
    long_description=read("README.md"),
    long_description_content_type="text/markdown",
    packages=find_packages("src"),
    package_dir={"": "src"},
    entry_points={
        "console_scripts": [
            "marathon-acme-trio=marathon_acme_trio.cli:cli_main",
        ],
    },
    python_requires=">=3.7",
    install_requires=[
        "acme",
        "click",
        "cryptography",
        "httpx",
        "hypercorn",
        "josepy",
        "marathon",
        "Quart",
        "Quart-Trio",
        "trio",
        "trio-util",
    ],
    extras_require={
        "dev": [
            "black",
            "flake8",
            "isort",
            "mypy",
            "pep517",
            "pytest>=5.0.1",
            "pytest-cov",
            "pytest-trio",
            "testtools",
            "trio-typing",
        ]
    },
    classifiers=[
        "Private :: Do Not Upload",
    ],
)
