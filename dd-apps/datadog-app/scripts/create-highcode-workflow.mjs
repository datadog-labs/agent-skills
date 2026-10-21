#!/usr/bin/env node

import { spawn } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import process from 'node:process';

const DEFAULT_DOMAIN = 'app.datadoghq.com';
const STEP_NAME = 'JavaScript_Script';
const JS_FUNCTION_WITH_ACTIONS =
  'com.datadoghq.datatransformation.jsFunctionWithActions';
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
  const options = await parseArgs(process.argv.slice(2));

  if (options.help) {
    printUsage();
    return;
  }

  const config = options.configPath ? await loadConfig(options.configPath) : null;
  await resolveOptionsDomain(options, config);
  validateOptions(options);
  configureTls(options);

  const authEnv = await resolveAuthEnv(options);

  if (options.configPath) {
    await runConfigMode(options, authEnv, config);
    return;
  }

  const { app } = await loadAppDefinition(options, authEnv);
  await applyWorkflow(options, authEnv, app);
}

async function parseArgs(args) {
  const options = {
    apiBaseUrl: null,
    apiTrigger: false,
    appId: null,
    appUrl: null,
    appVersion: 'latest',
    buildCommand: ['npm', 'run', 'build'],
    configPath: null,
    description: null,
    domain: null,
    domainProvided: false,
    dryRun: false,
    functionName: null,
    handle: null,
    help: false,
    output: true,
    outputName: 'output',
    outputType: 'STRING',
    params: {},
    printSpec: false,
    projectDir: process.cwd(),
    published: true,
    scheduleRrule: null,
    tags: [],
    workflowName: null,
    workflowId: null,
    workflowTrigger: false,
    writeConfig: true,
  };

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];

    if (arg === '--') {
      options.buildCommand = args.slice(i + 1);
      break;
    }

    switch (arg) {
      case '-h':
      case '--help':
        options.help = true;
        break;
      case '--api-base-url':
        options.apiBaseUrl = readValue(args, ++i, arg);
        break;
      case '--api-trigger':
        options.apiTrigger = true;
        break;
      case '--app-id':
        options.appId = readValue(args, ++i, arg);
        break;
      case '--app-url':
        options.appUrl = readValue(args, ++i, arg);
        break;
      case '--app-version':
        options.appVersion = readValue(args, ++i, arg);
        break;
      case '--config':
        options.configPath = readValue(args, ++i, arg);
        break;
      case '--description':
        options.description = readValue(args, ++i, arg);
        break;
      case '--domain':
        options.domain = normalizeDomain(readValue(args, ++i, arg));
        options.domainProvided = true;
        break;
      case '--dry-run':
        options.dryRun = true;
        break;
      case '--function':
      case '--export':
        options.functionName = readValue(args, ++i, arg);
        break;
      case '--handle':
        options.handle = readValue(args, ++i, arg);
        break;
      case '--name':
      case '--workflow-name':
        options.workflowName = readValue(args, ++i, arg);
        break;
      case '--no-output':
        options.output = false;
        break;
      case '--output-name':
        options.outputName = readValue(args, ++i, arg);
        break;
      case '--output-type':
        options.outputType = readValue(args, ++i, arg).toUpperCase();
        break;
      case '--params-file':
        options.params = JSON.parse(
          await readFile(readValue(args, ++i, arg), 'utf8'),
        );
        break;
      case '--params-json':
        options.params = JSON.parse(readValue(args, ++i, arg));
        break;
      case '--print-spec':
        options.printSpec = true;
        break;
      case '--project-dir':
        options.projectDir = readValue(args, ++i, arg);
        break;
      case '--rrule':
      case '--schedule-rrule':
        options.scheduleRrule = readValue(args, ++i, arg).replace(/\\n/g, '\n');
        break;
      case '--tag':
        options.tags.push(readValue(args, ++i, arg));
        break;
      case '--unpublished':
        options.published = false;
        break;
      case '--workflow-trigger':
        options.workflowTrigger = true;
        break;
      case '--workflow-id':
        options.workflowId = readValue(args, ++i, arg);
        break;
      case '--no-write-config':
        options.writeConfig = false;
        break;
      default:
        throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (!options.scheduleRrule && !options.apiTrigger && !options.workflowTrigger) {
    options.workflowTrigger = true;
  }

  return options;
}

