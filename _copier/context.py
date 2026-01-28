import re

from copier_template_extensions import ContextHook


class ContextUpdater(ContextHook):

    detection_run = False

    def _detect_waft_version(self):
        with open("bootstrap", "r") as file:
            for line in file:
                if line.startswith("export LIBRARIES_VERSION_BRANCH"):
                    pattern = r':-(.+?)}'
                    _match = re.search(pattern, line)
                    if _match:
                        return _match.group(1)

    def _detect_odoo_version(self):
        with open(".env-shared", "r") as file:
            for line in file:
                if line.startswith("ODOO_VERSION="):
                    pattern = r'=\"(.+?)\"'
                    _match = re.search(pattern, line)
                    if _match:
                        return _match.group(1)

    def hook(self, context):
        context_updates = {}

        print(context["_copier_phase"])
        print(context.get("default_waft_version"))
        print(context.get("default_odoo_version"))
        if not self.detection_run:
            print("Trying to detect waftlib version from bootstrap file...")
            waftlib_version = self._detect_waft_version()
            context_updates["default_waftlib_version"] = waftlib_version
            print("Trying to detect Odoo version from .env-shared file...")
            odoo_version = self._detect_odoo_version()
            context_updates["default_odoo_version"] = odoo_version
            self.detection_run = True
            
        return context_updates
