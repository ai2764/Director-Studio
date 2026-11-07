from types import SimpleNamespace
import pytest
from app.shot_workspace import continuity_selection as mod

@pytest.mark.parametrize('text', ['接上上一镜，镜头继续前进', 'Continue the previous take without a cut.', 'Continue from the previous shot.', '延续前一镜'])
def test_explicit(text):
    assert mod.explicitly_continues(text)

@pytest.mark.parametrize('text', ['Same room.', '不要接上上一镜', "Don't continue the previous shot.", 'Hard cut, then continue the previous shot.'])
def test_no_guess(text):
    assert not mod.explicitly_continues(text)

def test_first_shot_rejected():
    with pytest.raises(mod.VideoContextError, match='previous shot'):
        mod.apply_explicit_continuation(SimpleNamespace(shot_ids=['s1']), SimpleNamespace(id='s1'), '接上上一镜')

def test_pins_latest_predecessor_not_old_manual_source(monkeypatch):
    project = SimpleNamespace(id='p', shot_ids=['s1','s2','s3'])
    shot = SimpleNamespace(id='s3')
    captured = []
    def require(p,s,config):
        assert config.source_shot_id == 's2'
        assert config.source_job_id is None
        return SimpleNamespace(id='s2'), SimpleNamespace(id='latest-job'), 'video', None
    monkeypatch.setattr(mod, '_require_previous_video', require)
    monkeypatch.setattr(mod, 'configure_video_context', lambda p,s,c: captured.append(c))
    monkeypatch.setattr('app.core.projects.store.load_shot', lambda p,s: shot)
    assert mod.apply_explicit_continuation(project,shot,'接上上一镜') is shot
    assert captured[0].source_job_id == 'latest-job'

def test_missing_video_never_saves(monkeypatch):
    def missing(*args):
        raise mod.VideoContextError('Source shot has no H3 job')
    monkeypatch.setattr(mod,'_require_previous_video',missing)
    monkeypatch.setattr(mod,'configure_video_context',lambda *a: pytest.fail('must not save'))
    with pytest.raises(mod.VideoContextError):
        mod.apply_explicit_continuation(SimpleNamespace(shot_ids=['a','b']),SimpleNamespace(id='b'),'接上上一镜')
