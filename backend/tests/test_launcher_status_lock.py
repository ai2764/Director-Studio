from types import SimpleNamespace

from app import launcher


def test_status_sees_lock_holder_even_without_state(tmp_path):
    cfg = SimpleNamespace(run_dir=tmp_path)
    assert not launcher.instance_running(cfg)
    holder = launcher.InstanceLock(tmp_path / "launcher.lock")
    assert holder.acquire()
    try:
        assert launcher.instance_running(cfg)
        (tmp_path / "launcher.json").write_text("{", encoding="utf-8")  # torn write
        assert launcher.read_state(cfg, wait=0.3) is None
    finally:
        holder.release()
    assert not launcher.instance_running(cfg)
