"""Virtual environment management.

Odoo 14.0+ (Python >= 3.8): uv creates the venv and installs the Python
interpreter (https://docs.astral.sh/uv).

Odoo 8.0-13.0 (Python 2.7 / 3.6): uv cannot install interpreters that old.
Waft requires a matching system binary (python2.7 / python3.6, e.g. from the
deadsnakes PPA) and creates the venv with a pinned virtualenv==20.15.1 - the
last release able to create Python 2.7 and 3.6 environments - executed via
"uv tool run" so nothing is installed globally.
"""

from __future__ import annotations

import shutil
import subprocess
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


def uv_binary() -> str:
    uv = shutil.which("uv")
    if not uv:
        raise WaftError(
            "uv is not installed; see "
            "https://docs.astral.sh/uv/getting-started/installation/"
        )
    return uv


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
    path = shutil.which(binary)
    if path:
        return path
    raise WaftError(
        f"{binary} is required for this Odoo version but was not found.\n"
        f"Install it first, for example on Ubuntu:\n"
        f"    sudo add-apt-repository ppa:deadsnakes/ppa\n"
        f"    sudo apt update && sudo apt install {binary} {binary}-dev\n"
        f"then re-run 'waft sync'."
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
