"""Fetching git-based addon repositories into .tmp/repos/.

Entries without merges use plain git (shallow clone / fetch). Entries with
merges keep the old git-aggregator semantics, executed via "uv tool run"
from the git-aggregator package; override the package with the
WAFT_GIT_AGGREGATOR configuration variable (e.g. a git+https fork URL).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from . import config as config_mod
from .project import Project
from .venv import _run, uv_binary


def _branch(entry, cfg: dict[str, str]) -> str:
    return config_mod.substitute(
        entry.branch or "${ODOO_VERSION}",
        cfg,
        f"addon entry '{entry.name}' branch",
    )


def fetch_repo(project: Project, entry, cfg: dict[str, str], dest: Path) -> None:
    """Clone or update one git addon repository."""
    branch = _branch(entry, cfg)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if entry.merges:
        _aggregate(project, entry, branch, cfg, dest)
        return
    depth = entry.depth or cfg.get("WAFT_DEPTH_DEFAULT", "1")
    if (dest / ".git").is_dir():
        _run(["git", "-C", dest, "fetch", "--depth", depth, "origin", branch])
        _run(["git", "-C", dest, "checkout", "--force", "-B", branch, "FETCH_HEAD"])
    else:
        _run(
            [
                "git",
                "clone",
                "--depth",
                depth,
                "--branch",
                branch,
                "--single-branch",
                entry.url,
                dest,
            ]
        )


def _aggregate(
    project: Project, entry, branch: str, cfg: dict[str, str], dest: Path
) -> None:
    """git-aggregator path: multiple merges combined into one working tree."""
    merges = [
        config_mod.substitute(merge, cfg, f"addon entry '{entry.name}' merges")
        for merge in entry.merges
    ]
    depth = entry.depth or cfg.get("WAFT_DEPTH_MERGE", "100")
    conf = {
        str(dest): {
            "remotes": {"origin": entry.url},
            "target": f"origin {branch}",
            "merges": merges,
            "defaults": {"depth": int(depth)},
        }
    }
    project.tmp_dir.mkdir(exist_ok=True)
    conf_file = project.tmp_dir / f"aggregate-{entry.name}.yml"
    conf_file.write_text(
        yaml.safe_dump(conf, default_flow_style=False), encoding="utf-8"
    )
    aggregator = cfg.get("WAFT_GIT_AGGREGATOR", "git-aggregator")
    _run(
        [
            uv_binary(),
            "tool",
            "run",
            "--from",
            aggregator,
            "gitaggregate",
            "-c",
            conf_file,
        ]
    )
