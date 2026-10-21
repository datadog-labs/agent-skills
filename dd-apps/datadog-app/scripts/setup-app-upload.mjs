#!/usr/bin/env node

import { chmod, cp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import process from 'node:process';

const DEFAULT_DOMAIN = 'app.datadoghq.com';
const DEFAULT_SCRIPT_NAME = 'upload';
const DEFAULT_MANIFEST_PATH = '.datadog/highcode-workflows.json';
const DEFAULT_BASH_PATH = '.datadog/create-highcode-workflows.sh';
const DEFAULT_CREATOR_PATH = '.datadog/create-highcode-workflow.mjs';
const VITE_CONFIG_NAMES = [
  'vite.config.ts',
  'vite.config.mts',
  'vite.config.cts',
  'vite.config.js',
  'vite.config.mjs',
  'vite.config.cjs',
];

main().catch((error) => {
  console.error(`\nError: ${error.message}`);
  process.exitCode = 1;
});

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    printUsage();
    return;
  }

  const projectDir = resolve(options.projectDir);
  const packageJsonPath = join(projectDir, 'package.json');
  const packageJson = parseJson(await readFile(packageJsonPath, 'utf8'), packageJsonPath);

  if (!isPlainObject(packageJson)) {
    throw new Error(`${packageJsonPath} must contain a JSON object.`);
  }

  await resolveOptionsDomain(options, projectDir);
  const viteLogLevelUpdated = await ensureViteDatadogLogLevel(projectDir);
  await mkdir(join(projectDir, '.datadog'), { recursive: true });
  await installCreatorScript(projectDir);
  await writeUploadBashScript(projectDir);
  await upsertManifest(projectDir, options);
  await upsertPackageScript(packageJsonPath, packageJson, options.scriptName);

  console.log(`Configured npm script "${options.scriptName}".`);
  if (viteLogLevelUpdated) {
    console.log('Updated Vite config to set datadogVitePlugin logLevel: "info".');
  }
  console.log(`Manifest: ${join(projectDir, DEFAULT_MANIFEST_PATH)}`);
  console.log(`Upload wrapper: ${join(projectDir, DEFAULT_BASH_PATH)}`);
}

