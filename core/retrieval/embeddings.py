from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from hashlib import sha256
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, computed_field


class EmbeddingSpaceDefinition(BaseModel):
    """Immutable identity for one non-interchangeable embedding space."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model_name: str
    model_version: str
    dimensions: int = Field(ge=8, le=16_000)
    distance_metric: str = "cosine"
    normalization: str = "l2"
    document_template_version: str = "memory-document-v1"
    query_template_version: str = "memory-query-v1"
    data_boundary: Literal["local", "private_network", "external"] = "local"
    config: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @computed_field
    @property
    def config_hash(self) -> str:
        return _canonical_hash(self.config)

    @computed_field
    @property
    def id(self) -> str:
        return _canonical_hash(
            {
                "provider": self.provider,
                "model_name": self.model_name,
                "model_version": self.model_version,
                "dimensions": self.dimensions,
                "distance_metric": self.distance_metric,
                "normalization": self.normalization,
                "document_template_version": self.document_template_version,
                "query_template_version": self.query_template_version,
                "data_boundary": self.data_boundary,
                "config_hash": self.config_hash,
            }
        )


class EmbeddingProvider(Protocol):
    @property
    def space(self) -> EmbeddingSpaceDefinition: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FeatureHashEmbeddingProvider:
    """Small deterministic local baseline with no model download.

    This is deliberately labelled as feature hashing, not as a learned semantic
    model. It gives the offline demo a real vector path while keeping the
    provider boundary replaceable by a local or remote embedding model.
    """

    def __init__(self, *, dimensions: int = 384) -> None:
        if not 8 <= dimensions <= 16_000:
            raise ValueError("embedding dimensions must be between 8 and 16000")
        self._space = EmbeddingSpaceDefinition(
            provider="local",
            model_name="unicode-feature-hash",
            model_version="1.0.0",
            dimensions=dimensions,
            data_boundary="local",
            config={
                "word_features": True,
                "character_ngrams": "2,3",
                "signed_hashing": True,
            },
        )

    @property
    def space(self) -> EmbeddingSpaceDefinition:
        return self._space

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text, prefix="document") for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text, prefix="query")

    def _embed(self, text: str, *, prefix: str) -> list[float]:
        normalized = normalize_text(text)
        if not normalized:
            raise ValueError("cannot embed empty text")
        vector = [0.0] * self._space.dimensions
        features = Counter(_text_features(normalized))
        features[f"{prefix}:template-v1"] += 0.05
        for feature, count in features.items():
            digest = sha256(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % self._space.dimensions
            sign = -1.0 if digest[8] & 1 else 1.0
            vector[index] += sign * (1.0 + math.log(float(count)))
        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude == 0:
            raise ValueError("embedding produced a zero vector")
        return [value / magnitude for value in vector]


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(normalized.split())


def lexical_features(text: str) -> set[str]:
    return set(_text_features(normalize_text(text)))


def lexical_overlap_score(query: str, document: str) -> float:
    query_features = lexical_features(query)
    document_features = lexical_features(document)
    if not query_features or not document_features:
        return 0.0
    overlap = query_features & document_features
    if not overlap:
        return 0.0
    query_coverage = len(overlap) / len(query_features)
    document_coverage = len(overlap) / len(document_features)
    return (0.85 * query_coverage) + (0.15 * document_coverage)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors from different dimensions cannot be compared")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def _text_features(normalized: str) -> list[str]:
    features: list[str] = []
    for token in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", normalized):
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            if len(token) == 1:
                features.append(f"han:{token}")
            for size in (2, 3):
                features.extend(
                    f"han{size}:{token[index : index + size]}"
                    for index in range(max(0, len(token) - size + 1))
                )
        else:
            features.append(f"word:{token}")
    compact = "".join(normalized.split())
    for size in (2, 3):
        features.extend(
            f"char{size}:{compact[index : index + size]}"
            for index in range(max(0, len(compact) - size + 1))
        )
    return features or [f"text:{normalized}"]


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
