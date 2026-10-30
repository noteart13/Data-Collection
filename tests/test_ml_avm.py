#!/usr/bin/env python3
"""
Unit tests for Machine Learning Automated Valuation Model (ML AVM).
Validates distance calculations, feature engineering, prediction bounds,
confidence scoring, and model training.
"""

import math
import tempfile
import os
import pytest
import numpy as np

from app.operations.ml_avm import (
    MLAutomatedValuationModel,
    calculate_distance_to_cbd,
    haversine_distance_km,
    AU_CAPITAL_CBDS,
    REGIONAL_PRICE_BASELINES,
    ValuationResult
)
from app.train_avm import generate_bootstrap_comps


def test_haversine_distance():
    # Sydney Martin Place to Parramatta Square (~20 km)
    syd_lat, syd_lon = -33.8688, 151.2093
    parra_lat, parra_lon = -33.8150, 151.0011
    dist = haversine_distance_km(syd_lat, syd_lon, parra_lat, parra_lon)
    assert 18.0 <= dist <= 24.0, f"Expected ~20km, got {dist}"

    # Zero distance
    assert haversine_distance_km(syd_lat, syd_lon, syd_lat, syd_lon) == 0.0


def test_calculate_distance_to_cbd():
    # Known point in Sydney
    dist_nsw = calculate_distance_to_cbd(-33.8688, 151.2093, state="NSW")
    assert dist_nsw == 0.0

    # Fallback coordinates
    dist_none = calculate_distance_to_cbd(None, None, state="NSW")
    assert dist_none == 25.0


def test_extract_features():
    avm = MLAutomatedValuationModel()
    features = avm.extract_features(
        lat=-33.8688, lon=151.2093,
        bed=4, bath=3, parking=2, area_sqm=650,
        prop_cat="house", state="NSW"
    )
    assert isinstance(features, np.ndarray)
    assert features.shape == (1, 11)

    # Check house one-hot index (index 7 is is_house)
    assert features[0, 7] == 1.0
    assert features[0, 8] == 0.0  # is_unit
    assert features[0, 9] == 0.0  # is_townhouse
    assert features[0, 3] == 4.0  # bed
    assert features[0, 4] == 3.0  # bath


def test_confidence_scoring():
    avm = MLAutomatedValuationModel()

    # Complete record with recent sales comps
    high_conf = avm.calculate_confidence_score(
        lat=-33.85, lon=151.20,
        bed=3, bath=2, area_sqm=500,
        official_sales=[{"contractDate": "2024-06-15", "price": 1200000}]
    )
    assert high_conf >= 0.85

    # Incomplete record with no sales history
    low_conf = avm.calculate_confidence_score(
        lat=None, lon=None,
        bed=None, bath=None, area_sqm=None,
        official_sales=None
    )
    assert 0.40 <= low_conf <= 0.60
    assert high_conf > low_conf


def test_predict_bounds_and_structure():
    avm = MLAutomatedValuationModel()
    res = avm.predict(
        lat=-33.8688, lon=151.2093,
        bed=3, bath=2, parking=1, area_sqm=550,
        prop_cat="house", state="NSW", suburb="Sydney"
    )

    assert isinstance(res, ValuationResult)
    assert res.valuation_mid > 200000
    assert res.valuation_low <= res.valuation_mid
    assert res.valuation_high >= res.valuation_mid
    assert 0.0 < res.confidence_score <= 1.0
    assert res.is_synthetic is False
    assert res.valuation_method in ["ML_GRADIENT_BOOST_V1", "HEDONIC_CALIBRATED_ML_V1"]

    d = res.to_dict()
    assert "valuation_mid" in d
    assert "valuation_low" in d
    assert "valuation_high" in d
    assert "confidence_score" in d


def test_model_training_and_serialization():
    avm = MLAutomatedValuationModel()
    comps = generate_bootstrap_comps(n_samples=60, state="NSW")

    with tempfile.TemporaryDirectory() as tmpdir:
        model_file = os.path.join(tmpdir, "test_avm.joblib")
        metrics = avm.train(comps, target_field="price", save_path=model_file)

        assert os.path.exists(model_file)
        assert metrics["samples_total"] == 60
        assert metrics["mae"] > 0
        assert metrics["mdape_pct"] > 0

        # Load newly trained model in fresh instance
        loaded_avm = MLAutomatedValuationModel(model_path=model_file)
        assert loaded_avm.is_trained is True

        pred = loaded_avm.predict(
            lat=-33.8688, lon=151.2093,
            bed=4, bath=2, parking=2, area_sqm=600,
            prop_cat="house", state="NSW", suburb="Sydney"
        )
        assert pred.valuation_method == "ML_GRADIENT_BOOST_V1"
        assert pred.valuation_mid > 300000
