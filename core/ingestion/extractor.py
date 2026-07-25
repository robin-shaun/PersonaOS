from __future__ import annotations

import re
from hashlib import sha256
from typing import Protocol

from core.ingestion.models import (
    ChunkDraft,
    MemoryCandidateDraft,
    MemoryType,
)


class MemoryCandidateExtractor(Protocol):
    name: str
    version: str

    def extract(
        self,
        chunks: list[ChunkDraft],
    ) -> list[MemoryCandidateDraft]: ...


class RulesMemoryCandidateExtractor:
    """Offline baseline that copies source claims without inventing facts."""

    name = "source-chunk-rules"
    version = "1.0.0"

    _DATE_PATTERN = re.compile(r"\b(?:19|20)\d{2}[-/.年](?:0?[1-9]|1[0-2])")
    _PROCEDURAL_PATTERN = re.compile(
        r"(?:步骤|流程|方法|先.+再|第[一二三四五六七八九十]+步|^\s*\d+[.)、])",
        re.MULTILINE,
    )
    _PREFERENCE_PATTERN = re.compile(
        r"(?:我喜欢|我不喜欢|我偏好|我的偏好|更喜欢|prefer|dislike)",
        re.IGNORECASE,
    )
    _RELATIONSHIP_PATTERN = re.compile(
        r"(?:是我的|我的(?:同事|朋友|家人|导师|客户)|隶属于|合作关系|relationship)",
        re.IGNORECASE,
    )

    def extract(
        self,
        chunks: list[ChunkDraft],
    ) -> list[MemoryCandidateDraft]:
        candidates: list[MemoryCandidateDraft] = []
        for chunk in chunks:
            raw_content = chunk.content.strip()
            if not raw_content:
                continue
            memory_type = self._memory_type(raw_content)
            normalized = " ".join(raw_content.split()).casefold()
            fingerprint = sha256(f"{memory_type}\0{normalized}".encode()).hexdigest()
            summary = " ".join(raw_content.split())
            if len(summary) > 280:
                summary = f"{summary[:279]}…"
            candidates.append(
                MemoryCandidateDraft(
                    chunk_ordinal=chunk.ordinal,
                    memory_type=memory_type,
                    epistemic_status="source_verified",
                    raw_content=raw_content,
                    structured_summary=summary,
                    candidate_fingerprint=fingerprint,
                    confidence=0.90,
                    importance=0.50,
                    sensitivity="private",
                    visibility="owner",
                    extractor_name=self.name,
                    extractor_version=self.version,
                )
            )
        return candidates

    def _memory_type(self, content: str) -> MemoryType:
        if self._PREFERENCE_PATTERN.search(content):
            return "preference"
        if self._PROCEDURAL_PATTERN.search(content):
            return "procedural"
        if self._RELATIONSHIP_PATTERN.search(content):
            return "relationship"
        if self._DATE_PATTERN.search(content):
            return "episodic"
        return "semantic"
