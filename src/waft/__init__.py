"""Waft: an Odoo installation and project-management tool.

Successor of https://github.com/Therp/waft and https://github.com/Therp/waftlib.
"""

__version__ = "0.1.0.dev0"

#: Project directory format version. Bump when `waft init` output changes in a
#: way that requires a migration step in `waft sync` (see waft.scaffold).
#: Format 2: the Odoo source checkout lives in /odoo/ (git-ignored).
#: Format 3: all sources live in .src/ (Odoo checkout + addon repo clones).
#: Format 4: the Odoo checkout lives in addons/odoo/; addon repos in .tmp/repos/.
#: Format 5: addon repos are cloned into addons/<entry>/ and every addon is
#: declared with an install type (clone/pypi/source); nothing is implicit.
PROJECT_FORMAT = 5
