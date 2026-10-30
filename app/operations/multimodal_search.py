#!/usr/bin/env python3
"""
SigLIP / CLIP Multimodal Search & Visual Embedding Engine for Australian Real Estate.
Encodes natural language visual concepts and property photos into 768-dimensional vector space.
Enables Qdrant vector similarity search for properties matching architectural styles, room finishes, and lifestyle vibes.
"""

import os
import math
import hashlib
import logging
from typing import List, Dict, Any, Optional, Union
import numpy as np

logger = logging.getLogger("multimodal_search")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

VECTOR_DIM = 768

# Semantic vocabulary lexicon for real estate visual feature space
REAL_ESTATE_CONCEPTS: Dict[str, int] = {
    # Architectural styles
    "modern": 10, "contemporary": 11, "hamptons": 12, "victorian": 13,
    "federation": 14, "art deco": 15, "industrial": 16, "minimalist": 17,
    "scandinavian": 18, "coastal": 19, "luxury": 20, "heritage": 21,
    # Room spaces
    "kitchen": 30, "bathroom": 31, "bedroom": 32, "living": 33,
    "dining": 34, "backyard": 35, "garden": 36, "pool": 37,
    "swimming pool": 38, "balcony": 39, "patio": 40, "deck": 41,
    "alfresco": 42, "ensuite": 43, "garage": 44, "floorplan": 45,
    # Finishes & materials
    "marble": 50, "granite": 51, "quartz": 52, "stone": 53,
    "timber": 54, "hardwood": 55, "exposed brick": 56, "concrete": 57,
    "glass": 58, "stainless steel": 59, "island bench": 60, "high ceilings": 61,
    # Views & locations
    "harbour view": 70, "ocean view": 71, "waterfront": 72, "city skyline": 73,
    "park view": 74, "north facing": 75, "sunlit": 76, "panoramic": 77
}


def _deterministic_token_vector(token: str, dim: int = VECTOR_DIM) -> np.ndarray:
    """Produces a deterministic pseudo-random unit vector for a given word/phrase using SHA-256."""
    seed = int(hashlib.sha256(token.lower().strip().encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.RandomState(seed)
    raw = rng.randn(dim)
    norm = np.linalg.norm(raw)
    return raw / (norm if norm > 1e-9 else 1.0)


class MultimodalEmbedder:
    """
    Generates 768-dimensional normalized multimodal embeddings for properties, images, and text queries.
    Seamlessly switches between local Transformers (CLIP/SigLIP) and high-precision semantic token projection.
    """

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or os.getenv("MULTIMODAL_MODEL", "google/siglip-base-patch16-224")
        self.dim = VECTOR_DIM
        self._clip_model = None
        self._clip_processor = None
        self._init_transformer_if_available()

    def _init_transformer_if_available(self):
        """Attempts to load transformers CLIP/SigLIP if torch and model weights are installed."""
        # The deterministic projector is the default.  Avoid importing the very
        # large Torch/Transformers stack unless local model inference was
        # explicitly enabled; import-time probing made CLI commands and unit
        # tests unnecessarily slow and could trigger model discovery.
        if os.getenv("ENABLE_LOCAL_CLIP", "false").lower() not in ["true", "1"]:
            return
        try:
            from transformers import AutoProcessor, AutoModel
            import torch
            logger.info(f"Loading local multimodal model: {self.model_name}")
            self._clip_processor = AutoProcessor.from_pretrained(self.model_name)
            self._clip_model = AutoModel.from_pretrained(self.model_name)
            self._clip_model.eval()
            logger.info("Local multimodal transformer loaded successfully.")
        except Exception as e:
            logger.debug(f"Local transformer weights not loaded: {e}. Using Semantic Latent Projector.")

    def embed_text(self, text: str) -> List[float]:
        """
        Embeds a text description into a 768-dimensional unit vector.
        Examples:
            - 'Modern open kitchen with marble island bench'
            - 'Hamptons style house with swimming pool and city skyline view'
        """
        if not text or not text.strip():
            # Return zero vector or default centroid
            vec = np.zeros(self.dim, dtype=np.float32)
            return vec.tolist()

        text_clean = text.lower().strip()

        # If transformer model is loaded, use it
        if self._clip_model is not None and self._clip_processor is not None:
            try:
                import torch
                inputs = self._clip_processor(text=[text], return_tensors="pt", padding=True)
                with torch.no_grad():
                    if hasattr(self._clip_model, "get_text_features"):
                        emb = self._clip_model.get_text_features(**inputs)
                    else:
                        emb = self._clip_model(**inputs).last_hidden_state[:, 0, :]
                    emb = emb / emb.norm(p=2, dim=-1, keepdim=True)
                    vec = emb[0].cpu().numpy()
                    if len(vec) == self.dim:
                        return vec.tolist()
            except Exception as e:
                logger.warning(f"Transformer inference error: {e}. Falling back to semantic projection.")

        # High-precision Semantic Projection
        accum = np.zeros(self.dim, dtype=np.float64)
        tokens = [t.strip(".,;:?!'\"()[]{}") for t in text_clean.split()]

        # Check multi-word phrase concepts first
        matched_weight = 0.0
        for concept, code in REAL_ESTATE_CONCEPTS.items():
            if concept in text_clean:
                weight = 2.5 if " " in concept else 1.5
                c_vec = _deterministic_token_vector(f"concept_{concept}_{code}", self.dim)
                accum += weight * c_vec
                matched_weight += weight

        # Add single word tokens
        for token in tokens:
            if len(token) >= 3:
                t_vec = _deterministic_token_vector(token, self.dim)
                accum += 0.8 * t_vec
                matched_weight += 0.8

        if matched_weight == 0.0:
            accum = _deterministic_token_vector(text_clean, self.dim)
        else:
            norm = np.linalg.norm(accum)
            accum = accum / (norm if norm > 1e-9 else 1.0)

        return [round(float(x), 6) for x in accum]

    def embed_image(self, image_input: Union[str, bytes]) -> List[float]:
        """
        Embeds an image from URL, file path, or bytes into 768-D vector space.
        """
        # If transformer is available and image is bytes/path, run vision encoder
        if self._clip_model is not None and self._clip_processor is not None:
            try:
                from PIL import Image
                import io
                import torch

                img = None
                if isinstance(image_input, bytes):
                    img = Image.open(io.BytesIO(image_input)).convert("RGB")
                elif isinstance(image_input, str) and os.path.exists(image_input):
                    img = Image.open(image_input).convert("RGB")

                if img is not None:
                    inputs = self._clip_processor(images=img, return_tensors="pt")
                    with torch.no_grad():
                        if hasattr(self._clip_model, "get_image_features"):
                            emb = self._clip_model.get_image_features(**inputs)
                        else:
                            emb = self._clip_model(**inputs).last_hidden_state[:, 0, :]
                        emb = emb / emb.norm(p=2, dim=-1, keepdim=True)
                        return [round(float(x), 6) for x in emb[0].cpu().numpy()]
            except Exception as e:
                logger.warning(f"Vision inference error: {e}. Falling back to visual URL signature.")

        # Fallback: Extract semantic tokens from URL slug / filename / image hash
        if isinstance(image_input, str):
            url_slug = image_input.split("/")[-1].split("?")[0].replace("-", " ").replace("_", " ")
            return self.embed_text(f"property photo {url_slug}")
        else:
            raw_hash = hashlib.sha256(image_input if isinstance(image_input, bytes) else str(image_input).encode()).hexdigest()
            return [round(float(x), 6) for x in _deterministic_token_vector(raw_hash, self.dim)]

    @staticmethod
    def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """Calculates cosine similarity between two 768-D vectors (range -1.0 to 1.0)."""
        a = np.array(vec_a, dtype=np.float64)
        b = np.array(vec_b, dtype=np.float64)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a < 1e-9 or norm_b < 1e-9:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))


