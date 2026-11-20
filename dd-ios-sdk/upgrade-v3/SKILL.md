---
name: dd-ios-sdk-v3-upgrade
description: Upgrades an iOS application from Datadog's v2 mobile SDK (dd-sdk-ios 2.x) to the latest compatible stable v3 release, including module changes, Objective-C imports, user info, RUM views, Session Replay privacy levels and URLSession instrumentation.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,ios-sdk,ios,swift,objective-c,spm,cocoapods,carthage,rum,logs,trace,session-replay,migration,v3,upgrade
---

# Upgrade the Datadog iOS SDK from v2 to v3

Use this skill when an iOS project uses the Datadog iOS SDK 2.x (`dd-sdk-ios`) and must be
upgraded to a stable 3.x release. The v3 migration removes the `DatadogObjc` module, makes
the user ID mandatory, replaces the legacy URLSession delegates and splits the Session
Replay privacy level into three settings.

Do not use it when:

- the project does not use Datadog yet: this is an installation, not an upgrade.

This skill incorporates the "Migration from 2.x to 3.0" section of
[`MIGRATION.md`][migration] at tag `3.19.0`, checked against the public API surface of
`3.19.0` and `2.30.2`. Where this skill is more specific than that guide, it says so. Use the
instructions below as the source of truth; consult the live guide only when the project uses
an API not covered here or the chosen target is newer than `3.19.0` (the guide is unchanged at
`3.20.0`).

## Ground rules

- Inspect the whole project before editing. Change only what the migration requires.
- Never change the project's dependency manager (for example from vendored xcframeworks to Swift
  Package Manager, or from Carthage to Swift Package Manager). That is a decision for the project
  owner: if the upgrade needs it, stop and report it instead.
- Preserve the application's telemetry. Do not remove a Datadog product, an instrumentation
  or a test to make the project compile.
- Never invent a value. If a required value (for example a user ID) is not in the project, handle
  that item as its step says (step 5 for the user ID) and report it.
- Never change the client token, application ID, `env`, `site`, `service` or
  `trackingConsent` values the project already sets.
- Do not adopt a Datadog feature the project did not already use, and do not enable new options.
  Linking the module of code that already used a feature through `DatadogObjc` is not a new feature.
- Never change build settings, including the deployment target, unless step 2 requires it
  and says so.
- Edit real files only. If the dependency manager's command is not available, do not create
  or simulate a resolved file or lock file; report that it was not run.
- **Never write the client token anywhere in your output**: not in the report, not in a list of
  "preserved" or "unchanged" values, not partially and not masked. Write `clientToken: unchanged`
  and nothing else. This rule has been broken before, always when listing the values that were
  kept; see the check at the end of step 10.
- Report honestly what ran and what did not, including the build.

## 1. Establish the migration scope

Before editing, inspect the project for:

- the dependency manager: `XCRemoteSwiftPackageReference` for `dd-sdk-ios` in
  `*.xcodeproj/project.pbxproj`, `Package.swift`, `Podfile` and `Podfile.lock`, `Cartfile` and
  `Cartfile.resolved`, or manually linked XCFrameworks. Several may coexist;
- the current SDK version, read from the resolved file (`Package.resolved`, `Podfile.lock`,
  `Cartfile.resolved`) and not from memory;
- every `Datadog*` product or pod in use, and every `import Datadog…`, `@import Datadog…`
  and `#import <Datadog…>`; and the Alamofire extension (`pod 'DatadogAlamofireExtension'`,
  `import DatadogAlamofireExtension`, `DDRequestInterceptor`, `DDEventMonitor`);
- calls to `Datadog.initialize`, `Datadog.setUserInfo`, `RUMView(path:`,
  `SessionReplay.Configuration` and `defaultPrivacyLevel`, and any use of
  `DatadogURLSessionDelegate`, `DDURLSessionDelegate` or `DDNSURLSessionDelegate`;
- the deployment target of every target that links Datadog, and the Xcode version: run
  `xcodebuild -version`, or use the version the task states when `xcodebuild` is not available.

Stop and report, without editing, in these cases:

- the project does not use the Datadog iOS SDK;
- the project vendors the SDK as xcframeworks by hand (no Swift Package Manager, CocoaPods or
  Carthage) and any target builds for visionOS: the release zip has no visionOS slice, so this
  path cannot upgrade it (see "XCFrameworks (manual)" in the appendix).

