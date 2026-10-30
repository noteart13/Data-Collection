import setuptools
from pathlib import Path

long_description = ""
readme_file = Path(__file__).resolve().parent / "README.md"
if readme_file.exists():
    with open(readme_file, "r", encoding="utf-8") as fh:
        long_description = fh.read()

setuptools.setup(
    name="property-master-pipeline",
    version="0.2.0",
    author="Property Data Engineering",
    author_email="dev@propertydata.local",
    description="Australian Property Master Data Ingestion, Scraping & Valuation Pipeline",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=setuptools.find_packages(include=["app", "app.*"]),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.10",
    install_requires=[
        "qdrant-client>=1.9.0",
        "fastapi>=0.110.0",
        "uvicorn>=0.29.0",
        "pydantic>=2.7.0",
        "pydantic-settings>=2.2.0",
        "requests>=2.31.0",
        "httpx>=0.27.0",
        "beautifulsoup4>=4.12.0",
        "python-dotenv>=1.0.0",
        "numpy>=1.26.0,<3",
        "scikit-learn>=1.4,<2",
        "joblib>=1.3,<2",
    ],
    entry_points={
        "console_scripts": [
            "property-cli=cli:main",
        ],
    },
)
