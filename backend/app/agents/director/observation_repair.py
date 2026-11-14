"""Repair representation errors without taking a second look at the pixels."""
import copy
import re


def normalize_observation(payload):
    payload = copy.deepcopy(payload)
    if not isinstance(payload, dict):
        return payload
    conflicts = payload.get("conflicts", [])
    for conflict in conflicts if isinstance(conflicts, list) else []:
        if isinstance(conflict, dict) and "quote" not in conflict:
            aliases = [key for key in ("description_quote", "description") if key in conflict]
            if len(aliases) == 1:
                conflict["quote"] = conflict.pop(aliases[0])
    facts = payload.get("facts", [])
    for fact in facts if isinstance(facts, list) else []:
        if isinstance(fact, dict) and fact.get("visibility") in {"not_visible", "uncertain"}:
            fact["value"] = None
    return payload


def _existing_evidence(value, description, original, current):
    if value in description:
        return True
    # A not-visible metadata fact may cite its existing source, but may never
    # use that metadata as evidence of an observed appearance.
    return (original.get("visibility") in {"not_visible", "uncertain"}
            and original.get("source_id") and current.get("source_id")
            and value in (original.get("source_quote") or "")
            and value in (current.get("source_quote") or ""))


def apply_representation_repair(original, patch):
    """Only evidence/citation pointers are editable, never visual assertions."""
    if not isinstance(patch, dict) or set(patch) != {"repairs"}:
        raise ValueError("Observation repair must return only a repairs array")
    edits = patch["repairs"]
    if not isinstance(edits, list) or not 1 <= len(edits) <= 16:
        raise ValueError("Observation repair requires 1–16 field patches")
    result = normalize_observation(original)
    seen = set()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"path", "value"}:
            raise ValueError("Observation repair patch requires path and value")
        path, value = edit["path"], edit["value"]
        match = re.fullmatch(r"(facts|conflicts)\[(0|[1-9][0-9]*)\]\.(value|evidence|source_id|source_quote|quote)", path) if isinstance(path, str) else None
        if not match or path in seen or not isinstance(value, str) or not value.strip():
            raise ValueError("Invalid or duplicate observation repair path/value")
        seen.add(path)
        group, index, field = match.group(1), int(match.group(2)), match.group(3)
        items = result.get(group)
        if not isinstance(items, list) or index >= len(items) or not isinstance(items[index], dict):
            raise ValueError("Observation repair row is outside the original candidate")
        item = items[index]
        if group == "conflicts":
            allowed = field == "quote" and value in result.get("description", "")
        elif field == "evidence":
            allowed = (item.get("evidence") == value or
                       (not item.get("evidence") and _existing_evidence(value, result.get("description", ""), item, item)))
        elif field == "value":
            allowed = (item.get("value") == value or
                       (not item.get("value") and item.get("visibility") == "observed"
                        and (value in (item.get("evidence") or "") or value in result.get("description", ""))))
        else:
            allowed = field in {"source_id", "source_quote"} and item.get("source_id") and item.get("source_quote")
        if not allowed:
            raise ValueError(f"Observation repair cannot edit {path}: preserve existing evidence; "
                             "new evidence or conflict quotes must copy the candidate description verbatim")
        item[field] = value
    return result


def validate_representation_repair(original, repaired):
    """A text model may fix citations/shape, never supply new visual evidence."""
    original = normalize_observation(original)
    candidate = repaired.model_dump()
    for key in ("readable", "description", "concerns"):
        if original.get(key) != candidate[key]:
            raise ValueError(f"Observation repair changed visual evidence: {key}")
    if original.get("uncertainties", []) != candidate["uncertainties"]:
        raise ValueError("Observation repair changed uncertainty evidence")
    for key in ("facts", "conflicts"):
        before, after = original.get(key, []), candidate[key]
        if not isinstance(before, list) or len(before) != len(after):
            raise ValueError(f"Observation repair added or removed {key}")
        for old, new in zip(before, after, strict=True):
            if not isinstance(old, dict):
                raise ValueError("Observation repair has no original evidence")
            if key == "conflicts":
                if any(old.get(field) != new[field] for field in ("attribute", "reason")):
                    raise ValueError("Observation repair changed a visual dispute")
                # Exact description quote is checked by validate_observation_sources.
                continue
            for field, default in (("attribute", None), ("value", None), ("visibility", None),
                                   ("source_kind", "model_observation")):
                if (field == "value" and not old.get("value") and old.get("visibility") == "observed"
                        and (new[field] in (old.get("evidence") or "") or new[field] in original["description"])):
                    continue
                if old.get(field, default) != new[field]:
                    raise ValueError(f"Observation repair changed visual fact: {field}")
            evidence = old.get("evidence")
            if evidence:
                if evidence != new["evidence"]:
                    raise ValueError("Observation repair changed fact evidence")
            elif not _existing_evidence(new["evidence"], original["description"], old, new):
                raise ValueError("Missing fact evidence must quote the original description")
            # A correction may rebind an existing citation, but cannot manufacture one.
            if not (old.get("source_id") and old.get("source_quote")):
                if old.get("source_id") != new["source_id"] or old.get("source_quote") != new["source_quote"]:
                    raise ValueError("Observation repair invented source evidence")
