"""Odoo version migration: `waft migrate`.

Port of the old waftlib migrate.py engine, adapted to the new waft world:

- progress is recorded in .waft/data/migration-progress.json, so an
  interrupted migration resumes exactly where it stopped;
- each intermediate version gets its own nested waft build under
  .tmp/migration/<version>/ (venv + Odoo source), reusing the normal waft
  machinery (scaffold/venv/source/addons);
- community migrations use OpenUpgrade: the OpenUpgrade fork as Odoo source
  for <= 13.0, OCB + openupgrade_framework with --upgrade-path for >= 14.0;
- --enterprise uses Odoo's upgrade service client script instead;
- hook scripts live in .waft/migration/<phase>/ and
  .waft/migration/<phase>-<version>/ (phases: pre-migration, pre-upgrade,
  post-upgrade, post-migration) and run in sorted filename order:
  .sql via psql, .sh via bash, .py via the project venv python.
"""

from __future__ import annotations

import argparse
import json

from . import addons as addons_mod
from . import config as config_mod
from . import database
from . import source as source_mod
from . import venv as venv_mod
from . import versions
from .project import Project, WaftError
from .venv import _run

OPENUPGRADE_REPO = "https://github.com/OCA/OpenUpgrade.git"
UPGRADE_SERVICE_URL = "https://upgrade.odoo.com/upgrade"
HOOK_PHASES = ("pre-migration", "pre-upgrade", "post-upgrade", "post-migration")


def parse_args(argv: list[str]):
    parser = argparse.ArgumentParser(
        prog="waft migrate",
        description="Migrate the Odoo database up to the project's ODOO_VERSION.",
    )
    parser.add_argument("-d", "--database", help="database (default: PGDATABASE)")
    parser.add_argument(
        "-f", "--start-version", help="override the detected database version"
    )
    parser.add_argument(
        "-e",
        "--enterprise",
        action="store_true",
        help="use Odoo's enterprise upgrade service instead of OpenUpgrade",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="enterprise: request a real upgrade instead of a test one",
    )
    parser.add_argument(
        "--no-backups", action="store_true", help="skip database/filestore backups"
    )
    parser.add_argument(
        "--plan", action="store_true", help="show the planned steps and exit"
    )
    parser.add_argument(
        "--reset-progress",
        nargs="?",
        const="all",
        metavar="VERSION",
        help="forget recorded progress (from VERSION onward, or everything)",
    )
    return parser.parse_args(argv)


# ----------------------------------------------------------------- progress
def progress_file(project: Project):
    return project.data_dir / "migration-progress.json"