When you stop, edit nothing, not even the source code, and say in the report what you found, why
the upgrade cannot proceed and what the project owner can decide, for example moving to Swift
Package Manager. Do not make that move yourself.

## 2. Choose the target version and check requirements

Follow the appendix section "Choose the target version" at the end of this file. In short:

- The target is the **newest stable 3.x** release whose `Package.swift` requirements (platforms and
  `swift-tools-version`) the project's deployment target and Xcode meet.
- List the releases with every method in the appendix. Use the **fallback version `3.14.0`** only
  when GitHub itself cannot be reached (connection errors or timeouts from every method); a missing
  tool such as `git: command not found` is **not** a network failure, so use the `curl` method.
  When you use the fallback, say so prominently in the report, with the reason. It is the newest 3.x verified to compile
  with Xcode 16.0, and it supports iOS 12 and later. You cannot check the requirements of any other
  version without network, so do not pick one from memory. If the project's Xcode is known to be
  older than 16, stop and report: `3.14.0` needs `swift-tools-version: 6.0`.
- Move down to an older 3.x tag **only** when you can name the requirement the newer one fails, and
  report it. If no 3.x release fits, stop and report; do not raise the deployment target.
- **If you moved down, step 3 is mandatory for SPM: write a capped range, not an open one.** Xcode
  resolves the newest version an open range allows, so `upToNextMajorVersion` from the older tag
  silently resolves back to the newest release at build time, which fails exactly the requirement
  you just named. This is the case where the project's kind must change. Example for 3.16.1 chosen
  because 3.17.0 needs iOS 15:
  ```
  requirement = {
      kind = versionRange;
      maximumVersion = 3.17.0;
      minimumVersion = 3.16.1;
  };
  ```
  Never write "no capping needed" after moving down. Only when the chosen version is the newest
  stable release do you keep the project's existing kind.
- `swift-tools-version` does not reveal every compiler requirement. If the build in step 10 fails
  with syntax errors inside Datadog package sources, treat it as a failed requirement, move down
  one tag, and report it.

## 3. Upgrade the dependencies

Use the chosen concrete version everywhere. Do not leave a mix of v2 and v3 products, and do
not use moving versions. Follow the appendix at the end of this file for the edit and the resolve
command of each manager.

The v3 products are `DatadogCore`, `DatadogCrashReporting`, `DatadogLogs`, `DatadogRUM`,
`DatadogSessionReplay`, `DatadogTrace` and `DatadogWebViewTracking`.

- Remove the `DatadogObjc` product, pod or framework. Its contents now live in the product modules.
  When the project linked `DatadogObjc`, link the module of every product the code uses (see the appendix):
  `DatadogObjc` used to bring Core, RUM, Logs and Trace along.
- Keep the other products the project already used, and make sure `DatadogCore` is present.
- SPM: change the requirement of the existing `dd-sdk-ios` reference. Keep its kind (for example
  `upToNextMajorVersion`) and set its version to the chosen one; do not switch to an exact version
  and add no second reference. **Exception:** if you moved down in step 2, use the capped
  `versionRange` described there (chosen tag up to, but excluding, the first incompatible one).
- Never edit, create or regenerate `Package.resolved` or `Podfile.lock` by hand. Xcode or
  CocoaPods rewrites them when the customer builds; a hand-edited `originHash` or revision is worse
  than a stale file. Run the resolve command only when the tool is available, and never copy its
  output into the report as if you had edited the file.
- CocoaPods: remove `pod 'DatadogAlamofireExtension'` if present, and update the Datadog pods with
  `pod update` (plain `pod install` keeps the old pinned versions). Carthage: update the requirement and link the v3 xcframeworks, as the appendix says.
- CocoaPods and Carthage are not supported from SDK 4.0. Say so in the report.

## 4. Update imports and Objective-C

Remove every `import DatadogObjc` and `@import DatadogObjc`. In Objective-C, import the
modules of the products in use (`@import DatadogCore;`, `@import DatadogRUM;`,
`@import DatadogLogs;`, and so on), and keep the `@import DatadogSessionReplay;` that already
exists. The Objective-C names (`DDDatadog`, `DDConfiguration`, `DDRUM`, `DDLogs`, …) are unchanged;
change one only if the compiler reports that it no longer exists.

