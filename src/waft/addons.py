"""Addon sources, resolver and addons/ linking.

Addon configuration lives under the ADDONS key of .waft/conf/shared.yml
(secret.yml can override an entry, or disable one by setting it to null).
Entry kinds:

.. code-block:: yaml

    ADDONS:
      oca-web:                       # entry name (repo alias or addon name)
        kind: git
        url: https://github.com/OCA/web.git
        branch: ${ODOO_VERSION}      # default; any config var can be used
        merges:                      # optional; git-aggregator semantics
          - origin ${ODOO_VERSION}
          - origin refs/pull/1234/head
        depth: 1                     # optional; WAFT_DEPTH_* defaults
        addons: ["web_responsive"]   # globs of addons to link; default ["*"]
      queue-job:
        kind: pypi
        spec: odoo-addon-queue_job==16.0.*
      partner-custom:
        kind: link
        path: ../dev/partner-custom  # relative to the project root

Plain addon directories inside addons/ need no configuration at all.
`waft sync` fetches git repos into .tmp/repos/, installs pypi addons into
the venv, and links everything into addons/ (the single addons_path).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import aggregate
from . import config as config_mod
from . import venv as venv_mod
from .project import Project, WaftError

MANIFESTS = ("__manifest__.py", "__openerp__.py")
KINDS = ("git", "pypi", "link", "directory")


@dataclass
class AddonEntry:
    """One configured addon source."""

    name: str
    kind: str = "directory"
    url: str = ""
    branch: str = ""
    merges: list[str] = field(default_factory=list)
    depth: str = ""
    addons: list[str] = field(default_factory=lambda: ["*"])
    spec: str = ""
    path: str = ""

    def to_yaml(self) -> dict:
        data: dict = {"kind": self.kind}
        for key in ("url", "branch", "depth", "spec", "path"):
            value = getattr(self, key)
            if value:
                data[key] = value
        if self.merges:
            data["merges"] = list(self.merges)
        if self.kind == "git" and self.addons != ["*"]:
            data["addons"] = list(self.addons)
        return data


def parse_entry(name: str, raw) -> AddonEntry:
    if not isinstance(raw, dict):
        raise WaftError(f"ADDONS entry {name!r} must be a mapping")
    kind = str(raw.get("kind", "directory"))
    if kind not in KINDS:
        raise WaftError(
            f"ADDONS entry {name!r}: unknown kind {kind!r} "
            f"(choose from {', '.join(KINDS)})"
        )
    entry = AddonEntry(
        name=name,
        kind=kind,
        url=str(raw.get("url", "")),
        branch=str(raw.get("branch", "")),
        merges=[str(merge) for merge in raw.get("merges") or []],
        depth=str(raw.get("depth", "")),
        addons=[str(glob) for glob in raw.get("addons") or ["*"]],
        spec=str(raw.get("spec", "")),
        path=str(raw.get("path", "")),
    )
    if kind == "git" and not entry.url:
        raise WaftError(f"ADDONS entry {name!r} (git) needs a 'url'")
    if kind == "pypi" and not entry.spec:
        raise WaftError(f"ADDONS entry {name!r} (pypi) needs a 'spec'")
    if kind == "link" and not entry.path:
        raise WaftError(f"ADDONS entry {name!r} (link) needs a 'path'")
    return entry


def load_entries(project: Project) -> dict[str, AddonEntry]:
    """ADDONS entries from shared.yml, overridden/disabled by secret.yml."""
    entries: dict[str, AddonEntry] = {}
    for path in (project.shared_yml, project.secret_yml):
        mapping = config_mod.load_yaml_mapping(path).get("ADDONS")
        if mapping is None:
            continue
        if not isinstance(mapping, dict):
            raise WaftError(f"ADDONS in {path} must be a mapping")
        for name, raw in mapping.items():
            if raw is None:
                entries.pop(str(name), None)
            else:
                entries[str(name)] = parse_entry(str(name), raw)
    return entries


def _save_entry(project: Project, name: str, data: dict | None) -> None:
    mapping = config_mod.load_yaml_mapping(project.shared_yml)
    addons = mapping.get("ADDONS") or {}
    if data is None:
        if name not in addons:
            raise WaftError(f"addon entry {name!r} not found in {project.shared_yml}")
        del addons[name]
    else:
        addons[name] = data
    mapping["ADDONS"] = addons
    project.shared_yml.write_text(
        yaml.safe_dump(mapping, default_flow_style=False, sort_keys=True),
        encoding="utf-8",
    )


def repo_dir(project: Project, entry: AddonEntry) -> Path:
    return project.tmp_dir / "repos" / entry.name


def has_manifest(path: Path) -> bool:
    return any((path / manifest).is_file() for manifest in MANIFESTS)


def _links_state(project: Project) -> Path:
    return project.data_dir / "addon-links"


def _read_links(project: Project) -> set[str]:
    state = _links_state(project)
    if not state.is_file():
        return set()
    return {line for line in state.read_text(encoding="utf-8").splitlines() if line}


def _write_links(project: Project, names) -> None:
    state = _links_state(project)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("".join(f"{name}\n" for name in sorted(names)), encoding="utf-8")


def _register(desired: dict[str, Path], name: str, target: Path, entry: AddonEntry):
    if name in desired:
        raise WaftError(
            f"addon {name!r} is provided by multiple entries "
            f"(latest: {entry.name!r}); remove the duplicate"
        )
    desired[name] = target


def converge(project: Project, cfg: dict[str, str]) -> list[str]:
    """Fetch/install configured addon sources and (re)link addons/."""
    entries = load_entries(project)
    desired: dict[str, Path] = {}
    for entry in entries.values():
        if entry.kind == "git":
            repo = repo_dir(project, entry)
            aggregate.fetch_repo(project, entry, cfg, repo)
            matched = []
            for pattern in entry.addons:
                for child in sorted(repo.glob(pattern)):
                    if child.is_dir() and has_manifest(child):
                        matched.append(child)
            if not matched:
                raise WaftError(
                    f"addon entry {entry.name!r}: no addons matching "
                    f"{entry.addons} in {repo}"
                )
            for child in matched:
                _register(desired, child.name, child, entry)
        elif entry.kind == "pypi":
            venv_mod.run_pip(project, cfg, ["install", entry.spec])
        elif entry.kind == "link":
            target = Path(entry.path)
            if not target.is_absolute():
                target = (project.root / target).resolve()
            if not has_manifest(target):
                raise WaftError(
                    f"addon entry {entry.name!r}: no Odoo manifest in {target}"
                )
            _register(desired, entry.name, target, entry)
        elif entry.kind == "directory":
            target = project.addons_dir / entry.name
            if not (target.is_dir() and has_manifest(target)):
                print(
                    f"warning: addon entry {entry.name!r}: {target} is not "
                    "an addon directory (yet)"
                )
    project.addons_dir.mkdir(exist_ok=True)
    for name, target in desired.items():
        linkpath = project.addons_dir / name
        rel = os.path.relpath(target, project.addons_dir)
        if linkpath.is_symlink():
            if os.readlink(linkpath) == rel:
                continue
            linkpath.unlink()
        elif linkpath.exists():
            raise WaftError(
                f"cannot link addon {name!r}: {linkpath} already exists "
                "and is not a symlink"
            )
        linkpath.symlink_to(rel)
    for name in _read_links(project) - desired.keys():
        stale = project.addons_dir / name
        if stale.is_symlink():
            stale.unlink()
    _write_links(project, desired.keys())
    return sorted(desired)


def addon_list(project: Project) -> int:
    """`waft odoo addon --list`."""
    entries = load_entries(project)
    rows: list[tuple[str, str, str]] = []
    for name, entry in sorted(entries.items()):
        if entry.kind == "git":
            repo = repo_dir(project, entry)
            status = "fetched" if repo.exists() else "not fetched (run 'waft sync')"
            rows.append((name, "git", f"{entry.url} [{status}]"))
        elif entry.kind == "pypi":
            rows.append((name, "pypi", entry.spec))
        elif entry.kind == "link":
            rows.append((name, "link", entry.path))
        else:
            rows.append((name, "directory", str(project.addons_dir / name)))
    seen = {name for name, _, _ in rows}
    if project.addons_dir.is_dir():
        for child in sorted(project.addons_dir.iterdir()):
            if child.name in seen or child.name.startswith("."):
                continue
            if child.is_symlink():
                rows.append((child.name, "linked", os.readlink(child)))
            elif child.is_dir() and has_manifest(child):
                rows.append((child.name, "directory", "unmanaged"))
    if not rows:
        print("no addons configured; see 'waft odoo addon --help'")
        return 0
    name_width = max(len(row[0]) for row in rows)
    kind_width = max(len(row[1]) for row in rows)
    for name, kind, detail in rows:
        print(f"{name.ljust(name_width)}  {kind.ljust(kind_width)}  {detail}")
    return 0


def _infer_kind(options: dict) -> str:
    if options.get("url"):
        return "git"
    if options.get("spec"):
        return "pypi"
    if options.get("path"):
        return "link"
    return "directory"


def addon_add(project: Project, name: str, options: dict) -> int:
    """`waft odoo addon --add NAME [--url ...|--spec ...|--path ...]`."""
    if name in load_entries(project):
        raise WaftError(
            f"addon entry {name!r} already exists; use --config to change it"
        )
    options = dict(options)
    options.setdefault("kind", _infer_kind(options))
    entry = parse_entry(name, options)
    _save_entry(project, name, entry.to_yaml())
    print(f"added addon entry {name!r} ({entry.kind}) to {project.shared_yml}")
    if entry.kind != "directory":
        print("run 'waft sync' to fetch/install and link it")
    return 0


def addon_configure(project: Project, name: str, options: dict) -> int:
    """`waft odoo addon --config NAME [--url ...|--branch ...|...]`."""
    entries = load_entries(project)
    if name not in entries:
        raise WaftError(f"addon entry {name!r} not found; use --add first")
    if not options:
        raise WaftError(
            "nothing to configure; pass e.g. --url, --branch, --spec or --path"
        )
    raw = entries[name].to_yaml()
    raw.update(options)
    entry = parse_entry(name, raw)
    _save_entry(project, name, entry.to_yaml())
    print(f"updated addon entry {name!r} in {project.shared_yml}")
    print("run 'waft sync' to apply the change")
    return 0


def addon_delete(project: Project, name: str) -> int:
    """`waft odoo addon --delete NAME`."""
    _save_entry(project, name, None)
    link = project.addons_dir / name
    if link.is_symlink():
        link.unlink()
    _write_links(project, _read_links(project) - {name})
    print(f"removed addon entry {name!r} from {project.shared_yml}")
    return 0


def addon_update(project: Project, name: str) -> int:
    """`waft odoo addon --update NAME`: refresh one addon from its source."""
    entry = load_entries(project).get(name)
    if entry is None:
        raise WaftError(f"addon entry {name!r} not found; see --list")
    cfg = config_mod.load_config(project)
    if entry.kind == "git":
        aggregate.fetch_repo(project, entry, cfg, repo_dir(project, entry))
        converge(project, cfg)
        print(f"updated addon entry {name!r} from {entry.url}")
    elif entry.kind == "pypi":
        venv_mod.run_pip(project, cfg, ["install", "--upgrade", entry.spec])
        print(f"updated {entry.spec}")
    else:
        print(f"addon entry {name!r} is kind {entry.kind!r}; nothing to update")
    return 0
