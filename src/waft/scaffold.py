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
/odoo/
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


#: Ordered project-format migrations: {target_format: callable(project)}.
#: A migration upgrades a project from target_format - 1 to target_format.
MIGRATIONS: dict[int, callable] = {2: _migration_2}


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
    config.generate_odoo_conf(project)
    print(f"regenerated {project.odoo_conf}")
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

    linked = addons_mod.converge(project, cfg)
    print(f"addons linked: {', '.join(linked) if linked else '(none)'}")
    return 0
