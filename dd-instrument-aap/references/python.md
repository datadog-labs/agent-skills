# Python

Open this in step 5 for a Python service outside AWS Lambda. The tracer is the `ddtrace` package. Supported frameworks: `django`, `flask`, `fastapi`, `tornado`, `uwsgi`.

## Install `ddtrace`

Skip this when `ddtrace` is already a dependency. Otherwise detect the package manager from the files in this order, and use the first match:

| Files | Command |
|---|---|
| `uv.lock` | `uv add --no-sync ddtrace` |
| `poetry.lock` | `poetry add --lock ddtrace` |
| `Pipfile.lock` | Add `ddtrace` under `[packages]` in `Pipfile`, then `pipenv lock` |
| `pdm.lock` | `pdm add --no-sync ddtrace` |
| `requirements.txt` | Add `ddtrace` (no pin), then `pip install -r requirements.txt` |
| `pyproject.toml` only | `[tool.poetry]`, `[tool.pdm]` or `[tool.uv]` picks that tool's command above; otherwise add `"ddtrace~=X.Y"` to `[project] dependencies`, with `X.Y` from `info.version` at `https://pypi.org/pypi/ddtrace/json` |
| None | `pip install --upgrade ddtrace` |

With a lockfile, run the tool's command; editing `pyproject.toml` by hand leaves the lockfile stale. When the tool is not installed here, add the `~=X.Y` pin to the dependency list and ask the user to run the lock command, such as `uv lock`. Once a dependency file declares `ddtrace`, do not also add `pip install ddtrace` to a start script or Dockerfile that already installs that file.

## Start the app

Start the app with the `ddtrace-run` wrapper, for example `ddtrace-run python app.py` or `ddtrace-run gunicorn app:app`, and set the AAP variables from step 5 in the place the deployment reference names. When the command starts with `uv run`, `poetry run`, `pipenv run` or `pdm run`, put `ddtrace-run` after it (`uv run ddtrace-run python app.py`), or call `.venv/bin/ddtrace-run`, because `ddtrace-run` is installed inside the virtual environment and is not on `PATH` outside it.

**uWSGI** does not support `ddtrace-run`. Add these options instead, on the command line or in `uwsgi.ini` (`enable-threads = 1`, `lazy-apps = 1`, `import = ddtrace.bootstrap.sitecustomize`):

```bash
uwsgi --enable-threads --lazy-apps --import=ddtrace.bootstrap.sitecustomize --module wsgi:app
```

Below uWSGI 2.0.30, also add `--skip-atexit`. With gevent, monkey-patch in the app code, not with `--gevent-monkey-patch`.

## Frameworks

Django, Flask and FastAPI need nothing more. **Tornado** also needs `DD_TRACE_TORNADO_ENABLED=true`, set beside `DD_APPSEC_ENABLED`, and it needs it on the Remote Configuration path too.

## In a container image

Install `ddtrace` the same way as the project's other dependencies: copy the manifest and its lockfile before the install step (`requirements.txt` then `pip install -r requirements.txt`, `pyproject.toml` and `uv.lock` then `uv sync`, `poetry.lock` then `poetry install --no-interaction`, `Pipfile.lock` then `pipenv install --deploy`, `pdm.lock` then `pdm install`). Only an image with no dependency file gets `RUN pip install ddtrace`. Then prefix the existing `CMD` or `ENTRYPOINT` with `ddtrace-run`, for example `CMD ["ddtrace-run", "python", "app.py"]`, or place it after `uv run` or `poetry run` as above. For uWSGI, add the uWSGI options above to its command or `uwsgi.ini` instead.

## With APM already on

Keep the existing `ddtrace` version and start command. Add only the AAP variables.
