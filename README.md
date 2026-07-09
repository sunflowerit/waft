# Waft

An Odoo installation and project-management tool.

## Install

```
pipx install waft
```

## Quick start

```
mkdir my-odoo-project && cd my-odoo-project
waft init                 # scaffold the project (asks for the Odoo version)
edit .waft/conf/secret.yml
waft sync                 # create venv, install Odoo, generate odoo.conf
waft database initial
waft odoo run
```

See `waft --help` and AGENTS.MD for the full specification.

## Development

```
pip install -e .[dev]
pytest
pre-commit install
```
