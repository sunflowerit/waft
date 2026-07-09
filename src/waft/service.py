"""systemd service management.

`waft service start|stop|restart` tries plain systemctl first (works when
the user has been granted rights), then non-interactive sudo. When both
fail it prints help explaining how to install .waft/template/odoo.service
and how to grant the user the needed rights.
"""

from __future__ import annotations

import getpass

from . import config as config_mod
from . import venv as venv_mod
from .project import Project

ACTIONS = ("start", "stop", "restart")


def manage(project: Project, action: str) -> int:
    """`waft service start|stop|restart`."""
    cfg = config_mod.load_config(project)
    unit = cfg.get("WAFT_SERVICE_NAME") or "odoo"
    attempts = (
        ["systemctl", action, unit],
        ["sudo", "-n", "systemctl", action, unit],
    )
    for cmd in attempts:
        print(f"+ {' '.join(cmd)}")
        try:
            result = venv_mod.subprocess.run(cmd, check=False)
        except FileNotFoundError:
            continue
        if result.returncode == 0:
            print(f"service {unit}: {action} OK")
            return 0
    _print_help(project, unit, action)
    return 1


def _print_help(project: Project, unit: str, action: str) -> None:
    unit_file = project.template_dir / "odoo.service"
    user = getpass.getuser()
    print(f"""\
could not {action} the '{unit}' systemd service.

If the service is not installed yet, install it as root:

    cp {unit_file} /etc/systemd/system/{unit}.service
    systemctl daemon-reload
    systemctl enable --now {unit}

To let user '{user}' manage the service without a password, add a
sudoers drop-in (as root):

    echo '{user} ALL=(root) NOPASSWD: /usr/bin/systemctl start {unit}, \
/usr/bin/systemctl stop {unit}, /usr/bin/systemctl restart {unit}' \
> /etc/sudoers.d/waft-{unit}
    chmod 440 /etc/sudoers.d/waft-{unit}

Then re-run: waft service {action}""")
