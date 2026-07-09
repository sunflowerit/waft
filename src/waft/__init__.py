"""Waft: an Odoo installation and project-management tool.

Successor of https://github.com/Therp/waft and https://github.com/Therp/waftlib.
"""

__version__ = "0.1.0.dev0"

#: Project directory format version. Bump when `waft init` output changes in a
#: way that requires a migration step in `waft sync` (see waft.scaffold).
#: Format 2: the Odoo source checkout lives in /odoo/ (git-ignored).
PROJECT_FORMAT = 2
