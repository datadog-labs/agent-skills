# Ruby

Open this in step 5 for a Ruby service outside AWS Lambda. The tracer is the `datadog` gem. Supported frameworks: `rails`, `sinatra`, `rack`, `grape`, `graphql`.

## Install the gem

When the Gemfile has no Datadog gem yet, add this line once, in the repository's Gemfile, and run `bundle install`:

```ruby
gem 'datadog', '~> 2.0', require: 'datadog/auto_instrument'
```

Do not add commands that change the Gemfile at run time, such as in a start script. Do not add other Datadog gems, such as `datadog-appsec` or a command wrapper. Do not wrap the start command with `ddtrace-run`, `datadog-run` or another wrapper, because the Gemfile `require` loads the instrumentation.

## Enable AAP

Pick the method that matches how Datadog starts today, and do not switch methods:

- **Auto-instrumentation** (the Gemfile line above, or no Datadog initializer): set the AAP variables from step 5 on the running app. Without APM, that includes `DD_APM_TRACING_ENABLED=false`.
- **Initializer** (APM on, with Datadog configured in code, such as `config/initializers/datadog.rb`): add AAP to the existing `Datadog.configure` block, with one `c.appsec.instrument` line per recorded framework:

  ```ruby
  Datadog.configure do |c|
    # existing configuration
    c.appsec.enabled = true
    c.appsec.instrument :rails
  end
  ```

  Still set `DD_APPSEC_AGENTIC_ONBOARDING=true` as an environment variable on the running app.

## Frameworks

With auto-instrumentation, frameworks need nothing more. With an initializer, each recorded framework needs its `c.appsec.instrument :<framework>` line.

## In a container image

The image build must install the updated gems: copy `Gemfile` before `bundle install`, and copy `Gemfile.lock` too, but only when the project has one, because `COPY` fails on a missing path. Keep the Gemfile line from above, without a duplicate, and set the AAP variables where the deployment reference says.
