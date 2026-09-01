import pytest

from waft import __version__
from waft.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_no_arguments_prints_help(capsys):
    code, out, _ = run(capsys)
    assert code == 0
    assert "usage: waft" in out


def test_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_init_and_info(tmp_path, capsys):
    code, out, err = run(
        capsys,
        "-d",
        str(tmp_path),
        "init",
        "--odoo-version",
        "16.0",
        "--db-name",
        "db1",
    )
    assert code == 0, err
    assert "initialized waft project for Odoo 16.0" in out

    code, out, err = run(capsys, "-d", str(tmp_path), "info")
    assert code == 0, err
    assert "16.0" in out
    assert "3.10.6" in out
    assert "db1" in out


def test_info_without_project(tmp_path, capsys):
    code, _, err = run(capsys, "-d", str(tmp_path), "info")
    assert code == 1
    assert "no waft project found" in err


def test_init_requires_version_non_interactive(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    code, _, err = run(capsys, "-d", str(tmp_path), "init")
    assert code == 1
    assert "--odoo-version is required" in err


def test_config_roundtrip_via_cli(tmp_path, capsys):
    run(capsys, "-d", str(tmp_path), "init", "--odoo-version", "16.0")

    code, out, err = run(
        capsys, "-d", str(tmp_path), "odoo", "config", "set", "ODOO_WORKERS=3"
    )
    assert code == 0, err
    assert "set ODOO_WORKERS" in out

    code, out, _ = run(capsys, "-d", str(tmp_path), "odoo", "config", "list")
    assert code == 0
    assert "ODOO_WORKERS=3" in out

    code, out, _ = run(capsys, "-d", str(tmp_path), "odoo", "config", "test")
    assert code == 0
    assert "configuration OK" in out

    code, out, _ = run(
        capsys, "-d", str(tmp_path), "odoo", "config", "remove", "ODOO_WORKERS"
    )
    assert code == 0
    assert "removed ODOO_WORKERS" in out


def test_sync(tmp_path, capsys, monkeypatch):
    run(capsys, "-d", str(tmp_path), "init", "--odoo-version", "16.0")
    monkeypatch.setattr("waft.venv.ensure_venv", lambda project, cfg=None: True)
    monkeypatch.setattr("waft.venv.update_requirements", lambda project, cfg=None: 0)
    monkeypatch.setattr("waft.source.ensure_odoo_source", lambda project, cfg: None)
    monkeypatch.setattr("waft.source.install_odoo", lambda project, cfg: True)
    code, out, err = run(capsys, "-d", str(tmp_path), "sync")
    assert code == 0, err
    assert "regenerated" in out
    assert "created virtual environment" in out
    assert "installed Odoo" in out
    assert "addons: (none declared)" in out


def test_sync_order_source_before_requirements(tmp_path, capsys, monkeypatch):
    """Odoo's requirements.txt only exists after the checkout, so it wins."""
    run(capsys, "-d", str(tmp_path), "init", "--odoo-version", "16.0")
    order = []
    monkeypatch.setattr("waft.venv.ensure_venv", lambda project, cfg=None: True)
    monkeypatch.setattr(
        "waft.source.ensure_odoo_source",
        lambda project, cfg: order.append("source"),
    )
    monkeypatch.setattr(
        "waft.venv.update_requirements",
        lambda project, cfg=None: order.append("requirements"),
    )
    monkeypatch.setattr("waft.source.install_odoo", lambda project, cfg: True)
    run(capsys, "-d", str(tmp_path), "sync")
    assert order == ["source", "requirements"]


def test_pip_without_venv_fails(tmp_path, capsys):
    run(capsys, "-d", str(tmp_path), "init", "--odoo-version", "16.0")
    code, _, err = run(capsys, "-d", str(tmp_path), "pip", "list")
    assert code == 1
    assert "no virtual environment yet" in err


def test_migrate_without_database_fails(tmp_path, capsys):
    run(capsys, "-d", str(tmp_path), "init", "--odoo-version", "16.0")
    code, _, err = run(capsys, "-d", str(tmp_path), "migrate")
    assert code == 1
    assert "PGDATABASE is not set" in err


def test_odoo_commands_require_install(tmp_path, capsys):
    run(capsys, "-d", str(tmp_path), "init", "--odoo-version", "16.0")
    for argv in (
        ["odoo", "run"],
        ["odoo", "shell"],
        ["odoo", "upgrade", "web"],
        ["odoo", "install", "web"],
        ["odoo-bin"],
        ["database", "initial"],
    ):
        code, _, err = run(capsys, "-d", str(tmp_path), *argv)
        assert code == 1, argv
        assert "waft: error:" in err, argv


def test_command_logged_to_waft_log(tmp_path, capsys):
    run(capsys, "-d", str(tmp_path), "init", "--odoo-version", "16.0")
    run(capsys, "-d", str(tmp_path), "info")
    log = (tmp_path / ".waft" / "log" / "waft.log").read_text()
    assert "info" in log
    assert "odoo version" in log
