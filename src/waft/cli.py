"""waft command line interface: grouped subcommands, argparse only."""

from __future__ import annotations

import argparse
import sys

from . import PROJECT_FORMAT, __version__, scaffold, versions
from . import addons as addons_mod
from . import config as config_mod
from . import database as database_mod
from . import migrate as migrate_mod
from . import odoo as odoo_mod
from . import service as service_mod
from . import venv as venv_mod
from .project import Project, WaftError, command_log, find_project, require_project


def _project(args) -> Project:
    return require_project(args.directory)


# ----------------------------------------------------------------- handlers
def cmd_init(args) -> int:
    odoo_version = args.odoo_version
    if not odoo_version:
        if not sys.stdin.isatty():
            raise WaftError("--odoo-version is required when not running interactively")
        answer = input(f"Odoo version [{versions.LATEST}]: ").strip()
        odoo_version = answer or versions.LATEST
    project = scaffold.init_project(
        args.directory or ".",
        odoo_version,
        db_name=args.db_name,
        db_user=args.db_user,
        db_password=args.db_password,
        db_host=args.db_host,
        db_port=args.db_port,
    )
    print(f"initialized waft project for Odoo {odoo_version} in {project.root}")
    print(f"next: review {project.secret_yml}, then run 'waft sync'")
    return 0


def cmd_sync(args) -> int:
    return scaffold.sync(_project(args))


