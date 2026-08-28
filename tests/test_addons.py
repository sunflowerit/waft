import subprocess

import pytest
import yaml

from waft import addons
from waft import venv as venv_mod
from waft.cli import main
from waft.config import load_config
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


def _fake_repo(project, entry_name, addon_names):
    repo = project.tmp_dir / "repos" / entry_name
    (repo / ".git").mkdir(parents=True)
    for name in addon_names:
        addon = repo / name
        addon.mkdir()
        (addon / "__manifest__.py").write_text("{}")
    return repo


# ---------------------------------------------------------------- entries
def test_load_entries_kinds(project):
    _set_addons(
        project,
        {
            "oca-web": {"kind": "git", "url": "https://x/web.git"},
            "qj": {"kind": "pypi", "spec": "odoo-addon-queue_job==16.0.*"},
            "loc": {"kind": "link", "path": "../dev/loc"},
            "plain": {},
        },
    )
    entries = addons.load_entries(project)
    assert entries["oca-web"].kind == "git"
    assert entries["oca-web"].addons == ["*"]
    assert entries["qj"].kind == "pypi"
    assert entries["loc"].kind == "link"
    assert entries["plain"].kind == "directory"


@pytest.mark.parametrize(
    "raw,match",
    [
        ({"kind": "nope"}, "unknown kind"),
        ({"kind": "git"}, "needs a 'url'"),
        ({"kind": "pypi"}, "needs a 'spec'"),
        ({"kind": "link"}, "needs a 'path'"),
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
            "oca-web": {"kind": "git", "url": "https://x/web.git"},
            "qj": {"kind": "pypi", "spec": "spec1"},
        },
    )
    project.secret_yml.write_text(
        yaml.safe_dump(
            {"ADDONS": {"oca-web": None, "qj": {"kind": "pypi", "spec": "spec2"}}}
        )
    )
    entries = addons.load_entries(project)
    assert "oca-web" not in entries
    assert entries["qj"].spec == "spec2"


# ---------------------------------------------------------------- converge
def test_converge_git_update_and_link(project, calls, have_uv):
    _set_addons(project, {"oca-web": {"kind": "git", "url": "https://x/web.git"}})
    repo = _fake_repo(project, "oca-web", ["web_a", "web_b"])
    linked = addons.converge(project, load_config(project))
    assert linked == ["web_a", "web_b"]
    # branch substituted from ODOO_VERSION, fetch + checkout on existing repo
    assert ["git", "-C", str(repo), "fetch", "--depth", "1", "origin", "16.0"] in calls
    for name in ("web_a", "web_b"):
        link = project.addons_dir / name
        assert link.is_symlink()
        assert (link / "__manifest__.py").is_file()


def test_converge_git_addon_globs(project, calls, have_uv):
    _set_addons(
        project,
        {
            "oca-web": {
                "kind": "git",
                "url": "https://x/web.git",
                "addons": ["web_a"],
            }
        },
    )
    _fake_repo(project, "oca-web", ["web_a", "web_b"])
    linked = addons.converge(project, load_config(project))
    assert linked == ["web_a"]
    assert not (project.addons_dir / "web_b").exists()


def test_converge_no_matching_addons(project, calls, have_uv):
    _set_addons(
        project,
        {"empty": {"kind": "git", "url": "https://x/e.git", "addons": ["nope"]}},
    )
    _fake_repo(project, "empty", ["real"])
    with pytest.raises(WaftError, match="no addons matching"):
        addons.converge(project, load_config(project))


def test_converge_duplicate_addon(project, calls, have_uv):
    _set_addons(
        project,
        {
            "r1": {"kind": "git", "url": "https://x/1.git"},
            "r2": {"kind": "git", "url": "https://x/2.git"},
        },
    )
    _fake_repo(project, "r1", ["dup"])
    _fake_repo(project, "r2", ["dup"])
    with pytest.raises(WaftError, match="multiple entries"):
        addons.converge(project, load_config(project))


def test_converge_pypi(project, calls, have_uv):
    _set_addons(project, {"qj": {"kind": "pypi", "spec": "odoo-addon-x==16.0.*"}})
    addons.converge(project, load_config(project))
    assert any("odoo-addon-x==16.0.*" in call for call in calls)


def test_converge_link_kind(project, tmp_path_factory):
    external = tmp_path_factory.mktemp("dev") / "my_addon"
    external.mkdir()
    (external / "__manifest__.py").write_text("{}")
    _set_addons(project, {"my_addon": {"kind": "link", "path": str(external)}})
    linked = addons.converge(project, load_config(project))
    assert linked == ["my_addon"]
    assert (project.addons_dir / "my_addon").is_symlink()


def test_converge_link_missing_manifest(project, tmp_path_factory):
    external = tmp_path_factory.mktemp("dev") / "empty"
    external.mkdir()
    _set_addons(project, {"empty": {"kind": "link", "path": str(external)}})
    with pytest.raises(WaftError, match="no Odoo manifest"):
        addons.converge(project, load_config(project))


