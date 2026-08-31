"""Virtual environment management.

Odoo 14.0+ (Python >= 3.8): uv creates the venv and installs the Python
interpreter (https://docs.astral.sh/uv).

Odoo 8.0-13.0 (Python 2.7 / 3.6): uv cannot install interpreters that old.
Waft needs a matching system binary (python2.7 / python3.6) and creates the
venv with a pinned virtualenv==20.15.1 - the last release able to create
Python 2.7 and 3.6 environments - executed via "uv tool run" so nothing is
installed globally.

uv itself is a dependency of the waft package, so "pipx install waft"
already provides it; waft prefers a uv found on PATH and falls back to the
bundled one. Missing host tools (uv, or an old Python) are installed into
the system environment when possible - installing system packages goes
through sudo, which may ask for a password.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as config_mod
from . import versions
from .project import Project, WaftError

#: Last virtualenv release that can create Python 2.7 and 3.6 environments.
VIRTUALENV_PIN = "virtualenv==20.15.1"

#: Minimum Python 3 minor version installable by uv (python-build-standalone).
_UV_MIN_PY3_MINOR = 7


def _run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    """Run a command, echoing it; raise a WaftError on failure."""
    printable = " ".join(str(part) for part in cmd)
    print(f"+ {printable}")
    try:
        return subprocess.run([str(part) for part in cmd], check=True, **kwargs)
    except FileNotFoundError as exc:
        raise WaftError(f"command not found: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise WaftError(
            f"command failed with exit code {exc.returncode}: {printable}"
        ) from exc


def _bundled_uv() -> str | None:
    """The uv shipped with the waft package (uv.find_uv_bin)."""
    try:
        from uv import find_uv_bin
    except ImportError:
        return None
    try:
        path = find_uv_bin()
    except FileNotFoundError:
        return None
    return path if Path(path).exists() else None


def install_uv() -> str | None:
    """Install uv into the system environment; returns its path or None."""
    print("uv not found; installing it in the system environment")
    attempts = [["pipx", "install", "uv"]]
    if not _in_virtualenv():
        attempts.append([sys.executable, "-m", "pip", "install", "--user", "uv"])
    for cmd in attempts:
        if cmd[0] != sys.executable and shutil.which(cmd[0]) is None:
            continue
        print(f"+ {' '.join(cmd)}")
        if subprocess.run(cmd, check=False).returncode == 0:
            found = shutil.which("uv") or _bundled_uv()
            if found:
                return found
    return None


def _in_virtualenv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def uv_binary() -> str:
    """uv from PATH, else the bundled one, else install it."""
    uv = shutil.which("uv") or _bundled_uv() or install_uv()
    if not uv:
        raise WaftError(
            "uv is required but could not be installed automatically; "
            "install it with 'pipx install uv' or see "
            "https://docs.astral.sh/uv/getting-started/installation/"
        )
    return uv


def apt_install(packages: list[str], ppa: str | None = None) -> bool:
    """Install system packages with apt-get; True when it succeeded.

    Uses sudo unless already root, so this may prompt for a password.
    """
    if shutil.which("apt-get") is None:
        return False
    root = getattr(os, "geteuid", lambda: 1)() == 0
    sudo: list[str] = []
    if not root:
        if shutil.which("sudo") is None:
            return False
        sudo = ["sudo"]
    steps = []
    if ppa and shutil.which("add-apt-repository"):
        steps.append([*sudo, "add-apt-repository", "-y", ppa])
    steps.append([*sudo, "apt-get", "update"])
    steps.append([*sudo, "apt-get", "install", "-y", *packages])
    for cmd in steps:
        print(f"+ {' '.join(cmd)}")
        if subprocess.run(cmd, check=False).returncode != 0:
            return False
    return True


def python_spec(info: versions.OdooVersion) -> tuple[str, bool]:
    """Return (spec, uv_managed) for the version's Python interpreter.

    uv_managed=True: spec is a version like "3.10" that uv installs itself.
    uv_managed=False: spec is a binary name like "python2.7" that must exist
    on the system.
    """
    major, minor = (int(part) for part in info.python_version.split(".")[:2])
    if major >= 3 and minor >= _UV_MIN_PY3_MINOR:
        return f"{major}.{minor}", True
    return f"python{major}.{minor}", False


def system_python(binary: str) -> str:
    """An old interpreter (python2.7 / python3.6) from the system.

    Installed with apt when missing - first from the distribution, then from
    the deadsnakes PPA, which carries interpreters Ubuntu has dropped.
    """
    path = shutil.which(binary)
    if path:
        return path
    print(f"{binary} not found; installing it in the system environment")
    packages = [binary, f"{binary}-dev"]
    for ppa in (None, "ppa:deadsnakes/ppa"):
        if apt_install(packages, ppa=ppa):
            path = shutil.which(binary)
            if path:
                return path
    raise WaftError(
        f"{binary} is required for Odoo {'.'.join(binary[len('python'):].split('.'))} "
        f"but could not be installed automatically.\n"
        f"Ubuntu may no longer package it; try:\n"
        f"    sudo add-apt-repository ppa:deadsnakes/ppa\n"
        f"    sudo apt update && sudo apt install {binary} {binary}-dev\n"
        f"or build it from source (for example with pyenv), then re-run "
        f"'waft sync'."
    )


def venv_python(project: Project) -> Path:
    return project.venv_dir / "bin" / "python"


def venv_exists(project: Project) -> bool:
    return venv_python(project).exists()


def ensure_venv(project: Project, cfg: dict[str, str] | None = None) -> bool:
    """Create .venv if missing; returns True when newly created."""
    if venv_exists(project):
        return False
    cfg = cfg if cfg is not None else config_mod.load_config(project)
    info = versions.get(cfg["ODOO_VERSION"])
    spec, managed = python_spec(info)
    if managed:
        _run([uv_binary(), "venv", "--python", spec, project.venv_dir])
    else:
        binary = system_python(spec)
        _run(
            [
                uv_binary(),
                "tool",
                "run",
                "--from",
                VIRTUALENV_PIN,
                "virtualenv",
                "--python",
                binary,
                project.venv_dir,
            ]
        )
    return True


def _pip_command(project: Project, info: versions.OdooVersion, args: list[str]) -> list:
    """Build the pip command for the project's venv.

    uv-managed interpreters use "uv pip"; 2.7/3.6 venvs use their own pip.
    """
    _, managed = python_spec(info)
    if managed:
        return [
            uv_binary(),
            "pip",
            args[0],
            "--python",
            venv_python(project),
            *args[1:],
        ]
    return [project.venv_dir / "bin" / "pip", *args]


def run_pip(project: Project, cfg: dict[str, str], args: list[str]) -> None:
    """Run one pip operation inside the project's venv."""
    info = versions.get(cfg["ODOO_VERSION"])
    _run(_pip_command(project, info, list(args)))


