"""Whisper.cpp model auto-download / cache.

Centralises the convention of stashing ggml-format whisper.cpp models in
``~/.cache/drama_subtitler/models/`` so neither end users (CLI) nor
embedders (nasorg) need to manage the file by hand.

A single function — :func:`ensure_whispercpp_model` — is the public
contract. It returns an absolute :class:`pathlib.Path` to a verified
model file, downloading on first use.
"""
from __future__ import annotations

import os
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Optional


MODEL_CACHE_DIR = Path(
    os.environ.get("DRAMA_SUBTITLER_MODEL_DIR")
    or (Path.home() / ".cache" / "drama_subtitler" / "models")
).expanduser()

# Resolved upstream URLs for the ggml whisper.cpp models hosted by the
# whisper.cpp author on Hugging Face. Only entries we've vetted are listed
# — unknown names raise rather than silently picking a wrong default.
HF_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
KNOWN_MODELS = {
    "tiny":           f"{HF_BASE}/ggml-tiny.bin",
    "tiny.en":        f"{HF_BASE}/ggml-tiny.en.bin",
    "base":           f"{HF_BASE}/ggml-base.bin",
    "base.en":        f"{HF_BASE}/ggml-base.en.bin",
    "small":          f"{HF_BASE}/ggml-small.bin",
    "small.en":       f"{HF_BASE}/ggml-small.en.bin",
    "medium":         f"{HF_BASE}/ggml-medium.bin",
    "medium.en":      f"{HF_BASE}/ggml-medium.en.bin",
    "large-v1":       f"{HF_BASE}/ggml-large-v1.bin",
    "large-v2":       f"{HF_BASE}/ggml-large-v2.bin",
    "large-v3":       f"{HF_BASE}/ggml-large-v3.bin",
    "large-v3-turbo": f"{HF_BASE}/ggml-large-v3-turbo.bin",
}


def _download(url: str, dest: Path) -> None:
    """Stream-download ``url`` to ``dest`` atomically."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    try:
        with urllib.request.urlopen(url) as resp, open(tmp, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            chunk = 1 << 20  # 1 MiB
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                out.write(buf)
                done += len(buf)
                if total and sys.stderr.isatty():
                    pct = 100 * done / total
                    sys.stderr.write(
                        f"\rdrama_subtitler: downloading {dest.name} "
                        f"{done / 1e6:6.0f} / {total / 1e6:6.0f} MB ({pct:5.1f}%)"
                    )
                    sys.stderr.flush()
            if total and sys.stderr.isatty():
                sys.stderr.write("\n")
        os.replace(tmp, dest)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def ensure_whispercpp_model(name: str = "large-v3") -> Path:
    """Return a path to the whisper.cpp ggml model for ``name``.

    Resolution order:

    1. ``$WHISPER_CPP_MODEL_PATH`` env var, if set and pointing at a file.
    2. ``$DRAMA_SUBTITLER_MODEL_DIR/ggml-<name>.bin`` (or the standard
       ``~/.cache/drama_subtitler/models/`` location).
    3. Download the canonical Hugging Face URL into the cache dir.

    ``name`` may be a known size (``"large-v3"``) or an absolute path /
    explicit ``ggml-*.bin`` file name; the latter two are returned as-is
    if they exist on disk.
    """
    env = os.environ.get("WHISPER_CPP_MODEL_PATH")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p.resolve()
        raise FileNotFoundError(
            f"WHISPER_CPP_MODEL_PATH points at non-existent file: {p}")

    # Caller passed an absolute or relative path to a .bin directly.
    if name.endswith(".bin"):
        p = Path(name).expanduser()
        if p.is_file():
            return p.resolve()
        # Fall through to treat the basename as a model name.
        name = p.stem.replace("ggml-", "")

    if name not in KNOWN_MODELS:
        raise ValueError(
            f"unknown whisper.cpp model: {name!r}; known models: "
            f"{', '.join(sorted(KNOWN_MODELS))}")

    dest = MODEL_CACHE_DIR / f"ggml-{name}.bin"
    if dest.is_file() and dest.stat().st_size > 0:
        return dest.resolve()

    url = KNOWN_MODELS[name]
    sys.stderr.write(
        f"drama_subtitler: model {name!r} not in cache; downloading from {url}\n"
    )
    _download(url, dest)
    return dest.resolve()


def model_status(name: str = "large-v3") -> dict:
    """Return a small dict describing the on-disk state of ``name``.

    Useful for status endpoints / CLIs that want to show "downloaded?
    where? how big?" without triggering a download.
    """
    dest = MODEL_CACHE_DIR / f"ggml-{name}.bin"
    out: dict = {
        "name": name,
        "cache_dir": str(MODEL_CACHE_DIR),
        "expected_path": str(dest),
        "downloaded": dest.is_file(),
        "size_bytes": dest.stat().st_size if dest.is_file() else 0,
        "url": KNOWN_MODELS.get(name),
    }
    env = os.environ.get("WHISPER_CPP_MODEL_PATH")
    if env:
        ep = Path(env).expanduser()
        out["env_override"] = str(ep)
        out["env_override_exists"] = ep.is_file()
    return out


def main(argv: Optional[list[str]] = None) -> int:  # pragma: no cover - thin CLI
    """Tiny CLI: ``python -m drama_subtitler.models download large-v3``."""
    import argparse
    p = argparse.ArgumentParser(prog="drama_subtitler.models")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("name", nargs="?", default="large-v3")
    s = sub.add_parser("status")
    s.add_argument("name", nargs="?", default="large-v3")
    sub.add_parser("list")
    args = p.parse_args(argv)
    if args.cmd == "download":
        path = ensure_whispercpp_model(args.name)
        print(path)
        return 0
    if args.cmd == "status":
        import json
        print(json.dumps(model_status(args.name), indent=2))
        return 0
    if args.cmd == "list":
        for n in sorted(KNOWN_MODELS):
            print(n)
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
