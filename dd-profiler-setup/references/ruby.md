# Ruby profiler enablement

Use the shared environment variables guidance in `SKILL.md` Step 4: set `DD_SERVICE`, `DD_ENV`, `DD_VERSION`.

1. Add `gem 'datadog', '~> 2.30'` to the `Gemfile` and run `bundle install`.
2. Enable via environment (`DD_PROFILING_ENABLED=true`, `DD_ENV`, `DD_SERVICE`, `DD_VERSION`) or `Datadog.configure { |c| c.profiling.enabled = true; ... }` (Rails: `config/initializers/datadog.rb`).
3. Start the app with the profiler wrapper: `bundle exec ddprofrb exec <start command>` (for example `bundle exec ddprofrb exec bin/rails s`). When that is not possible (for example Phusion Passenger), add `require 'datadog/profiling/preload'` to the entrypoint such as `config.ru`.
