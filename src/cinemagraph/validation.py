"""Cross-entry-point request validation, shared by the CLI and any future API layer.

Raises plain ValueError, not click.UsageError -- this is a library concern,
not a CLI concern. Callers translate: the CLI catches ValueError and
re-raises as click.UsageError; a future API would catch it and return a 422.
"""
from typing import Any

from . import effects as effects_pkg


def resolve_effect_kwargs(
    effect_names: list[str],
    per_effect_options: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Validate that every supplied per-effect override belongs to a
    requested effect and is a real override for that effect (per its
    registered Effect.allowed_kwargs), then build the effect_kwargs dict
    animate_photo() expects.

    `per_effect_options` is keyed by owning effect name, e.g.
    {"rain": {"count": 150, "opacity": None}, "ripple": {"amplitude": 5.0}}.
    Values of None are treated as "not supplied" and skipped.
    """
    resolved: dict[str, dict[str, Any]] = {}
    for owning_effect, values in per_effect_options.items():
        for key, value in values.items():
            if value is None:
                continue
            if owning_effect not in effect_names:
                raise ValueError(f"--{owning_effect}-{key} only applies when --effect {owning_effect} is included.")
            effect = effects_pkg.base.get(owning_effect)
            if effect.allowed_kwargs and key not in effect.allowed_kwargs:
                raise ValueError(f"'{key}' is not a valid override for effect '{owning_effect}'.")
            resolved.setdefault(owning_effect, {})[key] = value
    return resolved