function validateOptions(options) {
  if (!options.configPath && !options.functionName) {
    throw new Error('Missing required --function <exportName>.');
  }
  if (!options.configPath && !options.workflowName) {
    throw new Error('Missing required --workflow-name <name>.');
  }
  if (options.scheduleRrule && !options.published) {
    throw new Error('Scheduled workflows must be published.');
  }
  if (options.apiTrigger && !options.published) {
    throw new Error('API-triggered workflows must be published.');
  }
  if (!options.appId && !options.appUrl && options.buildCommand.length === 0) {
    throw new Error('Build command after -- must not be empty.');
  }
  if (!isPlainObject(options.params)) {
    throw new Error('--params-json/--params-file must be a JSON object.');
  }
  if (
    ![
      'STRING',
      'NUMBER',
      'BOOLEAN',
      'OBJECT',
      'ARRAY_STRING',
      'ARRAY_NUMBER',
      'ARRAY_BOOLEAN',
      'ARRAY_OBJECT',
    ].includes(options.outputType)
  ) {
    throw new Error(
      '--output-type must be one of STRING, NUMBER, BOOLEAN, OBJECT, ARRAY_STRING, ARRAY_NUMBER, ARRAY_BOOLEAN, ARRAY_OBJECT.',
    );
  }
  if (options.output && !options.outputName) {
    throw new Error('--output-name must not be empty.');
  }
}

async function loadConfig(configPath) {
  return parseJson(await readFile(configPath, 'utf8'), configPath);
}

async function resolveOptionsDomain(options, config) {
  if (options.domainProvided) {
    options.domain = normalizeDomain(options.domain);
    return;
  }

  if (
    isPlainObject(config) &&
    typeof config.domain === 'string' &&
    config.domain.trim()
  ) {
    options.domain = normalizeDomain(config.domain);
    return;
  }

  const viteDomain = await detectDomainFromViteConfig(options.projectDir);
  options.domain = normalizeDomain(viteDomain || DEFAULT_DOMAIN);

  if (viteDomain) {
    console.log(`Using Datadog domain from Vite config: ${options.domain}`);
  }
}

