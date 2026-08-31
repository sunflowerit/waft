import subprocess

import pytest
import yaml

from waft import addons
from waft import venv as venv_mod
from waft.cli import main
from waft.config import load_config, render_odoo_conf
from waft.project import WaftError
from waft.scaffold import init_project


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def fake_run(cmd, check=True, **kwargs):
        recorded.append([str(part) for part in cmd])
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(venv_mod.subprocess, "run", fake_run)
    return recorded


@pytest.fixture
def have_uv(monkeypatch):
    monkeypatch.setattr(venv_mod.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.fixture
def project(tmp_path):
    return init_project(tmp_path, "16.0")


def _set_addons(project, mapping):
    data = yaml.safe_load(project.shared_yml.read_text()) or {}
    data["ADDONS"] = mapping
    project.shared_yml.write_text(yaml.safe_dump(data))


def _make_addon(directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "__manifest__.py").write_text("{}")
    return directory


# ------------------------------------------------------------------- specs
@pytest.mark.parametrize(
    "raw,expected",
    [
        # browser URL with branch and subdirectory
        (
            "https://github.com/example/addons-example/tree/a-branch/addons/addon",
            "git+https://github.com/example/addons-example"
            "@a-branch#subdirectory=addons/addon",
        ),
        # trailing .git on the subdirectory is tolerated
        (
            "https://github.com/example/addons-example/tree/16.0/addons/addon.git",
            "git+https://github.com/example/addons-example"
            "@16.0#subdirectory=addons/addon",
        ),
        # GitLab's /-/tree/ form
        (
            "https://gitlab.com/org/repo/-/tree/15.0/sub",
            "git+https://gitlab.com/org/repo@15.0#subdirectory=sub",
        ),
        # ssh form
        (
            "git@github.com:example/addons-example/tree/a-branch/addons/addon.git",
            "git+ssh://git@github.com/example/addons-example"
            "@a-branch#subdirectory=addons/addon",
        ),
        # branch without a subdirectory
        (
            "https://github.com/example/repo/tree/16.0",
            "git+https://github.com/example/repo@16.0",
        ),
        # anything pip already understands is untouched
        ("odoo-addon-queue_job==16.0.*", "odoo-addon-queue_job==16.0.*"),
        (
            "git+https://github.com/o/r@16.0#subdirectory=x",
            "git+https://github.com/o/r@16.0#subdirectory=x",
        ),
        ("https://github.com/example/repo.git", "https://github.com/example/repo.git"),
    ],
)
def test_normalize_spec(raw, expected):
    assert addons.normalize_spec(raw) == expected


# ----------------------------------------------------------------- entries
def test_load_entries(project):
    _set_addons(
        project,
        {
            "addons-example": {
                "install": "clone",
                "url": "https://x/e.git",
                "branch": "16.0",
            },
            "addonx": {"install": "pypi", "path": "addons/addons-example/addonx"},
            "addony": {"install": "source", "path": "addons/addons-example/addony"},
            "remote": {"install": "pypi", "spec": "odoo-addon-x"},
        },
    )
    entries = addons.load_entries(project)
    assert entries["addons-example"].install == "clone"
    assert entries["addonx"].install == "pypi"
    assert entries["addony"].path == "addons/addons-example/addony"
    assert entries["remote"].spec == "odoo-addon-x"


@pytest.mark.parametrize(
    "raw,match",
    [
        ({"install": "nope"}, "unknown install type"),
        ({"install": "clone"}, "needs a 'url'"),
        ({"install": "pypi"}, "needs a 'spec' or a 'path'"),
        ({"install": "source"}, "needs a 'path'"),
        ("notadict", "must be a mapping"),
    ],
)
def test_entry_validation(project, raw, match):
    _set_addons(project, {"bad": raw})
    with pytest.raises(WaftError, match=match):
        addons.load_entries(project)


def test_secret_overrides_and_disables(project):
    _set_addons(
        project,
        {
            "a": {"install": "pypi", "spec": "spec1"},
            "b": {"install": "pypi", "spec": "spec2"},
        },
    )
    project.secret_yml.write_text(
        yaml.safe_dump({"ADDONS": {"a": None, "b": {"install": "pypi", "spec": "s3"}}})
    )
    entries = addons.load_entries(project)
    assert "a" not in entries
    assert entries["b"].spec == "s3"


# ---------------------------------------------------------------- converge
def test_clone_goes_into_addons(project, calls, have_uv):
    _set_addons(
        project, {"addons-example": {"install": "clone", "url": "https://x/e.git"}}
    )
    done = addons.converge(project, load_config(project))
    assert done == ["addons-example (cloned)"]
    clone = calls[0]
    assert clone[:2] == ["git", "clone"]
    assert str(project.addons_dir / "addons-example") in clone
    assert "16.0" in clone  # branch defaults to ODOO_VERSION


def test_clone_is_gitignored_by_default(project, calls, have_uv):
    _set_addons(
        project, {"addons-example": {"install": "clone", "url": "https://x/e.git"}}
    )
    addons.converge(project, load_config(project))
    gitignore = project.gitignore.read_text().splitlines()
    assert "/addons/addons-example/" in gitignore
    assert addons.GITIGNORE_BEGIN in gitignore

    # opting out removes it again
    _set_addons(
        project,
        {
            "addons-example": {
                "install": "clone",
                "url": "https://x/e.git",
                "gitignore": False,
            }
        },
    )
    addons.converge(project, load_config(project))
    assert "/addons/addons-example/" not in project.gitignore.read_text().splitlines()


def test_local_addon_can_opt_into_gitignore(project, calls, have_uv):
    _make_addon(project.addons_dir / "addons-local" / "addonz")
    _set_addons(
        project,
        {
            "addonz": {
                "install": "source",
                "path": "addons/addons-local/addonz",
                "gitignore": True,
            }
        },
    )
    addons.converge(project, load_config(project))
    assert "/addons/addons-local/addonz/" in project.gitignore.read_text().splitlines()


def test_pypi_from_spec(project, calls, have_uv):
    _set_addons(
        project,
        {
            "addon": {
                "install": "pypi",
                "spec": "https://github.com/example/e/tree/16.0/addons/addon",
            }
        },
    )
    addons.converge(project, load_config(project))
    install = calls[-1]
    assert "git+https://github.com/example/e@16.0#subdirectory=addons/addon" in install


def test_pypi_from_local_path(project, calls, have_uv):
    addon = _make_addon(project.addons_dir / "addons-example" / "addonx")
    (addon / "setup.py").write_text("from setuptools import setup; setup()")
    _set_addons(
        project,
        {"addonx": {"install": "pypi", "path": "addons/addons-example/addonx"}},
    )
    addons.converge(project, load_config(project))
    assert str(addon) in calls[-1]


def test_pypi_from_oca_setup_directory(project, calls, have_uv):
    """OCA repositories keep the packaging in setup/<addon>/."""
    repo = project.addons_dir / "addons-example"
    _make_addon(repo / "addonx")
    setup_dir = repo / "setup" / "addonx"
    setup_dir.mkdir(parents=True)
    (setup_dir / "setup.py").write_text("from setuptools import setup; setup()")
    _set_addons(
        project,
        {"addonx": {"install": "pypi", "path": "addons/addons-example/addonx"}},
    )
    addons.converge(project, load_config(project))
    assert str(setup_dir) in calls[-1]


def test_pypi_local_path_without_packaging(project, calls, have_uv):
    _make_addon(project.addons_dir / "addons-example" / "addonx")
    _set_addons(
        project,
        {"addonx": {"install": "pypi", "path": "addons/addons-example/addonx"}},
    )
    with pytest.raises(WaftError, match="no setup.py or pyproject.toml"):
        addons.converge(project, load_config(project))


def test_source_requires_existing_addon(project, calls, have_uv):
    _set_addons(project, {"addony": {"install": "source", "path": "addons/e/addony"}})
    with pytest.raises(WaftError, match="does not exist"):
        addons.converge(project, load_config(project))
    (project.addons_dir / "e" / "addony").mkdir(parents=True)
    with pytest.raises(WaftError, match="no Odoo manifest"):
        addons.converge(project, load_config(project))


# -------------------------------------------------------------- addons_path
def test_addons_path_only_lists_declared_sources(project, calls, have_uv):
    repo = project.addons_dir / "addons-example"
    _make_addon(repo / "addony")
    _make_addon(repo / "addonx")
    _make_addon(project.addons_dir / "addons-local" / "addonz")
    (project.odoo_dir / "addons").mkdir(parents=True)
    _set_addons(
        project,
        {
            "addons-example": {"install": "clone", "url": "https://x/e.git"},
            "addonx": {"install": "pypi", "spec": "odoo-addon-x"},
            "addony": {"install": "source", "path": "addons/addons-example/addony"},
            "addonz": {"install": "source", "path": "addons/addons-local/addonz"},
        },
    )
    path = addons.addons_path(project)
    assert str(project.odoo_dir / "addons") in path
    assert str(repo) in path  # contains the declared addony
    assert str(project.addons_dir / "addons-local") in path
    # the bare addons/ directory is never added wholesale
    assert str(project.addons_dir) not in path

    conf = render_odoo_conf(project)
    line = next(row for row in conf.splitlines() if row.startswith("addons_path"))
    assert str(repo) in line and str(project.addons_dir) + "\n" not in line


def test_addons_path_empty_without_declarations(project):
    assert addons.addons_path(project) == []


# ---------------------------------------------------------------- commands
def test_add_infers_install_type(project):
    addons.addon_add(project, "repo", {"url": "https://x/e.git"})
    addons.addon_add(project, "remote", {"spec": "odoo-addon-x"})
    addons.addon_add(project, "local", {"path": "addons/addons-local/addonz"})
    entries = addons.load_entries(project)
    assert entries["repo"].install == "clone"
    assert entries["remote"].install == "pypi"
    assert entries["local"].install == "source"


def test_add_rejects_duplicate_and_reserved_name(project):
    addons.addon_add(project, "repo", {"url": "https://x/e.git"})
    with pytest.raises(WaftError, match="already exists"):
        addons.addon_add(project, "repo", {"url": "https://x/e.git"})
    with pytest.raises(WaftError, match="reserved for the Odoo source"):
        addons.addon_add(project, "odoo", {"url": "https://x/odoo.git"})


def test_configure_changes_install_type(project):
    addons.addon_add(project, "addony", {"path": "addons/addons-example/addony"})
    assert addons.load_entries(project)["addony"].install == "source"
    addons.addon_configure(project, "addony", {"install": "pypi"})
    assert addons.load_entries(project)["addony"].install == "pypi"
    with pytest.raises(WaftError, match="nothing to configure"):
        addons.addon_configure(project, "addony", {})
    with pytest.raises(WaftError, match="not found"):
        addons.addon_configure(project, "ghost", {"install": "pypi"})


def test_delete_keeps_files_but_drops_declaration(project, capsys):
    _make_addon(project.addons_dir / "addons-local" / "addonz")
    addons.addon_add(project, "addonz", {"path": "addons/addons-local/addonz"})
    addons.addon_delete(project, "addonz")
    assert addons.load_entries(project) == {}
    assert (project.addons_dir / "addons-local" / "addonz").is_dir()
    assert "left on disk" in capsys.readouterr().out


def test_update_clone_and_pypi(project, calls, have_uv):
    addons.addon_add(project, "repo", {"url": "https://x/e.git"})
    (project.addons_dir / "repo" / ".git").mkdir(parents=True)
    addons.addon_update(project, "repo")
    assert any("fetch" in call for call in calls)

    addons.addon_add(project, "remote", {"spec": "odoo-addon-x"})
    addons.addon_update(project, "remote")
    assert any("--upgrade" in call for call in calls)


def test_list(project, capsys):
    _make_addon(project.addons_dir / "addons-local" / "addonz")
    addons.addon_add(project, "repo", {"url": "https://x/e.git"})
    addons.addon_add(project, "addonz", {"path": "addons/addons-local/addonz"})
    addons.addon_list(project)
    out = capsys.readouterr().out
    assert "repo" in out and "clone" in out and "not fetched" in out
    assert "addonz" in out and "source" in out and "[ok]" in out


def test_cli_declares_the_three_options(tmp_path, capsys):
    main(["-d", str(tmp_path), "init", "--odoo-version", "16.0"])
    # option 1: a single addon as a PyPI package from a remote repository
    assert (
        main(
            [
                "-d",
                str(tmp_path),
                "odoo",
                "addon",
                "--add",
                "addon",
                "--spec",
                "https://github.com/example/e/tree/a-branch/addons/addon",
            ]
        )
        == 0
    )
    # option 2: clone a repository, then declare addons inside it
    main(
        [
            "-d",
            str(tmp_path),
            "odoo",
            "addon",
            "--add",
            "addons-example",
            "-t",
            "clone",
            "--url",
            "https://github.com/example/addons-example.git",
            "--branch",
            "a-branch-name",
        ]
    )
    main(
        [
            "-d",
            str(tmp_path),
            "odoo",
            "addon",
            "--add",
            "addony",
            "--installation-type",
            "source",
            "--path",
            "addons/addons-example/addony",
        ]
    )
    # option 3: a local addon, tracked in git
    main(
        [
            "-d",
            str(tmp_path),
            "odoo",
            "addon",
            "--add",
            "addonz",
            "-t",
            "source",
            "--path",
            "addons/addons-local/addonz",
            "--no-gitignore",
        ]
    )
    data = yaml.safe_load((tmp_path / ".waft" / "conf" / "shared.yml").read_text())
    entries = data["ADDONS"]
    assert entries["addon"]["install"] == "pypi"
    assert entries["addons-example"]["install"] == "clone"
    assert entries["addons-example"]["branch"] == "a-branch-name"
    assert entries["addony"] == {
        "install": "source",
        "path": "addons/addons-example/addony",
    }
    assert entries["addonz"]["gitignore"] is False
    assert main(["-d", str(tmp_path), "odoo", "addon", "--list"]) == 0
    assert "addons-example" in capsys.readouterr().out
