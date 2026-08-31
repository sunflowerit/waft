"""`waft init` project generation and `waft sync` format migrations.

No template engine: the project tree and all files are generated directly by
this module. The project is stamped with a format version
(.waft/version); `waft sync` applies migration steps when the stamp is older
than the installed waft.
"""

from __future__ import annotations

import getpass
from pathlib import Path

import yaml

from . import PROJECT_FORMAT, __version__, config, versions
from .project import Project, WaftError

GITIGNORE = """\
# waft: generated files and machine-local state
.waft/conf/secret.yml
.waft/conf/odoo.conf
.waft/data/
.waft/log/
.tmp/
.venv/
/addons/odoo/
__pycache__/
*.pyc
"""

ODOO_SERVICE = """\
[Unit]
Description=Odoo (waft project {name})
After=network.target postgresql.service

[Service]
Type=simple
User={user}
WorkingDirectory={root}
ExecStart={root}/.venv/bin/odoo -c {root}/.waft/conf/odoo.conf \\
    --logfile {root}/.waft/log/odoo.log
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""


def _write_version_stamp(project: Project) -> None:
    project.version_file.write_text(
        yaml.safe_dump({"format": PROJECT_FORMAT, "waft": __version__}),
        encoding="utf-8",
    )


def read_version_stamp(project: Project) -> int:
    """Project format version; raises if the stamp is missing or invalid."""
    if not project.version_file.is_file():
        raise WaftError(
            f"missing {project.version_file}; is this a waft project? Run 'waft init'."
        )
    data = yaml.safe_load(project.version_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("format"), int):
        raise WaftError(f"invalid version stamp in {project.version_file}")
    return data["format"]


def init_project(
    root: Path | str,
    odoo_version: str,
    db_name: str = "",
    db_user: str = "",
    db_password: str = "",
    db_host: str = "",
    db_port: str = "",
) -> Project:
    """Create the customer repository directories and files tree (first time)."""
    project = Project(root)
    if project.exists():
        raise WaftError(
            f"'{project.root}' is already a waft project; use 'waft sync' to update it"
        )
    versions.get(odoo_version)  # validate before touching the filesystem

    for directory in (
        project.addons_dir,
        project.tmp_dir,
        project.conf_dir,
        project.backup_dir,
        project.odoo_data_dir / "addons",
        project.odoo_data_dir / "filestore",
        project.odoo_data_dir / "sessions",
        project.log_dir,
        project.template_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    shared: dict[str, str] = {"ODOO_VERSION": odoo_version}
    if db_host:
        shared["PGHOST"] = db_host
    if db_port:
        shared["PGPORT"] = db_port
    if db_user:
        shared["PGUSER"] = db_user
    project.shared_yml.write_text(
        "# Waft shared project configuration (tracked by git).\n"
        "# Secrets belong in secret.yml, never here.\n"
        + yaml.safe_dump(shared, default_flow_style=False, sort_keys=True),
        encoding="utf-8",
    )

    secret: dict[str, str] = {
        "PGDATABASE": db_name,
        "PGPASSWORD": db_password,
        "ODOO_ADMIN_PASSWORD": "",
    }
    project.secret_yml.write_text(
        "# Waft secret configuration (git-ignored). Machine-local values and secrets.\n"
        + yaml.safe_dump(secret, default_flow_style=False, sort_keys=True),
        encoding="utf-8",
    )

    if not project.gitignore.exists():
        project.gitignore.write_text(GITIGNORE, encoding="utf-8")

    if not project.requirements_txt.exists():
        project.requirements_txt.write_text(
            "# Extra PyPI requirements for this project (applied on top of the\n"
            "# per-Odoo-version defaults shipped inside waft).\n",
            encoding="utf-8",
        )

    (project.template_dir / "odoo.service").write_text(
        ODOO_SERVICE.format(
            name=project.root.name, root=project.root, user=getpass.getuser()
        ),
        encoding="utf-8",
    )

    _write_version_stamp(project)
    config.generate_odoo_conf(project)
    return project


def _migration_2(project: Project) -> None:
    """Format 2: the Odoo source checkout lives in /odoo/ (git-ignored)."""
    text = (
        project.gitignore.read_text(encoding="utf-8")
        if project.gitignore.is_file()
        else ""
    )
    if "/odoo/" not in text.splitlines():
        if text and not text.endswith("\n"):
            text += "\n"
        project.gitignore.write_text(text + "/odoo/\n", encoding="utf-8")


def _rewrite_gitignore(project: Project, drop: list[str], add: str) -> None:
    lines = (
        project.gitignore.read_text(encoding="utf-8").splitlines()
        if project.gitignore.is_file()
        else []
    )
    lines = [line for line in lines if line.strip() not in drop]
    if add not in [line.strip() for line in lines]:
        lines.append(add)
    project.gitignore.write_text(
        "".join(f"{line}\n" for line in lines), encoding="utf-8"
    )


def _migration_3(project: Project) -> None:
    """Format 3 (superseded by format 4): sources lived in .src/."""
    _rewrite_gitignore(project, ["/odoo/"], ".src/")


def _migration_4(project: Project) -> None:
    """Format 4: Odoo checkout in addons/odoo/, addon repos in .tmp/repos/."""
    project.addons_dir.mkdir(parents=True, exist_ok=True)
    legacy_src = project.root / ".src"
    candidates = [project.root / "odoo"]  # format 1/2 layout
    if legacy_src.is_dir():
        candidates.insert(0, legacy_src / "odoo")
    for legacy_odoo in candidates:
        if legacy_odoo.is_dir() and not project.odoo_dir.exists():
            legacy_odoo.rename(project.odoo_dir)
    if legacy_src.is_dir():
        repos = project.tmp_dir / "repos"
        repos.mkdir(parents=True, exist_ok=True)
        for repo in sorted(legacy_src.iterdir()):
            destination = repos / repo.name
            if not destination.exists():
                repo.rename(destination)
        if not any(legacy_src.iterdir()):
            legacy_src.rmdir()
    # The editable Odoo install and the addon links point at the old paths.
    for stale_state in (
        project.data_dir / "odoo-installed",
        project.data_dir / "addon-links",
        project.data_dir / "addon-links.json",
    ):
        if stale_state.is_file():
            stale_state.unlink()
    if project.addons_dir.is_dir():
        for child in project.addons_dir.iterdir():
            if child.is_symlink() and not child.exists():
                child.unlink()
    _rewrite_gitignore(project, ["/odoo/", ".src/"], "/addons/odoo/")


_INSTALL_FROM_KIND = {
    "git": "clone",
    "pypi": "pypi",
    "link": "source",
    "directory": "source",
}


def _migration_5(project: Project) -> None:
    """Format 5: repos cloned into addons/<entry>/, explicit install types."""
    legacy_repos = project.tmp_dir / "repos"
    if legacy_repos.is_dir():
        project.addons_dir.mkdir(parents=True, exist_ok=True)
        for repo in sorted(legacy_repos.iterdir()):
            destination = project.addons_dir / repo.name
            if not destination.exists():
                repo.rename(destination)
        if not any(legacy_repos.iterdir()):
            legacy_repos.rmdir()
    # Addons used to be symlinked into addons/; they are declared now.
    state = project.data_dir / "addon-links"
    if state.is_file():
        for name in state.read_text(encoding="utf-8").split():
            link = project.addons_dir / name
            if link.is_symlink():
                link.unlink()
        state.unlink()
    # Translate the old ADDONS schema (kind:) into install types.
    for path in (project.shared_yml, project.secret_yml):
        mapping = config.load_yaml_mapping(path)
        addons = mapping.get("ADDONS")
        if not isinstance(addons, dict):
            continue
        changed = False
        for name, raw in list(addons.items()):
            if not isinstance(raw, dict) or "kind" not in raw:
                continue
            raw = dict(raw)
            kind = str(raw.pop("kind"))
            raw.pop("addons", None)  # per-addon globs have no equivalent
            raw["install"] = _INSTALL_FROM_KIND.get(kind, "source")
            if raw["install"] == "source" and not raw.get("path"):
                raw["path"] = f"addons/{name}"
            addons[name] = raw
            changed = True
        if changed:
            mapping["ADDONS"] = addons
            path.write_text(
                yaml.safe_dump(mapping, default_flow_style=False, sort_keys=True),
                encoding="utf-8",
            )
            print(
                f"note: {path} ADDONS entries were converted to install types; "
                "repositories are cloned but their addons must now be declared "
                "individually (waft odoo addon --add ... -t source|pypi)"
            )


#: Ordered project-format migrations: {target_format: callable(project)}.
#: A migration upgrades a project from target_format - 1 to target_format.
MIGRATIONS: dict[int, callable] = {
    2: _migration_2,
    3: _migration_3,
    4: _migration_4,
    5: _migration_5,
}


def apply_migrations(project: Project) -> list[int]:
    """Bring the project format up to PROJECT_FORMAT; returns applied steps."""
    current = read_version_stamp(project)
    if current > PROJECT_FORMAT:
        raise WaftError(
            f"project format {current} is newer than this waft ({PROJECT_FORMAT}); "
            "upgrade waft (pipx upgrade waft)"
        )
    applied: list[int] = []
    for target in range(current + 1, PROJECT_FORMAT + 1):
        migration = MIGRATIONS.get(target)
        if migration is not None:
            migration(project)
        applied.append(target)
    if applied:
        _write_version_stamp(project)
    return applied


def sync(project: Project) -> int:
    """`waft sync`: idempotent convergence. Safe to re-run at any time."""
    from . import source
    from . import venv as venv_mod

    applied = apply_migrations(project)
    if applied:
        print(f"applied project format migration(s): {', '.join(map(str, applied))}")
    cfg = config.load_config(project)
    if venv_mod.ensure_venv(project, cfg):
        print(f"created virtual environment {project.venv_dir}")
    else:
        print(f"virtual environment {project.venv_dir} is up to date")
    venv_mod.update_requirements(project, cfg)
    source.ensure_odoo_source(project, cfg)
    if source.install_odoo(project, cfg):
        print("installed Odoo into the virtual environment")
    else:
        print("Odoo installation is up to date")
    from . import addons as addons_mod

    done = addons_mod.converge(project, cfg)
    print(f"addons: {', '.join(done) if done else '(none declared)'}")
    # The addons path depends on the declared source addons, so the Odoo
    # configuration is generated once everything is on disk.
    config.generate_odoo_conf(project)
    print(f"regenerated {project.odoo_conf}")
    return 0