def cmd_info(args) -> int:
    project = _project(args)
    cfg = config_mod.load_config(project)
    info = versions.get(cfg["ODOO_VERSION"])
    addon_count = (
        sum(
            1
            for entry in project.addons_dir.iterdir()
            if not entry.name.startswith(".")
        )
        if project.addons_dir.is_dir()
        else 0
    )
    rows = [
        ("project", str(project.root)),
        ("waft version", __version__),
        (
            "project format",
            f"{scaffold.read_version_stamp(project)} (current: {PROJECT_FORMAT})",
        ),
        ("odoo version", info.name),
        ("python version", info.python_version),
        (
            "virtualenv",
            str(project.venv_dir) if project.venv_dir.is_dir() else "not created",
        ),
        ("database", cfg["PGDATABASE"] or "(not set)"),
        ("database host", cfg["PGHOST"] or "(local socket)"),
        ("database port", cfg["PGPORT"]),
        ("database user", cfg["PGUSER"] or "(current user)"),
        ("addons", str(addon_count)),
        ("odoo config", str(project.odoo_conf)),
        ("log file", str(project.waft_log)),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{label.ljust(width)}  {value}")
    return 0


def _remainder(args_list: list[str]) -> list[str]:
    """Drop the leading '--' separator from argparse REMAINDER lists."""
    return args_list[1:] if args_list and args_list[0] == "--" else args_list


def cmd_pip(args) -> int:
    return venv_mod.pip(_project(args), _remainder(args.pip_args))


def cmd_migrate(args) -> int:
    return migrate_mod.migrate(_project(args), _remainder(args.migrate_args))


def cmd_odoo_run(args) -> int:
    return odoo_mod.run(_project(args), _remainder(args.odoo_args))


def cmd_odoo_shell(args) -> int:
    return odoo_mod.shell(_project(args))


def cmd_odoo_upgrade(args) -> int:
    return odoo_mod.upgrade(_project(args), args.modules)


def cmd_odoo_install(args) -> int:
    return odoo_mod.install(_project(args), args.module)


def cmd_odoo_requirements(args) -> int:
    if not args.update:
        raise WaftError("nothing to do; use 'waft odoo requirements --update'")
    return venv_mod.update_requirements(_project(args))


def cmd_odoo_reset_password(args) -> int:
    return odoo_mod.reset_password(_project(args), args.login, args.password)


def cmd_odoo_modules_translate(args) -> int:
    modules = [m.strip() for m in args.modules.split(",") if m.strip()]
    languages = [lang.strip() for lang in args.languages.split(",") if lang.strip()]
    if not modules or not languages:
        raise WaftError("both --modules and --languages are required")
    return odoo_mod.translate_modules(_project(args), modules, languages)


def cmd_odoo_config_set(args) -> int:
    project = _project(args)
    target = config_mod.config_set(project, args.assignment)
    var = args.assignment.partition("=")[0].strip()
    print(f"set {var} in {target}")
    print(f"regenerated {project.odoo_conf}")
    return 0


def cmd_odoo_config_list(args) -> int:
    for key, value in config_mod.config_list(_project(args)):
        print(f"{key}={value}")
    return 0


def cmd_odoo_config_remove(args) -> int:
    project = _project(args)
    removed = config_mod.config_remove(project, args.variable)
    for path in removed:
        print(f"removed {args.variable} from {path}")
    print(f"regenerated {project.odoo_conf}")
    return 0


def cmd_odoo_config_test(args) -> int:
    problems = config_mod.config_test(_project(args))
    for problem in problems:
        print(problem)
    errors = [p for p in problems if not p.startswith("warning:")]
    if not errors:
        print("configuration OK")
    return 1 if errors else 0


def _addon_options(args) -> dict:
    """Collect addon source options from CLI flags into an ADDONS mapping."""
    options: dict = {}
    for key in ("install", "url", "branch", "depth", "path", "spec"):
        value = getattr(args, f"addon_{key}")
        if value:
            options[key] = value
    if args.addon_merge:
        options["merges"] = list(args.addon_merge)
    if args.addon_gitignore is not None:
        options["gitignore"] = args.addon_gitignore
    return options


def cmd_odoo_addon(args) -> int:
    project = _project(args)
    options = _addon_options(args)
    if args.list:
        return addons_mod.addon_list(project)
    if args.add:
        return addons_mod.addon_add(project, args.add, options)
    if args.delete:
        return addons_mod.addon_delete(project, args.delete)
    if args.update:
        return addons_mod.addon_update(project, args.update)
    if args.config:
        return addons_mod.addon_configure(project, args.config, options)
    raise WaftError("choose one of --add/--delete/--update/--config/--list")


def cmd_odoo_bin(args) -> int:
    return odoo_mod.odoo_bin(_project(args), _remainder(args.odoo_args))


def cmd_database_initial(args) -> int:
    return database_mod.initial(_project(args))


def cmd_service(args) -> int:
    return service_mod.manage(_project(args), args.action)


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="waft",
        description="Waft: an Odoo installation and project-management tool.",
    )
    parser.add_argument(
        "-d",
        "--directory",
        metavar="PATH",
        default=None,
        help="run waft against a project directory from outside it",
    )
    parser.add_argument("--version", action="version", version=f"waft {__version__}")
    sub = parser.add_subparsers(dest="group", metavar="<command>")

    p = sub.add_parser("init", help="prepare the project directories and files tree")
    p.add_argument(
        "--odoo-version",
        help=f"Odoo version ({', '.join(versions.SUPPORTED_VERSIONS)})",
    )
    p.add_argument(
        "--db-name", default="", help="PGDATABASE value (stored in secret.yml)"
    )
    p.add_argument("--db-user", default="", help="PGUSER value")
    p.add_argument(
        "--db-password", default="", help="PGPASSWORD value (stored in secret.yml)"
    )
    p.add_argument("--db-host", default="", help="PGHOST value")
    p.add_argument("--db-port", default="", help="PGPORT value")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser(
        "sync", help="idempotent convergence: venv, Odoo, config, addons"
    )
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("info", help="show the project configuration")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("pip", help="manage PyPI packages inside the venv")
    p.add_argument("pip_args", nargs=argparse.REMAINDER, metavar="pip options")
    p.set_defaults(func=cmd_pip)

    p = sub.add_parser("migrate", help="migrate Odoo to a higher version")
    p.add_argument("migrate_args", nargs=argparse.REMAINDER, metavar="options")
    p.set_defaults(func=cmd_migrate)

    odoo = sub.add_parser("odoo", help="Odoo commands (run, shell, config, addon, ...)")
    odoo_sub = odoo.add_subparsers(
        dest="odoo_command", metavar="<command>", required=True
    )

    p = odoo_sub.add_parser("run", help="run Odoo with logs in the interactive session")
    p.add_argument("odoo_args", nargs=argparse.REMAINDER, metavar="odoo options")
    p.set_defaults(func=cmd_odoo_run)

    p = odoo_sub.add_parser("shell", help="run the Odoo shell")
    p.set_defaults(func=cmd_odoo_shell)

    p = odoo_sub.add_parser("upgrade", help="upgrade Odoo modules")
    p.add_argument("modules", nargs="+", help="module names")
    p.set_defaults(func=cmd_odoo_upgrade)

    p = odoo_sub.add_parser("install", help="install an Odoo module")
    p.add_argument("module")
    p.set_defaults(func=cmd_odoo_install)

    p = odoo_sub.add_parser(
        "requirements", help="update PyPI packages from requirements.txt"
    )
    p.add_argument("-u", "--update", action="store_true", help="apply the requirements")
    p.set_defaults(func=cmd_odoo_requirements)

    p = odoo_sub.add_parser("reset-password", help="reset an Odoo user password")
    p.add_argument("login")
    p.add_argument("--password", help="the new password (prompted for when omitted)")
    p.set_defaults(func=cmd_odoo_reset_password)

    modules = odoo_sub.add_parser("modules", help="module tools")
    modules_sub = modules.add_subparsers(
        dest="modules_command", metavar="<command>", required=True
    )
    p = modules_sub.add_parser("translate", help="(re)export module translations")
    p.add_argument(
        "-m", "--modules", required=True, metavar="MOD[,MOD...]", help="module names"
    )
    p.add_argument(
        "-l",
        "--languages",
        required=True,
        metavar="LANG[,LANG...]",
        help="language codes, e.g. nl,fr",
    )
    p.set_defaults(func=cmd_odoo_modules_translate)

    cfg = odoo_sub.add_parser("config", help="manage the Odoo configuration")
    cfg_sub = cfg.add_subparsers(
        dest="config_command", metavar="<command>", required=True
    )
    p = cfg_sub.add_parser(
        "set", help="set VARIABLE=value in shared.yml/secret.yml + odoo.conf"
    )
    p.add_argument("assignment", metavar="VARIABLE=value")
    p.set_defaults(func=cmd_odoo_config_set)
    p = cfg_sub.add_parser("list", help="list the effective configuration")
    p.set_defaults(func=cmd_odoo_config_list)
    p = cfg_sub.add_parser("remove", help="remove a configuration variable")
    p.add_argument("variable")
    p.set_defaults(func=cmd_odoo_config_remove)
    p = cfg_sub.add_parser("test", help="validate the configuration files")
    p.set_defaults(func=cmd_odoo_config_test)

    p = odoo_sub.add_parser("addon", help="declare and manage addons")
    p.add_argument("-a", "--add", metavar="NAME", help="declare an addon entry")
    p.add_argument("-d", "--delete", metavar="NAME", help="remove an addon entry")
    p.add_argument(
        "-u", "--update", metavar="NAME", help="refresh an entry from its source"
    )
    p.add_argument("-c", "--config", metavar="NAME", help="reconfigure an entry")
    p.add_argument("-l", "--list", action="store_true", help="list declared addons")
    p.add_argument(
        "-t",
        "--installation-type",
        dest="addon_install",
        choices=addons_mod.INSTALL_TYPES,
        help="clone (fetch a repository), pypi (pip install into the venv) or "
        "source (add its directory to addons_path); inferred from "
        "--url/--spec when omitted",
    )
    p.add_argument(
        "--url", dest="addon_url", help="git repository URL (installation type clone)"
    )
    p.add_argument(
        "--branch", dest="addon_branch", help="git branch; default ${ODOO_VERSION}"
    )
    p.add_argument(
        "--merge",
        dest="addon_merge",
        action="append",
        metavar="REMOTE REF",
        help="git-aggregator merge line; repeatable",
    )
    p.add_argument("--depth", dest="addon_depth", help="git clone/fetch depth")
    p.add_argument(
        "--path",
        dest="addon_path",
        help="directory of the addon or clone, e.g. addons/addons-example/addony",
    )
    p.add_argument(
        "--spec",
        dest="addon_spec",
        help="pip requirement for installation type pypi; any form pip or pipx "
        "accepts, including a browser URL like "
        "https://host/org/repo/tree/BRANCH/addons/addon",
    )
    p.add_argument(
        "--gitignore",
        dest="addon_gitignore",
        action="store_true",
        default=None,
        help="keep this directory out of git (default for clones)",
    )
    p.add_argument(
        "--no-gitignore",
        dest="addon_gitignore",
        action="store_false",
        help="track this directory in the project repository",
    )
    p.set_defaults(func=cmd_odoo_addon)

    p = sub.add_parser("odoo-bin", help="run .venv/bin/odoo with raw options")
    p.add_argument("odoo_args", nargs=argparse.REMAINDER, metavar="odoo options")
    p.set_defaults(func=cmd_odoo_bin)

    database = sub.add_parser("database", help="database commands")
    database_sub = database.add_subparsers(
        dest="database_command", metavar="<command>", required=True
    )
    p = database_sub.add_parser("initial", help="initialize the default Odoo database")
    p.set_defaults(func=cmd_database_initial)

    p = sub.add_parser("service", help="manage the Odoo systemd service")
    p.add_argument("action", choices=["start", "stop", "restart"])
    p.set_defaults(func=cmd_service)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        project = find_project(args.directory) if args.group != "init" else None
        with command_log(project, argv):
            return int(args.func(args) or 0)
    except WaftError as exc:
        print(f"waft: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("waft: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
