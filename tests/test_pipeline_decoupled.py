"""
Unit and Integration Tests for Decoupled Pipeline Stages,
Checkpointing, Data Quality Framework, and Resumable Batch Execution.
"""

import os
import json
import tempfile
import pytest

from app.operations.pipeline import (
    PipelineConfig,
    PipelineOrchestrator,
    SyntheticDiscovery,
    GnafDiscovery,
    OpenDataDiscovery,
    OpenDataExtractor,
    EnrichmentExtractor,
    PropertySchemaValidator,
    MemoryBatchSink,
    FileBatchSink,
)
from app.operations.checkpoint_manager import CheckpointManager
from app.operations.data_quality import DataQualityAuditor, QualityScorecard


def test_synthetic_discovery_generation():
    """Verify SyntheticDiscovery generates valid Australian properties with deterministic seed."""
    discovery = SyntheticDiscovery(seed=123, error_rate=0.0)
    items = list(discovery.discover(limit=50))
    assert len(items) == 50

    sample = items[0]
    assert "street" in sample
    assert "suburb" in sample
    assert sample["state"] in {"NSW", "VIC", "QLD", "ACT", "WA", "SA", "TAS", "NT"}
    assert -45.0 <= sample["coordinates"]["lat"] <= -8.0
    assert 110.0 <= sample["coordinates"]["lon"] <= 155.0


def test_extraction_and_enrichment():
    """Verify EnrichmentExtractor adds physical attributes, sales history, and cadastre."""
    raw_item = {
        "id": "PROP_TEST_01",
        "street": "100 George St",
        "suburb": "Sydney",
        "state": "NSW",
        "postcode": "2000",
        "propertyType": "house",
        "coordinates": {"lat": -33.8688, "lon": 151.2093}
    }
    extractor = EnrichmentExtractor(enrich_probability=1.0, seed=42)
    enriched = extractor.extract(raw_item)

    assert enriched["is_enriched"] is True
    assert enriched["bed"] >= 1
    assert enriched["bath"] >= 1
    assert enriched["area"]["totalArea"] > 0
    assert enriched["expectedPrice"] > 0
    assert len(enriched["historysale"]) == 1
    assert "cadastral_parcel" in enriched


def test_schema_validation_and_rejection():
    """Verify PropertySchemaValidator accepts valid and rejects corrupted records."""
    validator = PropertySchemaValidator(reject_invalid_geo=True)

    # Valid item
    valid_item = {
        "id": "VALID_01",
        "street": "12 Macquarie St",
        "suburb": "Sydney",
        "state": "NSW",
        "postcode": "2000",
        "coordinates": {"lat": -33.8688, "lon": 151.2093}
    }
    is_valid, validated, err = validator.validate(valid_item)
    assert is_valid is True
    assert err == ""
    assert "data_quality" in validated

    # Corrupt coordinates (lat = 85.0 North Pole)
    corrupt_item = dict(valid_item)
    corrupt_item["coordinates"] = {"lat": 85.0, "lon": 0.0}
    is_valid_c, _, err_c = validator.validate(corrupt_item)
    assert is_valid_c is False
    assert "Geospatial Validation Error" in err_c


