from __future__ import annotations

from typing import Literal

ModelDataBoundary = Literal["local", "private_network", "external"]
MemorySensitivity = Literal["public", "private", "restricted"]

MODEL_DATA_BOUNDARIES = frozenset({"local", "private_network", "external"})
MEMORY_SENSITIVITIES = frozenset({"public", "private", "restricted"})

_BOUNDARY_SENSITIVITIES: dict[str, tuple[str, ...]] = {
    "local": ("public", "private", "restricted"),
    "private_network": ("public", "private"),
    "external": ("public",),
}


class ModelDataPolicyError(PermissionError):
    pass


def normalize_model_boundaries(values: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(item.strip().lower() for item in values))
    unknown = set(normalized) - MODEL_DATA_BOUNDARIES
    if unknown:
        raise ValueError(
            f"Unsupported model data boundaries: {', '.join(sorted(unknown))}"
        )
    if "local" not in normalized:
        raise ValueError("local must remain an allowed model data boundary")
    return [
        item for item in ("local", "private_network", "external") if item in normalized
    ]


def normalize_sensitivity(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in MEMORY_SENSITIVITIES:
        raise ValueError(f"Unsupported memory sensitivity: {value}")
    return normalized


def allowed_sensitivities_for_boundary(boundary: str) -> tuple[str, ...]:
    try:
        return _BOUNDARY_SENSITIVITIES[boundary]
    except KeyError as exc:
        raise ValueError(f"Unsupported model data boundary: {boundary}") from exc


def require_boundary_allowed(
    *,
    allowed_boundaries: list[str],
    requested_boundary: str,
) -> None:
    normalized = normalize_model_boundaries(allowed_boundaries)
    if requested_boundary not in normalized:
        raise ModelDataPolicyError(
            f"Persona does not allow model data boundary: {requested_boundary}"
        )
