"""Odoo source checkout and installation.

The Odoo source is cloned into WORK_WAFT_DIRECTORY/odoo (git-ignored) from
ODOO_REPOSITORY (default odoo/odoo; set it to OCB or a mirror in shared.yml)
at the branch named after ODOO_VERSION, then installed into the venv with
the per-version strategy from waft.versions. A marker file with the
installed commit keeps `waft sync` idempotent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import versions
from .project import Project, WaftError
from .venv import _run, run_pip


def odoo_source_dir(project: Project) -> Path:
    return project.odoo_dir


def _marker(project: Project) -> Path:
    return project.data_dir / "odoo-installed"


def ensure_odoo_source(project: Project, cfg: dict[str, str]) -> None:
    """Clone the Odoo source, or fast-forward it when already present."""
    src = odoo_source_dir(project)
    if (src / ".git").is_dir():
        _run(["git", "-C", src, "pull", "--ff-only"])
        return
    src.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "git",
            "clone",
            "--depth",
            cfg.get("WAFT_DEPTH_DEFAULT", "1"),
            "--branch",
            cfg["ODOO_VERSION"],
            "--single-branch",
            cfg["ODOO_REPOSITORY"],
            src,
        ]
    )


def _current_commit(project: Project) -> str:
    result = subprocess.run(
        ["git", "-C", str(odoo_source_dir(project)), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return (result.stdout or "").strip()


def install_odoo(project: Project, cfg: dict[str, str]) -> bool:
    """(Re)install Odoo into the venv; skipped when the checkout is unchanged."""
    src = odoo_source_dir(project)
    if not src.is_dir():
        raise WaftError(f"Odoo source not found at {src}; run 'waft sync'")
    info = versions.get(cfg["ODOO_VERSION"])
    commit = _current_commit(project)
    marker = _marker(project)
    if commit and marker.is_file() and marker.read_text().strip() == commit:
        return False
    if info.install == "pip-no-deps":
        args = ["install", "--no-deps", str(src)]
    elif info.install == "pip":
        args = ["install", str(src)]
    else:  # editable (Odoo 15.0+)
        args = ["install", "--no-deps", "-e", str(src)]
    run_pip(project, cfg, args)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(commit + "\n", encoding="utf-8")
    return True
