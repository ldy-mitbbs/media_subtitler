"""Public pipeline import.

The implementation still lives at ``app/models/subtitle_pipeline.py`` (the
original Flask-app layout). We load that leaf module *without* importing
``app/__init__.py`` (which would force a Flask + config import on every
consumer) by using ``importlib.util.spec_from_file_location``.

External consumers should::

    from drama_subtitler import SubtitlePipeline

The path indirection is an internal detail and can go away once the
implementation file is moved into this package proper.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_impl() -> ModuleType:
    # drama_subtitler/<this_file> → repo_root/app/models/subtitle_pipeline.py
    repo_root = Path(__file__).resolve().parent.parent
    target = repo_root / "app" / "models" / "subtitle_pipeline.py"
    if not target.is_file():
        raise ImportError(
            "drama_subtitler: implementation file missing at "
            f"{target}. Reinstall the package or check your checkout."
        )
    mod_name = "drama_subtitler._impl_subtitle_pipeline"
    cached = sys.modules.get(mod_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(mod_name, str(target))
    if not spec or not spec.loader:
        raise ImportError(f"could not load {target}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


_impl = _load_impl()
SubtitlePipeline = _impl.SubtitlePipeline  # type: ignore[attr-defined]

__all__ = ["SubtitlePipeline"]