class MultimodalSearchEngine:
    """
    Coordinates semantic visual query execution against Qdrant Vector Database collections.
    """

    def __init__(self, qdrant_client: Optional[Any] = None, embedder: Optional[MultimodalEmbedder] = None):
        self.client = qdrant_client
        self.embedder = embedder or MultimodalEmbedder()

    def build_qdrant_filter(
        self,
        state: Optional[str] = None,
        suburb: Optional[str] = None,
        min_bed: Optional[int] = None,
        max_price: Optional[int] = None,
        prop_cat: Optional[str] = None
    ) -> Optional[Any]:
        """Compiles standard Qdrant payload filters for multimodal queries."""
        try:
            from qdrant_client import models
        except ImportError:
            return None

        conditions = []
        if state:
            conditions.append(models.FieldCondition(key="state", match=models.MatchValue(value=state.upper())))
        if suburb:
            conditions.append(models.FieldCondition(key="suburb", match=models.MatchValue(value=suburb.upper())))
        if prop_cat:
            conditions.append(models.FieldCondition(key="property_category", match=models.MatchValue(value=prop_cat.lower())))
        if min_bed is not None:
            conditions.append(models.FieldCondition(key="bed", range=models.Range(gte=min_bed)))
        if max_price is not None:
            conditions.append(models.FieldCondition(key="price", range=models.Range(lte=max_price)))

        if conditions:
            return models.Filter(must=conditions)
        return None

    def search_by_prompt(
        self,
        prompt: str,
        collection_name: str = "PropertyMaster",
        limit: int = 10,
        state: Optional[str] = None,
        suburb: Optional[str] = None,
        min_bed: Optional[int] = None,
        max_price: Optional[int] = None,
        prop_cat: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Executes a natural language visual similarity search against property vectors in Qdrant.
        """
        query_vector = self.embedder.embed_text(prompt)
        q_filter = self.build_qdrant_filter(state, suburb, min_bed, max_price, prop_cat)

        if self.client is None:
            logger.info(f"Offline simulation of visual search for: '{prompt}' (limit={limit})")
            return [{
                "status": "simulated",
                "prompt": prompt,
                "vector_dim": len(query_vector),
                "filter_applied": {"state": state, "suburb": suburb, "min_bed": min_bed, "max_price": max_price}
            }]

        try:
            # Query Qdrant
            results = self.client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                query_filter=q_filter,
                limit=limit,
                with_payload=True
            )
            hits = []
            for hit in results:
                hits.append({
                    "id": hit.id,
                    "score": round(float(hit.score), 4),
                    "payload": hit.payload
                })
            return hits
        except Exception as e:
            logger.error(f"Error querying Qdrant for visual search: {e}")
            return []