## 5. Core: user info

In v3 `Datadog.setUserInfo(id:name:email:extraInfo:)` requires a non-optional `id`.

- A call that passes an `id` needs no change.
- A call without an `id`, or with `id: nil`, no longer compiles. If the code has an optional
  user identifier, call it only when present (`if let id { Datadog.setUserInfo(id: id, …) }`).
- In Objective-C, `+[DDDatadog setUserInfoWithId:name:email:extraInfo:]` (already deprecated in
  2.x) no longer exists. Use `setUserInfoWithUserId:name:email:extraInfo:` where an identifier
  exists. Passing `nil` there compiles with only a nullability warning, but it violates the
  contract: treat it as a missing identifier.
- If the project has no identifier at all, do not invent one. Comment the call out, keep its
  other arguments in the comment, and add a one-line `TODO` saying that an `id` is required.
  Report it as a behavior change: user information is no longer sent until the owner provides
  an `id`.

## 6. RUM

- Replace `RUMView(path: x, attributes: a)` with `RUMView(name: x, attributes: a)`.
- The new `Monitor.addViewAttribute…` / `removeViewAttribute…` APIs are optional. Do not adopt them.
- `RUM.Configuration` has new options that are **on by default** in 3.19.0: `trackMemoryWarnings`
  and `trackSlowFrames`. The upgrade therefore starts reporting memory warnings and slow frames
  without any code change. Do not set them yourself; report them as behavior changes so the owner
  can turn them off if they want the previous telemetry.
- App Hangs and Watchdog terminations are no longer reported from app extensions or widgets.
  Report it if the project has extensions that link Datadog.

## 7. Logs and Trace

These are behavior changes, not edits. Report them; change code only if it fails to compile.

- Logs no longer reports fatal errors. Error Tracking for crashes needs Crash Reporting
  enabled together with RUM. If the project enables Logs but not Crash Reporting, report that
  crashes will no longer be reported; do not enable Crash Reporting yourself.
- Trace sampling is deterministic when used with RUM, and it follows the RUM `session.id`.
  `FirstPartyHostsTracing` samples every request by default and injects the trace context
  only into sampled requests. Report any project that configures tracing for first-party hosts.
- Other telemetry changes of 3.0.0 that can affect existing dashboards and monitors. Report them
  all, and change nothing:
  - Network errors (no connection, cancelled requests, timeouts) now carry the error category `Network`.
  - The `os` and `device` attributes now share one model across Logs, RUM and Trace, at the root of
    each event. Queries on the previous log or span attribute paths deserve a check.
  - The memory vital is now measured from `phys_footprint`, so its values are not comparable with v2.
  - View attributes are propagated to every child event, and global attributes are captured when each
    event happens.

## 8. Session Replay privacy

`defaultPrivacyLevel` is replaced by three settings. Where the project passes
`defaultPrivacyLevel:` to `SessionReplay.Configuration(...)` or assigns the property, replace
it with these values, which are the conversion the SDK itself applied in 2.30.2:

| `defaultPrivacyLevel` (v2) | `textAndInputPrivacyLevel` | `imagePrivacyLevel` | `touchPrivacyLevel` |
|---|---|---|---|
| `.allow` | `.maskSensitiveInputs` | `.maskNone` | `.show` |
| `.maskUserInput` | `.maskAllInputs` | `.maskNonBundledOnly` | `.hide` |
| `.mask` | `.maskAll` | `.maskAll` | `.hide` |

In Objective-C the v2 code creates `[[DDSessionReplayConfiguration alloc] initWithReplaySampleRate:<rate>]`
and then assigns the `defaultPrivacyLevel` property (`DDSessionReplayConfigurationPrivacyLevel…`:
`Allow`, `Mask`, `MaskUserInput`). In v3 both the single-argument initializer and the property are gone.
Use `initWithReplaySampleRate:textAndInputPrivacyLevel:imagePrivacyLevel:touchPrivacyLevel:` with
`DDTextAndInputPrivacyLevel…` (`MaskSensitiveInputs`, `MaskAllInputs`, `MaskAll`),
`DDImagePrivacyLevel…` (`MaskNone`, `MaskNonBundledOnly`, `MaskAll`) and `DDTouchPrivacyLevel…`
(`Show`, `Hide`), with the same values as the table, and delete the property assignment.

