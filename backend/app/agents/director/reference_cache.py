"""Observation cache identity and diagnostics, independent of shot presentation."""
import hashlib
import json
import logging
import uuid

from .reference_facts import validate_observation_sources

logger = logging.getLogger(__name__)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class ObservationCache:
    def __init__(self, directory, identity):
        legacy = {**identity, "record": dict(identity["record"])}
        canonical = {**identity, "version": 6, "record": dict(identity["record"])}
        canonical["record"].pop("filename", None)
        sources = canonical["record"].get("sources", [])
        # Citation IDs can change without changing the evidence. Every hit still
        # validates and, only for a unique verbatim quote, rebinds current IDs.
        canonical["record"]["sources"] = [
            {k: v for k, v in source.items() if k not in {"id", "source_message_id"}}
            for source in sources]
        self.path = directory / f"{_digest(canonical)}.json"
        # Existing exact v5 observations remain usable after this upgrade.
        legacy_key = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
        self.legacy_path = directory / f"{legacy_key}.json"
        record = canonical["record"]
        self.dimensions = {
            "image": record.get("content_sha256"),
            "metadata": _digest({k: v for k, v in record.items() if k not in {"sources", "content_sha256"}}),
            "source_evidence": _digest(record["sources"]),
            "inspection_request": _digest(identity["inspection_request"]),
            "model": _digest({k: identity[k] for k in ("model", "provider", "endpoint")}),
            "policy": identity["fact_policy"],
        }
        self.metadata_path = directory / "cache_metadata" / f"{_digest([record['asset_id'], record.get('file_key'), record.get('role')])}.json"

    def load(self, observation_type, sources):
        selected = self.path if self.path.exists() else self.legacy_path
        try:
            observation = observation_type.model_validate_json(selected.read_text(encoding="utf-8"))
            validate_observation_sources(observation, sources)
            if observation.readable:
                return observation, "hit"
            return None, "cached_image_unreadable"
        except FileNotFoundError:
            return None, self.miss_reason()
        except (OSError, ValueError):
            return None, "cached_evidence_invalid"

    def miss_reason(self):
        try:
            previous = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "not_cached"
        if not isinstance(previous, dict):
            return "cache_metadata_invalid"
        for dimension, value in self.dimensions.items():
            if previous.get(dimension) != value:
                return f"{dimension}_changed"
        return "cache_file_missing"

    def save(self, observation):
        try:
            _write(self.path, observation.model_dump_json())
            _write(self.metadata_path, json.dumps(self.dimensions))
        except OSError:
            logger.exception("Could not cache visual observation")
