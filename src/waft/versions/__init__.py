"""Per-Odoo-version knowledge: data, not code branches.

Everything that differs between Odoo versions (Python version, install
strategy, setuptools pins, odoo.conf key evolution, port flags) is defined
here so the rest of waft stays version-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..project import WaftError

#: Supported Odoo versions, oldest first.
SUPPORTED_VERSIONS = [f"{major}.0" for major in range(8, 20)]
LATEST = SUPPORTED_VERSIONS[-1]

_PYTHON_VERSIONS = {
    8: "2.7.18",
    9: "2.7.18",
    10: "2.7.18",
    11: "3.6.15",
    12: "3.6.15",
    13: "3.6.15",
    14: "3.8.6",
    15: "3.8.6",
    16: "3.10.6",
    17: "3.10.13",
    18: "3.12.1",
    19: "3.13.5",
}


@dataclass(frozen=True)
class OdooVersion:
    """Version-specific installation and configuration knowledge."""

    name: str  # e.g. "16.0"
    major: int
    python_version: str  # full python version, e.g. "3.10.6"
    install: str  # "pip-no-deps" | "pip" | "editable"
    setuptools: str | None  # pip constraint for setuptools, None = latest
    port_flag: str  # odoo CLI flag for the http/xmlrpc port
    db_template: str  # postgres template database
    uses_gevent: bool  # gevent_port (16+) vs longpolling_port

    @property
    def python_major(self) -> int:
        return int(self.python_version.split(".")[0])


def _build(name: str) -> OdooVersion:
    major = int(name.split(".")[0])
    if major == 8:
        install = "pip-no-deps"
    elif major <= 14:
        install = "pip"
    else:
        install = "editable"
    if major >= 18:
        setuptools = None
    elif 15 <= major <= 17:
        setuptools = ">=64,<82"
    elif major == 11:
        setuptools = "<58"
    else:
        setuptools = "<82"
    return OdooVersion(
        name=name,
        major=major,
        python_version=_PYTHON_VERSIONS[major],
        install=install,
        setuptools=setuptools,
        port_flag="http-port" if major >= 19 else "xmlrpc-port",
        db_template="template1" if major <= 13 else "template0",
        uses_gevent=major >= 16,
    )


REGISTRY: dict[str, OdooVersion] = {name: _build(name) for name in SUPPORTED_VERSIONS}


def get(version: str) -> OdooVersion:
    try:
        return REGISTRY[str(version)]
    except KeyError:
        raise WaftError(
            f"unsupported Odoo version {version!r}; "
            f"supported: {', '.join(SUPPORTED_VERSIONS)}"
        ) from None


def config_defaults(version: str) -> dict[str, str]:
    """Default configuration variables for one Odoo version.

    Variable names stay compatible with the old waftlib .env-* files.
    """
    get(version)  # validate
    return {
        "ODOO_VERSION": str(version),
        "PGDATABASE": "",
        "PGHOST": "",
        "PGPORT": "5432",
        "PGUSER": "",
        "PGPASSWORD": "",
        "ODOO_ADMIN_PASSWORD": "",
        "ODOO_DBFILTER": ".*",
        "ODOO_I18N_OVERWRITE": "false",
        "ODOO_REPOSITORY": "https://github.com/odoo/odoo.git",
        "ODOO_INITIAL_LANG": "nl_NL",
        "ODOO_LIMIT_MEMORY_HARD": "2684354560",
        "ODOO_LIMIT_MEMORY_SOFT": "2147483648",
        "ODOO_LIST_DB": "false",
        "ODOO_MAX_CRON_THREADS": "1",
        "ODOO_UNACCENT": "false",
        "ODOO_WITHOUT_DEMO": "all",
        "ODOO_WORKERS": "8",
        "QUEUE_JOB_CHANNELS": "root:1",
        "QUEUE_JOB_SCHEME": "http",
        "QUEUE_JOB_HOST": "localhost",
        "QUEUE_JOB_PORT": "8069",
        "WAFT_DEPTH_DEFAULT": "1",
        "WAFT_DEPTH_MERGE": "100",
        "WAFT_GIT_AGGREGATOR": "git-aggregator",
        "WAFT_LOG_LEVEL": "INFO",
        "WAFT_SERVICE_NAME": "odoo",
        "WAFT_UPGRADE_URL": "https://upgrade.odoo.com/upgrade",
    }


def odoo_conf_template(version: str) -> dict[str, dict[str, str]]:
    """odoo.conf sections/keys for one Odoo version.

    Values may reference configuration variables as ``${VAR}``; waft
    substitutes them safely (a missing variable is a clear error, never a
    raw KeyError). ``WAFT_*`` variables are computed by waft itself.
    """
    info = get(version)
    options: dict[str, str] = {
        "addons_path": "${WAFT_ADDONS_PATH}",
        "admin_passwd": "${ODOO_ADMIN_PASSWORD}",
        "data_dir": "${WAFT_DATA_DIR}",
        "db_host": "${PGHOST}",
        "db_name": "${PGDATABASE}",
        "db_password": "${PGPASSWORD}",
        "db_port": "${PGPORT}",
        "db_template": info.db_template,
        "db_user": "${PGUSER}",
        "dbfilter": "${ODOO_DBFILTER}",
        "lang": "${ODOO_INITIAL_LANG}",
        "limit_memory_hard": "${ODOO_LIMIT_MEMORY_HARD}",
        "limit_memory_soft": "${ODOO_LIMIT_MEMORY_SOFT}",
        "list_db": "${ODOO_LIST_DB}",
        "max_cron_threads": "${ODOO_MAX_CRON_THREADS}",
        "unaccent": "${ODOO_UNACCENT}",
        "without_demo": "${ODOO_WITHOUT_DEMO}",
        "workers": "${ODOO_WORKERS}",
    }
    if info.uses_gevent:
        options["gevent_port"] = "8072"
    else:
        options["longpolling_port"] = "8072"
    queue_job = {
        "channels": "${QUEUE_JOB_CHANNELS}",
        "scheme": "${QUEUE_JOB_SCHEME}",
        "host": "${QUEUE_JOB_HOST}",
        "port": "${QUEUE_JOB_PORT}",
    }
    return {"options": options, "queue_job": queue_job}
