import pytest

from waft import config
from waft.project import WaftError
from waft.scaffold import init_project


@pytest.fixture
def project(tmp_path):
    return init_project(tmp_path, "16.0", db_name="testdb")


def test_precedence_defaults_shared_secret(project):
    cfg = config.load_config(project)
    assert cfg["PGPORT"] == "5432"  # code default
    project.shared_yml.write_text(
        "ODOO_VERSION: '16.0'\nPGPORT: 5433\nODOO_WORKERS: 4\n"
    )
    cfg = config.load_config(project)
    assert cfg["PGPORT"] == "5433"  # shared overrides default
    project.secret_yml.write_text("PGPORT: 5434\n")
    cfg = config.load_config(project)
    assert cfg["PGPORT"] == "5434"  # secret overrides shared
    assert cfg["ODOO_WORKERS"] == "4"


def test_missing_odoo_version(project):
    project.shared_yml.write_text("PGPORT: 5433\n")
    project.secret_yml.write_text("")
    with pytest.raises(WaftError, match="ODOO_VERSION is not set"):
        config.load_config(project)


def test_odoo_conf_generation(project):
    text = project.odoo_conf.read_text()
    assert "[options]" in text
    assert "db_port = 5432" in text
    assert "db_name = testdb" in text
    assert "gevent_port = 8072" in text  # 16.0 uses gevent
    assert str(project.addons_dir) in text
    assert "[queue_job]" in text
    assert "${" not in text  # everything substituted


def test_odoo_conf_longpolling_for_old_versions(tmp_path):
    project = init_project(tmp_path, "12.0")
    text = project.odoo_conf.read_text()
    assert "longpolling_port = 8072" in text
    assert "gevent_port" not in text


def test_substitute_missing_variable():
    with pytest.raises(WaftError) as exc:
        config.substitute("x=${NOPE}", {"OK": "1"}, "test source")
    assert "NOPE" in str(exc.value)
    assert "test source" in str(exc.value)


def test_config_set_secret_goes_to_secret_yml(project):
    target = config.config_set(project, "PGPASSWORD=hunter2")
    assert target == project.secret_yml
    assert "hunter2" in project.secret_yml.read_text()
    assert "hunter2" not in project.shared_yml.read_text()
    assert "hunter2" in project.odoo_conf.read_text()


def test_config_set_shared(project):
    target = config.config_set(project, "ODOO_WORKERS=2")
    assert target == project.shared_yml
    assert "workers = 2" in project.odoo_conf.read_text()


def test_config_set_invalid(project):
    with pytest.raises(WaftError, match="expected VARIABLE=value"):
        config.config_set(project, "NOEQUALS")
    with pytest.raises(WaftError, match="invalid variable name"):
        config.config_set(project, "BAD NAME=1")


def test_config_remove(project):
    config.config_set(project, "ODOO_WORKERS=2")
    removed = config.config_remove(project, "ODOO_WORKERS")
    assert removed == [project.shared_yml]
    assert "workers = 8" in project.odoo_conf.read_text()  # back to default
    with pytest.raises(WaftError, match="not found"):
        config.config_remove(project, "ODOO_WORKERS")


def test_config_list_masks_secrets(project):
    config.config_set(project, "PGPASSWORD=hunter2")
    listed = dict(config.config_list(project))
    assert listed["PGPASSWORD"] == "********"
    assert listed["PGDATABASE"] == "********"  # secret var with value
    assert listed["ODOO_ADMIN_PASSWORD"] == ""  # empty secrets stay visible


def test_config_test_ok(project):
    assert config.config_test(project) == []


def test_config_test_detects_problems(project):
    project.shared_yml.write_text(
        "ODOO_VERSION: '16.0'\nPGPASSWORD: leaked\nTYPO_VAR: 1\n"
    )
    problems = config.config_test(project)
    assert any("PGPASSWORD" in p and "move it" in p for p in problems)
    assert any(p.startswith("warning:") and "TYPO_VAR" in p for p in problems)


def test_config_test_invalid_yaml(project):
    project.shared_yml.write_text("ODOO_VERSION: [unclosed\n")
    problems = config.config_test(project)
    assert problems and "invalid YAML" in problems[0]
