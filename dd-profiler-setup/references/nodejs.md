# Node.js profiler enablement

Use the shared environment variables guidance in `SKILL.md` Step 4: set `DD_SERVICE`, `DD_ENV`, `DD_VERSION`.

1. `npm install --save dd-trace@latest`.
2. Enable the profiler with the SDK loaded first:

   ```shell
   export DD_PROFILING_ENABLED=true DD_ENV=<ENV> DD_SERVICE=<SERVICE_NAME> DD_VERSION=<VERSION>
   node -r dd-trace/init app.js
   ```

   If the app already calls `require('dd-trace').init(...)`, set `DD_PROFILING_ENABLED=true` or pass `profiling: true` to the existing `init` instead of adding another.
3. For TypeScript or bundled apps, keep the SDK import first in the entrypoint.
