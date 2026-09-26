"""Pure task selection; omissions are explicit, never silent truncation."""
import json
from .task_context_models import TaskPacket


class ContextRequired(ValueError):
    code = "CONTEXT_REQUIRED"

    def __init__(self, packet):
        self.packet = packet
        super().__init__("CONTEXT_REQUIRED: required evidence is missing or exceeds the available input budget")


def packet_fits(packet, max_chars):
    return len(json.dumps(packet.model_dump(mode="json"), ensure_ascii=False)) <= max_chars


def require_complete(packet):
    if not packet.complete:
        raise ContextRequired(packet)
    return packet


def build_task_packet(snapshot, request, *, authority, max_chars, extra_sources=()):
    pid = snapshot.project_id
    keys = {f"project:{pid}", f"script:{pid}"}
    common = snapshot.sources[f"project:{pid}"].payload
    facts = {"project": {k: v for k, v in common.items() if k != "requirements"},
        "requirements": common["requirements"], "script": snapshot.sources[f"script:{pid}"].payload["text"],
        "target": None, "continuity": [], "dialogue_sources": [], "references": [],
        "voice_refs": [], "source_audio": None, "existing_prompt": None, "revision_history": []}
    missing = []
    if request.kind == "shot_prompt":
        target = snapshot.shots.get(request.target_shot_id)
        if target is None:
            missing.append({"code": "CONTEXT_TARGET_MISSING", "source_key": f"shot:{request.target_shot_id}"})
        else:
            keys.add(f"shot:{request.target_shot_id}")
            keys.update(target["source_keys"])
            facts["target"] = snapshot.sources[f"shot:{request.target_shot_id}"].payload
            for name in ("dialogue_sources", "references", "voice_refs", "source_audio"):
                facts[name] = target[name]
            facts["existing_prompt"] = target["prompt_sections"]
            facts["revision_history"] = target["prompt_revision_requests"]
            missing.extend(target["missing"])
            ids = list(snapshot.shots)
            index = ids.index(request.target_shot_id)
            for neighbor_id in ids[max(0, index - 1):index] + ids[index + 1:index + 2]:
                neighbor = snapshot.shots[neighbor_id]
                facts["continuity"].append({"kind": "adjacent_summary_not_dependency_edge",
                    **{k: neighbor.get(k) for k in ("id", "title", "scene_id", "script_beat", "dialogue", "camera_motion")}})
                keys.add(f"shot:{neighbor_id}")
            for layout in target["selected_layouts"]:
                if layout.get("origin_kind") == "clip_tail_frame":
                    facts["continuity"].append({"kind": "selected_tail", **layout})
            for line in target["dialogue_sources"]:
                source_id = line.get("source", {}).get("source_id")
                if source_id and f"message:{source_id}" in snapshot.sources:
                    keys.add(f"message:{source_id}")
    if extra_sources:
        facts["queried_sources"] = {}
        for key in extra_sources:
            if key.startswith("catalog:"):
                continue
            record = snapshot.sources.get(key)
            if record is None:
                missing.append({"code": "CONTEXT_SOURCE_MISSING", "source_key": key})
            else:
                keys.add(key)
                facts["queried_sources"][key] = record.model_dump(mode="json")
    packet = TaskPacket(project_id=pid, task=request, authority=authority, facts=facts,
        source_versions={k: snapshot.sources[k].version for k in sorted(keys) if k in snapshot.sources},
        available_context=[{"source_key": "catalog:shots", "count": len(snapshot.shots)},
            {"source_key": "catalog:messages", "count": len(snapshot.sources["catalog:messages"].payload["items"])},
            {"source_key": f"script:{pid}", "chars": len(facts["script"])}],
        omitted=[{"kind": "unselected_shot_details", "read": "catalog:shots"}],
        missing=missing, complete=not missing)
    if not packet_fits(packet, max_chars):
        size = len(packet.model_dump_json())
        packet.facts = {}
        packet.complete = False
        packet.missing = [{"code": "CONTEXT_BUDGET_EXCEEDED", "source_key": f"script:{pid}",
            "version": snapshot.sources[f"script:{pid}"].version, "required_chars": size,
            "available_chars": max_chars, "read": {"source_key": f"script:{pid}"},
            "action": "Narrow the task or increase context capacity; paging does not enlarge writer capacity."}]
    return packet
