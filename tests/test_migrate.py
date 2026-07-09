import subprocess

import pytest

from waft import migrate
from waft import venv as venv_mod
from waft.config import load_config
from waft.project import Project, WaftError
from waft.scaffold import init_project


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


@pytest.fixture
def project(tmp_path):
    return init_project(tmp_path, "16.0", db_name="proddb")


@pytest.fixture
def fake_step(monkeypatch, project):
    """Avoid the heavy nested build: reuse the main project as step project."""
    built = []

    def fake_ensure(main_project, version, cfg):
        built.append(version)
        return main_project, None

    monkeypatch.setattr(migrate, "ensure_step_project", fake_ensure)
    return built


def test_plan_steps():
    assert migrate.plan_steps("14.0", "17.0") == ["15.0", "16.0", "17.0"]
    with pytest.raises(WaftError, match="already on Odoo"):
        migrate.plan_steps("16.0", "16.0")
    with pytest.raises(WaftError, match="migrate down"):
        migrate.plan_steps("17.0", "16.0")
    with pytest.raises(WaftError, match="unsupported Odoo version"):
        migrate.plan_steps("7.0", "16.0")


def test_detect_db_version(project, calls):
    venv_mod.subprocess.run.outputs["latest_version"] = "14.0.1.3\n"
    assert migrate.detect_db_version(load_config(project), "proddb") == "14.0"


def test_detect_db_version_unreadable(project, calls):
    with pytest.raises(WaftError, match="could not detect"):
        migrate.detect_db_version(load_config(project), "proddb")


def test_progress_roundtrip_and_reset(project):
    progress = migrate.load_progress(project)
    progress["versions"]["15.0"] = {"upgrade": True}
    progress["versions"]["16.0"] = {"backup": True}
    progress["hooks"]["pre-upgrade-16.0"] = ["10-a.sql"]
    progress["hooks"]["pre-migration"] = ["00-x.sh"]
    migrate.save_progress(project, progress)

    migrate.reset_progress(project, "16.0")
    progress = migrate.load_progress(project)
    assert "15.0" in progress["versions"]
    assert "16.0" not in progress["versions"]
    assert "pre-upgrade-16.0" not in progress["hooks"]
    assert progress["hooks"]["pre-migration"] == ["00-x.sh"]

    migrate.reset_progress(project, "all")
    assert migrate.load_progress(project) == {"versions": {}, "hooks": {}}


def test_migrate_plan_only(project, calls):
    venv_mod.subprocess.run.outputs["latest_version"] = "14.0.1.3\n"
    code = migrate.migrate(project, ["--plan"])
    assert code == 0
    assert not any(cmd[0] == "createdb" for cmd in calls)
    assert not migrate.progress_file(project).is_file()


def test_run_hooks_order_resume(project, calls):
    hooks = migrate.hooks_dir(project) / "pre-migration"
    hooks.mkdir(parents=True)
    (hooks / "20-b.sh").write_text("true\n")
    (hooks / "10-a.sql").write_text("SELECT 1;\n")
    (hooks / "30-c.py").write_text("pass\n")
    (hooks / "README.txt").write_text("ignored\n")
    cfg = load_config(project)
    progress = migrate.load_progress(project)
    migrate.run_hooks(project, cfg, "proddb", "pre-migration", None, progress)
    kinds = [cmd[0] for cmd in calls]
    assert kinds[0] == "psql" and kinds[1] == "bash"
    assert len(calls) == 3
    assert progress["hooks"]["pre-migration"] == ["10-a.sql", "20-b.sh", "30-c.py"]
    # resume: nothing runs twice
    migrate.run_hooks(project, cfg, "proddb", "pre-migration", None, progress)
    assert len(calls) == 3


def test_upgrade_command_openupgrade_14_plus(project):
    scripts = project.tmp_dir / "repos" / "openupgrade" / "scripts"
    cmd = migrate.upgrade_command(project, "16.0", "proddb", scripts)
    joined = " ".join(str(part) for part in cmd)
    assert "-u base" in joined
    assert "--load=base,web,openupgrade_framework" in joined
    assert f"--upgrade-path={scripts}" in joined
    assert "--xmlrpc-port=18069" in joined


