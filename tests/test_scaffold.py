import pytest

from waft import PROJECT_FORMAT
from waft.project import Project, WaftError
from waft.scaffold import apply_migrations, init_project, read_version_stamp


def test_init_creates_tree(tmp_path):
    project = init_project(
        tmp_path / "proj", "16.0", db_name="mydb", db_password="s3cret"
    )
    for path in (
        project.addons_dir,
        project.tmp_dir,
        project.conf_dir,
        project.backup_dir,
        project.odoo_data_dir / "filestore",
        project.log_dir,
        project.template_dir,
    ):
        assert path.is_dir(), path
    assert project.shared_yml.is_file()
    assert project.secret_yml.is_file()
    assert project.odoo_conf.is_file()
    assert project.requirements_txt.is_file()
    assert (project.template_dir / "odoo.service").is_file()
    assert "ODOO_VERSION: '16.0'" in project.shared_yml.read_text()
    secret = project.secret_yml.read_text()
    assert "mydb" in secret and "s3cret" in secret
    assert "s3cret" not in project.shared_yml.read_text()


def test_init_gitignore_and_stamp(tmp_path):
    project = init_project(tmp_path, "19.0")
    gitignore = project.gitignore.read_text()
    for entry in (
        ".waft/conf/secret.yml",
        ".waft/conf/odoo.conf",
        ".waft/data/",
        ".waft/log/",
        ".tmp/",
        ".venv/",
        "/addons/odoo/",
    ):
        assert entry in gitignore
    assert read_version_stamp(project) == PROJECT_FORMAT


def test_init_twice_fails(tmp_path):
    init_project(tmp_path, "16.0")
    with pytest.raises(WaftError, match="already a waft project"):
        init_project(tmp_path, "16.0")


def test_init_rejects_unknown_version(tmp_path):
    with pytest.raises(WaftError, match="unsupported Odoo version"):
        init_project(tmp_path / "x", "7.0")
    assert not (tmp_path / "x" / ".waft").exists()


def test_migrations_noop_when_current(tmp_path):
    project = init_project(tmp_path, "16.0")
    assert apply_migrations(project) == []


def test_stamp_newer_than_waft_fails(tmp_path):
    project = init_project(tmp_path, "16.0")
    project.version_file.write_text(f"format: {PROJECT_FORMAT + 1}\nwaft: 9.9.9\n")
    with pytest.raises(WaftError, match="newer than this waft"):
        apply_migrations(project)


def test_read_stamp_missing(tmp_path):
    with pytest.raises(WaftError, match="missing"):
        read_version_stamp(Project(tmp_path))


def test_migration_from_format_1(tmp_path):
    """Format 1 layout (odoo/ at the root) upgrades to addons/odoo/."""
    project = init_project(tmp_path, "16.0")
    project.version_file.write_text("format: 1\nwaft: 0.0.1\n")
    project.gitignore.write_text(
        project.gitignore.read_text().replace("/addons/odoo/\n", "")
    )
    legacy_odoo = project.root / "odoo"
    (legacy_odoo / "odoo").mkdir(parents=True)
    (project.data_dir / "odoo-installed").parent.mkdir(parents=True, exist_ok=True)
    (project.data_dir / "odoo-installed").write_text("deadbeef\n")
    (project.addons_dir / "gone").symlink_to("../.tmp/repos/x/gone")

    applied = apply_migrations(project)
    assert applied == [2, 3, 4]
    assert (project.odoo_dir / "odoo").is_dir()
    assert not legacy_odoo.exists()
    # the editable install pointed at the old path
    assert not (project.data_dir / "odoo-installed").exists()
    assert not (project.addons_dir / "gone").is_symlink()  # dangling link removed
    gitignore = project.gitignore.read_text().splitlines()
    assert "/addons/odoo/" in gitignore
    assert "/odoo/" not in gitignore and ".src/" not in gitignore
    assert read_version_stamp(project) == PROJECT_FORMAT
    # re-running is a no-op and must not duplicate entries
    assert apply_migrations(project) == []
    assert project.gitignore.read_text().splitlines().count("/addons/odoo/") == 1


def test_migration_from_format_3(tmp_path):
    """Format 3 layout (.src/) upgrades to addons/odoo + .tmp/repos."""
    project = init_project(tmp_path, "16.0")
    project.version_file.write_text("format: 3\nwaft: 0.0.1\n")
    src = project.root / ".src"
    (src / "odoo" / "odoo").mkdir(parents=True)
    (src / "oca-web" / "web_a").mkdir(parents=True)
    (project.data_dir / "addon-links.json").parent.mkdir(parents=True, exist_ok=True)
    (project.data_dir / "addon-links.json").write_text('{"web_a": {"mode": "soft"}}')

    assert apply_migrations(project) == [4]
    assert (project.odoo_dir / "odoo").is_dir()
    assert (project.tmp_dir / "repos" / "oca-web" / "web_a").is_dir()
    assert not src.exists()
    assert not (project.data_dir / "addon-links.json").exists()
    assert "/addons/odoo/" in project.gitignore.read_text().splitlines()
