import re

from copier_template_extensions import ContextHook


class ContextUpdater(ContextHook):
    def hook(self, context):
        context_updates = {}

        print("Trying to detect waftlib version from bootstrap file...")
        with open("bootstrap", "r") as file:
            for line in file:
                if line.startswith("export LIBRARIES_VERSION_BRANCH"):
                    pattern = r':-(.+?)}'
                    _match = re.search(pattern, line)
                    if _match:
                        context_updates["default_waftlib_version"] = _match.group(1)
                        break

        print("Trying to detect Odoo version from .env-shared file...")
        with open(".env-shared", "r") as file:
            for line in file:
                if line.startswith("ODOO_VERSION="):
                    pattern = r'=\"(.+?)\"'
                    _match = re.search(pattern, line)
                    if _match:
                        context_updates["default_odoo_version"] = _match.group(1)
                        break

        return context_updates
