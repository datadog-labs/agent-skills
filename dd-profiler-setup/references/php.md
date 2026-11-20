# PHP profiler enablement

Use the shared environment variables guidance in `SKILL.md` Step 4: set `DD_SERVICE`, `DD_ENV`, `DD_VERSION`.

1. Install the profiler extension:

   ```shell
   curl --proto '=https' --tlsv1.2 -sSfLO https://github.com/DataDog/dd-trace-php/releases/latest/download/datadog-setup.php
   php datadog-setup.php --enable-profiling --php-bin=all
   ```

   The installer is interactive; run it with the user's approval and prefer the non-interactive command it prints.
2. Configure: `php datadog-setup.php config set -d datadog.service=<SERVICE_NAME> -d datadog.env=<ENV> -d datadog.version=<VERSION>`.
3. Restart Apache, PHP-FPM, FrankenPHP, or other servers, then confirm `php -v` lists `datadog-profiling`.
