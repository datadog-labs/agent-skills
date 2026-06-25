# Upgrading

Use this when the user wants to upgrade an existing Datadog Apps project or pull in behavior available from the latest app scaffolder.

## Primary Datadog Packages

Focus on these Datadog packages first:

- `@datadog/vite-plugin`: Datadog Vite integration used for local development, build, and upload behavior.
- `@datadog/action-catalog`: typed Action Catalog client used by backend functions.

Preserve the app's existing package manager and lockfile style.

## Before Upgrading

Read release notes or package metadata before changing versions:

```bash
npm view @datadog/vite-plugin version repository.url homepage bugs.url
npm view @datadog/action-catalog version repository.url homepage bugs.url
```

Then check the package's release notes, changelog, GitHub releases, or npm package page for breaking changes, new features, migration notes, and peer dependency changes. If release notes are not available, inspect the version history and package metadata before choosing a target version.

Inspect the current app:

```bash
npm outdated @datadog/vite-plugin @datadog/action-catalog
npm ls @datadog/vite-plugin @datadog/action-catalog
```

If the app does not use npm, run the equivalent package-manager commands.

## Upgrade

For npm projects:

```bash
npm install @datadog/action-catalog@latest
npm install -D @datadog/vite-plugin@latest
```

After upgrading, check and preserve:

- `vite.config.ts` Datadog plugin configuration, especially `auth.site`.
- `package.json` scripts such as `dev`, `build`, and `upload`.
- Existing backend function imports from `@datadog/action-catalog/...`.
- Project-local instructions in `AGENTS.md`.

## Compare Against A Fresh Scaffold

If the user asks for a feature that may come from the latest scaffolder, create a temporary baseline app and compare it with the existing project instead of guessing.

Create a baseline outside the app repo:

```bash
tmp_dir="$(mktemp -d)"
npm create @datadog/apps@latest -- "$tmp_dir/base-app" --template vite-react -y --skip-post-scaffold
```

Compare relevant files:

```bash
diff -ru "$tmp_dir/base-app/package.json" package.json
diff -ru "$tmp_dir/base-app/vite.config.ts" vite.config.ts
diff -ru "$tmp_dir/base-app/AGENTS.md" AGENTS.md
diff -ru "$tmp_dir/base-app/src" src
```

Port only the specific scaffolder changes needed for the user's goal. Do not replace the app wholesale or discard existing app logic.

## Verification

Run the project's normal checks after upgrading:

```bash
npm run typecheck
npm run lint
npm run build
```

For backend function or upload-related changes, also test the OAuth-backed commands:

```bash
npm run dev
npm run upload
```

To verify the optional key-based path, set both `DD_API_KEY` and `DD_APP_KEY` before running the same commands.
