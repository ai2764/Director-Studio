"""Conservative reuse of backend-validated visual evidence for automatic chat images."""
from __future__ import annotations

import hashlib
import json
import re
from time import monotonic
from ...config import settings

from ...core.projects.dialogue import digest
from ...core.projects.store import load_project, project_dir
from .reference_facts import REFERENCE_POLICY_VERSION, reference_sources, validate_observation_sources


def requests_reinspection(message: str) -> bool:
    return bool(re.search(
        r"重新|再看|再读|再检查|仔细|细节|看清|确认.*(?:图|外观)|"
        r"reinspect|recheck|look\b.*\bagain|inspect\b.*\bagain|closely|detail|zoom",
        message, re.I,
    ))


class VerifiedImages:
    def __init__(self, project_id: str, message: str):
        self.started = monotonic()
        self.project = load_project(project_id)
        self.records = []
        self.reused = []
        if (self.project is None or not settings.director_reuse_verified_images
                or requests_reinspection(message)):
            return
        try:
            payload = json.loads((project_dir(project_id) / "agent" / "reference_facts.json").read_text(encoding="utf-8"))
            if payload.get("version") == REFERENCE_POLICY_VERSION:
                self.records = list(payload["references"].values())
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            pass

    def reuse(self, *, asset, role: str, file_key: str | None, data: bytes,
              label: str, reference_notes: str = "") -> bool:
        from .material_review import ReferenceObservation

        if asset is None or self.project is None:
            return False
        for record in self.records:
            if not isinstance(record, dict):
                continue
            if (record.get("asset_id") != asset.id or record.get("role") != role
                    or not isinstance(record.get("file_key"), str) or not record["file_key"]
                    or (file_key and record.get("file_key") != file_key)
                    or record.get("content_sha256") != hashlib.sha256(data).hexdigest()
                    # Legacy observations inspected pixels plus the supplied
                    # sources, not the screenplay. Those sources are checked
                    # below; do not force another vision pass just to migrate.
                    or record.get("script_signature", digest(self.project.script_text)) != digest(self.project.script_text)
                    or not isinstance(record.get("observation_model"), dict)
                    or record["observation_model"].get("fact_policy") != REFERENCE_POLICY_VERSION
                    or record.get("asset_name") != asset.name
                    or record.get("approved_notes") != asset.notes
                    or record.get("approved_description") != str((asset.meta or {}).get("description") or "")):
                continue
            sources = reference_sources(self.project, record)
            if record.get("sources") != sources:
                continue
            try:
                observation = ReferenceObservation.model_validate({
                    k: record[k] for k in ReferenceObservation.model_fields if k in record})
                validate_observation_sources(observation, sources)
            except (ValueError, TypeError, KeyError):
                continue
            if (not observation.readable or observation.concerns or observation.conflicts or observation.uncertainties
                    or any(fact.visibility == "uncertain" for fact in observation.facts)):
                continue
            self.reused.append({"label": label, "asset_id": asset.id, "file_key": record["file_key"],
                "reference_notes": reference_notes, **observation.model_dump(exclude={"readable"})})
            return True
        return False

    def finish(self, pack):
        if self.reused:
            pack["note"] = (pack.get("note", "") + "\nReused verified visual observations (unchanged image bytes; "
                "original images are not attached for these entries). These are model observations, not user approval. "
                "Use the numbered evidence below for prompt grounding. If the requested detail is absent, "
                "call inspect_asset with the exact asset_id and file_key before asserting it. "
                "Do not claim fresh pixel inspection or infer unseen details:\n"
                + json.dumps(self.reused, ensure_ascii=False))
        pack["reused_image_count"] = len(self.reused)
        from ...core.llm.performance import record_performance
        record_performance({"kind": "image_context", "phase": "image_context", "path": "image_context",
            "image_count": len(pack.get("images_b64", [])), "reused_image_count": len(self.reused),
            "image_base64_chars": sum(len(image) for image in pack.get("images_b64", [])),
            "text_chars": len(pack.get("note", "")), "elapsed_ms": round((monotonic() - self.started) * 1000)})
        return pack
