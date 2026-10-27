# Java profiler enablement

Use the shared environment variables guidance in `SKILL.md` Step 4: set `DD_SERVICE`, `DD_ENV`, `DD_VERSION`.

1. Ensure `dd-java-agent.jar` is on the image or host (`https://dtdg.co/latest-java-tracer`). If already present, reuse it.
2. Add to the JVM launch (the `-javaagent` flag must come before `-jar`):

   ```shell
   export DD_SERVICE=<SERVICE_NAME>
   export DD_ENV=<ENV>
   export DD_VERSION=<VERSION>
   export DD_PROFILING_ENABLED=true
   java -javaagent:dd-java-agent.jar -jar <app>.jar
   ```

   Equivalent system properties: `-Ddd.service`, `-Ddd.env`, `-Ddd.version`, `-Ddd.profiling.enabled=true`.
3. GraalVM native-image is a Preview feature with a separate build step; point to https://docs.datadoghq.com/profiler/enabling/?prog_lang=java&runtime=graalvm_native_image.
