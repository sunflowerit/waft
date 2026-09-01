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
    # nothing is on the addons path until addons are declared
    assert "addons_path = \n" in text or "addons_path =\n" in text
    assert "[queue_job]" in text
    assert "${" not in text  # everything substituted


def test_odoo_conf_longpolling_for_old_versions(tmp_path):
    project = init_project(tmp_path, "12.0")
    text = project.odoo_conf.read_text()
    assert "longpolling_port = 8072" in text
    assert "gevent_port" not in text


def test_odoo_conf_has_the_full_option_set(project):
    """odoo.conf carries every default option, as the old templates did."""
    text = project.odoo_conf.read_text()
    keys = {line.split("=")[0].strip() for line in text.splitlines() if "=" in line}
    # a spread of options that only exist in the full set
    for key in (
        "csv_internal_sep",
        "db_maxconn",
        "db_sslmode",
        "limit_request",
        "limit_time_cpu",
        "log_handler",
        "log_level",
        "proxy_mode",
        "server_wide_modules",
        "smtp_server",
        "test_enable",
        "transient_age_limit",
    ):
        assert key in keys, key
    assert len(keys) > 50
    # long values survive the generator's line wrapping intact
    assert (
        "log_handler = :INFO,werkzeug:WARN,openerp.service.server:INFO,"
        "longpolling:WARN" in text
    )


@pytest.mark.parametrize("version", ["8.0", "13.0", "16.0", "18.0", "19.0"])
def test_every_version_renders(tmp_path, version):
    project = init_project(tmp_path / version, version, db_name="db")
    text = project.odoo_conf.read_text()
    assert "${" not in text  # all variables substituted
    assert "[options]" in text and "[queue_job]" in text


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


def test_config_set_preserves_structured_keys(project):
    """Setting a variable must not drop the ADDONS block (regression)."""
    from waft import addons

    addons.addon_add(project, "oca-web", {"url": "https://x/web.git"})
    config.config_set(project, "ODOO_WORKERS=4")
    assert "oca-web" in addons.load_entries(project)
    config.config_set(project, "PGPASSWORD=secret")  # goes to secret.yml
    config.config_remove(project, "ODOO_WORKERS")
    assert "oca-web" in addons.load_entries(project)


def test_config_set_refuses_structured_keys(project):
    from waft import addons

    addons.addon_add(project, "oca-web", {"url": "https://x/web.git"})
    with pytest.raises(WaftError, match="structured setting"):
        config.config_set(project, "ADDONS=nope")
    with pytest.raises(WaftError, match="structured setting"):
        config.config_remove(project, "ADDONS")


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
