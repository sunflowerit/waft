"""Addon declarations: what to fetch and how each addon is installed.

Nothing under addons/ is picked up automatically. Every addon is declared
under the ADDONS key of .waft/conf/shared.yml (secret.yml can override an
entry, or disable one with null) and says how it is installed:

``install: clone``
    Fetch a git repository into addons/<name>/ (or ``path``). Fetching
    only - the addons inside it are not installed by this entry.

``install: pypi``
    Install the addon into the virtual environment with pip, either from
    ``spec`` (any requirement pip accepts, including a git URL) or from a
    local ``path`` such as a subdirectory of a cloned repository.

``install: source``
    Use the addon from disk: waft adds the directory that *contains* it to
    ``addons_path`` in odoo.conf. Odoo scans addons_path entries for
    addons, so the entry must be the parent - which means sibling addons in
    the same directory become visible to Odoo as well (they still have to
    be installed in the database to do anything).

Example::

    ADDONS:
      # one addon as a PyPI package straight from a remote repository
      addon:
        install: pypi
        spec: https://github.com/example/addons-example/tree/a-branch/addons/addon

      # a whole repository cloned into addons/addons-example
      addons-example:
        install: clone
        url: https://github.com/example/addons-example.git
        branch: a-branch-name
      addonx:
        install: pypi
        path: addons/addons-example/addonx
      addony:
        install: source
        path: addons/addons-example/addony

      # an addon the user placed in addons/addons-local/ by hand
      addonz:
        install: source
        path: addons/addons-local/addonz
        gitignore: true
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import aggregate
from . import config as config_mod
from . import venv as venv_mod
from .project import Project, WaftError

MANIFESTS = ("__manifest__.py", "__openerp__.py")
INSTALL_TYPES = ("clone", "pypi", "source")

GITIGNORE_BEGIN = "# waft: addon entries (managed)"
GITIGNORE_END = "# waft: end addon entries"

#: Browser URLs such as https://host/org/repo/tree/<branch>/<subdirectory>
_WEB_URL = re.compile(
    r"^(?P<scheme>https?)://(?P<host>[^/]+)/(?P<org>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?"
    r"/(?:-/)?(?:tree|src)/(?P<branch>[^/]+?)(?:/(?P<subdir>.+?))?/?$"
)
#: git@host:org/repo[/tree/<branch>/<subdirectory>]
_SSH_URL = re.compile(
    r"^git@(?P<host>[^:]+):(?P<org>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?"
    r"(?:/tree/(?P<branch>[^/]+?)(?:/(?P<subdir>.+?))?)?/?$"
)


def normalize_spec(value: str) -> str:
    """Translate browser-style repository URLs into a pip requirement.

    Anything pip (or pipx) already understands is passed through unchanged,
    so plain package names, version pins and git+... URLs keep working.
    """
    value = value.strip()
    for pattern, prefix in ((_WEB_URL, None), (_SSH_URL, "git+ssh://git@")):
        match = pattern.match(value)
        if not match or not match.group("branch"):
            continue
        parts = match.groupdict()
        base = (
            f"{prefix}{parts['host']}/{parts['org']}/{parts['repo']}"
            if prefix
            else f"git+{parts['scheme']}://{parts['host']}/{parts['org']}/{parts['repo']}"
        )
        spec = f"{base}@{parts['branch']}"
        subdir = (parts.get("subdir") or "").strip("/")
        if subdir.endswith(".git"):
            subdir = subdir[: -len(".git")]
        if subdir:
            spec = f"{spec}#subdirectory={subdir}"
        return spec
    return value


@dataclass
class AddonEntry:
    """One declared addon or repository."""

    name: str
    install: str = "source"
    url: str = ""
    branch: str = ""
    merges: list[str] = field(default_factory=list)
    depth: str = ""
    path: str = ""
    spec: str = ""
    gitignore: bool | None = None

    def to_yaml(self) -> dict:
        data: dict = {"install": self.install}
        for key in ("url", "branch", "depth", "path", "spec"):
            value = getattr(self, key)
            if value:
                data[key] = value
        if self.merges:
            data["merges"] = list(self.merges)
        if self.gitignore is not None:
            data["gitignore"] = self.gitignore
        return data

    @property
    def ignored(self) -> bool:
        """Whether waft keeps this entry's directory out of git."""
        if self.gitignore is not None:
            return self.gitignore
        return self.install == "clone"


