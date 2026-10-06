"""One authoring repair per shot, distinct from internal creative prompt retries."""
from __future__ import annotations

from ...core.projects.models import Shot


class ConfigurationRecovery:
    def __init__(self):
        self.attempted: set[str] = set()
        self.pending: dict[str, dict] = {}

    @staticmethod
    def stop_for_scope(result: dict) -> None:
        result.update(retryable=False, concludes_turn=True,
                      reply=f"{result['error']}. This turn is scoped to prompt work. "
                            "Correct the saved parameters in a normal Director turn or the shot editor.")

    @staticmethod
    def target(shots: list[Shot], args: dict) -> Shot | None:
        from .intent import resolve_shot
        shot = resolve_shot(shots, shot_id=args.get("shot_id"),
                            shot_index=args.get("shot_index") or args.get("index"),
                            title=args.get("title"))
        return shot or (shots[0] if len(shots) == 1 else None)

    @staticmethod
    def values(shot: Shot, fields) -> dict:
        data = shot.model_dump(mode="json")
        return {field: data["voice_refs" if field == "voice_matches" else field]
                for field in fields}

    def before_write(self, shot: Shot) -> dict | None:
        previous = self.pending.get(shot.id)
        if previous is None:
            return None
        current = self.values(shot, previous)
        if all(current[field] != previous[field] for field in previous):
            self.pending.pop(shot.id)
            return None
        return {"ok": False, "code": "SHOT_CONFIGURATION_CONFLICT", "shot_id": shot.id,
                "retryable": True, "concludes_turn": False,
                "error": "Use revise_shot to correct the reported parameter before retrying write_prompt. "
                         "No new prompt attempt was made."}

    def record(self, shot: Shot, result: dict) -> bool:
        fields = {issue.get("field") for issue in result.get("issues", [])}
        if shot.id in self.attempted or not fields or not fields <= {"duration_s", "voice_matches"}:
            result.update(retryable=False, concludes_turn=True)
            return False
        self.attempted.add(shot.id)
        self.pending[shot.id] = self.values(shot, fields)
        result.update(retryable=True, concludes_turn=False)
        return True
