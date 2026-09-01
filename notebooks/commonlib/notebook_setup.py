"""Ensure commonlib imports work from workflow subfolders."""

from __future__ import annotations

import sys
from pathlib import Path


def find_notebooks_root() -> Path:
    """Return the notebooks/ directory that contains commonlib/."""
    for root in (Path.cwd(), *Path.cwd().parents):
        if (root / "commonlib" / "config.py").is_file():
            return root
        notebooks_root = root / "notebooks"
        if (notebooks_root / "commonlib" / "config.py").is_file():
            return notebooks_root
    raise RuntimeError(
        "Could not find notebooks/commonlib/. Start Jupyter from notebooks/ "
        "or open a notebook under export/, plan/, apply/, sdk-plan/, or the notebooks root."
    )


def setup() -> Path:
    """Add notebooks/ to sys.path so `import commonlib...` works."""
    root = find_notebooks_root()
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root
