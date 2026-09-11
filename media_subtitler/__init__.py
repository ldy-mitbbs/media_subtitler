"""media_subtitler — pip-installable façade.

Re-exports the public surface (`SubtitlePipeline`, model auto-download
helper) so external consumers can ``import media_subtitler`` without
relying on the ``app.*`` Flask layout.
"""
# Keep utility submodules importable without loading the source-tree façade.
# The desktop app imports app.models directly; its packaged Python files are
# not standalone source files for pipeline._load_impl() to open.
__all__ = ["SubtitlePipeline", "ensure_whispercpp_model", "MODEL_CACHE_DIR"]


def __getattr__(name):
    if name == "SubtitlePipeline":
        from .pipeline import SubtitlePipeline
        value = SubtitlePipeline
    elif name in {"ensure_whispercpp_model", "MODEL_CACHE_DIR"}:
        from . import models
        value = getattr(models, name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
