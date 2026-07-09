import subprocess

import pytest

from waft import service
from waft import venv as venv_mod
from waft.scaffold import init_project


@pytest.fixture
def project(tmp_path):
    return init_project(tmp_path, "16.0")


@pytest.fixture
def fake_systemctl(monkeypatch):
    state = {"returncodes": [], "calls": [], "raise_for": set()}

    def fake_run(cmd, check=False, **kwargs):
        cmd = [str(part) for part in cmd]
        state["calls"].append(cmd)
        if cmd[0] in state["raise_for"]:
            raise FileNotFoundError(cmd[0])
        rc = state["returncodes"].pop(0) if state["returncodes"] else 0
        return subprocess.CompletedProcess(cmd, rc)

    monkeypatch.setattr(venv_mod.subprocess, "run", fake_run)
    return state


def test_start_first_attempt(project, fake_systemctl, capsys):
    assert service.manage(project, "start") == 0
    assert fake_systemctl["calls"] == [["systemctl", "start", "odoo"]]
    assert "start OK" in capsys.readouterr().out


def test_falls_back_to_sudo(project, fake_systemctl, capsys):
    fake_systemctl["returncodes"] = [1, 0]
    assert service.manage(project, "restart") == 0
    assert fake_systemctl["calls"] == [
        ["systemctl", "restart", "odoo"],
        ["sudo", "-n", "systemctl", "restart", "odoo"],
    ]


def test_help_when_all_fail(project, fake_systemctl, capsys):
    fake_systemctl["returncodes"] = [1, 1]
    assert service.manage(project, "stop") == 1
    out = capsys.readouterr().out
    assert "/etc/systemd/system/odoo.service" in out
    assert "sudoers" in out
    assert str(project.template_dir / "odoo.service") in out
    assert "waft service stop" in out


def test_help_when_systemctl_missing(project, fake_systemctl, capsys):
    fake_systemctl["raise_for"] = {"systemctl", "sudo"}
    assert service.manage(project, "start") == 1
    assert "/etc/systemd/system" in capsys.readouterr().out


def test_unit_name_from_config(project, fake_systemctl):
    project.shared_yml.write_text(
        "ODOO_VERSION: '16.0'\nWAFT_SERVICE_NAME: odoo-acme\n"
    )
    assert service.manage(project, "start") == 0
    assert fake_systemctl["calls"] == [["systemctl", "start", "odoo-acme"]]
