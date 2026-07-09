import subprocess

import pytest

from waft import venv as venv_mod
from waft import versions
from waft.project import WaftError
from waft.scaffold import init_project


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def fake_run(cmd, check=True, **kwargs):
        recorded.append([str(part) for part in cmd])
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(venv_mod.subprocess, "run", fake_run)
    return recorded


@pytest.fixture
def have_uv(monkeypatch):
    monkeypatch.setattr(venv_mod.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.fixture
def project(tmp_path):
    return init_project(tmp_path, "16.0")


def _fake_venv(project):
    venv_mod.venv_python(project).parent.mkdir(parents=True)
    venv_mod.venv_python(project).touch()


def test_python_spec():
    assert venv_mod.python_spec(versions.get("16.0")) == ("3.10", True)
    assert venv_mod.python_spec(versions.get("14.0")) == ("3.8", True)
    assert venv_mod.python_spec(versions.get("19.0")) == ("3.13", True)
    assert venv_mod.python_spec(versions.get("12.0")) == ("python3.6", False)
    assert venv_mod.python_spec(versions.get("8.0")) == ("python2.7", False)


def test_ensure_venv_uses_uv(project, calls, have_uv):
    assert venv_mod.ensure_venv(project) is True
    assert calls == [["/usr/bin/uv", "venv", "--python", "3.10", str(project.venv_dir)]]


def test_ensure_venv_python2_fallback(tmp_path, calls, have_uv):
    project = init_project(tmp_path, "8.0")
    assert venv_mod.ensure_venv(project) is True
    assert calls[0][:6] == [
        "/usr/bin/uv",
        "tool",
        "run",
        "--from",
        venv_mod.VIRTUALENV_PIN,
        "virtualenv",
    ]
    assert "/usr/bin/python2.7" in calls[0]


def test_ensure_venv_python2_missing(tmp_path, calls, monkeypatch):
    project = init_project(tmp_path, "8.0")
    monkeypatch.setattr(
        venv_mod.shutil,
        "which",
        lambda name: "/usr/bin/uv" if name == "uv" else None,
    )
    with pytest.raises(WaftError, match="python2.7 is required"):
        venv_mod.ensure_venv(project)


def test_ensure_venv_skips_existing(project, calls):
    _fake_venv(project)
    assert venv_mod.ensure_venv(project) is False
    assert calls == []


def test_uv_missing_is_clear_error(project, calls, monkeypatch):
    monkeypatch.setattr(venv_mod.shutil, "which", lambda name: None)
    with pytest.raises(WaftError, match="uv is not installed"):
        venv_mod.ensure_venv(project)


def test_pip_requires_venv(project):
    with pytest.raises(WaftError, match="run 'waft sync' first"):
        venv_mod.pip(project, ["install", "requests"])


def test_pip_requires_args(project):
    with pytest.raises(WaftError, match="usage: waft pip"):
        venv_mod.pip(project, [])


def test_pip_routes_via_uv(project, calls, have_uv):
    _fake_venv(project)
    venv_mod.pip(project, ["install", "requests"])
    assert calls == [
        [
            "/usr/bin/uv",
            "pip",
            "install",
            "--python",
            str(venv_mod.venv_python(project)),
            "requests",
        ]
    ]


def test_pip_py2_uses_venv_pip(tmp_path, calls, have_uv):
    project = init_project(tmp_path, "10.0")
    _fake_venv(project)
    venv_mod.pip(project, ["list"])
    assert calls == [[str(project.venv_dir / "bin" / "pip"), "list"]]


def test_update_requirements_sequence(project, calls, have_uv):
    _fake_venv(project)
    venv_mod.update_requirements(project)
    # 16.0: setuptools pin first, project requirements.txt last.
    assert "setuptools>=64,<82" in calls[0]
    assert calls[-1][-2:] == ["-r", str(project.requirements_txt)]


def test_update_requirements_no_setuptools_pin_for_19(tmp_path, calls, have_uv):
    project = init_project(tmp_path, "19.0")
    _fake_venv(project)
    venv_mod.update_requirements(project)
    assert not any("setuptools" in part for call in calls for part in call)