def test_data_quality_framework_auditing():
    """Verify DataQualityAuditor computes completeness, accuracy, and duplicate metrics."""
    auditor = DataQualityAuditor()

    records = [
        # Record 1: Perfect enriched record
        {
            "street": "15 Collins St", "suburb": "Melbourne", "state": "VIC", "postcode": "3000",
            "coordinates": {"lat": -37.8136, "lon": 144.9631},
            "bed": 3, "bath": 2, "area": {"totalArea": 180.0}, "expectedPrice": 1250000.0,
            "source": "GNAF_OFFICIAL", "historysale": [{"price": 1100000}]
        },
        # Record 2: Missing bed, bath, price (sparse)
        {
            "street": "200 Pitt St", "suburb": "Sydney", "state": "NSW", "postcode": "2000",
            "coordinates": {"lat": -33.8710, "lon": 151.2080},
            "source": "GNAF_OFFICIAL"
        },
        # Record 3: Duplicate of Record 1
        {
            "street": "15 Collins St", "suburb": "Melbourne", "state": "VIC", "postcode": "3000",
            "coordinates": {"lat": -37.8136, "lon": 144.9631},
            "source": "GNAF_OFFICIAL"
        }
    ]

    scorecard: QualityScorecard = auditor.audit_batch(records)

    assert scorecard.total_records == 3
    assert scorecard.unique_records == 2
    assert scorecard.duplicate_records == 1
    assert scorecard.duplicate_rate_pct == 33.33
    assert scorecard.field_completeness_pct["street"] == 100.0
    assert scorecard.field_completeness_pct["bed"] == 33.3
    assert scorecard.geo_accuracy_pct == 100.0
    assert scorecard.composite_quality_score > 0
    assert "Australian Property Data Quality Scorecard" in scorecard.to_markdown()


def test_checkpoint_atomic_resumption(tmp_path):
    """Verify CheckpointManager saves atomically and resumes without duplicate processing."""
    chk_file = tmp_path / "test_checkpoint.json"

    # Run 1: process 20 items, crash/stop
    cm1 = CheckpointManager(checkpoint_file=str(chk_file), enable_signal_handlers=False)
    for i in range(1, 21):
        cm1.record_success(f"REC_{i:03d}", offset=i, is_enriched=(i % 2 == 0))
    cm1.save_checkpoint(force=True)

    assert chk_file.exists()
    assert cm1.processed_count == 20
    assert cm1.enriched_count == 10

    # Run 2: resume
    cm2 = CheckpointManager(checkpoint_file=str(chk_file), enable_signal_handlers=False)
    assert cm2.is_resumed is True
    assert cm2.last_offset == 20
    assert cm2.processed_count == 20
    assert cm2.is_processed("REC_005") is True
    assert cm2.is_processed("REC_025") is False
    assert cm2.recovery_duration_sec >= 0.0


def test_orchestrator_simulated_crash_and_recovery(tmp_path):
    """
    Test simulating a pipeline crash at record 150 of 300,
    then resuming the pipeline to verify 100% completion with zero duplicates.
    """
    chk_file = tmp_path / "pipeline_resilience_test.json"

    # Part 1: Start 300 records, inject fault at 150
    config_1 = PipelineConfig(
        batch_size=50,
        workers=2,
        checkpoint_file=str(chk_file),
        limit=300,
        simulate_crash_at=150
    )
    sink_1 = MemoryBatchSink()
    orch_1 = PipelineOrchestrator(
        config=config_1,
        discovery=SyntheticDiscovery(seed=999, error_rate=0.0),
        extractor=EnrichmentExtractor(),
        validator=PropertySchemaValidator(),
        sink=sink_1
    )
    rep_1 = orch_1.run()

    assert rep_1.status == "INTERRUPTED_SIMULATED_FAULT"
    assert rep_1.total_processed == 150
    assert sink_1.get_count() == 150

    # Part 2: Resume from checkpoint and process remainder (to total limit 300)
    config_2 = PipelineConfig(
        batch_size=50,
        workers=2,
        checkpoint_file=str(chk_file),
        limit=300,
        simulate_crash_at=None
    )
    sink_2 = MemoryBatchSink()
    orch_2 = PipelineOrchestrator(
        config=config_2,
        discovery=SyntheticDiscovery(seed=999, error_rate=0.0),
        extractor=EnrichmentExtractor(),
        validator=PropertySchemaValidator(),
        sink=sink_2
    )
    rep_2 = orch_2.run()

    assert rep_2.status == "COMPLETED"
    assert rep_2.was_resumed is True
    assert rep_2.total_processed == 300
    assert rep_2.recovery_time_sec >= 0.0
    # sink_2 should only receive the second half (150 items), no duplicates!
    assert sink_2.get_count() == 150
