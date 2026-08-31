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


def test_ensure_venv_python2_installs_from_apt(tmp_path, calls, monkeypatch):
    """A missing old interpreter is installed into the system environment."""
    project = init_project(tmp_path, "8.0")
    present = {"uv", "apt-get", "sudo", "add-apt-repository"}

    def fake_which(name):
        if name in present:
            return f"/usr/bin/{name}"
        return None

    def fake_run(cmd, check=True, **kwargs):
        cmd = [str(part) for part in cmd]
        calls.append(cmd)
        if "install" in cmd and "python2.7" in cmd:
            present.add("python2.7")  # apt made it available
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(venv_mod.shutil, "which", fake_which)
    monkeypatch.setattr(venv_mod.subprocess, "run", fake_run)
    assert venv_mod.ensure_venv(project) is True
    assert ["sudo", "apt-get", "update"] in calls
    assert any("python2.7-dev" in call for call in calls)
    assert any("virtualenv" in " ".join(call) for call in calls)


def test_ensure_venv_python2_unavailable(tmp_path, calls, monkeypatch):
    project = init_project(tmp_path, "8.0")
    monkeypatch.setattr(
        venv_mod.shutil,
        "which",
        lambda name: "/usr/bin/uv" if name == "uv" else None,
    )
    with pytest.raises(WaftError, match="could not be installed automatically"):
        venv_mod.ensure_venv(project)


def test_uv_prefers_path(monkeypatch):
    monkeypatch.setattr(venv_mod.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(venv_mod, "_bundled_uv", lambda: "/bundled/uv")
    assert venv_mod.uv_binary() == "/usr/bin/uv"


def test_uv_falls_back_to_bundled(monkeypatch):
    monkeypatch.setattr(venv_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(venv_mod, "_bundled_uv", lambda: "/bundled/uv")
    assert venv_mod.uv_binary() == "/bundled/uv"


def test_uv_installed_when_missing(monkeypatch):
    monkeypatch.setattr(venv_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(venv_mod, "_bundled_uv", lambda: None)
    monkeypatch.setattr(venv_mod, "install_uv", lambda: "/installed/uv")
    assert venv_mod.uv_binary() == "/installed/uv"


def test_install_uv_uses_pipx(monkeypatch, calls):
    installed = {}

    def fake_which(name):
        if name == "pipx":
            return "/usr/bin/pipx"
        if name == "uv":
            return "/usr/bin/uv" if installed else None
        return None

    def fake_run(cmd, check=True, **kwargs):
        cmd = [str(part) for part in cmd]
        calls.append(cmd)
        if cmd[:2] == ["/usr/bin/pipx", "install"] or cmd[:2] == ["pipx", "install"]:
            installed["uv"] = True
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(venv_mod.shutil, "which", fake_which)
    monkeypatch.setattr(venv_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(venv_mod, "_bundled_uv", lambda: None)
    assert venv_mod.install_uv() == "/usr/bin/uv"
    assert ["pipx", "install", "uv"] in calls


def test_uv_error_when_uninstallable(monkeypatch):
    monkeypatch.setattr(venv_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(venv_mod, "_bundled_uv", lambda: None)
    monkeypatch.setattr(venv_mod, "install_uv", lambda: None)
    with pytest.raises(WaftError, match="could not be installed automatically"):
        venv_mod.uv_binary()


def test_apt_install_without_sudo(monkeypatch):
    monkeypatch.setattr(
        venv_mod.shutil,
        "which",
        lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
    )
    monkeypatch.setattr(venv_mod.os, "geteuid", lambda: 1000, raising=False)
    assert venv_mod.apt_install(["python2.7"]) is False


def test_ensure_venv_skips_existing(project, calls):
    _fake_venv(project)
    assert venv_mod.ensure_venv(project) is False
    assert calls == []


def test_uv_missing_is_clear_error(project, calls, monkeypatch):
    monkeypatch.setattr(venv_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(venv_mod, "_bundled_uv", lambda: None)
    monkeypatch.setattr(venv_mod, "install_uv", lambda: None)
    with pytest.raises(WaftError, match="could not be installed automatically"):
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
