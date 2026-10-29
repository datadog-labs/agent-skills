# Python profiler enablement

Use the shared environment variables guidance in `SKILL.md` Step 4: set `DD_SERVICE`, `DD_ENV`, `DD_VERSION`.

1. Add `ddtrace` to the project's dependencies (`pip install ddtrace`).
2. Run the app under `ddtrace-run` with profiling enabled:

   ```shell
   DD_PROFILING_ENABLED=true DD_ENV=<ENV> DD_SERVICE=<SERVICE_NAME> DD_VERSION=<VERSION> \
     ddtrace-run python app.py
   ```

   When `ddtrace-run` cannot wrap the entrypoint, start `ddtrace.profiling.Profiler(env=..., service=..., version=...).start()` as early as possible in the program, before other imports.
3. On Alpine, `gcc musl-dev linux-headers` may be needed if no binary wheel is available.
