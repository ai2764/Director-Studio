from __future__ import annotations

import os
import stat

import pytest

from app.core.config_store import envfile

SAMPLE = """# header comment
DS_A=1
# DS_B=commented
DS_B=first
DS_C="quoted value"  
export DS_B=second
DS_ZH=中文
"""


def test_parse_last_occurrence_wins_and_quotes():
    values = envfile.parse(SAMPLE)
    assert values["DS_B"] == "second"
    assert values["DS_C"] == "quoted value"
    assert values["DS_ZH"] == "中文"


def test_apply_changes_preserves_comments_and_order():
    out = envfile.apply_changes(SAMPLE, {"DS_B": "third", "DS_NEW": "x y", "DS_A": None})
    lines = out.splitlines()
    assert lines[0] == "# header comment"
    assert "# DS_B=commented" in lines
    assert "export DS_B=third" in lines
    assert any(l.startswith("# (superseded by Director Studio Settings) DS_B=first") for l in lines)
    assert "DS_A=1" not in lines
    assert lines[-2] == envfile.MANAGED_MARKER
    assert lines[-1] == "DS_NEW='x y'"
    assert envfile.parse(out)["DS_NEW"] == "x y"
    assert envfile.parse(out)["DS_ZH"] == "中文"


@pytest.mark.parametrize("value", ["plain", "with space", "has$dollar", 'json {"a": "b"}', "it's", "a\\b", ""])
def test_values_round_trip_through_python_dotenv(tmp_path, value):
    from dotenv import dotenv_values

    text = envfile.apply_changes("", {"DS_X": value})
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    assert dotenv_values(path)["DS_X"] == value
    assert envfile.parse(text)["DS_X"] == value


def test_rejects_newlines_and_bad_keys():
    with pytest.raises(envfile.EnvFileError):
        envfile.apply_changes("", {"DS_X": "a\nb"})
    with pytest.raises(envfile.EnvFileError):
        envfile.apply_changes("", {"BAD-KEY": "a"})


def test_crlf_preserved():
    out = envfile.apply_changes("DS_A=1\r\nDS_B=2\r\n", {"DS_A": "3"})
    assert out == "DS_A=3\r\nDS_B=2\r\n"


def test_atomic_write_backup_and_permissions(tmp_path):
    path = tmp_path / ".env"
    path.write_text("DS_A=1\n", encoding="utf-8")
    with envfile.file_lock(path):
        envfile.atomic_write(path, "DS_A=2\n")
    assert path.read_text() == "DS_A=2\n"
    assert (tmp_path / ".env.bak").read_text() == "DS_A=1\n"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / ".env.bak").stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".env.tmp-*"))


def test_revision_changes_with_content():
    assert envfile.revision_of("a") != envfile.revision_of("b")
    assert envfile.revision_of(None) == "missing"
