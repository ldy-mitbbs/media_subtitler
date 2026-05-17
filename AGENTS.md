# Agent Notes

## Python Environment

- Use the repository virtual environment for Python commands.
- Prefer `.venv/bin/python` over `python`.
- Prefer `.venv/bin/pytest` over `pytest`.
- If a command fails because dependencies are missing, retry it through `.venv` before assuming the project is broken.

## Validation

- For focused pipeline work, run:

  ```bash
  .venv/bin/pytest tests/test_subtitle_pipeline.py -q
  ```

- Before shipping broader changes, run:

  ```bash
  .venv/bin/pytest -q
  ```
