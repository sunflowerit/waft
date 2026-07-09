import subprocess

import pytest

from waft import source
from waft import venv as venv_mod
from waft.config import load_config
from waft.project import WaftError
from waft.scaffold import init_project


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def fake_run(cmd, check=True, **kwargs):
        recorded.append([str(part) for part in cmd])
        return subprocess.CompletedProcess(cmd, 0, stdout="abc123\n")

    monkeypatch.setattr(venv_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(source.subprocess, "run", fake_run)
    return recorded


@pytest.fixture
def have_uv(monkeypatch):
    monkeypatch.setattr(venv_mod.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_clone_when_missing(tmp_path, calls, have_uv):
    project = init_project(tmp_path, "16.0")
    cfg = load_config(project)
    source.ensure_odoo_source(project, cfg)
    assert calls[0][:2] == ["git", "clone"]
    assert "--branch" in calls[0] and "16.0" in calls[0]
    assert "https://github.com/odoo/odoo.git" in calls[0]
    assert str(source.odoo_source_dir(project)) in calls[0]


def test_pull_when_present(tmp_path, calls):
    project = init_project(tmp_path, "16.0")
    (source.odoo_source_dir(project) / ".git").mkdir(parents=True)
    source.ensure_odoo_source(project, load_config(project))
    assert calls[0][:3] == ["git", "-C", str(source.odoo_source_dir(project))]
    assert "pull" in calls[0]


def test_install_missing_source(tmp_path, calls):
    project = init_project(tmp_path, "16.0")
    with pytest.raises(WaftError, match="Odoo source not found"):
        source.install_odoo(project, load_config(project))


@pytest.mark.parametrize(
    "version,editable,no_deps",
    [
        ("8.0", False, True),
        ("12.0", False, False),
        ("16.0", True, True),
    ],
)
def test_install_strategy(tmp_path, calls, have_uv, version, editable, no_deps):
    project = init_project(tmp_path, version)
    source.odoo_source_dir(project).mkdir()
    assert source.install_odoo(project, load_config(project)) is True
    pip_call = calls[-1]
    assert ("-e" in pip_call) == editable
    assert ("--no-deps" in pip_call) == no_deps
    assert str(source.odoo_source_dir(project)) in pip_call
    # marker written for idempotence
    assert (project.data_dir / "odoo-installed").read_text().strip() == "abc123"


def test_install_skips_when_commit_unchanged(tmp_path, calls, have_uv):
    project = init_project(tmp_path, "16.0")
    source.odoo_source_dir(project).mkdir()
    (project.data_dir / "odoo-installed").write_text("abc123\n")
    assert source.install_odoo(project, load_config(project)) is False
    # only the rev-parse call, no pip install
    assert len(calls) == 1 and "rev-parse" in calls[0]
