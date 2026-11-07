"""Explicit continuity is an authoring constraint, never a model guess."""
import re
from ..core.projects.models import ShotVideoContext
from ..core.projects.video_context import _require_previous_video, configure_video_context, VideoContextError


def explicitly_continues(description: str) -> bool:
    # Negated/contradictory instructions must not silently enable continuation.
    if re.search(r"(?:不要|不需|无需|不|别).{0,8}(?:接续|接上|延续|衔接)|(?:do not|don't|never|no)\s+continu|hard cut|独立镜头|切镜", description, re.I):
        return False
    return bool(re.search(
        r"(?:接上|接续|延续|衔接)(?:上一镜|前一镜|上一个镜头)|"
        r"continu(?:e|ing|ation)\s+(?:(?:from|of|with)\s+)?(?:the\s+)?(?:previous|preceding|last)\s+(?:shot|take|clip)|"
        r"(?:without a cut|unbroken camera movement|一镜到底)", description, re.I))


def apply_explicit_continuation(project, shot, description: str):
    if not explicitly_continues(description):
        return shot
    index = project.shot_ids.index(shot.id)
    if index == 0:
        raise VideoContextError("Explicit continuation requires a previous shot.")
    # No stale manually pinned source: select the adjacent predecessor's current take.
    config = ShotVideoContext(mode="previous_shot", source_shot_id=project.shot_ids[index-1])
    source, job, key, _path = _require_previous_video(project, shot, config)
    config = config.model_copy(update={"source_shot_id": source.id, "source_job_id": job.id, "source_output_key": key})
    configure_video_context(project.id, shot.id, config)
    from ..core.projects.store import load_shot
    return load_shot(project.id, shot.id)
