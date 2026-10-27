"""Diagnostic JS interpreter only; never an accepted browser runtime identity."""
import os
import shutil


def node_executable():
    selected = os.environ.get("BUFFALO_DIAGNOSTIC_NODE")
    if selected is None:
        selected = shutil.which("node")
    if (not selected or not os.path.isabs(selected)
            or os.path.normpath(selected) != selected
            or not os.path.isfile(selected) or not os.access(selected, os.X_OK)):
        return None
    return selected
