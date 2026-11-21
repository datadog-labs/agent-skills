# .NET

Open this in step 5 for a .NET service outside AWS Lambda. The tracer is the Datadog .NET tracer, a native CLR profiler installed from the [dd-trace-dotnet releases](https://github.com/DataDog/dd-trace-dotnet/releases). Supported frameworks: `aspnet-mvc`, `aspnet-web-api`. Neither needs framework-specific steps.

## Contents

- Profiler variables
- Install on a Linux host
- Install on Windows
- In a container image

## Profiler variables

Set these next to the AAP variables from step 5, in the place the deployment reference names. For .NET Core and .NET 5 or later on Linux:

```bash
CORECLR_ENABLE_PROFILING=1
CORECLR_PROFILER={846F5F1C-F9AE-4B07-969E-05C26BC060D8}
CORECLR_PROFILER_PATH=/opt/datadog/Datadog.Trace.ClrProfiler.Native.so
DD_DOTNET_TRACER_HOME=/opt/datadog
```

.NET Framework uses `COR_ENABLE_PROFILING`, `COR_PROFILER` and `COR_PROFILER_PATH` with the same GUID. On Windows, the profiler path is `C:\Program Files\Datadog\.NET Tracer\win-x64\Datadog.Trace.ClrProfiler.Native.dll` and the home is `C:\Program Files\Datadog\.NET Tracer`. With APM already on, keep the existing profiler setup and add only the AAP variables.

## Install on a Linux host

Resolve the latest version, then install the package for the host's architecture (`amd64` or `arm64` for `.deb`, `x86_64` or `aarch64` for `.rpm`):

```bash
LATEST_TAG=$(curl -fsSI https://github.com/DataDog/dd-trace-dotnet/releases/latest | grep -io '/tag/v[0-9][0-9.]*' | cut -d/ -f3)
[ -n "$LATEST_TAG" ] || LATEST_TAG="v$(curl -fsSL https://api.nuget.org/v3-flatcontainer/datadog.trace/index.json | grep -o '"[0-9]*\.[0-9]*\.[0-9]*"' | tr -d '"' | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)"
V="${LATEST_TAG#v}"
case "$(uname -m)" in
  x86_64) DEB_ARCH=amd64; RPM_ARCH=x86_64 ;;
  aarch64|arm64) DEB_ARCH=arm64; RPM_ARCH=aarch64 ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac
# Debian or Ubuntu:
curl -fLO --retry 5 "https://github.com/DataDog/dd-trace-dotnet/releases/download/${LATEST_TAG}/datadog-dotnet-apm_${V}_${DEB_ARCH}.deb" && sudo dpkg -i "datadog-dotnet-apm_${V}_${DEB_ARCH}.deb"
# RHEL or CentOS:
curl -fLO --retry 5 "https://github.com/DataDog/dd-trace-dotnet/releases/download/${LATEST_TAG}/datadog-dotnet-apm-${V}-1.${RPM_ARCH}.rpm" && sudo rpm -Uvh "datadog-dotnet-apm-${V}-1.${RPM_ARCH}.rpm"
```

Keep only the branch that matches the host when the repository shows it. The NuGet fallback covers GitHub API rate limits.

## Install on Windows

Install the x64 MSI for the latest release (there is no MSI for other architectures), then restart IIS when it hosts the app (`net stop /y was` then `net start w3svc`):

```powershell
$Tag = (Invoke-RestMethod -Uri "https://api.github.com/repos/DataDog/dd-trace-dotnet/releases/latest").tag_name
$V = $Tag.TrimStart("v")
Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/DataDog/dd-trace-dotnet/releases/download/$Tag/datadog-dotnet-apm-$V-x64.msi" -OutFile "datadog-dotnet-apm-$V-x64.msi"
$p = Start-Process msiexec.exe -ArgumentList "/i `"datadog-dotnet-apm-$V-x64.msi`" /quiet /norestart" -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "msiexec failed with exit code $($p.ExitCode); run PowerShell as Administrator" }
```

When the GitHub API is rate limited, take the newest stable version from `https://api.nuget.org/v3-flatcontainer/datadog.trace/index.json`. Set the profiler variables in the process that launches the app. Use machine scope only for IIS or a Windows service that requires it, with `[Environment]::SetEnvironmentVariable("<NAME>", "<value>", "Machine")` or the service's own environment.

## In a container image

Install the tracer in the Dockerfile, not on the machine running this setup, because the profiler must exist in the image that runs the app. Install it in the final runtime stage, or install it in an earlier stage and `COPY --from=<stage> /opt/datadog /opt/datadog`. Use no `sudo`. Pin the current version from the releases page, because the package file name contains it. Pick the architecture of the image, not of the build host:

```dockerfile
ARG DD_TRACER_VERSION=<version from the releases page>
RUN DD_TRACER_ARCH="$(dpkg --print-architecture)" \
    && DD_TRACER_PACKAGE="datadog-dotnet-apm_${DD_TRACER_VERSION}_${DD_TRACER_ARCH}.deb" \
    && curl -fLo "/tmp/${DD_TRACER_PACKAGE}" "https://github.com/DataDog/dd-trace-dotnet/releases/download/v${DD_TRACER_VERSION}/${DD_TRACER_PACKAGE}" \
    && dpkg -i "/tmp/${DD_TRACER_PACKAGE}" \
    && rm "/tmp/${DD_TRACER_PACKAGE}" \
    && mkdir -p /var/log/datadog
ENV CORECLR_ENABLE_PROFILING=1
ENV CORECLR_PROFILER="{846F5F1C-F9AE-4B07-969E-05C26BC060D8}"
ENV CORECLR_PROFILER_PATH=/opt/datadog/Datadog.Trace.ClrProfiler.Native.so
ENV DD_DOTNET_TRACER_HOME=/opt/datadog
```

- RPM-based images (RHEL, Amazon Linux, Azure Linux): the package is `datadog-dotnet-apm-${DD_TRACER_VERSION}-1.$(uname -m).rpm`, installed with `rpm -Uvh`.
- The commands need `curl`. Install it first when the base image lacks it (`apt-get update && apt-get install -y curl`, or `microdnf install -y curl`).
- Alpine and other musl images: use `datadog-dotnet-apm-${DD_TRACER_VERSION}-musl.tar.gz`, and point `CORECLR_PROFILER_PATH` and `DD_DOTNET_TRACER_HOME` at the directory you extract it into.
- Chiseled or distroless runtime images: install in a build stage and copy `/opt/datadog` into the runtime stage.
- A Windows container image needs the `windows` environment instead: stop and tell the user, because none of the Linux paths apply.