def parse_entry(name: str, raw) -> AddonEntry:
    if not isinstance(raw, dict):
        raise WaftError(f"ADDONS entry {name!r} must be a mapping")
    install = str(raw.get("install", "source"))
    if install not in INSTALL_TYPES:
        raise WaftError(
            f"ADDONS entry {name!r}: unknown install type {install!r} "
            f"(choose from {', '.join(INSTALL_TYPES)})"
        )
    gitignore = raw.get("gitignore")
    entry = AddonEntry(
        name=name,
        install=install,
        url=str(raw.get("url", "")),
        branch=str(raw.get("branch", "")),
        merges=[str(merge) for merge in raw.get("merges") or []],
        depth=str(raw.get("depth", "")),
        path=str(raw.get("path", "")),
        spec=str(raw.get("spec", "")),
        gitignore=None if gitignore is None else bool(gitignore),
    )
    if install == "clone" and not entry.url:
        raise WaftError(f"ADDONS entry {name!r} (clone) needs a 'url'")
    if install == "pypi" and not (entry.spec or entry.path):
        raise WaftError(f"ADDONS entry {name!r} (pypi) needs a 'spec' or a 'path'")
    if install == "source" and not entry.path:
        raise WaftError(f"ADDONS entry {name!r} (source) needs a 'path'")
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


def has_manifest(path: Path) -> bool:
    return any((path / manifest).is_file() for manifest in MANIFESTS)


def entry_path(project: Project, entry: AddonEntry) -> Path:
    """Where the entry lives on disk."""
    if entry.path:
        path = Path(entry.path)
        return path if path.is_absolute() else (project.root / path)
    return project.addons_dir / entry.name


def pip_target(project: Project, entry: AddonEntry) -> str:
    """What to hand to pip for a 'pypi' entry."""
    if entry.spec:
        return normalize_spec(entry.spec)
    path = entry_path(project, entry)
    if not path.is_dir():
        raise WaftError(
            f"addon entry {entry.name!r}: {path} does not exist "
            "(fetch the repository first with 'waft sync')"
        )
    for candidate in (path, path.parent / "setup" / path.name):
        if (candidate / "setup.py").is_file() or (
            candidate / "pyproject.toml"
        ).is_file():
            return str(candidate)
    raise WaftError(
        f"addon entry {entry.name!r}: no setup.py or pyproject.toml for {path}; "
        "use 'install: source' instead, or point 'path' at a packaged addon"
    )


def source_paths(project: Project) -> list[str]:
    """Directories to put in addons_path, from the declared source addons.

    Odoo scans an addons_path entry *for* addons, so the entry is the
    directory containing the declared addon.
    """
    paths: list[str] = []
    for entry in load_entries(project).values():
        if entry.install != "source":
            continue
        parent = entry_path(project, entry).parent
        if parent.is_dir() and str(parent) not in paths:
            paths.append(str(parent))
    return sorted(paths)


def addons_path(project: Project) -> list[str]:
    """The full addons_path: the Odoo checkout plus declared source addons."""
    paths = [
        str(candidate)
        for candidate in (
            project.odoo_dir / "addons",
            project.odoo_dir / "odoo" / "addons",
        )
        if candidate.is_dir()
    ]
    for path in source_paths(project):
        if path not in paths:
            paths.append(path)
    return paths


def update_gitignore(project: Project, entries: dict[str, AddonEntry]) -> None:
    """Keep the managed block of addon directories in .gitignore current."""
    wanted = []
    for entry in sorted(entries.values(), key=lambda item: item.name):
        if not entry.ignored:
            continue
        path = entry_path(project, entry)
        try:
            relative = path.relative_to(project.root)
        except ValueError:  # outside the project: nothing to ignore
            continue
        line = f"/{relative.as_posix()}/"
        if line not in wanted:
            wanted.append(line)
    lines = (
        project.gitignore.read_text(encoding="utf-8").splitlines()
        if project.gitignore.is_file()
        else []
    )
    if GITIGNORE_BEGIN in lines:
        start = lines.index(GITIGNORE_BEGIN)
        end = lines.index(GITIGNORE_END) + 1 if GITIGNORE_END in lines else start + 1
        lines = lines[:start] + lines[end:]
    while lines and not lines[-1].strip():
        lines.pop()
    if wanted:
        lines += [GITIGNORE_BEGIN, *wanted, GITIGNORE_END]
    project.gitignore.write_text(
        "".join(f"{line}\n" for line in lines), encoding="utf-8"
    )


