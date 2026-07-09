import subprocess

import pytest

from waft import database, odoo
from waft import venv as venv_mod
from waft.project import WaftError
from waft.scaffold import init_project


@pytest.fixture
def project(tmp_path):
    project = init_project(tmp_path, "16.0", db_name="testdb")
    bin_dir = project.venv_dir / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "odoo").touch()
    (bin_dir / "python").touch()
    return project


@pytest.fixture
def calls(monkeypatch):
    recorded = []
    outputs = {}

    def fake_run(cmd, check=True, **kwargs):
        cmd = [str(part) for part in cmd]
        recorded.append(cmd)
        stdout = ""
        for token, value in outputs.items():
            if any(token in part for part in cmd):
                stdout = value
                break
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout)

    fake_run.outputs = outputs
    monkeypatch.setattr(venv_mod.subprocess, "run", fake_run)
    return recorded


def test_run_passes_args(project, calls):
    assert odoo.run(project, ["--dev=all"]) == 0
    assert calls == [
        [
            str(project.venv_dir / "bin" / "odoo"),
            "-c",
            str(project.odoo_conf),
            "--dev=all",
        ]
    ]


def test_run_requires_install(tmp_path):
    project = init_project(tmp_path, "16.0")
    with pytest.raises(WaftError, match="run 'waft sync' first"):
        odoo.run(project, [])


def test_shell_uses_side_port(project, calls):
    odoo.shell(project)
    assert calls[0][1] == "shell"
    assert "--xmlrpc-port=18080" in calls[0]  # 16.0 still uses xmlrpc-port


def test_upgrade_builds_command(project, calls):
    odoo.upgrade(project, ["web", "base"])
    cmd = calls[0]
    assert "-u" in cmd and "web,base" in cmd
    assert "--stop-after-init" in cmd
    assert "-d" in cmd and "testdb" in cmd
    assert "--xmlrpc-port=18069" in cmd
    assert "--i18n-overwrite" not in cmd


def test_upgrade_i18n_overwrite(project, calls):
    from waft import config

    config.config_set(project, "ODOO_I18N_OVERWRITE=true")
    odoo.upgrade(project, ["web"])
    assert "--i18n-overwrite" in calls[0]


def test_install_builds_command(project, calls):
    odoo.install(project, "web")
    cmd = calls[0]
    assert "-i" in cmd and "web" in cmd


def test_odoo_bin_raw(project, calls):
    odoo.odoo_bin(project, ["--version"])
    assert calls == [[str(project.venv_dir / "bin" / "odoo"), "--version"]]


def test_reset_password(project, calls):
    calls_fake = venv_mod.subprocess.run
    calls_fake.outputs["passlib"] = "HASHED\n"
    calls_fake.outputs["RETURNING"] = "5\n"
    assert odoo.reset_password(project, "admin", password="secret") == 0
    joined = ["\x00".join(cmd) for cmd in calls]
    assert any("passlib" in cmd for cmd in joined)
    update = next(cmd for cmd in calls if any("RETURNING" in part for part in cmd))
    sql = " ".join(update)
    assert "password = 'HASHED'" in sql
    assert "login = 'admin'" in sql


def test_reset_password_no_user(project, calls):
    venv_mod.subprocess.run.outputs["passlib"] = "HASHED\n"
    with pytest.raises(WaftError, match="no Odoo user with login"):
        odoo.reset_password(project, "ghost", password="secret")


def test_reset_password_empty(project, calls):
    with pytest.raises(WaftError, match="empty password"):
        odoo.reset_password(project, "admin", password="")


def test_translate_exports_po(project, calls):
    module = project.addons_dir / "my_module"
    module.mkdir()
    (module / "__manifest__.py").write_text("{}")
    odoo.translate_modules(project, ["my_module"], ["nl", "fr"])
    exports = [cmd for cmd in calls if any("--i18n-export" in part for part in cmd)]
    assert len(exports) == 2
    assert any("nl.po" in part for part in exports[0])
    assert any("fr.po" in part for part in exports[1])
    assert (module / "i18n").is_dir()


def test_translate_missing_module(project, calls):
    with pytest.raises(WaftError, match="not found"):
        odoo.translate_modules(project, ["nope"], ["nl"])


def test_database_initial_creates_and_inits(project, calls):
    assert database.initial(project) == 0
    joined = [" ".join(cmd) for cmd in calls]
    assert any("pg_database" in cmd for cmd in joined)  # existence check
    assert any(cmd[0] == "createdb" for cmd in calls)
    odoo_cmd = calls[-1]
    assert "-i" in odoo_cmd and "base" in odoo_cmd
    assert "--stop-after-init" in odoo_cmd


def test_database_initial_already_initialized(project, calls):
    venv_mod.subprocess.run.outputs["pg_database"] = "1\n"
    venv_mod.subprocess.run.outputs["ir_module_module"] = "1\n"
    assert database.initial(project) == 0
    assert not any(cmd[0] == "createdb" for cmd in calls)
    assert not any("-i" in cmd for cmd in calls)


def test_database_initial_requires_dbname(tmp_path, calls):
    project = init_project(tmp_path, "16.0")
    (project.venv_dir / "bin").mkdir(parents=True)
    (project.venv_dir / "bin" / "odoo").touch()
    with pytest.raises(WaftError, match="PGDATABASE is not set"):
        database.initial(project)


def test_pg_env_applies_config(project):
    from waft.config import load_config

    env = database.pg_env(load_config(project))
    assert env["PGDATABASE"] == "testdb"
    assert env["PGPORT"] == "5432"