def test_converge_removes_stale_links(project, calls, have_uv):
    _set_addons(project, {"oca-web": {"kind": "git", "url": "https://x/web.git"}})
    _fake_repo(project, "oca-web", ["web_a", "web_b"])
    addons.converge(project, load_config(project))
    assert (project.addons_dir / "web_b").is_symlink()
    _set_addons(
        project,
        {"oca-web": {"kind": "git", "url": "https://x/web.git", "addons": ["web_a"]}},
    )
    addons.converge(project, load_config(project))
    assert not (project.addons_dir / "web_b").exists()
    assert (project.addons_dir / "web_a").is_symlink()


def test_converge_refuses_to_clobber_real_dir(project, calls, have_uv):
    _set_addons(project, {"oca-web": {"kind": "git", "url": "https://x/web.git"}})
    _fake_repo(project, "oca-web", ["web_a"])
    (project.addons_dir / "web_a").mkdir()
    with pytest.raises(WaftError, match="not a symlink"):
        addons.converge(project, load_config(project))


def test_converge_aggregator_for_merges(project, calls, have_uv):
    _set_addons(
        project,
        {
            "oca-web": {
                "kind": "git",
                "url": "https://x/web.git",
                "merges": ["origin ${ODOO_VERSION}", "origin refs/pull/1/head"],
            }
        },
    )
    _fake_repo(project, "oca-web", ["web_a"])
    addons.converge(project, load_config(project))
    agg = next(call for call in calls if "gitaggregate" in call)
    assert agg[:4] == ["/usr/bin/uv", "tool", "run", "--from"]
    conf_file = project.tmp_dir / "aggregate-oca-web.yml"
    conf = yaml.safe_load(conf_file.read_text())
    repo_conf = conf[str(project.tmp_dir / "repos" / "oca-web")]
    assert repo_conf["target"] == "origin 16.0"
    assert repo_conf["merges"] == ["origin 16.0", "origin refs/pull/1/head"]


# ---------------------------------------------------------------- commands
def test_addon_add_infers_kind_and_lists(project, capsys):
    addons.addon_add(project, "oca-web", {"url": "https://x/web.git"})
    entries = addons.load_entries(project)
    assert entries["oca-web"].kind == "git"
    with pytest.raises(WaftError, match="already exists"):
        addons.addon_add(project, "oca-web", {})
    addons.addon_list(project)
    out = capsys.readouterr().out
    assert "oca-web" in out and "git" in out


def test_addon_add_rejects_reserved_odoo_name(project):
    with pytest.raises(WaftError, match="reserved for the Odoo source"):
        addons.addon_add(project, "odoo", {"url": "https://x/odoo.git"})


def test_addon_list_ignores_odoo_checkout(project, capsys):
    (project.odoo_dir / "odoo").mkdir(parents=True)
    addons.addon_list(project)
    assert "no addons configured" in capsys.readouterr().out


def test_addon_configure_and_delete(project):
    addons.addon_add(project, "oca-web", {"url": "https://x/web.git"})
    addons.addon_configure(project, "oca-web", {"branch": "17.0"})
    assert addons.load_entries(project)["oca-web"].branch == "17.0"
    with pytest.raises(WaftError, match="nothing to configure"):
        addons.addon_configure(project, "oca-web", {})
    addons.addon_delete(project, "oca-web")
    assert addons.load_entries(project) == {}
    with pytest.raises(WaftError, match="not found"):
        addons.addon_delete(project, "oca-web")


def test_addon_update_git(project, calls, have_uv):
    addons.addon_add(project, "oca-web", {"url": "https://x/web.git"})
    _fake_repo(project, "oca-web", ["web_a"])
    addons.addon_update(project, "oca-web")
    assert any("fetch" in call for call in calls)
    assert (project.addons_dir / "web_a").is_symlink()


def test_addon_update_pypi(project, calls, have_uv):
    addons.addon_add(project, "qj", {"spec": "odoo-addon-x"})
    addons.addon_update(project, "qj")
    assert any("--upgrade" in call for call in calls)


def test_addon_cli_roundtrip(tmp_path, capsys):
    main(["-d", str(tmp_path), "init", "--odoo-version", "16.0"])
    code = main(
        [
            "-d",
            str(tmp_path),
            "odoo",
            "addon",
            "--add",
            "oca-web",
            "--url",
            "https://x/web.git",
            "--addons",
            "web_a,web_b",
            "--merge",
            "origin ${ODOO_VERSION}",
        ]
    )
    assert code == 0
    data = yaml.safe_load((tmp_path / ".waft" / "conf" / "shared.yml").read_text())
    entry = data["ADDONS"]["oca-web"]
    assert entry["url"] == "https://x/web.git"
    assert entry["addons"] == ["web_a", "web_b"]
    assert entry["merges"] == ["origin ${ODOO_VERSION}"]
    code = main(["-d", str(tmp_path), "odoo", "addon", "--list"])
    assert code == 0
    assert "oca-web" in capsys.readouterr().out