The v2 default was `.mask`, which equals the v3 defaults. A project that never passed
`defaultPrivacyLevel` needs no change. A project that already passes the three settings needs
no change. Keep every other argument (`replaySampleRate`, `startRecordingImmediately`,
`customEndpoint`) exactly as it is.

## 9. URLSession instrumentation

The legacy delegate types no longer exist. From 3.7.0, resource and trace tracking is automatic once
`urlSessionTracking` is configured in `RUM.Configuration` and/or `Trace.Configuration`, but the
detailed timing breakdown (DNS, SSL, time to first byte) that the legacy delegates provided
now needs an explicit registration.

- Keep the project's existing `urlSessionTracking` configuration. Do not add one.
- Replace every subclass or instance of `DatadogURLSessionDelegate`, `DDURLSessionDelegate` or
  `DDNSURLSessionDelegate` with a plain delegate: subclass `NSObject` and conform to
  `URLSessionDataDelegate`, keep the delegate's own methods, and remove `super` calls to the old
  base class. Keep passing that delegate to the `URLSession`.
- `URLSessionInstrumentation` lives in the `DatadogInternal` module. In the file that registers the
  delegate add `import DatadogInternal` and **nothing else**: with Swift Package Manager do **not**
  add a `DatadogInternal` product to the project, because it is not a product of the package
  (adding it fails the build with `Missing package product 'DatadogInternal'`). With CocoaPods it
  comes with `DatadogCore`; with Carthage the xcframework is already linked.
- Register the delegate class once, after `RUM.enable` or `Trace.enable`, so the timing breakdown
  is preserved. Use the API that exists in the chosen version: read
  `DatadogInternal/Sources/NetworkInstrumentation/URLSession/URLSessionInstrumentation.swift`
  from the package checkout, or from
  `https://raw.githubusercontent.com/DataDog/dd-sdk-ios/<tag>/DatadogInternal/Sources/NetworkInstrumentation/URLSession/URLSessionInstrumentation.swift`
  when there is no checkout. Use
  `URLSessionInstrumentation.enableDurationBreakdown(with: .init(delegateClass: Delegate.self))`
  when it exists; `URLSessionInstrumentation.enable(with:)` is deprecated in recent 3.x
  releases and must not be introduced there.
- Recent 2.x releases already deprecate the legacy delegates and already provide
  `URLSessionInstrumentation.enable(with:)`. If the project already calls it and the chosen
  version has `enableDurationBreakdown(with:)`, rename the call and keep its arguments.
- **Alamofire.** The `DatadogAlamofireExtension` pod was deprecated in 2.x and no longer exists in 3.x.
  Remove the pod and `import DatadogAlamofireExtension`, and remove `DDRequestInterceptor()` and
  `DDEventMonitor()` from every `Session(...)` that uses them, keeping any other interceptor or
  monitor. Alamofire requests are then tracked automatically through `urlSessionTracking`. To keep
  the timing breakdown the extension provided, register Alamofire's delegate once, after `RUM.enable`
  (`import Alamofire` and `import DatadogRUM`):
  `URLSessionInstrumentation.enableDurationBreakdown(with: .init(delegateClass: Alamofire.SessionDelegate.self))`.
  Never add the Alamofire registration to a project that did not use the extension.
- If the project has no delegate and no such call, add nothing.

## 10. Verify the result

Resolve the packages with the manager's command and build the project's scheme with its
normal command (for example `xcodebuild build -scheme <scheme> -destination 'generic/platform=iOS Simulator'`
with code signing disabled). Prefer the command the project's CI uses. If the tools are not
available, do not simulate either step: report that they did not run and leave the resolved file
untouched.

Before finishing, confirm that:

- every resolved Datadog product is on the chosen 3.x version, and `DatadogObjc` is gone;
- every product the project used before is still used, and none was added;
- no `DatadogURLSessionDelegate`, `DDURLSessionDelegate`, `DDNSURLSessionDelegate`,
  `RUMView(path:` or `defaultPrivacyLevel` remains, and no active `setUserInfo` call lacks an `id`
  (a commented-out call with a `TODO` is acceptable and must be reported);
