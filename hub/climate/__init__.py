"""CLIMATE code workspace."""

from __future__ import annotations

__all__ = ["ClimateCodingAdapter", "ClimateService"]


def __getattr__(name: str):
    if name == "ClimateCodingAdapter":
        from hub.climate.coding import ClimateCodingAdapter

        return ClimateCodingAdapter
    if name == "ClimateService":
        from hub.climate.service import ClimateService

        return ClimateService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
