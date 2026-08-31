"""Ensure commonlib imports work from workflow subfolders."""

from __future__ import annotations

import sys
from pathlib import Path


def setup() -> Path:
    cwd = Path.cwd()
    for root in (cwd, cwd.parent):
        if (root / "commonlib" / "config.py").is_file():
            root_str = str(root)
            if root_str not in sys.path:
                sys.path.insert(0, root_str)
            return root
    raise RuntimeError(
        "Could not find commonlib/. Start Jupyter from notebooks "
        "or open a notebook from the notebooks/ directory."
    )
