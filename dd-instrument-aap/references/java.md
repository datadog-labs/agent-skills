# Java

Open this in step 5 for a Java service outside AWS Lambda. The tracer is the Datadog Java agent, `dd-java-agent.jar`, loaded with `-javaagent`. Supported frameworks: `spring`, `spring-boot`, `grizzly`, `java-servlet`, `jetty`, `tomcat`, `vertx`, `grpc`. None of them needs framework-specific steps.

## Install the agent

Skip this when the start command already loads `dd-java-agent.jar`. For a container image, see below. Otherwise download the latest agent next to the app or into a shared directory such as `/opt/datadog`:

```bash
wget -O dd-java-agent.jar https://dtdg.co/latest-java-tracer
```

On Windows PowerShell, use `Invoke-WebRequest -Uri https://dtdg.co/latest-java-tracer -OutFile dd-java-agent.jar -UseBasicParsing`. Do not use `wget` there: it is an alias for `Invoke-WebRequest` and rejects `-O`.

## Start the app

Add `-javaagent` to the existing start command, and set the AAP variables from step 5 as environment variables in the place the deployment reference names:

```bash
java -javaagent:/opt/datadog/dd-java-agent.jar -jar your-app.jar
```

Use environment variables as the only AAP configuration, and do not repeat the same settings as `-D` properties. When the project already configures the agent only with `-D` properties, `-Ddd.appsec.enabled=true` and `-Ddd.appsec.agentic_onboarding=true` are the equivalents.

On Windows PowerShell, set `$Env:DD_APPSEC_ENABLED = "true"` and the other variables before the `java` line, because PowerShell splits an unquoted `-Ddd.appsec.enabled=true` at the dot and the JVM exits with `Could not find or load main class`. Do not translate bash `\` continuations into backticks; build an argument array instead.

## In a container image

Add the agent in the runtime stage, readable by every user, and load it in the start command:

```dockerfile
ADD --chmod=0644 https://dtdg.co/latest-java-tracer /dd-java-agent.jar
CMD ["java", "-javaagent:/dd-java-agent.jar", "-jar", "your-app.jar"]
```

`ADD` from a URL creates the file with mode 600, owned by root, so a JVM that runs as a non-root user (`USER app`, a distroless `:nonroot` or a Chainguard image) cannot open it and exits at start. `--chmod` needs BuildKit, which is the default builder since Docker 23. With the legacy builder, add `RUN chmod 0644 /dd-java-agent.jar` after the `ADD` instead, which needs a shell in the image.

Keep the image's existing `CMD` or `ENTRYPOINT` arguments, and add only `-javaagent`.

## With APM already on

Keep the existing agent, its version and its start flags. Add only the AAP variables.