async function detectDomainFromViteConfig(projectDir) {
  const root = resolve(projectDir);

  for (const name of VITE_CONFIG_NAMES) {
    const path = join(root, name);
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

async function runConfigMode(options, authEnv, config) {
  if (!isPlainObject(config)) {
    throw new Error('--config must point to a JSON object.');
  }
  if (!Array.isArray(config.workflows) || config.workflows.length === 0) {
    throw new Error('Config must contain a non-empty workflows array.');
  }

  const configOptions = mergeConfigBaseOptions(options, config);
  validateOptions({ ...configOptions, configPath: null, functionName: 'x', workflowName: 'x' });
  const { app, appId } = await loadAppDefinition(configOptions, authEnv);
  let changed = false;

  for (const workflowConfig of config.workflows) {
    const workflowOptions = workflowOptionsFromConfig(
      configOptions,
      config,
      workflowConfig,
    );
    validateOptions({ ...workflowOptions, configPath: null });
    const result = await applyWorkflow(workflowOptions, authEnv, app);
    const workflowId = result?.workflowId;

    if (!workflowConfig.workflowId && workflowId && !options.dryRun) {
      workflowConfig.workflowId = workflowId;
      changed = true;
    }
  }

  if (config.lastAppBuilderAppId !== appId && appId && !options.dryRun) {
    config.lastAppBuilderAppId = appId;
    changed = true;
  }

  if (changed && options.writeConfig) {
    await writeJsonFile(options.configPath, config);
    console.log(`\nUpdated ${options.configPath} with created workflow IDs.`);
  } else if (changed) {
    console.log('\nWorkflow IDs were created but --no-write-config prevented writing them.');
  }
}

function mergeConfigBaseOptions(options, config) {
  return {
    ...options,
    apiBaseUrl: config.apiBaseUrl || options.apiBaseUrl,
    appId: config.appId || options.appId,
    appUrl: config.appUrl || options.appUrl,
    appVersion: config.appVersion || options.appVersion,
    domain: normalizeDomain(options.domain),
    domainProvided: options.domainProvided,
    published: config.published ?? options.published,
    tags: normalizeTags(config.tags, options.tags),
  };
}

function workflowOptionsFromConfig(baseOptions, config, workflowConfig) {
  if (!isPlainObject(workflowConfig)) {
    throw new Error('Each workflow config entry must be an object.');
  }

  const functionName =
    workflowConfig.function ||
    workflowConfig.functionName ||
    workflowConfig.export ||
    workflowConfig.exportName;
  const workflowName = workflowConfig.workflowName || workflowConfig.name;

  const scheduleRrule =
    workflowConfig.scheduleRrule ||
    workflowConfig.rrule ||
    config.scheduleRrule ||
    config.rrule ||
    null;
  const apiTrigger = workflowConfig.apiTrigger ?? config.apiTrigger ?? false;
  const workflowTrigger =
    workflowConfig.workflowTrigger ??
    config.workflowTrigger ??
    (!scheduleRrule && !apiTrigger);

  return {
    ...baseOptions,
    apiTrigger,
    description: workflowConfig.description ?? config.description ?? null,
    functionName,
    handle: workflowConfig.handle ?? null,
    output: workflowConfig.output ?? config.output ?? baseOptions.output,
    outputName:
      workflowConfig.outputName || config.outputName || baseOptions.outputName,
    outputType: (
      workflowConfig.outputType ||
      config.outputType ||
      baseOptions.outputType
    ).toUpperCase(),
    params: workflowConfig.params ?? config.params ?? {},
    published:
      workflowConfig.published ?? config.published ?? baseOptions.published,
    scheduleRrule,
    tags: normalizeTags(config.tags, workflowConfig.tags || []),
    workflowId: workflowConfig.workflowId || null,
    workflowName,
    workflowTrigger,
  };
}

async function loadAppDefinition(options, authEnv) {
  const appId = await resolveAppId(options, authEnv);
  const app = await datadogApi(options, authEnv, {
    method: 'GET',
    path: `/api/v2/app-builder/apps/${encodeURIComponent(appId)}${
      options.appVersion
        ? `?version=${encodeURIComponent(options.appVersion)}`
        : ''
    }`,
  });

  return { app, appId };
}

async function applyWorkflow(options, authEnv, app) {
  const query = findBackendFunctionQuery(app, options.functionName);
  const allowedConnectionIds = getAllowedConnectionIds(query);
  const spec = buildWorkflowSpec(query, allowedConnectionIds, app, options);
  const requestBody = buildWorkflowRequestBody(options, spec);
  const isUpdate = Boolean(options.workflowId);
  const action = isUpdate ? 'update' : 'create';

  if (options.printSpec || options.dryRun) {
    console.log(`\nWorkflow ${action} request for ${options.functionName}:`);
    console.log(JSON.stringify(requestBody, null, 2));
  }

  if (options.dryRun) {
    console.log(`\nDry run enabled; workflow was not ${isUpdate ? 'updated' : 'created'}.`);
    return { workflowId: options.workflowId || null };
  }

  const response = await datadogApi(options, authEnv, {
    method: isUpdate ? 'PATCH' : 'POST',
    path: isUpdate
      ? `/api/v2/workflows/${encodeURIComponent(options.workflowId)}`
      : '/api/v2/workflows',
    body: requestBody,
  });

  const workflowId = response?.data?.id || options.workflowId;
  console.log(`\nWorkflow ${isUpdate ? 'updated' : 'created'} for ${options.functionName}.`);
  if (workflowId) {
    console.log(`Workflow ID: ${workflowId}`);
    console.log(`Workflow URL: ${workflowUrl(options.domain, workflowId)}`);
  } else {
    console.log(JSON.stringify(response, null, 2));
  }

  return { workflowId, response };
}

function buildWorkflowRequestBody(options, spec) {
  return {
    data: pruneUndefined({
      id: options.workflowId || undefined,
      type: 'workflows',
      attributes: pruneUndefined({
        name: options.workflowName,
        description:
          options.description ||
          `Runs the ${options.functionName} high-code-app backend function.`,
        published: options.published,
        spec,
        tags: options.tags.length > 0 ? options.tags : undefined,
      }),
    }),
  };
}

function configureTls(options) {
  if (siteFromDomain(options.domain) === 'datad0g.com' && !process.env.NODE_TLS_REJECT_UNAUTHORIZED) {
    process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';
  }
}

function alignedSiteEnv(domain) {
  const site = siteFromDomain(domain);
  return {
    DD_SITE: site,
    DATADOG_SITE: site,
  };
}

function withAlignedSiteEnv(env, domain) {
  const site = siteFromDomain(domain);
  const uiDomain = uiDomainFromDomain(domain);
  const existingSite = normalizeEnvDomain(env.DD_SITE || env.DATADOG_SITE || '');

  if (existingSite && existingSite !== site && existingSite !== uiDomain) {
    throw new Error(
      [
        'Datadog domain mismatch.',
        `The selected workflow domain is ${uiDomain}, which maps to Vite auth.site ${site},`,
        `but the current environment is using ${existingSite}.`,
        '',
        `Set DD_SITE/DATADOG_SITE to ${site}, or pass --domain to match the current environment.`,
      ].join('\n'),
    );
  }

  return {
    ...env,
    ...alignedSiteEnv(domain),
  };
}

function normalizeEnvDomain(value) {
  return String(value || '')
    .trim()
    .replace(/^https?:\/\//, '')
    .replace(/^api\./, '')
    .replace(/\/.*$/, '');
}

async function resolveAuthEnv(options) {
  if (hasDatadogKeys(process.env)) {
    return withAlignedSiteEnv(process.env, options.domain);
  }

  throw new Error(
    [
      'DD_API_KEY and DD_APP_KEY are not set.',
      'This script calls the Datadog API directly, which needs key-based auth',
      'even when the app itself uses OAuth for local development and upload.',
      'Export both keys before running.',
    ].join('\n'),
  );
}

async function resolveAppId(options, authEnv) {
  if (options.appId) {
    return validateUuid(options.appId, '--app-id');
  }

  if (options.appUrl) {
    return parseAppId(options.appUrl);
  }

  const buildEnv = {
    ...authEnv,
    ...alignedSiteEnv(options.domain),
    DD_APPS_UPLOAD_ASSETS: '1',
    DATADOG_APPS_UPLOAD_ASSETS: '1',
  };

  const command = options.buildCommand;

  console.log(
    `Running upload build: DD_APPS_UPLOAD_ASSETS=1 DATADOG_APPS_UPLOAD_ASSETS=1 ${command.join(' ')}`,
  );

  const { stdout, stderr } = await runCommand(command[0], command.slice(1), {
    capture: true,
    env: buildEnv,
    stream: true,
  });

  return parseAppId(`${stdout}\n${stderr}`);
}

function findBackendFunctionQuery(app, functionName) {
  const queries = getAppQueries(app);
  const matches = queries.filter(
    (query) =>
      query?.type === 'action' &&
      getExportName(query.name) === functionName &&
      getActionFqn(query) === JS_FUNCTION_WITH_ACTIONS,
  );

  if (matches.length === 1) {
    return matches[0];
  }

  if (matches.length > 1) {
    throw new Error(
      `Found multiple backend function action queries for ${functionName}: ${matches
        .map((query) => `${query.name} (${query.id})`)
        .join(', ')}`,
    );
  }

  const available = queries
    .filter((query) => query?.type === 'action')
    .map((query) => `${query.name || '<unnamed>'} [${getActionFqn(query) || 'unknown fqn'}]`)
    .sort();

  throw new Error(
    `No ${JS_FUNCTION_WITH_ACTIONS} action query found for export ${functionName}.\n` +
      `Available action queries:\n${available.map((name) => `  - ${name}`).join('\n')}`,
  );
}

function buildWorkflowSpec(query, allowedConnectionIds, app, options) {
  const actionSpec = getActionSpec(query);
  const inputs = getActionInputs(query);
  const script = inputs.script;

  if (typeof script !== 'string' || script.length === 0) {
    throw new Error(`Query ${query.name} does not contain a script input.`);
  }

  const workflowScript = rewriteBackendFunctionScript(script);
  const triggers = buildTriggers(options);
  const connectionEnvs = buildConnectionEnvs(allowedConnectionIds, app);

  return pruneUndefined({
    handle: options.handle || undefined,
    connectionEnvs: connectionEnvs.length > 0 ? connectionEnvs : undefined,
    inputSchema: {
      parameters: [
        {
          name: 'params',
          type: 'OBJECT',
          defaultValue: options.params,
        },
      ],
    },
    outputSchema: options.output
      ? {
          parameters: [
            {
              name: options.outputName,
              type: options.outputType,
              value: `{{ Steps.${STEP_NAME}.data }}`,
            },
          ],
        }
      : undefined,
    steps: [
      {
        actionId: actionSpec.fqn,
        display: {
          bounds: {
            y: 192,
          },
        },
        name: STEP_NAME,
        parameters: [
          {
            name: 'script',
            value: workflowScript,
          },
          ...(allowedConnectionIds.length > 0
            ? [
                {
                  name: 'allowedConnectionIds',
                  value: allowedConnectionIds,
                },
              ]
            : []),
        ],
      },
    ],
    triggers,
  });
}

function buildTriggers(options) {
  const triggers = [];

  if (options.scheduleRrule) {
    triggers.push({
      scheduleTrigger: {
        rruleExpression: options.scheduleRrule,
      },
      startStepNames: [STEP_NAME],
    });
  }

  if (options.apiTrigger) {
    triggers.push({
      apiTrigger: {},
      startStepNames: [STEP_NAME],
    });
  }

  if (options.workflowTrigger) {
    triggers.push({
      workflowTrigger: {},
      startStepNames: [STEP_NAME],
    });
  }

  return triggers;
}

function buildConnectionEnvs(allowedConnectionIds, app) {
  if (allowedConnectionIds.length === 0) {
    return [];
  }

  const connectionsById = getAppConnections(app);
  return [
    {
      env: 'default',
      connections: allowedConnectionIds.map((connectionId, index) => {
        const appConnection = connectionsById.get(connectionId);
        return {
          connectionId,
          label: toConnectionLabel(
            appConnection?.label || appConnection?.name || `connection_${index + 1}`,
            index,
          ),
        };
      }),
    },
  ];
}

function rewriteBackendFunctionScript(script) {
  const replacement =
    'const trigger = $.Trigger || {};\n    const args = [trigger.params ?? {}];';
  const patterns = [
    /\bconst\s+args\s*=\s*\$\.backendFunctionArgs\s*\?\?\s*\[\]\s*;/,
    /\blet\s+args\s*=\s*\$\.backendFunctionArgs\s*\?\?\s*\[\]\s*;/,
    /\bvar\s+args\s*=\s*\$\.backendFunctionArgs\s*\?\?\s*\[\]\s*;/,
  ];

  for (const pattern of patterns) {
    if (pattern.test(script)) {
      return script.replace(pattern, replacement);
    }
  }

  throw new Error(
    'Could not rewrite backend function args. Expected generated script to contain `const args = $.backendFunctionArgs ?? [];`.',
  );
}

async function datadogApi(options, authEnv, request) {
  const apiBaseUrl =
    options.apiBaseUrl || `https://api.${siteFromDomain(options.domain)}`;
  const url = new URL(request.path, apiBaseUrl);
  const response = await fetch(url, {
    method: request.method,
    headers: pruneUndefined({
      Accept: 'application/json',
      'Content-Type': request.body ? 'application/json' : undefined,
      'DD-API-KEY': authEnv.DD_API_KEY || authEnv.DATADOG_API_KEY,
      'DD-APPLICATION-KEY': authEnv.DD_APP_KEY || authEnv.DATADOG_APP_KEY,
    }),
    body: request.body ? JSON.stringify(request.body) : undefined,
  });

  const text = await response.text();
  const body = text ? parseJson(text, `response from ${url}`) : null;

  if (!response.ok) {
    throw new Error(
      `${request.method} ${url} failed with ${response.status}: ${
        text || response.statusText
      }`,
    );
  }

  return body;
}

function getAppQueries(app) {
  const queries =
    app?.data?.attributes?.queries || app?.attributes?.queries || app?.queries;

  if (!Array.isArray(queries)) {
    throw new Error('App definition response does not contain attributes.queries.');
  }

  return queries;
}

function getAppConnections(app) {
  const result = new Map();
  const candidates = [];
  collectArraysByKey(app, 'connections', candidates);

  for (const array of candidates) {
    for (const connection of array) {
      const id =
        connection?.id ||
        connection?.connectionId ||
        connection?.data?.id ||
        connection?.attributes?.id;
      if (typeof id === 'string' && id.length > 0) {
        result.set(id, connection);
      }
    }
  }

  return result;
}

function collectArraysByKey(value, key, result, seen = new Set()) {
  if (!value || typeof value !== 'object' || seen.has(value)) {
    return;
  }

  seen.add(value);

  if (Array.isArray(value)) {
    for (const item of value) {
      collectArraysByKey(item, key, result, seen);
    }
    return;
  }

  for (const [objectKey, objectValue] of Object.entries(value)) {
    if (objectKey === key && Array.isArray(objectValue)) {
      result.push(objectValue);
    }
    collectArraysByKey(objectValue, key, result, seen);
  }
}

function getAllowedConnectionIds(query) {
  const inputs = getActionInputs(query);
  const ids = [];

  if (Array.isArray(inputs.allowedConnectionIds)) {
    ids.push(...inputs.allowedConnectionIds);
  }

  const connectionId = getActionSpec(query).connectionId;
  if (typeof connectionId === 'string' && connectionId.length > 0) {
    ids.push(connectionId);
  }

  return [...new Set(ids.filter((id) => typeof id === 'string' && id.length > 0))];
}

function getActionInputs(query) {
  const inputs = getActionSpec(query).inputs;

  if (!isPlainObject(inputs)) {
    throw new Error(`Query ${query.name} has non-object action inputs.`);
  }

  return inputs;
}

function getActionFqn(query) {
  const spec = query?.properties?.spec;
  return isPlainObject(spec) ? spec.fqn : undefined;
}

function getActionSpec(query) {
  const spec = query?.properties?.spec;

  if (!isPlainObject(spec)) {
    throw new Error(`Query ${query?.name || '<unnamed>'} has non-object action spec.`);
  }

  return spec;
}

function getExportName(queryName) {
  if (typeof queryName !== 'string') {
    return '';
  }
  const parts = queryName.split('.');
  return parts[parts.length - 1];
}

async function runCommand(command, args, options) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      env: options.env,
      stdio: ['inherit', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';

    child.stdout.on('data', (chunk) => {
      const text = chunk.toString();
      stdout += text;
      if (options.stream) {
        process.stdout.write(text);
      }
    });

    child.stderr.on('data', (chunk) => {
      const text = chunk.toString();
      stderr += text;
      if (options.stream) {
        process.stderr.write(text);
      }
    });

    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) {
        resolve({ stdout, stderr });
        return;
      }

      reject(
        new Error(
          `${command} ${args.join(' ')} exited with ${code}.\n${stderr || stdout}`,
        ),
      );
    });
  });
}

