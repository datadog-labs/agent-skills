# PHP

Open this in step 5 for a PHP service. The tracer is the Datadog PHP extension with its AppSec extension (`ddappsec`), installed by `datadog-setup.php`. Supported frameworks: `laravel`, `symfony`, `wordpress`, `cakephp`, `codeigniter`, `slim`, `lumen`, `neos-flow`, `yii`, `zend`, `roadrunner`, `magento`. None of them needs framework-specific steps.

## Install the tracer (APM off)

On a host:

```bash
curl -LO https://github.com/DataDog/dd-trace-php/releases/latest/download/datadog-setup.php
php datadog-setup.php --php-bin=all --enable-appsec
```

In a Dockerfile:

```dockerfile
RUN curl -LO https://github.com/DataDog/dd-trace-php/releases/latest/download/datadog-setup.php \
    && php datadog-setup.php --php-bin=all --enable-appsec \
    && rm datadog-setup.php
```

The installer finds every PHP binary, and installs and loads the AppSec extension on supported PHP builds.

## APM already on

Inspect first, and change nothing during the inspection. Reuse the existing tracer installation, and keep its version, its install method, the PHP configuration, the start command, the Agent settings, and the existing `DD_SERVICE`, `DD_ENV` and `DD_VERSION`. Do not add `DD_SERVICE`, even when you know the name; use a known name only for the lookup and the checks. Confirm that the AppSec extension is available to the PHP runtime that serves the app:

- **Available:** leave the installation step unchanged and add only the AAP variables.
- **Missing:** add it through the existing install method while keeping the tracer version. For an existing `datadog-setup.php` step, do not add `--enable-appsec`: it only turns AppSec on, which `DD_APPSEC_ENABLED=true` already does, and it cannot add a missing `ddappsec`. When the existing method cannot add it, explain which tracer version or PHP build is needed, and do not change the version yourself. Add no second installer call, and never run the installer on the Docker host.

## PHP-FPM workers

PHP-FPM clears its workers' environment unless the pool sets `clear_env = no`. Pass every Datadog tracer variable you set to the workers with `env[]` lines in the pool section that serves the app (the stock pool is `[www]`):

```ini
env[DD_APPSEC_ENABLED] = $DD_APPSEC_ENABLED
env[DD_APPSEC_AGENTIC_ONBOARDING] = $DD_APPSEC_AGENTIC_ONBOARDING
```

- Set the variables in the environment of the process that runs php-fpm (the task definition, pod spec, image `ENV` or systemd unit), not the web server's, and use the `$VAR` form, because a literal `false` or an empty value stops php-fpm from starting. When php-fpm starts through `service`, which clears the environment, use quoted literals instead, such as `env[DD_APPSEC_ENABLED] = "true"`.
- Add the lines to a pool file the repository already copies, or to a new `zz-datadog.conf` in the pool include directory (`php-fpm.d/` or `pool.d/`) that starts with that pool's header.
- From a Dockerfile `RUN`, single-quote the lines or escape `$`; copying a file is safer.
- Add a line for each other Datadog tracer variable you set, such as `DD_APM_TRACING_ENABLED` or `DD_SERVICE`. Never put `env[]` under `[global]`, never replace the stock pool file with a partial one, and never forward `DD_API_KEY`.
