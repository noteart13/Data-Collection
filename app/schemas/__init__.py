"""
Australian Property Master Schema & Data Quality Definitions.
Standardizes data contracts for Qdrant PropertyMaster collections and ingestion pipelines.
"""

from .property_schema import (
    PropertyMasterPayload,
    CoordinatesSchema,
    AreaSchema,
    PricingSchema,
    FeaturesSchema,
    HistorySaleItem,
    DataQualityMetadata,
    evaluate_data_quality,
    calculate_data_quality_score,
)

__all__ = [
    "PropertyMasterPayload",
    "CoordinatesSchema",
    "AreaSchema",
    "PricingSchema",
    "FeaturesSchema",
    "HistorySaleItem",
    "DataQualityMetadata",
    "evaluate_data_quality",
    "calculate_data_quality_score",
]
