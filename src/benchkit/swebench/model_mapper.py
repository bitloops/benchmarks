from __future__ import annotations

from dataclasses import dataclass
import re

SUPPORTED_ANTHROPIC_FAMILIES = {"opus", "sonnet", "haiku"}
_ANTHROPIC_CANONICAL_PATTERN = re.compile(
    r"^(?P<family>[a-z0-9]+)-(?P<major>\d+)-(?P<minor>\d+)(?P<suffix>(?:-[a-z0-9]+)*)$"
)


@dataclass(slots=True)
class ModelResolution:
    canonical_name: str
    resolved_name: str
    agent_id: str
    map_key: str | None
    source: str


def resolve_model_name(
    canonical_name: str,
    agent_id: str,
    model_map: dict[str, dict[str, str]],
    strict: bool = True,
) -> ModelResolution:
    canonical = canonical_name.strip()
    agent = agent_id.strip().lower()

    for map_key in (agent, "default", "*"):
        entries = model_map.get(map_key)
        if not entries:
            continue

        if canonical in entries:
            resolution = ModelResolution(
                canonical_name=canonical,
                resolved_name=entries[canonical],
                agent_id=agent,
                map_key=map_key,
                source="config:model_map",
            )
            if strict:
                _validate_model_resolution(resolution)
            return resolution

        lower_lookup = {key.lower(): value for key, value in entries.items()}
        candidate = lower_lookup.get(canonical.lower())
        if candidate:
            resolution = ModelResolution(
                canonical_name=canonical,
                resolved_name=candidate,
                agent_id=agent,
                map_key=map_key,
                source="config:model_map_casefold",
            )
            if strict:
                _validate_model_resolution(resolution)
            return resolution

        normalized_lookup = {
            _normalize_model_token(key): value
            for key, value in entries.items()
            if key.strip()
        }
        candidate = normalized_lookup.get(_normalize_model_token(canonical))
        if candidate:
            resolution = ModelResolution(
                canonical_name=canonical,
                resolved_name=candidate,
                agent_id=agent,
                map_key=map_key,
                source="config:model_map_normalized",
            )
            if strict:
                _validate_model_resolution(resolution)
            return resolution

    resolution = ModelResolution(
        canonical_name=canonical,
        resolved_name=canonical,
        agent_id=agent,
        map_key=None,
        source="identity",
    )
    if strict:
        _validate_model_resolution(resolution)
    return resolution


def suggest_model_id_for_agent(canonical_name: str, agent_id: str) -> str | None:
    normalized_canonical = _normalize_canonical_model_name(canonical_name)
    return _expected_agent_model_id(
        canonical_model_name=normalized_canonical,
        agent_id=agent_id.strip().lower(),
    )


def _validate_model_resolution(resolution: ModelResolution) -> None:
    expected_model_id = suggest_model_id_for_agent(
        canonical_name=resolution.canonical_name,
        agent_id=resolution.agent_id,
    )
    if not expected_model_id:
        return

    resolved_normalized = _normalize_model_token(resolution.resolved_name)
    expected_normalized = _normalize_model_token(expected_model_id)
    if resolved_normalized == expected_normalized:
        return

    raise ValueError(
        "Model mapping mismatch for agent "
        f"'{resolution.agent_id}': canonical '{resolution.canonical_name}' "
        f"requires '{expected_model_id}', but resolved to "
        f"'{resolution.resolved_name}' ({resolution.source}). "
        f"Set [model_map.{resolution.agent_id}] "
        f"\"{resolution.canonical_name}\" = \"{expected_model_id}\"."
    )


def _normalize_model_token(raw: str) -> str:
    cleaned = raw.strip().lower().replace(".", "-").replace("_", "-")
    return re.sub(r"-{2,}", "-", cleaned)


def _normalize_canonical_model_name(raw: str) -> str:
    cleaned = _normalize_model_token(raw)
    return cleaned.removeprefix("claude-")


def _expected_agent_model_id(canonical_model_name: str, agent_id: str) -> str | None:
    match = _ANTHROPIC_CANONICAL_PATTERN.match(canonical_model_name)
    if not match:
        return None

    family = match.group("family")
    if family not in SUPPORTED_ANTHROPIC_FAMILIES:
        return None

    major = match.group("major")
    minor = match.group("minor")
    suffix = match.group("suffix")

    if agent_id == "claude_code":
        return f"claude-{family}-{major}-{minor}{suffix}"
    if agent_id == "cursor":
        return f"{family}-{major}.{minor}{suffix}"
    return None
