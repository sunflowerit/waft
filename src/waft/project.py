"""Project discovery, paths and command logging for a waft project.

A *waft project* (WORK_WAFT_DIRECTORY) is a directory containing a `.waft/`
subdirectory, normally a clone of the customer waft repository.
"""

from __future__ import annotations

import contextlib
import datetime
import sys
from pathlib import Path


class WaftError(Exception):
    """A user-facing waft error. The CLI prints it and exits non-zero."""


WAFT_DIR = ".waft"


class Project:
    """Paths and state of one waft project."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()

    # -- directories -------------------------------------------------
    @property
    def waft_dir(self) -> Path:
        return self.root / WAFT_DIR

    @property
    def conf_dir(self) -> Path:
        return self.waft_dir / "conf"

    @property
    def data_dir(self) -> Path:
        return self.waft_dir / "data"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backup"

    @property
    def odoo_data_dir(self) -> Path:
        return self.data_dir / "Odoo"

    @property
    def log_dir(self) -> Path:
        return self.waft_dir / "log"

    @property
    def template_dir(self) -> Path:
        return self.waft_dir / "template"

    @property
    def addons_dir(self) -> Path:
        return self.root / "addons"

    @property
    def tmp_dir(self) -> Path:
        return self.root / ".tmp"

    @property
    def odoo_dir(self) -> Path:
        """The Odoo source checkout, inside the addons path."""
        return self.addons_dir / "odoo"

    @property
    def venv_dir(self) -> Path:
        return self.root / ".venv"

    # -- files -------------------------------------------------------
    @property
    def shared_yml(self) -> Path:
        return self.conf_dir / "shared.yml"

    @property
    def secret_yml(self) -> Path:
        return self.conf_dir / "secret.yml"

    @property
    def odoo_conf(self) -> Path:
        return self.conf_dir / "odoo.conf"

    @property
    def version_file(self) -> Path:
        return self.waft_dir / "version"

    @property
    def waft_log(self) -> Path:
        return self.log_dir / "waft.log"

    @property
    def odoo_log(self) -> Path:
        return self.log_dir / "odoo.log"

    @property
    def gitignore(self) -> Path:
        return self.root / ".gitignore"

    @property
    def requirements_txt(self) -> Path:
        return self.root / "requirements.txt"

    def exists(self) -> bool:
        return self.waft_dir.is_dir()

    def __repr__(self) -> str:  # pragma: no cover
        return f"Project({str(self.root)!r})"


def find_project(start: Path | str | None = None) -> Project | None:
    """Locate the waft project containing *start* (or cwd), walking upwards."""
    path = Path(start) if start else Path.cwd()
    path = path.resolve()
    for candidate in (path, *path.parents):
        if (candidate / WAFT_DIR).is_dir():
            return Project(candidate)
    return None


def require_project(start: Path | str | None = None) -> Project:
    project = find_project(start)
    if project is None:
        where = Path(start).resolve() if start else Path.cwd()
        raise WaftError(
            f"no waft project found at or above '{where}'. "
            "Run 'waft init' to create one, or use 'waft -d PATH'."
        )
    return project


class _Tee:
    """Write-through stream duplicating output into the waft log file."""

    def __init__(self, stream, logfile):
        self._stream = stream
        self._logfile = logfile

    def write(self, data: str) -> int:
        self._logfile.write(data)
        return self._stream.write(data)

    def flush(self) -> None:
        self._logfile.flush()
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


@contextlib.contextmanager
def command_log(project: Project | None, argv: list[str]):
    """Tee stdout/stderr of one waft command into .waft/log/waft.log."""
    if project is None or not project.exists():
        yield
        return
    project.log_dir.mkdir(parents=True, exist_ok=True)
    with open(project.waft_log, "a", encoding="utf-8") as logfile:
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        logfile.write(f"\n----- {stamp} waft {' '.join(argv)} -----\n")
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = _Tee(old_out, logfile)
        sys.stderr = _Tee(old_err, logfile)
        try:
            yield
        finally:
            sys.stdout, sys.stderr = old_out, old_err