function hasDatadogKeys(value) {
  return Boolean(
    (value.DD_API_KEY || value.DATADOG_API_KEY) &&
      (value.DD_APP_KEY || value.DATADOG_APP_KEY),
  );
}

function parseAppId(text) {
  const urlMatch = text.match(
    /app-builder\/apps\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/,
  );
  if (urlMatch) {
    return urlMatch[1];
  }

  const jsonMatch = text.match(
    /app_builder_id["'\s:=]+([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/,
  );
  if (jsonMatch) {
    return jsonMatch[1];
  }

  throw new Error(
    'Could not find an App Builder app ID in build output. Expected a URL like https://<domain>/app-builder/apps/<uuid>.',
  );
}

function validateUuid(value, label) {
  if (
    !/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(
      value,
    )
  ) {
    throw new Error(`${label} must be a UUID.`);
  }
  return value;
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

function workflowUrl(domain, workflowId) {
  return `https://${uiDomainFromDomain(domain)}/workflow/${workflowId}`;
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

function toConnectionLabel(value, index) {
  const label = String(value)
    .trim()
    .replace(/[^A-Za-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '');

  return label || `connection_${index + 1}`;
}

function normalizeTags(...tagLists) {
  const tags = [];
  for (const tagList of tagLists) {
    if (!tagList) {
      continue;
    }
    if (typeof tagList === 'string') {
      tags.push(tagList);
      continue;
    }
    if (Array.isArray(tagList)) {
      tags.push(...tagList.filter((tag) => typeof tag === 'string'));
      continue;
    }
    throw new Error('tags must be a string array.');
  }
  return [...new Set(tags)];
}

async function writeJsonFile(path, value) {
  await writeFile(`${path}.tmp`, `${JSON.stringify(value, null, 2)}\n`);
  await renameFile(`${path}.tmp`, path);
}

async function renameFile(from, to) {
  const { rename } = await import('node:fs/promises');
  await rename(from, to);
}

function parseJson(value, label) {
  try {
    return JSON.parse(value);
  } catch (error) {
    throw new Error(`Could not parse ${label} as JSON: ${error.message}`);
  }
}

function pruneUndefined(value) {
  if (Array.isArray(value)) {
    return value.map(pruneUndefined);
  }

  if (!value || typeof value !== 'object') {
    return value;
  }

  return Object.fromEntries(
    Object.entries(value)
      .filter(([, entryValue]) => entryValue !== undefined)
      .map(([entryKey, entryValue]) => [entryKey, pruneUndefined(entryValue)]),
  );
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
  console.log(`Create a Datadog workflow from a High Code App backend function.

Usage:
  node scripts/create-highcode-workflow.mjs \\
    --function refreshDatastore \\
    --workflow-name "Refresh Jira datastore" \\
    --schedule-rrule $'DTSTART;TZID=America/New_York:20260520T090000\\nFREQ=HOURLY;INTERVAL=1' \\
    --params-file params.json

By default this runs:
  DD_APPS_UPLOAD_ASSETS=1 DATADOG_APPS_UPLOAD_ASSETS=1 npm run build

Requires DD_API_KEY and DD_APP_KEY in the environment.

Options:
  --function <name>          Required backend function export name.
  --workflow-name <name>     Required workflow name.
  --domain <domain>          Datadog domain. Defaults to Vite config or ${DEFAULT_DOMAIN}.
  --schedule-rrule <rrule>   Add a schedule trigger.
  --api-trigger              Add an API trigger.
  --workflow-trigger         Add a UI/manual workflow trigger.
  --params-json <json>       Default Trigger.params object. Defaults to {}.
  --params-file <path>       Read default Trigger.params object from JSON file.
  --description <text>       Workflow description.
  --tag <tag>                Workflow tag. Can be repeated.
  --handle <handle>          Workflow handle.
  --app-id <uuid>            Skip build and fetch this app definition.
  --app-url <url>            Skip build and parse the app ID from this URL.
  --app-version <version>    App version for GET app. Defaults to latest.
  --api-base-url <url>       Override API base URL.
  --config <path>            Create/update all workflows from a JSON manifest.
  --project-dir <path>       App project directory for Vite domain detection.
  --workflow-id <uuid>       Update this existing workflow instead of creating.
  --no-write-config          Do not persist created workflow IDs to the manifest.
  --dry-run                  Print the create request without creating workflow.
  --print-spec               Print the create request before creating workflow.
  --no-output                Do not create a workflow output.
  --output-name <name>       Workflow output name. Defaults to output.
  --output-type <type>       Workflow output type. Defaults to STRING.
  --                         Override build command, for example: -- npx vite build
`);
}