function parseArgs(args) {
  const options = {
    apiTrigger: false,
    domain: null,
    domainFromVite: false,
    domainProvided: false,
    functions: [],
    help: false,
    projectDir: process.cwd(),
    scheduleRrule: null,
    scriptName: DEFAULT_SCRIPT_NAME,
    workflowTrigger: false,
  };

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    switch (arg) {
      case '-h':
      case '--help':
        options.help = true;
        break;
      case '--api-trigger':
        options.apiTrigger = true;
        break;
      case '--domain':
        options.domain = normalizeDomain(readValue(args, ++i, arg));
        options.domainProvided = true;
        break;
      case '--function':
      case '--export':
        options.functions.push({
          function: readValue(args, ++i, arg),
        });
        break;
      case '--project-dir':
        options.projectDir = readValue(args, ++i, arg);
        break;
      case '--schedule-rrule':
      case '--rrule':
        options.scheduleRrule = readValue(args, ++i, arg).replace(/\\n/g, '\n');
        break;
      case '--script-name':
        options.scriptName = readValue(args, ++i, arg);
        break;
      case '--workflow':
        options.functions.push(parseWorkflowArg(readValue(args, ++i, arg)));
        break;
      case '--workflow-trigger':
        options.workflowTrigger = true;
        break;
      default:
        throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return options;
}

function parseWorkflowArg(value) {
  const [functionName, ...nameParts] = value.split(':');
  const workflowName = nameParts.join(':');
  if (!functionName) {
    throw new Error('--workflow must be formatted as exportName or exportName:Workflow Name.');
  }
  return {
    function: functionName,
    workflowName: workflowName || undefined,
  };
}

async function resolveOptionsDomain(options, projectDir) {
  if (options.domainProvided) {
    options.domain = normalizeDomain(options.domain);
    return;
  }

  const viteDomain = await detectDomainFromViteConfig(projectDir);
  options.domain = normalizeDomain(viteDomain || DEFAULT_DOMAIN);
  options.domainFromVite = Boolean(viteDomain);

  if (viteDomain) {
    console.log(`Using Datadog domain from Vite config: ${options.domain}`);
  }
}

async function detectDomainFromViteConfig(projectDir) {
  for (const name of VITE_CONFIG_NAMES) {
    const path = join(projectDir, name);
    let content;

    try {
      content = await readFile(path, 'utf8');
    } catch (error) {
      if (error.code === 'ENOENT') {
        continue;
      }
      throw error;
    }

    const domain = extractDatadogDomain(content);
    if (domain) {
      return domain;
    }
  }

  return null;
}

function extractDatadogDomain(content) {
  const candidates = [];
  const patterns = [
    /\b(?:site|domain)\s*:\s*['"`]([^'"`]+)['"`]/g,
    /\b(?:site|domain)\s*:\s*[^,\n{}]*?(?:\?\?|\|\|)\s*['"`]([^'"`]+)['"`]/g,
  ];

  for (const pattern of patterns) {
    for (const match of content.matchAll(pattern)) {
      candidates.push(match[1]);
    }
  }

  return candidates.find(looksLikeDatadogDomain) || null;
}

function looksLikeDatadogDomain(value) {
  const normalized = normalizeDomain(value);
  return (
    normalized.includes('datadoghq.') ||
    normalized.includes('datad0g.') ||
    normalized.includes('ddstaging.') ||
    normalized.includes('ddsecurity.') ||
    normalized.includes('frog.')
  );
}

async function installCreatorScript(projectDir) {
  const skillDir = dirname(fileURLToPath(import.meta.url));
  const source = join(skillDir, 'create-highcode-workflow.mjs');
  const destination = join(projectDir, DEFAULT_CREATOR_PATH);
  await cp(source, destination);
  await chmod(destination, 0o755);
}

async function ensureViteDatadogLogLevel(projectDir) {
  for (const name of VITE_CONFIG_NAMES) {
    const path = join(projectDir, name);
    let content;

    try {
      content = await readFile(path, 'utf8');
    } catch (error) {
      if (error.code === 'ENOENT') {
        continue;
      }
      throw error;
    }

    const callIndex = content.indexOf('datadogVitePlugin');
    if (callIndex === -1) {
      continue;
    }

    if (/\blogLevel\s*:/.test(content.slice(callIndex, callIndex + 2000))) {
      return false;
    }

    const updated = content.replace(
      /datadogVitePlugin\(\s*{/,
      (match) => `${match}\n                logLevel: 'info',`,
    );

    if (updated === content) {
      return false;
    }

    await writeFile(path, updated);
    return true;
  }

  return false;
}

async function writeUploadBashScript(projectDir) {
  const destination = join(projectDir, DEFAULT_BASH_PATH);
  const content = `#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "\${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -z "\${DD_API_KEY:-\${DATADOG_API_KEY:-}}" || -z "\${DD_APP_KEY:-\${DATADOG_APP_KEY:-}}" ]]; then
  cat >&2 <<'EOF'
Missing Datadog API credentials.
Set DD_API_KEY and DD_APP_KEY in the environment before running npm run upload.
EOF
  exit 1
fi

CONFIG_PATH="\${HIGHCODE_WORKFLOWS_CONFIG:-${DEFAULT_MANIFEST_PATH}}"
CREATOR_SCRIPT="\${CREATE_HIGHCODE_WORKFLOW_SCRIPT:-${DEFAULT_CREATOR_PATH}}"
BUILD_OUTPUT="$(mktemp)"
trap 'rm -f "$BUILD_OUTPUT"' EXIT

read -r WORKFLOW_DOMAIN WORKFLOW_SITE WORKFLOW_UI_DOMAIN < <(node - "$CONFIG_PATH" <<'NODE'
const fs = require('fs');

const DEFAULT_DOMAIN = 'app.datadoghq.com';
const configPath = process.argv[2];

function normalizeDomain(value) {
  const domain = String(value || DEFAULT_DOMAIN)
    .trim()
    .replace(/^https?:\\/\\//, '')
    .replace(/^api\\./, '')
    .replace(/\\/.*$/, '');

  return domain === 'datadoghq.com' ? DEFAULT_DOMAIN : domain;
}

function siteFromDomain(domain) {
  const normalized = normalizeDomain(domain);
  if (normalized === 'dd.datad0g.com') {
    return 'datad0g.com';
  }
  return normalized.startsWith('app.') ? normalized.slice(4) : normalized;
}

function uiDomainFromDomain(domain) {
  const normalized = normalizeDomain(domain);
  if (normalized === 'datad0g.com') {
    return 'dd.datad0g.com';
  }
  return normalized;
}

let config = {};
try {
  config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
} catch (error) {
  // Let the workflow creator produce the final config parsing error later.
}

const domain = normalizeDomain(config.domain || DEFAULT_DOMAIN);
console.log([domain, siteFromDomain(domain), uiDomainFromDomain(domain)].join(' '));
NODE
)

EXISTING_SITE="\${DD_SITE:-\${DATADOG_SITE:-}}"
EXISTING_SITE="\${EXISTING_SITE#http://}"
EXISTING_SITE="\${EXISTING_SITE#https://}"
EXISTING_SITE="\${EXISTING_SITE#api.}"
EXISTING_SITE="\${EXISTING_SITE%%/*}"

if [[ -n "$EXISTING_SITE" && "$EXISTING_SITE" != "$WORKFLOW_SITE" && "$EXISTING_SITE" != "$WORKFLOW_UI_DOMAIN" ]]; then
  cat >&2 <<EOF
Datadog domain mismatch.
The workflow manifest uses $WORKFLOW_UI_DOMAIN, which maps to Vite auth.site $WORKFLOW_SITE,
but the current environment is using $EXISTING_SITE.

Set DD_SITE and DATADOG_SITE to $WORKFLOW_SITE, or update the manifest domain
so it matches the environment, then run npm run upload again.
EOF
  exit 1
fi

export DD_SITE="$WORKFLOW_SITE"
export DATADOG_SITE="$WORKFLOW_SITE"

DD_APPS_UPLOAD_ASSETS=1 DATADOG_APPS_UPLOAD_ASSETS=1 npm run build 2>&1 | tee "$BUILD_OUTPUT"

APP_URL="$(grep -Eo 'https://[^[:space:]]+/app-builder/apps/[0-9a-fA-F-]+' "$BUILD_OUTPUT" | tail -n 1)"
if [[ -z "$APP_URL" ]]; then
  cat >&2 <<'EOF'
Could not find App Builder URL in upload output.
Make sure datadogVitePlugin is configured with logLevel: 'info'; the upload succeeds silently at the default warn level.
EOF
  exit 1
fi

node "$CREATOR_SCRIPT" --config "$CONFIG_PATH" --app-url "$APP_URL" "$@"
`;

  await writeFile(destination, content);
  await chmod(destination, 0o755);
}

async function upsertManifest(projectDir, options) {
  const manifestPath = join(projectDir, DEFAULT_MANIFEST_PATH);
  let manifest = {
    domain: options.domain,
    workflows: [],
  };

  try {
    manifest = parseJson(await readFile(manifestPath, 'utf8'), manifestPath);
  } catch (error) {
    if (error.code !== 'ENOENT') {
      throw error;
    }
  }

  if (!isPlainObject(manifest)) {
    throw new Error(`${manifestPath} must contain a JSON object.`);
  }
  if (!Array.isArray(manifest.workflows)) {
    manifest.workflows = [];
  }
  if (!manifest.domain || (!options.domainProvided && options.domainFromVite)) {
    manifest.domain = options.domain;
  }

  for (const workflow of options.functions) {
    const existing = manifest.workflows.find(
      (entry) => entry.function === workflow.function,
    );
    const next = {
      function: workflow.function,
      workflowName:
        workflow.workflowName ||
        existing?.workflowName ||
        titleFromFunction(workflow.function),
      params: existing?.params ?? {},
      outputName: existing?.outputName || 'output',
      outputType: existing?.outputType || 'STRING',
      ...(options.scheduleRrule || existing?.scheduleRrule
        ? { scheduleRrule: options.scheduleRrule || existing.scheduleRrule }
        : {}),
      apiTrigger: existing?.apiTrigger ?? options.apiTrigger,
      workflowTrigger:
        existing?.workflowTrigger ??
        (!options.scheduleRrule && !options.apiTrigger
          ? true
          : options.workflowTrigger),
      ...(existing?.workflowId ? { workflowId: existing.workflowId } : {}),
    };

    if (existing) {
      Object.assign(existing, next);
    } else {
      manifest.workflows.push(next);
    }
  }

  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
}

async function upsertPackageScript(packageJsonPath, packageJson, scriptName) {
  packageJson.scripts ||= {};
  packageJson.scripts[scriptName] = `bash ${DEFAULT_BASH_PATH}`;
  await writeFile(packageJsonPath, `${JSON.stringify(packageJson, null, 2)}\n`);
}

function titleFromFunction(functionName) {
  return functionName
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[-_]+/g, ' ')
    .replace(/^./, (first) => first.toUpperCase());
}

function readValue(args, index, flag) {
  const value = args[index];
  if (!value || value.startsWith('--')) {
    throw new Error(`${flag} requires a value.`);
  }
  return value;
}

function normalizeDomain(value) {
  const domain = value
    .trim()
    .replace(/^https?:\/\//, '')
    .replace(/^api\./, '')
    .replace(/\/.*$/, '');

  return domain === 'datadoghq.com' ? DEFAULT_DOMAIN : domain;
}

function parseJson(value, label) {
  try {
    return JSON.parse(value);
  } catch (error) {
    throw new Error(`Could not parse ${label} as JSON: ${error.message}`);
  }
}

function isPlainObject(value) {
  return Boolean(
    value &&
      typeof value === 'object' &&
      !Array.isArray(value) &&
      Object.getPrototypeOf(value) === Object.prototype,
  );
}

function printUsage() {
  console.log(`Add deterministic high-code workflow upload files to an app project.

Usage:
  node scripts/setup-app-upload.mjs \\
    --project-dir /path/to/app \\
    --workflow refreshDatastore:"Refresh Jira datastore" \\
    --schedule-rrule $'DTSTART;TZID=America/New_York:20260520T090000\\nFREQ=HOURLY;INTERVAL=1' \\
    --api-trigger

This writes:
  - package.json script "upload"
  - ${DEFAULT_BASH_PATH}
  - ${DEFAULT_MANIFEST_PATH}
  - ${DEFAULT_CREATOR_PATH}

Options:
  --domain <domain>       Override Vite-configured domain. Defaults to ${DEFAULT_DOMAIN}.
  --script-name <name>    package.json script name. Defaults to ${DEFAULT_SCRIPT_NAME}.
`);
}
