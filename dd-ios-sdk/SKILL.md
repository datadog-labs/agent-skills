---
name: dd-ios-sdk
description: Routes Datadog iOS SDK upgrades to the migration instructions for the project's current and target major versions.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,ios-sdk,ios,swift,objective-c,spm,cocoapods,carthage,rum,logs,trace,session-replay,migration
  globs: "**/Package.swift,**/Podfile,**/Cartfile,**/*.xcodeproj/project.pbxproj"
---

# Datadog iOS SDK Upgrades

Version-specific migrations for iOS applications using the Datadog SDK.

## Skills

| Task | Skill |
|------|-------|
| Upgrade from v2 to v3 | `dd-ios-sdk/upgrade-v3` |

## Routing

**Upgrading from v2 to v3** (including removal of the `DatadogObjc` module,
required user IDs, `RUMView(path:)`, the granular Session Replay privacy levels,
or the legacy URLSession delegates):

**Immediately read** [upgrade-v3/SKILL.md](upgrade-v3/SKILL.md) and follow it;
do not perform the migration from memory or from this routing file alone.

For a source/target major-version pair not listed above, do not apply another
version's instructions. Explain that this skill does not yet contain that
migration route and consult the official migration guide
(`MIGRATION.md` in `DataDog/dd-sdk-ios`) before proposing changes.