- the deployment target and the other build settings are unchanged, unless step 2 said so;
- **every product the project links exists in the chosen version** (Swift Package Manager only).
  You cannot compile here, so check it mechanically. List the products of the package and the ones
  the project declares, and make sure the second list is contained in the first:
  ```sh
  curl -fsSL "https://raw.githubusercontent.com/DataDog/dd-sdk-ios/<tag>/Package.swift" | sed -n '/products:/,/dependencies:/p' | grep -oE 'name: "[A-Za-z]+"' | cut -d'"' -f2 | sort -u
  grep -ohE 'productName = Datadog[A-Za-z]+;' <App>.xcodeproj/project.pbxproj | sed 's/productName = //;s/;//' | sort -u
  ```
  A product that is not in the first list (for example `DatadogInternal`) must be removed, with its
  build file and its entries. Do the same for the `import Datadog...` lines: each module is a
  product, or `DatadogInternal`, which needs no product;
- the project builds, or the report says why the build could not run.

If one of these checks fails and you cannot fix it, **restore the project to its original state**
and report what blocked the upgrade. Do not deliver a change that you know will not compile.

Fix only migration-related failures. Do not rewrite unrelated code or remove instrumentation
to get a successful build.

Report the original and resulting SDK versions, how the target version was chosen (and any
move down, with the failing requirement), the dependency manager and its resolve command,
the files changed, the Session Replay values applied, the behavior changes from steps 6 and 7, the
decisions left to the owner, and the exact build command with its result.

**Before you send the report, check it for the client token.** Take the value of `clientToken` from
the project and make sure it appears nowhere in your report. If it does, rewrite that line as
`clientToken: unchanged`. The report is public to the reviewer of the pull request.

## Appendix: dependencies by manager

### Choose the target version

The target is always a 3.x release, so every listing below keeps only `3.x.y` tags.

1. Print the newest 3.x release **and** its requirements in one step. This avoids the rate-limited
   GitHub REST API:
   ```sh
   TAG=$(git ls-remote --tags --refs https://github.com/DataDog/dd-sdk-ios | sed 's#.*refs/tags/##' | grep -E '^3\.[0-9]+\.[0-9]+$' | sort -V | tail -1); echo "newest 3.x: $TAG"; curl -fsSL "https://raw.githubusercontent.com/DataDog/dd-sdk-ios/$TAG/Package.swift" | grep -E 'swift-tools-version|\.(iOS|tvOS|visionOS)\('
   ```
   If `git` is not installed (`git: command not found`) or fails, list the versions with `curl`
   instead. Use the public tags page, which is not rate limited like the REST API and lists every
   tag, **including patch releases** that the changelog omits (for example `3.16.1`):
   ```sh
   for p in 1 2 3; do curl -fsSL "https://github.com/DataDog/dd-sdk-ios/tags?page=$p"; done | grep -oE 'releases/tag/3\.[0-9]+\.[0-9]+' | sed 's#.*/##' | sort -uV | tail -1
   ```
   Each page lists about ten tags, newest first. Print the whole list (drop `tail -1`) to see older
   tags, and raise the page count if you need versions older than the first three pages. Then read
   that tag's `Package.swift` with the `curl` command above.
   Only if **both** ways fail to reach GitHub, use the fallback version `3.14.0` (see
   step 2 of `SKILL.md`) and report it. Do not guess any other version.
2. Compare those requirements with the project, and **choose the newest tag whenever they are met**.
   - The `.iOS(...)`, `.tvOS(...)` and `.visionOS(...)` entries give the minimum deployment target
     of each platform.
   - `swift-tools-version` gives the minimum Xcode: 5.9 needs Xcode 15, 6.0 needs Xcode 16, 6.2
     needs Xcode 26. Any Xcode 26.x meets 6.2.
   - A requirement is met when the deployment target is at least the platform minimum and the
     installed Xcode is at least the one the `swift-tools-version` needs.
