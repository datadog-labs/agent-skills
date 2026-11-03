---
name: dd-docs
description: Look up Datadog documentation, limits, and feature availability. Prefer search_datadog_documentation when that MCP tool is available, then one Markdown page from docs.datadoghq.com/llms.txt. For API reference, use pup docs api.
metadata:
  version: "1.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,docs,llms.txt,dd-docs
  alwaysApply: "false"
---

# Datadog Docs

Use the smallest source that answers the question, then stop. Cite the page URL you used.

1. `search_datadog_documentation` is available → Search.
2. The question is an API endpoint or request schema, and search did not answer it → API.
3. Otherwise → Page.

## Search

`search_datadog_documentation` (MCP toolset `documentation`, no extra permission) returns at most 10 source passages with links. It does not write an answer. It supports English, French, Japanese, Korean, and Spanish. With AI providers disabled for the org, it matches keywords only.

Call `read_datadog_documentation` with one source URL or document id only when a passage is not enough. It returns long pages in parts; stop at the part that answers.

If the tool is not in your tool list, skip this section. Enabling the toolset is a separate setup task.

## API

Pup reads the live API reference and needs no auth. Commands, smallest first:

```bash
pup docs api <topic> --operations
pup docs api <topic>
```

The operations list is the small result. Fetch `pup docs api <topic> <operation>` only when you need that operation's request schema: the page includes every sample. Skip `pup docs api` with no topic.

## Page

`https://docs.datadoghq.com/llms.txt` is the section index. Open the one section index it links to, then fetch one page by appending `.md`:

```
https://docs.datadoghq.com/monitors/llms.txt
https://docs.datadoghq.com/monitors.md
```

Documentation is served from `docs.datadoghq.com`. Site-specific values, such as API endpoints, are selected on that page.

## Uncited shortcut

`pup docs ask "<question>"` returns a short generated answer with no sources and needs no auth. Use it only when you will not treat the reply as documentation.
