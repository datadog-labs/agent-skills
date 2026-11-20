# .NET profiler enablement

Use the shared environment variables guidance in `SKILL.md` Step 4: set `DD_SERVICE`, `DD_ENV`, `DD_VERSION`.

The profiler ships with the .NET tracer (v2.8.0+) and attaches through the CLR profiling API, so only one APM solution can be active in the process.

1. Install the tracer if missing: machine-wide package (`.deb`, `.rpm`, `.tar.gz`, or Windows MSI) or per-app `Datadog.Trace.Bundle` NuGet package. IIS applications need the machine-wide installer.
2. On Linux, set for the service:

   ```text
   CORECLR_ENABLE_PROFILING=1
   CORECLR_PROFILER={846F5F1C-F9AE-4B07-969E-05C26BC060D8}
   CORECLR_PROFILER_PATH=/opt/datadog/Datadog.Trace.ClrProfiler.Native.so
   DD_DOTNET_TRACER_HOME=/opt/datadog
   LD_PRELOAD=/opt/datadog/continuousprofiler/Datadog.Linux.ApiWrapper.x64.so
   DD_PROFILING_ENABLED=1
   DD_SERVICE=<SERVICE_NAME>
   DD_ENV=<ENV>
   DD_VERSION=<VERSION>
   ```

   For a per-app install with the `Datadog.Trace.Bundle` NuGet package, the tracer files live in the application's output directory, so use paths under `<APP_DIRECTORY>/datadog` (the directory containing the app's `.dll` files) instead of `/opt/datadog`:

   ```text
   CORECLR_ENABLE_PROFILING=1
   CORECLR_PROFILER={846F5F1C-F9AE-4B07-969E-05C26BC060D8}
   CORECLR_PROFILER_PATH=<APP_DIRECTORY>/datadog/<RID>/Datadog.Trace.ClrProfiler.Native.so
   LD_PRELOAD=<APP_DIRECTORY>/datadog/<RID>/Datadog.Linux.ApiWrapper.x64.so
   DD_DOTNET_TRACER_HOME=<APP_DIRECTORY>/datadog
   DD_PROFILING_ENABLED=1
   DD_SERVICE=<SERVICE_NAME>
   DD_ENV=<ENV>
   DD_VERSION=<VERSION>
   ```

   `<RID>` is `linux-x64`, `linux-arm64`, or `linux-musl-x64` (Alpine), matching the runtime image. On Windows the `CORECLR_PROFILER_PATH` is `<APP_DIRECTORY>\datadog\win-x64\Datadog.Trace.ClrProfiler.Native.dll` (or `win-x86`) and `LD_PRELOAD` is not used. In Docker images on Linux, also run `<APP_DIRECTORY>/datadog/createLogPath.sh` at build time.

   Do not enable the profiler machine-wide or for all IIS apps. If APM was installed with these variables and SSI is also in use, remove the manual variables first.
3. For Windows services, IIS, Azure App Service, and standalone Windows apps, registry and extension steps differ; follow https://docs.datadoghq.com/profiler/enabling/?prog_lang=dot_net and do not guess.
