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


def test_ensure_venv_python2_builds_with_pyenv(tmp_path, calls, monkeypatch):
    """No apt package: waft builds the interpreter with pyenv."""
    project = init_project(tmp_path, "8.0")
    root = tmp_path / "pyenv"
    monkeypatch.setenv("PYENV_ROOT", str(root))
    monkeypatch.setattr(
        venv_mod.shutil,
        "which",
        lambda name: "/usr/bin/uv" if name in ("uv", "git") else None,
    )

    def fake_run(cmd, check=True, **kwargs):
        cmd = [str(part) for part in cmd]
        calls.append(cmd)
        if "install" in cmd and cmd[-1] == "2.7.18":
            built = root / "versions" / "2.7.18" / "bin"
            built.mkdir(parents=True)
            (built / "python2.7").touch()
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(venv_mod.subprocess, "run", fake_run)
    assert venv_mod.ensure_venv(project) is True
    assert any(cmd[:2] == ["git", "clone"] for cmd in calls)  # pyenv cloned
    assert any(cmd[-3:] == ["install", "-s", "2.7.18"] for cmd in calls)
    virtualenv = next(cmd for cmd in calls if "virtualenv" in " ".join(cmd))
    assert str(root / "versions" / "2.7.18" / "bin" / "python2.7") in virtualenv


def test_ensure_venv_python2_unavailable(tmp_path, calls, monkeypatch):
    project = init_project(tmp_path, "8.0")
    monkeypatch.setenv("PYENV_ROOT", str(tmp_path / "pyenv"))
    monkeypatch.setattr(
        venv_mod.shutil,
        "which",
        lambda name: "/usr/bin/uv" if name in ("uv", "git") else None,
    )

    def failing_run(cmd, check=True, **kwargs):
        cmd = [str(part) for part in cmd]
        calls.append(cmd)
        code = 1 if "install" in cmd else 0  # the pyenv build fails
        return subprocess.CompletedProcess(cmd, code, stdout="")

    monkeypatch.setattr(venv_mod.subprocess, "run", failing_run)
    with pytest.raises(WaftError, match="the pyenv build failed") as exc:
        venv_mod.ensure_venv(project)
    assert "OpenSSL 3" in str(exc.value)  # the 2.7 specific hint
    assert "WAFT_PYTHON=" in str(exc.value)


def test_waft_python_override(tmp_path, calls, have_uv):
    project = init_project(tmp_path, "8.0")
    interpreter = tmp_path / "custom" / "python2.7"
    interpreter.parent.mkdir()
    interpreter.touch()
    from waft import config

    config.config_set(project, f"WAFT_PYTHON={interpreter}")
    assert venv_mod.ensure_venv(project) is True
    assert str(interpreter) in calls[0]


def test_waft_python_override_missing(tmp_path, calls, have_uv):
    project = init_project(tmp_path, "8.0")
    from waft import config

    config.config_set(project, "WAFT_PYTHON=/nope/python2.7")
    with pytest.raises(WaftError, match="which does not exist"):
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
    odoo_requirements = project.odoo_dir / "requirements.txt"
    odoo_requirements.parent.mkdir(parents=True)
    odoo_requirements.write_text("Babel==2.9.1\n")
    venv_mod.update_requirements(project)
    # setuptools pin, then Odoo's own dependencies, then the project's
    assert "setuptools>=64,<82" in calls[0]
    assert calls[1][-2:] == ["-r", str(odoo_requirements)]
    assert calls[-1][-2:] == ["-r", str(project.requirements_txt)]


def test_pip_retries_after_installing_build_deps(project, monkeypatch, capsys):
    """A C-extension build failure triggers apt, then one retry."""
    _fake_venv(project)
    attempts = []
    monkeypatch.setattr(venv_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(venv_mod.os, "geteuid", lambda: 0, raising=False)

    def fake_run(cmd, check=True, **kwargs):
        cmd = [str(part) for part in cmd]
        attempts.append(cmd)
        pip_installs = [c for c in attempts if "pip" in c and "install" in c]
        if "pip" in cmd and len(pip_installs) == 1:  # first pip attempt fails
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(venv_mod.subprocess, "run", fake_run)
    from waft.config import load_config

    venv_mod.pip_with_build_deps(project, load_config(project), ["install", "x"])
    assert any("apt-get" in call and "install" in call for call in attempts)
    assert any("libldap2-dev" in call for call in attempts)
    assert len([c for c in attempts if "pip" in c and "install" in c]) == 2


def test_pip_reraises_when_apt_unavailable(project, monkeypatch):
    _fake_venv(project)
    monkeypatch.setattr(
        venv_mod.shutil,
        "which",
        lambda name: "/usr/bin/uv" if name == "uv" else None,  # no apt-get
    )

    def failing(cmd, check=True, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(venv_mod.subprocess, "run", failing)
    from waft.config import load_config

    with pytest.raises(WaftError, match="command failed"):
        venv_mod.pip_with_build_deps(project, load_config(project), ["install", "x"])


def test_update_requirements_without_checkout(project, calls, have_uv, capsys):
    """Odoo's dependencies come from its checkout; warn when it is absent."""
    _fake_venv(project)
    venv_mod.update_requirements(project)
    assert not any("addons/odoo/requirements.txt" in " ".join(c) for c in calls)
    assert "not found" in capsys.readouterr().out


def test_update_requirements_no_setuptools_pin_for_19(tmp_path, calls, have_uv):
    project = init_project(tmp_path, "19.0")
    _fake_venv(project)
    venv_mod.update_requirements(project)
    assert not any("setuptools" in part for call in calls for part in call)
