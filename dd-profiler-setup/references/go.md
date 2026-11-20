# Go profiler enablement

Use the shared environment variables guidance in `SKILL.md` Step 4: set `DD_SERVICE`, `DD_ENV`, `DD_VERSION`.

1. `go get github.com/DataDog/dd-trace-go/v2/profiler`.
2. Start the profiler once, early in `main`:

   ```go
   err := profiler.Start(
       profiler.WithService("<SERVICE_NAME>"),
       profiler.WithEnv("<ENV>"),
       profiler.WithVersion("<VERSION>"),
       profiler.WithProfileTypes(
           profiler.CPUProfile,
           profiler.HeapProfile,
       ),
   )
   if err != nil {
       log.Fatal(err)
   }
   defer profiler.Stop()
   ```

   Only CPU and heap profiles are on by default; block, mutex, and goroutine profiles are opt-in via `WithProfileTypes`.
3. If the repo uses `dd-trace-go` v1, use the v1 import path already in the project and mention the v2 migration guide rather than migrating silently.
4. If the app is built with Orchestrion, it already adds profiler code; only set `DD_PROFILING_ENABLED=true` at runtime.
