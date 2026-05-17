import os
import sys
import tempfile

# Add the project root to sys.path so `app` and `config` are importable.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_settings_dir = tempfile.TemporaryDirectory(prefix="media-subtitler-tests-")
os.environ["MEDIA_SUBTITLER_SETTINGS_PATH"] = os.path.join(
    _settings_dir.name,
    "settings.json",
)

# Load .env / .env.local so integration tests can pick up API keys automatically.
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(dotenv_path=".env", override=False)
    load_dotenv(dotenv_path=".env.local", override=False)

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that call real model APIs (may cost tokens / require local daemons)",
    )
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run slow tests (including heavy local model inference)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: tests that call real external APIs (Ollama, OpenRouter, etc.)")
    config.addinivalue_line("markers", "slow: tests that take >5s or load large models")
    config.addinivalue_line("markers", "ollama: tests that require a running Ollama daemon")
    config.addinivalue_line("markers", "openrouter: tests that require an OpenRouter API key")
    config.addinivalue_line("markers", "deepseek: tests that require a DeepSeek API key")
    config.addinivalue_line("markers", "unit: fast, isolated unit tests (default)")


def pytest_collection_modifyitems(config, items):
    skip_integration = pytest.mark.skip(
        reason="Pass --run-integration to execute integration tests"
    )
    skip_slow = pytest.mark.skip(
        reason="Pass --run-slow to execute slow tests"
    )

    run_integration = config.getoption("--run-integration")
    run_slow = config.getoption("--run-slow")

    for item in items:
        has_integration = any(m.name == "integration" for m in item.iter_markers())
        has_slow = any(m.name == "slow" for m in item.iter_markers())
        if has_integration and not run_integration:
            item.add_marker(skip_integration)
        if has_slow and not run_slow:
            item.add_marker(skip_slow)


def pytest_sessionfinish(session, exitstatus):
    _settings_dir.cleanup()
