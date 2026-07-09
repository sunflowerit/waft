"""Odoo commands: run/shell/upgrade/install/odoo-bin/reset-password/translate.

Utility commands use a side port (18069/18080) so they never clash with a
running Odoo service on the standard ports.
"""

from __future__ import annotations

import getpass as getpass_mod
import os
from pathlib import Path

from . import config as config_mod
from . import database
from . import venv as venv_mod
from . import versions
from .project import Project, WaftError

SHELL_PORT = "18080"
UTILITY_PORT = "18069"

_HASH_SNIPPET = (
    "import os; from passlib.context import CryptContext; "
    "print(CryptContext(schemes=['pbkdf2_sha512'])"
    ".hash(os.environ['WAFT_NEW_PASSWORD']))"
)


def odoo_binary(project: Project) -> Path:
    return project.venv_dir / "bin" / "odoo"


def require_odoo(project: Project) -> Path:
    binary = odoo_binary(project)
    if not binary.exists():
        raise WaftError("Odoo is not installed yet; run 'waft sync' first")
    return binary


def _base(project: Project) -> list:
    return [require_odoo(project), "-c", project.odoo_conf]


def _port(cfg: dict[str, str], port: str) -> str:
    info = versions.get(cfg["ODOO_VERSION"])
    return f"--{info.port_flag}={port}"


def run(project: Project, args: list[str]) -> int:
    """`waft odoo run`: run Odoo with the log in the interactive session."""
    venv_mod._run([*_base(project), *args])
    return 0


def shell(project: Project) -> int:
    """`waft odoo shell`: the Odoo shell with the existing configuration."""
    cfg = config_mod.load_config(project)
    binary = require_odoo(project)
    venv_mod._run([binary, "shell", "-c", project.odoo_conf, _port(cfg, SHELL_PORT)])
    return 0


def _module_command(project: Project, flag: str, modules: list[str]) -> None:
    cfg = config_mod.load_config(project)
    cmd = [*_base(project), flag, ",".join(modules), "--stop-after-init"]
    if cfg["PGDATABASE"]:
        cmd += ["-d", cfg["PGDATABASE"]]
    if flag == "-u" and cfg.get("ODOO_I18N_OVERWRITE", "").lower() == "true":
        cmd.append("--i18n-overwrite")
    cmd.append(_port(cfg, UTILITY_PORT))
    venv_mod._run(cmd)


def upgrade(project: Project, modules: list[str]) -> int:
    """`waft odoo upgrade {modules}`."""
    _module_command(project, "-u", modules)
    print(f"upgraded: {', '.join(modules)}")
    return 0


def install(project: Project, module: str) -> int:
    """`waft odoo install {module}`."""
    _module_command(project, "-i", [module])
    print(f"installed: {module}")
    return 0


def odoo_bin(project: Project, args: list[str]) -> int:
    """`waft odoo-bin {odoo options}`: raw .venv/bin/odoo passthrough."""
    venv_mod._run([require_odoo(project), *args])
    return 0


def reset_password(project: Project, login: str, password: str | None = None) -> int:
    """`waft odoo reset-password {login}`: set a new password via psql."""
    require_odoo(project)
    cfg = config_mod.load_config(project)
    db = cfg["PGDATABASE"]
    if not db:
        raise WaftError("PGDATABASE is not set; needed to reset a password")
    if password is None:
        password = getpass_mod.getpass(f"new password for {login!r}: ")
    if not password:
        raise WaftError("empty password; nothing done")
    env = {**os.environ, "WAFT_NEW_PASSWORD": password}
    result = venv_mod._run(
        [venv_mod.venv_python(project), "-c", _HASH_SNIPPET],
        env=env,
        capture_output=True,
        text=True,
    )
    hashed = (result.stdout or "").strip()
    if not hashed:
        raise WaftError("could not hash the password (is passlib in the venv?)")
    has_crypt = (
        database.query(
            cfg,
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'res_users' AND column_name = 'password_crypt'",
            database=db,
        )
        == "1"
    )
    column = "password_crypt" if has_crypt else "password"
    safe_login = login.replace("'", "''")
    rows = database.query(
        cfg,
        f"UPDATE res_users SET {column} = '{hashed}', active = true "
        f"WHERE login = '{safe_login}' RETURNING id",
        database=db,
    )
    if not rows:
        raise WaftError(f"no Odoo user with login {login!r} in database {db}")
    print(f"password reset for {login!r}")
    return 0


def translate_modules(
    project: Project, modules: list[str], languages: list[str]
) -> int:
    """`waft odoo modules translate`: (re)export module .po files."""
    cfg = config_mod.load_config(project)
    db = cfg["PGDATABASE"]
    if not db:
        raise WaftError("PGDATABASE is not set; needed to export translations")
    for module in modules:
        module_dir = project.addons_dir / module
        if not module_dir.is_dir():
            raise WaftError(f"module {module!r} not found in {project.addons_dir}")
        i18n_dir = module_dir / "i18n"
        i18n_dir.mkdir(exist_ok=True)
        for lang in languages:
            po_file = i18n_dir / f"{lang}.po"
            venv_mod._run(
                [
                    *_base(project),
                    "-d",
                    db,
                    f"--language={lang}",
                    f"--i18n-export={po_file}",
                    f"--modules={module}",
                    "--stop-after-init",
                    _port(cfg, UTILITY_PORT),
                ]
            )
            print(f"exported {po_file}")
    return 0
