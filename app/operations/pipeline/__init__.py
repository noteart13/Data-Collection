"""
Decoupled Modular Property Data Ingestion & Enrichment Pipeline.
Segments pipeline into distinct stages:
1. Discovery (G-NAF, Open Data, Web Sitemaps)
2. Extraction (Statutory REST, Web Crawlers, Enriched Features)
3. Validation (Schema Enforcement, Quality Scorecard, Sanity Bounds)
4. Sinks (Qdrant Vector DB, JSONL, Memory Storage with Resilient Batch Upsert)
"""

from .discovery import BaseDiscovery, SyntheticDiscovery, GnafDiscovery, OpenDataDiscovery
from .extractor import BaseExtractor, OpenDataExtractor, EnrichmentExtractor
from .validator import BaseValidator, PropertySchemaValidator
from .sink import BaseSink, QdrantBatchSink, MemoryBatchSink, FileBatchSink
from .orchestrator import PipelineOrchestrator, PipelineConfig

__all__ = [
    "BaseDiscovery",
    "SyntheticDiscovery",
    "GnafDiscovery",
    "OpenDataDiscovery",
    "BaseExtractor",
    "OpenDataExtractor",
    "EnrichmentExtractor",
    "BaseValidator",
    "PropertySchemaValidator",
    "BaseSink",
    "QdrantBatchSink",
    "MemoryBatchSink",
    "FileBatchSink",
    "PipelineOrchestrator",
    "PipelineConfig",
]
