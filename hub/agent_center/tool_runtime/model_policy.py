"""Cheapest-capable model selection with exact manual override preservation."""

from __future__ import annotations

from typing import Any, Callable


AvailabilityFn = Callable[[str], tuple[bool, str]]


def select_runtime_provider_model(
    *,
    manual_override: bool,
    selected_provider: str | None,
    selected_model: str | None,
    candidates: list[str] | None,
    availability: AvailabilityFn,
    price_fn: Callable[[str], float] | None = None,
    purpose: str = "tool_runtime",
) -> dict[str, Any]:
    """
    Prefer the cheapest capable available provider for synthesis/reasoning.

    When ``manual_override`` is True (or an explicit provider+model is fixed for
    Agent mode), preserve that exact choice — never silently substitute.
    """
    selected_provider = str(selected_provider or "").strip()
    selected_model = str(selected_model or "").strip()

    if manual_override or (selected_provider and selected_model and purpose == "agent_fixed"):
        if not selected_provider:
            return {
                "ok": False,
                "provider": "",
                "model": selected_model,
                "reason": "manual_provider_required",
                "error": "Manual override requires an explicit provider (no silent fallback).",
            }
        ok, detail = availability(selected_provider)
        if not ok:
            return {
                "ok": False,
                "provider": selected_provider,
                "model": selected_model,
                "reason": "manual_provider_unavailable",
                "error": (
                    f"Selected provider {selected_provider} unavailable. "
                    f"{detail or ''} No automatic fallback was used."
                ).strip(),
            }
        return {
            "ok": True,
            "provider": selected_provider,
            "model": selected_model,
            "reason": "manual_override",
            "error": "",
        }

    # Explicit model with provider: preserve model; may still pick cheapest provider
    # only when selected_provider is empty.
    if selected_provider:
        ok, detail = availability(selected_provider)
        if not ok:
            return {
                "ok": False,
                "provider": selected_provider,
                "model": selected_model,
                "reason": "selected_provider_unavailable",
                "error": (
                    f"Recommended provider {selected_provider} unavailable. "
                    f"{detail or ''} Choose another agent explicitly."
                ).strip(),
            }
        return {
            "ok": True,
            "provider": selected_provider,
            "model": selected_model,
            "reason": "selected_provider",
            "error": "",
        }

    ordered = [str(c).strip() for c in (candidates or []) if str(c).strip()]
    # Drop simulator from automatic cheapest path.
    ordered = [c for c in ordered if c not in {"hub-simulator", "low-cost"}]
    if price_fn is not None:
        ordered = sorted(
            dict.fromkeys(ordered),
            key=lambda candidate: (
                float(price_fn(candidate) or 0) <= 0,
                float(price_fn(candidate) or 0) if float(price_fn(candidate) or 0) > 0 else float("inf"),
            ),
        )
    else:
        ordered = list(dict.fromkeys(ordered))

    for candidate in ordered:
        ok, _detail = availability(candidate)
        if ok:
            return {
                "ok": True,
                "provider": candidate,
                "model": selected_model,
                "reason": "cheapest_capable",
                "error": "",
            }
    return {
        "ok": False,
        "provider": "",
        "model": selected_model,
        "reason": "no_capable_provider",
        "error": "No capable AI provider available (no silent fallback).",
    }