def load_progress(project: Project) -> dict:
    path = progress_file(project)
    if not path.is_file():
        return {"versions": {}, "hooks": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise WaftError(f"corrupt {path}: {exc}") from exc
    data.setdefault("versions", {})
    data.setdefault("hooks", {})
    return data


def save_progress(project: Project, progress: dict) -> None:
    path = progress_file(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(progress, indent=2) + "\n", encoding="utf-8")


def reset_progress(project: Project, from_version: str) -> None:
    if from_version == "all":
        save_progress(project, {"versions": {}, "hooks": {}})
        print("migration progress cleared")
        return
    versions.get(from_version)  # validate
    threshold = float(from_version)
    progress = load_progress(project)
    progress["versions"] = {
        version: state
        for version, state in progress["versions"].items()
        if float(version) < threshold
    }
    dropped = [v for v in versions.SUPPORTED_VERSIONS if float(v) >= threshold]
    progress["hooks"] = {
        key: scripts
        for key, scripts in progress["hooks"].items()
        if not any(key.endswith(f"-{version}") for version in dropped)
    }
    save_progress(project, progress)
    print(f"migration progress cleared from {from_version} onward")


# -------------------------------------------------------------------- plan
def detect_db_version(cfg: dict[str, str], db: str) -> str:
    out = database.query(
        cfg,
        "SELECT latest_version FROM ir_module_module WHERE name = 'base'",
        database=db,
    )
    try:
        major = int(out.split(".")[0])
    except (ValueError, IndexError):
        raise WaftError(
            f"could not detect the Odoo version of database {db!r} (got {out!r}); "
            "use --start-version"
        ) from None
    return f"{major}.0"


def plan_steps(start: str, target: str) -> list[str]:
    versions.get(start)
    versions.get(target)
    start_major, target_major = int(float(start)), int(float(target))
    if start_major == target_major:
        raise WaftError(f"the database is already on Odoo {target}")
    if start_major > target_major:
        raise WaftError(f"cannot migrate down from {start} to {target}")
    return [f"{major}.0" for major in range(start_major + 1, target_major + 1)]


# ------------------------------------------------------------------- hooks
def hooks_dir(project: Project):
    return project.waft_dir / "migration"


def run_hooks(
    project: Project,
    cfg: dict[str, str],
    db: str,
    phase: str,
    version: str | None,
    progress: dict,
) -> None:
    directories = [hooks_dir(project) / phase]
    if version:
        directories.append(hooks_dir(project) / f"{phase}-{version}")
    key = f"{phase}-{version}" if version else phase
    done = progress["hooks"].setdefault(key, [])
    env = database.pg_env(cfg)
    env["PGDATABASE"] = db
    env["MIGRATION_VERSION"] = version or ""
    env["WAFT_PROJECT"] = str(project.root)
    for directory in directories:
        if not directory.is_dir():
            continue
        for script in sorted(directory.iterdir()):
            if not script.is_file() or script.name in done:
                continue
            if script.suffix == ".sql":
                _run(
                    ["psql", "-v", "ON_ERROR_STOP=1", "-d", db, "-f", script],
                    env=env,
                )
            elif script.suffix == ".sh":
                _run(["bash", script], env=env)
            elif script.suffix == ".py":
                _run([venv_mod.venv_python(project), script], env=env)
            else:
                print(f"skipping hook {script} (unknown type)")
                continue
            done.append(script.name)
            save_progress(project, progress)
            print(f"hook {key}: {script.name} done")


# ------------------------------------------------------------------ backup
def backup(
    project: Project, cfg: dict[str, str], db: str, version: str, progress: dict
) -> None:
    state = progress["versions"].setdefault(version, {})
    if state.get("backup"):
        return
    backup_name = f"{db}-pre-{version}"
    if not database.db_exists(cfg, backup_name):
        _run(["createdb", "-T", db, backup_name], env=database.pg_env(cfg))
    filestore = project.odoo_data_dir / "filestore" / db
    backup_filestore = project.odoo_data_dir / "filestore" / backup_name
    if filestore.is_dir() and not backup_filestore.exists():
        _run(["cp", "-al", filestore, backup_filestore])
    state["backup"] = True
    save_progress(project, progress)
    print(f"backed up {db} -> {backup_name}")


# ----------------------------------------------------------- version steps
def ensure_step_project(project: Project, version: str, cfg: dict[str, str]):
    """Nested waft build for one intermediate version.

    Returns (step_project, openupgrade_scripts_path_or_None).
    """
    from . import scaffold

    info = versions.get(version)
    root = project.tmp_dir / "migration" / version
    step = Project(root)
    if not step.exists():
        scaffold.init_project(
            root,
            version,
            db_name=cfg["PGDATABASE"],
            db_user=cfg["PGUSER"],
            db_password=cfg["PGPASSWORD"],
            db_host=cfg["PGHOST"],
            db_port=cfg["PGPORT"],
        )
    openupgrade_scripts = None
    if info.major < 14:
        # OpenUpgrade is a full Odoo fork up to 13.0.
        step_cfg = config_mod.load_config(step)
        if step_cfg["ODOO_REPOSITORY"] != OPENUPGRADE_REPO:
            config_mod.config_set(step, f"ODOO_REPOSITORY={OPENUPGRADE_REPO}")
    else:
        if "openupgrade" not in addons_mod.load_entries(step):
            addons_mod.addon_add(
                step,
                "openupgrade",
                {"url": OPENUPGRADE_REPO, "addons": ["openupgrade_*"]},
            )
        openupgrade_scripts = (
            step.tmp_dir / "repos" / "openupgrade" / "openupgrade_scripts" / "scripts"
        )
    step_cfg = config_mod.load_config(step)
    venv_mod.ensure_venv(step, step_cfg)
    venv_mod.update_requirements(step, step_cfg)
    source_mod.ensure_odoo_source(step, step_cfg)
    source_mod.install_odoo(step, step_cfg)
    addons_mod.converge(step, step_cfg)
    return step, openupgrade_scripts


def upgrade_command(step: Project, version: str, db: str, openupgrade_scripts):
    info = versions.get(version)
    cmd = [
        step.venv_dir / "bin" / "odoo",
        "-c",
        step.odoo_conf,
        "-d",
        db,
        "-u",
        "base",
        "--stop-after-init",
        f"--{info.port_flag}=18069",
    ]
    if info.major >= 14 and openupgrade_scripts is not None:
        cmd += [
            "--load=base,web,openupgrade_framework",
            f"--upgrade-path={openupgrade_scripts}",
        ]
    return cmd


def _enterprise(
    project: Project, cfg: dict[str, str], db: str, target: str, production: bool
) -> None:
    project.tmp_dir.mkdir(exist_ok=True)
    script = project.tmp_dir / "enterprise-upgrade.py"
    url = cfg.get("WAFT_UPGRADE_URL") or UPGRADE_SERVICE_URL
    _run(["curl", "-sSf", "-o", script, url])
    mode = "production" if production else "test"
    _run(
        [venv_mod.venv_python(project), script, mode, "-d", db, "-t", target],
        env=database.pg_env(cfg),
    )


# ------------------------------------------------------------------- entry
def migrate(project: Project, argv: list[str]) -> int:
    opts = parse_args(argv)
    if opts.reset_progress:
        reset_progress(project, opts.reset_progress)
        return 0
    cfg = config_mod.load_config(project)
    db = opts.database or cfg["PGDATABASE"]
    if not db:
        raise WaftError("PGDATABASE is not set (or pass --database)")
    target = cfg["ODOO_VERSION"]
    start = opts.start_version or detect_db_version(cfg, db)
    steps = plan_steps(start, target)
    mode = "enterprise upgrade service" if opts.enterprise else "OpenUpgrade"
    print(f"migration plan for {db!r}: {start} -> {' -> '.join(steps)} ({mode})")
    if opts.plan:
        return 0
    progress = load_progress(project)
    progress.update({"database": db, "target": target})
    run_hooks(project, cfg, db, "pre-migration", None, progress)
    if opts.enterprise:
        if not opts.no_backups:
            backup(project, cfg, db, target, progress)
        _enterprise(project, cfg, db, target, opts.production)
    else:
        for version in steps:
            state = progress["versions"].setdefault(version, {})
            run_hooks(project, cfg, db, "pre-upgrade", version, progress)
            if not opts.no_backups:
                backup(project, cfg, db, version, progress)
            if state.get("upgrade"):
                print(f"Odoo {version}: upgrade already done, skipping")
            else:
                step, openupgrade_scripts = ensure_step_project(project, version, cfg)
                _run(upgrade_command(step, version, db, openupgrade_scripts))
                state["upgrade"] = True
                save_progress(project, progress)
                print(f"Odoo {version}: upgrade done")
            run_hooks(project, cfg, db, "post-upgrade", version, progress)
    run_hooks(project, cfg, db, "post-migration", None, progress)
    save_progress(project, progress)
    print(f"migration of {db!r} to Odoo {target} finished")
    return 0
