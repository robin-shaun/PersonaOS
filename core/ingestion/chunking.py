from __future__ import annotations

import json
from hashlib import sha256

from core.ingestion.models import ChunkDraft


class DeterministicTextChunker:
    name = "paragraph-window"
    version = "1.0.0"

    def __init__(
        self,
        *,
        max_chars: int = 1200,
        overlap_chars: int = 120,
    ) -> None:
        if max_chars < 200:
            raise ValueError("max_chars must be at least 200")
        if overlap_chars < 0 or overlap_chars >= max_chars // 2:
            raise ValueError("overlap_chars must be between 0 and half max_chars")
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars
        config = json.dumps(
            {
                "max_chars": max_chars,
                "overlap_chars": overlap_chars,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self.config_hash = sha256(config.encode("utf-8")).hexdigest()

    def split(self, text: str) -> list[ChunkDraft]:
        if not text.strip():
            raise ValueError("source text must contain non-whitespace content")

        chunks: list[ChunkDraft] = []
        start = 0
        text_length = len(text)
        while start < text_length:
            end = min(text_length, start + self.max_chars)
            if end < text_length:
                end = self._preferred_boundary(text, start, end)
            if end <= start:
                end = min(text_length, start + self.max_chars)

            content = text[start:end]
            if content.strip():
                line_start = text.count("\n", 0, start) + 1
                last_character = max(start, end - 1)
                line_end = text.count("\n", 0, last_character) + 1
                digest = sha256(content.encode("utf-8")).hexdigest()
                locator: dict[str, int | str] = {
                    "kind": "text_range",
                    "char_start": start,
                    "char_end": end,
                    "line_start": line_start,
                    "line_end": line_end,
                }
                chunks.append(
                    ChunkDraft(
                        ordinal=len(chunks),
                        content=content,
                        content_sha256=digest,
                        char_start=start,
                        char_end=end,
                        line_start=line_start,
                        line_end=line_end,
                        locator=locator,
                        chunker_name=self.name,
                        chunker_version=self.version,
                        chunker_config_hash=self.config_hash,
                    )
                )

            if end >= text_length:
                break
            next_start = end - self.overlap_chars
            start = max(start + 1, next_start)
        if not chunks:
            raise ValueError("source text did not produce any chunks")
        return chunks

    def _preferred_boundary(self, text: str, start: int, end: int) -> int:
        search_start = start + self.max_chars // 2
        for separator in ("\n\n", "\n", "。", ". ", " "):
            position = text.rfind(separator, search_start, end)
            if position >= search_start:
                return position + len(separator)
        return end
