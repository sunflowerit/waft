"""Layered configuration engine and odoo.conf generation.

Precedence (later overrides earlier):

    waft code defaults (waft.versions)  <  shared.yml  <  secret.yml

All defaults live in waft Python code; when a default must materialize as a
file, waft code generates it. Variable names stay compatible with the old
waftlib ``.env-*`` files.
"""

from __future__ import annotations

import configparser
import io
import re
from pathlib import Path

import yaml

from . import versions
from .project import Project, WaftError

#: Variables that belong in secret.yml (git-ignored), never in shared.yml.
SECRET_VARS = {"PGPASSWORD", "PGDATABASE", "ODOO_ADMIN_PASSWORD"}

_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VAR_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _coerce(value) -> str:
    """YAML scalars to the string form odoo.conf and the old env files use."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def load_yaml_mapping(path: Path) -> dict:
    """Load one YAML mapping file; missing file = empty mapping."""
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise WaftError(f"invalid YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise WaftError(f"{path} must contain a YAML mapping (variable: value)")
    return data


def load_yaml_vars(path: Path) -> dict[str, str]:
    """Scalar configuration variables from one config file.

    Structured keys (like ADDONS, handled by waft.addons) are skipped.
    """
    return {
        str(key): _coerce(value)
        for key, value in load_yaml_mapping(path).items()
        if not isinstance(value, (dict, list))
    }


def load_config(project: Project) -> dict[str, str]:
    """Merged configuration: code defaults < shared.yml < secret.yml."""
    shared = load_yaml_vars(project.shared_yml)
    secret = load_yaml_vars(project.secret_yml)
    version = secret.get("ODOO_VERSION") or shared.get("ODOO_VERSION")
    if not version:
        raise WaftError(
            f"ODOO_VERSION is not set in {project.shared_yml} "
            f"(nor {project.secret_yml})"
        )
    merged = versions.config_defaults(version)
    merged.update(shared)
    merged.update(secret)
    return merged


def computed_vars(project: Project) -> dict[str, str]:
    """WAFT_* variables computed by waft itself, usable in ${...} references."""
    return {
        "WAFT_ADDONS_PATH": str(project.addons_dir),
        "WAFT_DATA_DIR": str(project.odoo_data_dir),
        "WAFT_LOG_DIR": str(project.log_dir),
        "WAFT_ROOT": str(project.root),
    }


def substitute(text: str, mapping: dict[str, str], source: str) -> str:
    """Substitute ``${VAR}``/``$VAR`` references; missing var = clear error."""
    missing = sorted(
        {braced or bare for braced, bare in _VAR_REF.findall(text)} - mapping.keys()
    )
    if missing:
        raise WaftError(
            f"unknown configuration variable(s) {', '.join(missing)} "
            f"referenced by {source}"
        )
    return _VAR_REF.sub(lambda m: mapping[m.group(1) or m.group(2)], text)


def render_odoo_conf(project: Project, config: dict[str, str] | None = None) -> str:
    """Render the odoo.conf content for the project (no file written)."""
    config = config if config is not None else load_config(project)
    mapping = {**config, **computed_vars(project)}
    template = versions.odoo_conf_template(config["ODOO_VERSION"])
    parser = configparser.RawConfigParser()
    for section, keys in template.items():
        parser.add_section(section)
        for key, value in keys.items():
            parser.set(
                section,
                key,
                substitute(value, mapping, f"odoo.conf key '{section}.{key}'"),
            )
    out = io.StringIO()
    parser.write(out)
    return out.getvalue()


def generate_odoo_conf(project: Project, config: dict[str, str] | None = None) -> Path:
    """Write .waft/conf/odoo.conf from the merged configuration."""
    content = render_odoo_conf(project, config)
    project.conf_dir.mkdir(parents=True, exist_ok=True)
    project.odoo_conf.write_text(content, encoding="utf-8")
    return project.odoo_conf


def _dump_yaml_vars(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, default_flow_style=False, sort_keys=True)
    path.write_text(text, encoding="utf-8")


def config_set(project: Project, assignment: str) -> Path:
    """``waft odoo config set VAR=VALUE``: update shared.yml or secret.yml."""
    if "=" not in assignment:
        raise WaftError(f"expected VARIABLE=value, got {assignment!r}")
    var, _, value = assignment.partition("=")
    var = var.strip()
    if not _VAR_NAME.match(var):
        raise WaftError(f"invalid variable name {var!r}")
    target = project.secret_yml if var in SECRET_VARS else project.shared_yml
    data = load_yaml_vars(target)
    data[var] = value
    _dump_yaml_vars(target, data)
    generate_odoo_conf(project)
    return target


def config_remove(project: Project, var: str) -> list[Path]:
    """``waft odoo config remove VAR``: remove from config files."""
    removed: list[Path] = []
    for path in (project.shared_yml, project.secret_yml):
        data = load_yaml_vars(path)
        if var in data:
            del data[var]
            _dump_yaml_vars(path, data)
            removed.append(path)
    if not removed:
        raise WaftError(f"variable {var!r} not found in shared.yml or secret.yml")
    generate_odoo_conf(project)
    return removed


def config_list(project: Project, mask_secrets: bool = True) -> list[tuple[str, str]]:
    """``waft odoo config list``: the effective next-run configuration."""
    merged = load_config(project)
    items = []
    for key in sorted(merged):
        value = merged[key]
        if mask_secrets and key in SECRET_VARS and value:
            value = "********"
        items.append((key, value))
    return items


def config_test(project: Project) -> list[str]:
    """``waft odoo config test``: validate config files; return problems."""
    problems: list[str] = []
    for path in (project.shared_yml, project.secret_yml):
        try:
            load_yaml_vars(path)
        except WaftError as exc:
            problems.append(str(exc))
    if problems:
        return problems
    try:
        config = load_config(project)
        versions.get(config["ODOO_VERSION"])
        render_odoo_conf(project, config)
    except WaftError as exc:
        problems.append(str(exc))
        return problems
    shared = load_yaml_vars(project.shared_yml)
    for var in sorted(SECRET_VARS & shared.keys()):
        if shared[var]:
            problems.append(
                f"secret variable {var} is set in {project.shared_yml}; "
                f"move it to {project.secret_yml}"
            )
    known = versions.config_defaults(config["ODOO_VERSION"]).keys()
    for var in sorted(
        (shared.keys() | load_yaml_vars(project.secret_yml).keys()) - known
    ):
        problems.append(f"warning: unknown variable {var} (no effect on odoo.conf)")
    return problems