def pip(project: Project, args: list[str]) -> int:
    """`waft pip {pip options}`."""
    if not args:
        raise WaftError(
            "usage: waft pip <pip options>, e.g. 'waft pip install requests'"
        )
    if not venv_exists(project):
        raise WaftError("no virtual environment yet; run 'waft sync' first")
    cfg = config_mod.load_config(project)
    run_pip(project, cfg, args)
    return 0


def default_requirements(info: versions.OdooVersion) -> Path | None:
    """Per-version pinned requirements shipped inside the waft package."""
    path = Path(__file__).parent / "versions" / info.name / "requirements-default.txt"
    return path if path.is_file() else None


def update_requirements(project: Project, cfg: dict[str, str] | None = None) -> int:
    """Install the setuptools pin, per-version defaults, then project extras.

    Order (mirrors the old waftlib build script):
    1. setuptools pin for the Odoo version (waft.versions data);
    2. per-version pinned defaults shipped inside waft, when present;
    3. the project's own requirements.txt on top.
    """
    cfg = cfg if cfg is not None else config_mod.load_config(project)
    ensure_venv(project, cfg)
    info = versions.get(cfg["ODOO_VERSION"])
    if info.setuptools:
        run_pip(project, cfg, ["install", f"setuptools{info.setuptools}"])
    defaults = default_requirements(info)
    if defaults is not None:
        run_pip(project, cfg, ["install", "-r", str(defaults)])
    if project.requirements_txt.is_file():
        run_pip(project, cfg, ["install", "-r", str(project.requirements_txt)])
    return 0