3. Move down to an older tag **only** when you can name the specific requirement that the newest tag
   fails, and state it in the report. To see older tags, run the listing without `tail -1`, then
   read that tag's requirements with the same `curl` command.

   Known breakpoints:

   | Version | Change |
   |---|---|
   | 3.10.0 | First release with visionOS support |
   | 3.17.0 | iOS and tvOS minimum raised from 12 to 15 |
   | 3.15.0 to 3.18.0 | Declare `swift-tools-version: 6.0`, but their sources use syntax that Xcode 16.0 (Swift 6.0) rejects (`unexpected ',' separator`); they need a Swift 6.1 compiler, which is Xcode 16.3 or later. Verified on 3.15.0, 3.16.1 and 3.18.0. **3.14.0 is the newest that builds with Xcode 16.0** |
   | 3.19.0 | Requires `swift-tools-version: 6.2` (Xcode 26) |

   For example, a project with a deployment target of iOS 13 and Xcode 26 takes **3.16.x**: iOS 12
   minimum. With Xcode 16.0 it must take **3.14.0**, the newest that compiles there.
   `swift-tools-version` does not reveal every compiler requirement (see 3.15.0 to 3.18.0), so the
   build in step 10 of `SKILL.md` has the last word.
4. If no 3.x release fits, stop and report the unmet prerequisite. Do not raise the deployment
   target.
5. **CocoaPods only:** check that the chosen version is published. `pod trunk info DatadogCore` works
   where CocoaPods is installed. Otherwise:
   ```sh
   curl -fsS https://trunk.cocoapods.org/api/v1/pods/DatadogCore | python3 -c "import json,sys; print('$TAG' in [v['name'] for v in json.load(sys.stdin)['versions']])"
   ```
   If it prints `False`, use the newest published version that still fits steps 2 to 4. Sort the
   versions numerically, not as text (`3.9.1` is older than `3.20.0`), and say in the report why the
   newest tag was not used.

Report the chosen version and why it was chosen.

### Version requirement

Version resolution does not consider the project's deployment target or Xcode version. An open range
can therefore resolve to a newer release that the project cannot build.

- **Keep the project's existing requirement kind** and set its version to the chosen one: `upToNextMajorVersion`
  stays `upToNextMajorVersion`, and an exact requirement stays exact. Do not switch kinds.
- **The chosen version is the newest release:** that is all.
- **An older version was chosen because a newer one is incompatible:** the open range would still
  reach the incompatible release, so cap it just below the first one, for example
  `3.16.0 ..< 3.17.0`. This is the one case where the kind changes.
- **CocoaPods:** `~> <X.Y.Z>` allows only patch updates, so use it in both cases.
- **Carthage:** `~> <X.Y.Z>` allows anything up to the next major version. When an older version was
  chosen for compatibility, use `== <VERSION>`.

After resolving, read the version actually pinned in `Package.resolved`, `Podfile.lock` or
`Cartfile.resolved`, and confirm that it meets the requirements checked above. If you cannot
resolve because the tool is not installed, leave the resolved file untouched and report that it
must be regenerated.

### Swift Package Manager in an Xcode project

- Package URL: `https://github.com/DataDog/dd-sdk-ios`
- Package identity: `dd-sdk-ios`

Edit `project.pbxproj` in its existing style.

1. In the existing `XCRemoteSwiftPackageReference` for `dd-sdk-ios`, change only the version in
   `requirement`:
   ```
   requirement = {
       kind = upToNextMajorVersion;
       minimumVersion = <VERSION>;
   };
   ```
   For a capped range, use `kind = versionRange; minimumVersion = <VERSION>; maximumVersion = <FIRST_INCOMPATIBLE>;`.
2. **Remove `DatadogObjc`**, if the project links it. Delete all four places that mention it, and
   confirm with a search that no `DatadogObjc` is left:
   - its `XCSwiftPackageProductDependency` object;
   - its ID in the app target's `packageProductDependencies`;
   - its `PBXBuildFile` object;
   - its ID in the app target's `PBXFrameworksBuildPhase` `files` list.
