# Getting Started

Use this when the user wants to create, scaffold, configure prerequisites, or run a Datadog Apps project locally.

## Prerequisites

- Use Node.js 20.19+ on the Node 20 release line, or Node.js 22.12+ on the Node 22 release line.
- Prefer a current Node 22 release when scaffolding or debugging dependency install issues.
- Local development and upload use OAuth by default. API and application keys are optional for local key-based auth and are still commonly used in CI/CD.

Check Node:

```bash
node --version
```

## Scaffold

Choose where to create the app project before running the scaffolder.

Create a project with the published scaffolder:

```bash
npm create @datadog/apps@latest
```

Follow the prompts, then enter the generated app directory.

## Non-Interactive Scaffolding

Non-interactive scaffolding is the preferred way to create an app through this skill. AI agents can run the scaffolder directly for the user after collecting the required details. First inspect the current CLI options:

```bash
npm create @datadog/apps@latest -- --help
```

Use the help output to collect the required details from the user, such as target directory, template, and whether to accept defaults or overwrite an existing directory. Then run the scaffolder with explicit options. For example:

```bash
npm create @datadog/apps@latest -- my-app --template vite-react -y
```

Keep the generated project path consistent with the user's chosen repository layout.

## Local Development

### OAuth Default

Run the generated dev script directly:

```bash
npm run dev
```

When the dev server needs to call Datadog, such as when running a backend function locally, the Vite plugin uses OAuth by default. If authorization is required, the command opens a browser prompt. After authorization completes, the token is cached in the operating system credential store when supported.

Open the local URL printed by the dev server, commonly `http://localhost:5173/`.

### Optional API And Application Keys

If the user wants key-based auth for local development or uploads, set both `DD_API_KEY` and `DD_APP_KEY`. When both are set, the generated app uses those keys instead of OAuth.

The application key needs **Actions API Access** for backend function execution and **Apps** for uploading and publishing. See [App Builder Access and Authentication](https://docs.datadoghq.com/actions/app_builder/access_and_auth/) for details. Find or create keys at:

- API keys: `https://app.datadoghq.com/organization-settings/api-keys`
- Application keys: `https://app.datadoghq.com/organization-settings/application-keys`

Do not ask the user to provide actual key values in the conversation. Instead, create `.env.local` with placeholders if they want file-based local key auth:

1. Confirm `.env.local` is gitignored. Check `.gitignore` for `*.local` or `.env.local` before writing. The scaffolder includes this by default.
2. Write the file to the app root with placeholders:

```
DD_API_KEY=REPLACE_WITH_YOUR_API_KEY
DD_APP_KEY=REPLACE_WITH_YOUR_APP_KEY
```

3. Open the file immediately in the user's editor:

```bash
cursor .env.local 2>/dev/null || code .env.local 2>/dev/null || open .env.local
```

4. Tell the user to replace the placeholder values with their real keys.

Vite reads `.env.local` automatically, so once real values are in place, `npm run dev` and `npm run upload` use those keys without shell exports. If either key is absent, the commands continue to use OAuth by default.

**Optional shortcut (macOS only) — clipboard → file:** If the user asks for an alternative to editing the file, offer to write each key directly from the clipboard. The value goes clipboard → file without appearing in the conversation or tool output. Explain that the command being run is still visible in the transcript. Ask the user to copy their `DD_API_KEY` value to the clipboard first, then:

```bash
grep -v "^DD_API_KEY=" .env.local > .env.local.tmp && mv .env.local.tmp .env.local
printf "DD_API_KEY=" >> .env.local && pbpaste >> .env.local && printf "\n" >> .env.local
```

Repeat for `DD_APP_KEY`. Confirm the user has the correct value on their clipboard before running each step.

## Generated Project Shape

Scaffolded projects commonly include:

- `src/App.tsx`: root React UI.
- `src/**/*.backend.ts`: server-side backend functions.
- `vite.config.ts`: Datadog Vite plugin configuration.
- `package.json`: scripts such as `dev`, `build`, and `upload`.
- `AGENTS.md`: project-local instructions for AI coding assistants. Read this after scaffolding and before making app-specific changes; it may include project conventions, scripts, and development guidance.