def converge(project: Project, cfg: dict[str, str]) -> list[str]:
    """Fetch what must be fetched and install what must be installed."""
    entries = load_entries(project)
    done: list[str] = []
    for entry in sorted(entries.values(), key=lambda item: item.name):
        if entry.install == "clone":
            aggregate.fetch_repo(project, entry, cfg, entry_path(project, entry))
            done.append(f"{entry.name} (cloned)")
        elif entry.install == "pypi":
            venv_mod.run_pip(project, cfg, ["install", pip_target(project, entry)])
            done.append(f"{entry.name} (pypi)")
        elif entry.install == "source":
            path = entry_path(project, entry)
            if not path.is_dir():
                raise WaftError(f"addon entry {entry.name!r}: {path} does not exist")
            if not has_manifest(path):
                raise WaftError(
                    f"addon entry {entry.name!r}: no Odoo manifest in {path}"
                )
            done.append(f"{entry.name} (source)")
    update_gitignore(project, entries)
    return done


def addon_list(project: Project) -> int:
    """`waft odoo addon --list`."""
    entries = load_entries(project)
    if not entries:
        print("no addons declared; see 'waft odoo addon --help'")
        return 0
    rows: list[tuple[str, str, str]] = []
    for name, entry in sorted(entries.items()):
        path = entry_path(project, entry)
        if entry.install == "clone":
            state = "cloned" if (path / ".git").is_dir() else "not fetched"
            rows.append((name, "clone", f"{entry.url} -> {path} [{state}]"))
        elif entry.install == "pypi":
            detail = normalize_spec(entry.spec) if entry.spec else str(path)
            rows.append((name, "pypi", detail))
        else:
            state = "ok" if has_manifest(path) else "missing"
            rows.append((name, "source", f"{path} [{state}]"))
    name_width = max(len(row[0]) for row in rows)
    kind_width = max(len(row[1]) for row in rows)
    for name, kind, detail in rows:
        print(f"{name.ljust(name_width)}  {kind.ljust(kind_width)}  {detail}")
    return 0


def _infer_install(options: dict) -> str:
    if options.get("url"):
        return "clone"
    if options.get("spec"):
        return "pypi"
    return "source"


def addon_add(project: Project, name: str, options: dict) -> int:
    """`waft odoo addon --add NAME -t {clone,pypi,source} ...`."""
    if name in load_entries(project):
        raise WaftError(
            f"addon entry {name!r} already exists; use --config to change it"
        )
    if name == project.odoo_dir.name:
        raise WaftError(
            f"{name!r} is reserved for the Odoo source checkout in "
            f"{project.addons_dir}"
        )
    options = dict(options)
    options.setdefault("install", _infer_install(options))
    entry = parse_entry(name, options)
    _save_entry(project, name, entry.to_yaml())
    print(f"declared addon entry {name!r} ({entry.install}) in {project.shared_yml}")
    print("run 'waft sync' to apply it")
    return 0


def addon_configure(project: Project, name: str, options: dict) -> int:
    """`waft odoo addon --config NAME ...`."""
    entries = load_entries(project)
    if name not in entries:
        raise WaftError(f"addon entry {name!r} not found; use --add first")
    if not options:
        raise WaftError(
            "nothing to configure; pass e.g. --installation-type, --url, "
            "--branch, --path or --spec"
        )
    raw = entries[name].to_yaml()
    raw.update(options)
    entry = parse_entry(name, raw)
    _save_entry(project, name, entry.to_yaml())
    print(f"updated addon entry {name!r} ({entry.install}) in {project.shared_yml}")
    print("run 'waft sync' to apply the change")
    return 0


def addon_delete(project: Project, name: str) -> int:
    """`waft odoo addon --delete NAME`: forget the declaration."""
    entries = load_entries(project)
    entry = entries.get(name)
    _save_entry(project, name, None)
    if entry is not None:
        update_gitignore(project, {k: v for k, v in entries.items() if k != name})
        path = entry_path(project, entry)
        if entry.install != "pypi" and path.exists():
            print(f"note: {path} is left on disk; remove it manually if unwanted")
    print(f"removed addon entry {name!r} from {project.shared_yml}")
    return 0


def addon_update(project: Project, name: str) -> int:
    """`waft odoo addon --update NAME`: refresh one entry from its source."""
    entry = load_entries(project).get(name)
    if entry is None:
        raise WaftError(f"addon entry {name!r} not found; see --list")
    cfg = config_mod.load_config(project)
    if entry.install == "clone":
        aggregate.fetch_repo(project, entry, cfg, entry_path(project, entry))
        print(f"updated {name!r} from {entry.url}")
    elif entry.install == "pypi":
        venv_mod.run_pip(
            project, cfg, ["install", "--upgrade", pip_target(project, entry)]
        )
        print(f"updated {name!r}")
    else:
        print(f"addon entry {name!r} is used from source; nothing to fetch")
    return 0