3. **Add the products the code needs**, when the project linked `DatadogObjc` without them. In v2,
   `DatadogObjc` brought the Core, RUM, Logs and Trace modules along. In v3 each module must be linked:
   `DatadogCore` always, and `DatadogRUM`, `DatadogLogs` or `DatadogTrace` for the `DDRUM`,
   `DDLogs` or tracing classes the code uses. Do this only for products the code already uses.

   Every new object ID must be exactly 24 uppercase hexadecimal characters and must not already
   occur in the file. Do not invent IDs by hand. Generate them with a command and assign each to
   exactly one object:
   ```sh
   python3 -c 'import secrets; [print(secrets.token_hex(12).upper()) for _ in range(<N>)]'
   ```
   For each product, add an `XCSwiftPackageProductDependency` with `package = <PKG_ID>; productName = <Product>;`,
   add its ID to `packageProductDependencies`, and add a `PBXBuildFile` with `productRef = <PRODUCT_ID>`
   to the `PBXFrameworksBuildPhase`.
4. Resolve with `xcodebuild -resolvePackageDependencies -project <App>.xcodeproj` (or `-workspace`),
   if `xcodebuild` is available. Otherwise leave `Package.resolved` as it is: never edit it by hand.

### Swift Package Manager in Package.swift

```swift
dependencies: [
    .package(url: "https://github.com/DataDog/dd-sdk-ios", .upToNextMajor(from: "<VERSION>")),
    // capped range instead: .package(url: "https://github.com/DataDog/dd-sdk-ios", "<VERSION>"..<"<FIRST_INCOMPATIBLE>"),
],
```

Change only the version in the existing requirement and keep its form. Remove
`.product(name: "DatadogObjc", package: "dd-sdk-ios")` from every target, and add the product
modules the code needs, as in step 3 above. Resolve with `swift package resolve` or
`xcodebuild -resolvePackageDependencies`, if available.

### CocoaPods

In `Podfile`:

- Set every Datadog pod to `~> <VERSION>`, in each target block where it appears.
- Remove `pod 'DatadogObjc'`. Make sure `DatadogCore` is present, plus the pod of every module whose
  code the project uses.
- Update only the Datadog pods: `pod update DatadogCore DatadogRUM …` (list every Datadog pod in the
  `Podfile`), or `bundle exec pod update …` when a `Gemfile` pins CocoaPods. Plain `pod install` keeps the old
  versions pinned in `Podfile.lock`.
- Keep `Podfile.lock`, and build through the `.xcworkspace`.
- If CocoaPods is not installed, edit the `Podfile`, leave `Podfile.lock` untouched, and report that the
  pods must be updated. Do not install Ruby tooling.
- CocoaPods is not supported from SDK 4.0.

### Carthage

1. Update the requirement of `github "DataDog/dd-sdk-ios"` in `Cartfile` following the Version
   requirement section.
2. Run `carthage update dd-sdk-ios --use-xcframeworks --platform <PLATFORMS>`, where `<PLATFORMS>` is
   the comma-separated list of the target's platforms, for example `iOS,tvOS`. This updates only
   dd-sdk-ios. Datadog does not ship prebuilt Carthage binaries. If Carthage is not installed, edit the
   `Cartfile`, leave `Cartfile.resolved` untouched, and report that the update must be run.
3. In the app target, link `DatadogCore.xcframework` and `DatadogInternal.xcframework`, plus one
   xcframework for each product the project uses (`DatadogRUM`, `DatadogLogs`, `DatadogTrace`,
   `DatadogSessionReplay`, `DatadogCrashReporting` with `CrashReporter`, `DatadogWebViewTracking`).
   Unlink `DatadogObjc.xcframework`. Link and embed them exactly the way the project already handles
   its other Carthage xcframeworks.
4. Keep `Cartfile.resolved`.
5. Carthage is not supported from SDK 4.0.

### XCFrameworks (manual)

Use this only when the project already vendors xcframeworks for its dependencies.

The release zip contains only iOS and tvOS slices. When the target builds for visionOS, **stop
before editing anything** and report that the manual XCFramework path does not support visionOS.
Do not work around it by switching the project to another dependency manager. After extracting,
confirm that each required framework has slices for the platforms where it will be linked. If any
slice is missing, stop before linking.

1. Download `Datadog.xcframework.zip` from the GitHub release for the chosen tag.
2. Replace the vendored v2 xcframeworks with the same set listed for Carthage, and remove
   `DatadogObjc.xcframework`.
3. Follow the project's existing linking and embedding pattern.

[migration]: https://github.com/DataDog/dd-sdk-ios/blob/3.19.0/MIGRATION.md

