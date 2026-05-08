"""drama_subtitler — pip-installable façade.

Re-exports the public surface (`SubtitlePipeline`, model auto-download
helper) so external consumers can ``import drama_subtitler`` without
relying on the ``app.*`` Flask layout.
"""
from .pipeline import SubtitlePipeline
from .models import ensure_whispercpp_model, MODEL_CACHE_DIR

__all__ = ["SubtitlePipeline", "ensure_whispercpp_model", "MODEL_CACHE_DIR"]
