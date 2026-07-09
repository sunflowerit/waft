"""Database commands and PostgreSQL helpers.

All PostgreSQL access goes through psql/createdb with the PG* environment
variables taken from the merged waft configuration, so the behaviour matches
what Odoo itself will see.
"""

from __future__ import annotations

import os

from . import config as config_mod
from . import versions
from .project import Project, WaftError
from .venv import _run


def pg_env(cfg: dict[str, str]) -> dict[str, str]:
    """Process environment with the configured PG* variables applied."""
    env = os.environ.copy()
    for var in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"):
        if cfg.get(var):
            env[var] = cfg[var]
    return env


def query(cfg: dict[str, str], sql: str, database: str | None = None) -> str:
    """Run one SQL statement through psql; returns the trimmed output."""
    cmd = ["psql"]
    if database:
        cmd += ["-d", database]
    cmd += ["-tAc", sql]
    result = _run(cmd, env=pg_env(cfg), capture_output=True, text=True)
    return (result.stdout or "").strip()


def db_exists(cfg: dict[str, str], name: str) -> bool:
    safe = name.replace("'", "''")
    sql = f"SELECT 1 FROM pg_database WHERE datname = '{safe}'"
    return query(cfg, sql, database="template1") == "1"


def db_initialized(cfg: dict[str, str], name: str) -> bool:
    sql = (
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = 'ir_module_module'"
    )
    return query(cfg, sql, database=name) == "1"


def initial(project: Project) -> int:
    """`waft database initial`: create + initialize the default database."""
    cfg = config_mod.load_config(project)
    name = cfg["PGDATABASE"]
    if not name:
        raise WaftError(
            'PGDATABASE is not set; use "waft odoo config set PGDATABASE=name"'
        )
    odoo_binary = project.venv_dir / "bin" / "odoo"
    if not odoo_binary.exists():
        raise WaftError("Odoo is not installed yet; run 'waft sync' first")
    if not db_exists(cfg, name):
        _run(["createdb", name], env=pg_env(cfg))
        print(f"created database {name}")
    if db_initialized(cfg, name):
        print(f"database {name} is already initialized")
        return 0
    info = versions.get(cfg["ODOO_VERSION"])
    _run(
        [
            odoo_binary,
            "-c",
            project.odoo_conf,
            "-d",
            name,
            "-i",
            "base",
            "--stop-after-init",
            f"--{info.port_flag}=18069",
        ]
    )
    print(f"database {name} initialized")
    return 0
