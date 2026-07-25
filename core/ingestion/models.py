from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MemoryType = Literal[
    "episodic",
    "semantic",
    "procedural",
    "preference",
    "relationship",
    "reflection",
]
EpistemicStatus = Literal[
    "user_asserted",
    "source_verified",
    "model_summary",
    "model_inference",
    "user_rule",
]


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    ordinal: int
    content: str
    content_sha256: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    locator: dict[str, int | str]
    chunker_name: str
    chunker_version: str
    chunker_config_hash: str


@dataclass(frozen=True, slots=True)
class MemoryCandidateDraft:
    chunk_ordinal: int
    memory_type: MemoryType
    epistemic_status: EpistemicStatus
    raw_content: str
    structured_summary: str
    candidate_fingerprint: str
    confidence: float
    importance: float
    sensitivity: str
    visibility: str
    extractor_name: str
    extractor_version: str