def test_upgrade_command_openupgrade_fork(project):
    cmd = migrate.upgrade_command(project, "12.0", "proddb", None)
    joined = " ".join(str(part) for part in cmd)
    assert "-u base" in joined
    assert "--load" not in joined and "--upgrade-path" not in joined


def test_backup_once(project, calls):
    cfg = load_config(project)
    progress = migrate.load_progress(project)
    migrate.backup(project, cfg, "proddb", "15.0", progress)
    creates = [cmd for cmd in calls if cmd[0] == "createdb"]
    assert creates == [["createdb", "-T", "proddb", "proddb-pre-15.0"]]
    migrate.backup(project, cfg, "proddb", "15.0", progress)
    assert len([cmd for cmd in calls if cmd[0] == "createdb"]) == 1


def test_migrate_full_flow_and_resume(project, calls, fake_step):
    venv_mod.subprocess.run.outputs["latest_version"] = "14.0.1.3\n"
    assert migrate.migrate(project, []) == 0
    upgrades = [cmd for cmd in calls if "-u" in cmd and "base" in cmd]
    assert len(upgrades) == 2  # 15.0 and 16.0
    assert fake_step == ["15.0", "16.0"]
    progress = migrate.load_progress(project)
    assert progress["versions"]["15.0"]["upgrade"] is True
    assert progress["versions"]["16.0"]["upgrade"] is True
    assert progress["database"] == "proddb"

    # resume: nothing upgraded twice
    assert migrate.migrate(project, []) == 0
    upgrades = [cmd for cmd in calls if "-u" in cmd and "base" in cmd]
    assert len(upgrades) == 2
    assert fake_step == ["15.0", "16.0"]


def test_migrate_no_backups(project, calls, fake_step):
    venv_mod.subprocess.run.outputs["latest_version"] = "15.0.1.0\n"
    migrate.migrate(project, ["--no-backups"])
    assert not any(cmd[0] == "createdb" for cmd in calls)


def test_migrate_enterprise(project, calls):
    venv_mod.subprocess.run.outputs["latest_version"] = "15.0.1.0\n"
    assert migrate.migrate(project, ["--enterprise", "--no-backups"]) == 0
    curl = next(cmd for cmd in calls if cmd[0] == "curl")
    assert "https://upgrade.odoo.com/upgrade" in curl
    client = calls[-1]
    assert "test" in client and "-t" in client and "16.0" in client


def test_migrate_requires_database(tmp_path, calls):
    project = init_project(tmp_path, "16.0")
    with pytest.raises(WaftError, match="PGDATABASE is not set"):
        migrate.migrate(project, [])


def test_migrate_reset_progress_cli(project, calls):
    migrate.save_progress(
        project, {"versions": {"15.0": {"upgrade": True}}, "hooks": {}}
    )
    assert migrate.migrate(project, ["--reset-progress"]) == 0
    assert migrate.load_progress(project) == {"versions": {}, "hooks": {}}


def test_ensure_step_project_config(project, monkeypatch, calls):
    """The nested build gets the right repo configuration per version."""
    monkeypatch.setattr(migrate.venv_mod, "ensure_venv", lambda p, c=None: False)
    monkeypatch.setattr(migrate.venv_mod, "update_requirements", lambda p, c=None: 0)
    monkeypatch.setattr(migrate.source_mod, "ensure_odoo_source", lambda p, c: None)
    monkeypatch.setattr(migrate.source_mod, "install_odoo", lambda p, c: False)
    monkeypatch.setattr(migrate.addons_mod, "converge", lambda p, c: [])
    cfg = load_config(project)

    step, scripts = migrate.ensure_step_project(project, "12.0", cfg)
    assert scripts is None
    step_cfg = load_config(Project(step.root))
    assert step_cfg["ODOO_REPOSITORY"] == migrate.OPENUPGRADE_REPO
    assert step_cfg["ODOO_VERSION"] == "12.0"

    step, scripts = migrate.ensure_step_project(project, "15.0", cfg)
    assert scripts is not None and "openupgrade_scripts" in str(scripts)
    from waft import addons

    entries = addons.load_entries(step)
    assert entries["openupgrade"].url == migrate.OPENUPGRADE_REPO
