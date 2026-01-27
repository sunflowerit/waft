#!/usr/bin/env python3
import re
import yaml

# Path to the answers file
answers_file = ".copier-answers.yml"

# Load the existing answers
with open(answers_file, "r") as f:
    answers = yaml.safe_load(f) or {}

# Get the Odoo version from ".e
with open("bootstrap", "r") as file:
    for line in file:
        if line.startswith("export LIBRARIES_VERSION_BRANCH"):
            pattern = r':-(.+?)}'
            _match = re.search(pattern, line)
            if _match:
                answers["wwwaftlib_version"] = match.group(1)

# Get the waft version from "bootstrap.sh"
with open("bootstrap", "r") as file:
    for line in file:
        if line.startswith("ODOO_VERSION="):
            pattern = r'=\"(.+?)\"'
            _match = re.search(pattern, line)
            if _match:
                answers["ooodoo_version"] = match.group(1)

# Save the updated answers back to the file
with open(answers_file, "w") as f:
    yaml.dump(answers, f, default_flow_style=False, sort_keys=False)
