#!/usr/bin/env python3
"""
Unit tests for SigLIP/CLIP Multimodal Search and Vector Projection Engine.
Validates 768-D vector dimensions, unit normalization, semantic cosine similarity,
and query filter compilation.
"""

import math
import pytest
import numpy as np

from app.operations.multimodal_search import (
    MultimodalEmbedder,
    MultimodalSearchEngine,
    VECTOR_DIM
)


def test_embed_text_shape_and_norm():
    embedder = MultimodalEmbedder()
    vec = embedder.embed_text("Modern luxury house with quartz kitchen and infinity pool")

    assert len(vec) == VECTOR_DIM
    assert len(vec) == 768

    # Calculate L2 norm: should be ~1.0
    norm = math.sqrt(sum(x * x for x in vec))
    assert 0.99 <= norm <= 1.01, f"Vector should be unit normalized, got {norm}"


def test_embed_reproducibility():
    embedder = MultimodalEmbedder()
    prompt = "Hamptons coastal living with timber deck"
    vec1 = embedder.embed_text(prompt)
    vec2 = embedder.embed_text(prompt)
    assert vec1 == vec2, "Embeddings must be completely deterministic and reproducible"


def test_semantic_cosine_similarity():
    embedder = MultimodalEmbedder()

    # Two queries describing the same kitchen space
    q_kitchen1 = "Designer kitchen with marble island bench and modern finishes"
    q_kitchen2 = "Contemporary open kitchen featuring marble benchtops"

    # Query describing an outdoor garden/pool
    q_pool = "Spacious backyard garden with swimming pool and lawn"

    v_k1 = embedder.embed_text(q_kitchen1)
    v_k2 = embedder.embed_text(q_kitchen2)
    v_p = embedder.embed_text(q_pool)

    sim_kitchen = MultimodalEmbedder.cosine_similarity(v_k1, v_k2)
    sim_kitchen_pool = MultimodalEmbedder.cosine_similarity(v_k1, v_p)

    # Kitchen-to-Kitchen should have substantially higher similarity than Kitchen-to-Pool
    assert sim_kitchen > sim_kitchen_pool
    assert sim_kitchen > 0.40


def test_embed_image_url_and_hash():
    embedder = MultimodalEmbedder()
    img_url = "https://images.domain.com.au/property/123/kitchen-marble-island.jpg"
    v_img = embedder.embed_image(img_url)

    assert len(v_img) == 768
    norm = math.sqrt(sum(x * x for x in v_img))
    assert 0.99 <= norm <= 1.01

    # Image URL containing 'kitchen' should correlate positively with 'kitchen' text query
    v_text_kitchen = embedder.embed_text("Modern kitchen")
    sim = MultimodalEmbedder.cosine_similarity(v_img, v_text_kitchen)
    assert sim > 0.20


def test_search_engine_simulation():
    engine = MultimodalSearchEngine(qdrant_client=None)
    results = engine.search_by_prompt(
        prompt="Waterfront apartment with Sydney harbour bridge view",
        state="NSW",
        suburb="Sydney",
        min_bed=2,
        limit=5
    )

    assert len(results) == 1
    assert results[0]["status"] == "simulated"
    assert results[0]["vector_dim"] == 768
    assert results[0]["filter_applied"]["state"] == "NSW"
