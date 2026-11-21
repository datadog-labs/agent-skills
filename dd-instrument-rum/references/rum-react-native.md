# React Native and Expo RUM setup

Read this file instead of `rum-core.md` and the web framework references when `SKILL.md` classified the project as React Native. It covers bare React Native, Expo (managed or with committed native folders) and Expo Router.

## Contents

- Prerequisites
- App shape
- Install
- Native linking
- SDK init: root component, Expo Router, React Native Navigation (Wix)
- Credentials
- Build check
- Report

Never import `@datadog/browser-rum` or call `datadogRum.init` in a React Native project. The Browser SDK needs a DOM, so it breaks the React Native bundle. When an Expo app also ships a web build, the React Native SDK covers only its iOS and Android builds; say so in the report and add nothing for the web build.

## Prerequisites

Check these before you install or edit anything:

- **React Native version**, from the app's manifest. With 0.75 or later, use the latest `@datadog/mobile-react-native` 3.x. With 0.63.4 to 0.74, use the 3.12 line, because 3.13 and later need 0.75. With an older version, stop and report it; do not upgrade React Native.
- **Native minimums**, when the project has `ios/` or `android/`. Do not raise them yourself, because each one drops support for older devices or changes the whole native build.
  - iOS deployment target, from `platform :ios` in `ios/Podfile` (for `min_ios_version_supported`, read the value in `node_modules/react-native/scripts/react_native_pods.rb`): SDK 3.13 needs iOS 15.0, and SDK 3.12 needs iOS 12.0. With a target from 12.0 to below 15.0, use the 3.12 line and say so in the report. Below 12.0, stop and report it.
  - Android, from `android/build.gradle` and `android/gradle/wrapper/gradle-wrapper.properties`: `compileSdkVersion` 31 or later, Android Gradle Plugin 7 or later and Gradle 7 or later. When the project is below one of them, stop and report the upgrade.
- **Site.** The SDK takes a region code, not the site domain. Any value it does not know sends data to US1, so a wrong value moves data to the wrong site without an error.

  | Datadog site | SDK `site` |
  |---|---|
  | `datadoghq.com` | `US1` |
  | `us3.datadoghq.com` | `US3` |
  | `us5.datadoghq.com` | `US5` |
  | `datadoghq.eu` | `EU1` |
  | `ap1.datadoghq.com` | `AP1` |
  | `ap2.datadoghq.com` | `AP2` |
  | `ddog-gov.com` | `US1_FED` |
  | `us2.ddog-gov.com` | `US2_FED` |

  For `uk1.datadoghq.com`, stop and report it, unless the installed SDK's site parsing lists `UK1`. SDK 3.13.0 has no `UK1` value.
- **Expo Go.** Expo Go does not contain the SDK's native module, so it sends no data. The app must run as a development build or a release build. Say this in the report when the project runs in Expo Go today.

## App shape

Record these from the manifest and the source layout:

| Signal | Shape | Init location |
|---|---|---|
| `expo-router` in dependencies and an `app/_layout` or `src/app/_layout` file | Expo Router | The root layout |
| `expo` in dependencies, no `expo-router` | Expo | The root component that `registerRootComponent` or the manifest `main` loads, often `App.tsx` |
| `react-native-navigation` in dependencies | React Native Navigation (Wix) | The entry file, before `Navigation.events().registerAppLaunchedListener` |
| `react-native` with `ios/` and `android/`, none of the above | Bare React Native | The root component passed to `AppRegistry.registerComponent`, often `App.tsx` |

An Expo app is managed when it has no `ios/` or `android/` folder. Also record the navigation library (`expo-router`, `@react-navigation/native`, `react-native-navigation`, or none), TypeScript, the package manager, an existing env-file convention, and any existing Datadog setup.

**Existing setup.** Search the source, skipping `node_modules`, `ios/Pods` and build output, for imports from `@datadog/mobile-react-native` or `expo-datadog`, and for `DatadogProvider`, `DatadogProviderConfiguration`, `CoreConfiguration`, `DdSdkReactNative.initialize` or the older `DdSdkReactNativeConfiguration`. Apply the same rules as for web apps in `SKILL.md`: keep one valid init and all its values, add only missing navigation tracking, and stop when there are several inits.

## Install

Install the SDK and the navigation integration in one command with the project's package manager, both at the same exact version. Get the version first, for example with `npm view @datadog/mobile-react-native version`, or `npm view @datadog/mobile-react-native@3.12 version` for React Native 0.63.4 to 0.74. When the SDK is already installed, install the integration at the SDK's installed version.

| Navigation library | Integration package |
|---|---|
| `expo-router` or `@react-navigation/native` | `@datadog/mobile-react-navigation` |
| `react-native-navigation` (Wix) | `@datadog/mobile-react-native-navigation`, which needs `react-native-navigation` 7.5 or a later 7.x |
| None | No integration package |

Do not install `@datadog/mobile-react-native-navigation` for React Navigation or Expo Router: it is the Wix integration, and its required `react-native-navigation` peer is missing there.

Do not add `expo-datadog`, `@datadog/datadog-ci` or a symbol upload. They upload dSYMs, Proguard mapping files and source maps so that native crash stacks are readable, and they need a Datadog API key in the build. List them in the report as an optional follow-up.

## Native linking

- **`ios/Podfile` exists** (bare React Native, or Expo with committed native folders): run `pod install` in `ios/`, or `bundle exec pod install` when `ios/Gemfile` or the root `Gemfile` lists CocoaPods. CocoaPods runs only on macOS. On another system, list `cd ios && pod install` as a user step.
- **Android** links the SDK through autolinking, so no Gradle edit is needed.
- **Expo managed:** do not generate or commit native folders. The native projects come from `npx expo prebuild`, which `npx expo run:ios`, `npx expo run:android` and EAS Build run on their own. List that step in the report. Run `npx expo prebuild` yourself only when the user asks for it.

## SDK init

Create the configuration once at module scope, outside any component, and set the four tracking options shown below. The SDK defaults all four to `false`; `nativeCrashReportEnabled` turns on native crash reports. Keep `TrackingConsent.GRANTED` unless the app already asks users for tracking consent; then pass `TrackingConsent.PENDING` and update it with `DdSdkReactNative.setTrackingConsent` where the app records the answer. The credential values come from "Credentials".

### Root component: bare React Native and Expo without Expo Router

Wrap the outermost element that the root component returns in `DatadogProvider`. With React Navigation, track views from the `NavigationContainer`: reuse its `ref` if it has one, otherwise create one with `useNavigationContainerRef()` (React Navigation 6 and later) or `React.useRef(null)`. Add the call to an existing `onReady` instead of replacing it. With the static API, put the same `ref` and `onReady` on the component that `createStaticNavigation` returns.

```tsx
import { NavigationContainer, useNavigationContainerRef } from '@react-navigation/native';
import {
  DatadogProvider,
  DatadogProviderConfiguration,
  TrackingConsent,
} from '@datadog/mobile-react-native';
import { DdRumReactNavigationTracking } from '@datadog/mobile-react-navigation';

const datadogConfiguration = new DatadogProviderConfiguration(
  '<DD_CLIENT_TOKEN>',
  '<ENVIRONMENT>',
  TrackingConsent.GRANTED,
  {
    site: '<SDK_SITE>',
    rumConfiguration: {
      applicationId: '<DD_APPLICATION_ID>',
      trackInteractions: true,
      trackResources: true,
      trackErrors: true,
      nativeCrashReportEnabled: true,
    },
  },
);

export default function App() {
  const navigationRef = useNavigationContainerRef();
  return (
    <DatadogProvider configuration={datadogConfiguration}>
      <NavigationContainer
        ref={navigationRef}
        onReady={() => DdRumReactNavigationTracking.startTrackingViews(navigationRef.current)}
      >
        {/* existing navigators */}
      </NavigationContainer>
    </DatadogProvider>
  );
}
```

Without a navigation library, use only the `DatadogProvider` part. The SDK then records sessions, taps, resources and errors but no views; say in the report that views need `DdRum.startView` and `DdRum.stopView` calls.

### Expo Router

Edit the root layout (`app/_layout.tsx` or `src/app/_layout.tsx`). Wrap its outermost returned element, including existing providers, in `DatadogProvider`, and start view tracking from the router's navigation container in an effect:

```tsx
import {
  DatadogProvider,
  DatadogProviderConfiguration,
  TrackingConsent,
} from '@datadog/mobile-react-native';
import { DdRumReactNavigationTracking } from '@datadog/mobile-react-navigation';
import { Stack, useNavigationContainerRef } from 'expo-router';
import { useEffect } from 'react';

const datadogConfiguration = new DatadogProviderConfiguration(
  process.env.EXPO_PUBLIC_DD_RUM_CLIENT_TOKEN ?? '',
  process.env.EXPO_PUBLIC_DD_ENV ?? '',
  TrackingConsent.GRANTED,
  {
    site: '<SDK_SITE>',
    rumConfiguration: {
      applicationId: process.env.EXPO_PUBLIC_DD_RUM_APPLICATION_ID ?? '',
      trackInteractions: true,
      trackResources: true,
      trackErrors: true,
      nativeCrashReportEnabled: true,
    },
  },
);

export default function RootLayout() {
  const navigationRef = useNavigationContainerRef();
  useEffect(() => {
    DdRumReactNavigationTracking.startTrackingViews(navigationRef.current);
  }, [navigationRef]);
  return (
    <DatadogProvider configuration={datadogConfiguration}>
      <Stack />
    </DatadogProvider>
  );
}
```

Keep the layout's existing navigator (`Stack`, `Tabs`, `Slot` or `Drawer`) and its other code.

### React Native Navigation (Wix)

This library has no single root component to wrap. Initialize the SDK with a `CoreConfiguration` in the entry file and start view tracking before the app registers its launch listener:

```ts
import {
  CoreConfiguration,
  DdSdkReactNative,
  TrackingConsent,
} from '@datadog/mobile-react-native';
import { DdRumReactNativeNavigationTracking } from '@datadog/mobile-react-native-navigation';

const datadogConfiguration = new CoreConfiguration(
  '<DD_CLIENT_TOKEN>',
  '<ENVIRONMENT>',
  TrackingConsent.GRANTED,
  {
    site: '<SDK_SITE>',
    rumConfiguration: {
      applicationId: '<DD_APPLICATION_ID>',
      trackInteractions: true,
      trackResources: true,
      trackErrors: true,
      nativeCrashReportEnabled: true,
    },
  },
);
DdSdkReactNative.initialize(datadogConfiguration);
DdRumReactNativeNavigationTracking.startTracking();

// existing: Navigation.events().registerAppLaunchedListener(...)
```

`DdSdkReactNative.initialize` returns a promise. Call it without a top-level `await`, because Metro wraps each module in a plain function.

## Credentials

Get the application ID and the client token from `rum-application.md`, with `type: react-native`. Set `<SDK_SITE>` from the site table; it is not a secret.

- **Expo** (managed, prebuilt or Expo Router): use `EXPO_PUBLIC_DD_RUM_APPLICATION_ID`, `EXPO_PUBLIC_DD_RUM_CLIENT_TOKEN` and `EXPO_PUBLIC_DD_ENV`, as in the Expo Router example. Expo inlines only static `process.env.EXPO_PUBLIC_…` references, so do not destructure `process.env` or build the names at runtime. Write the values to `.env.local`, with `EXPO_PUBLIC_DD_ENV=development` unless the project names its local environment. Before writing, confirm that git ignores the file and does not track it: `git check-ignore -q .env.local && ! git ls-files --error-unmatch .env.local >/dev/null 2>&1`. When it is not ignored, add `.env*.local` to `.gitignore`. Append to an existing `.env.local` without reading it back, because it may hold secrets. EAS Build and CI do not see `.env.local`, so list the three variables in the report for the user to set there.
- **Datadog keys in a compiled env file.** `react-native-config` compiles every variable of its env file into the app: Android gets a `BuildConfig` field and a string resource for each line, and iOS gets the same values. That file is `.env` unless an `ENVFILE` variable, a `defaultEnvFile` property or `envConfigFiles` in the Android build, or an `ENVFILE` build setting in the Podfile names another one, and the iOS build falls back to `.env` when the named file is missing. `dd-account-setup` writes `DD_API_KEY` and `DD_APP_KEY` to `.env` when `.env.local` does not exist. When the app depends on `react-native-config`, check each env file that it reads before you write any RUM value or run a build, without printing values:

  ```sh
  grep -qE '^[[:space:]]*(export[[:space:]]+)?DD_(API|APP)_KEY[[:space:]]*=' .env && echo "keys in .env"
  ```

  When it finds keys, move those lines to `.env.local`, which `react-native-config` does not read unless `ENVFILE` names it. First confirm that git ignores `.env.local` and does not track it, as for Expo, and that no build setting names it. Move the lines without printing them, keep the file private, and tell the user what moved:

  ```sh
  KEYS='^[[:space:]]*(export[[:space:]]+)?DD_(API|APP)_KEY[[:space:]]*='
  touch .env.local && chmod 600 .env.local &&
    { [ ! -s .env.local ] || [ -z "$(tail -c 1 .env.local)" ] || echo >> .env.local; } &&
    grep -E "$KEYS" .env >> .env.local && cp -p .env .env.tmp &&
    { grep -vE "$KEYS" .env || true; } > .env.tmp && mv .env.tmp .env && echo "moved"
  ```

  When you cannot move them safely, stop before editing and tell the user to move both keys out of the compiled file. In both cases, tell the user that any build already made from that file contains the keys, so they should rotate both keys if such a build was shared. `react-native-dotenv` inlines only the names that the code imports from `@env`, and Expo inlines only `EXPO_PUBLIC_*` names, so they do not compile the keys.
- **Bare React Native and React Native Navigation:** when the project already loads env files, for example through `react-native-config` (`Config.DD_RUM_CLIENT_TOKEN`) or `react-native-dotenv` (imports from `@env`), follow that convention and put the values in its gitignored env file. Otherwise keep the quoted placeholders `<DD_CLIENT_TOKEN>`, `<DD_APPLICATION_ID>` and `<ENVIRONMENT>` from the examples, add a comment above the configuration that says to replace them, and give the user that step in the report: the file, each placeholder, and the value to use. The app sends no data until the placeholders are replaced.

Never put a Datadog API key or application key in the app, because anything in the app bundle is public. The client token is the only token the app needs.

## Build check

Run checks that end on their own. Do not start `npx expo start`, `npx react-native start` or a simulator, because they keep running.

1. **Typecheck**, when the project has `tsconfig.json`: the project's typecheck script, or `npx tsc --noEmit`. Fix errors that your edits caused.
2. **Metro bundle** into a temporary folder outside the project:
   - Expo: `npx expo export --platform android --output-dir "$(mktemp -d)"`
   - Bare React Native: `OUT=$(mktemp -d) && npx react-native bundle --platform android --dev false --entry-file <entry file> --bundle-output "$OUT/index.android.bundle" --assets-dest "$OUT"`, with the entry file from the manifest `main` field or `index.js`.
   A bundle that fails on an import from your edit is an instrumentation error; fix it and run the bundle again.
3. **Native linking:** the `pod install` from "Native linking", on macOS only.

A passing bundle shows that the JavaScript side resolves. It does not show that the native module is linked or that events arrive. The app cannot send events from this environment, so verification on a simulator or device is a user step.

## Report

Use the report format in `common-verify-report.md`. Also list each step left for the user:

- the credential placeholders to replace, or the `EXPO_PUBLIC_DD_*` variables to set in EAS Build or CI;
- `npx expo prebuild`, `npx expo run:ios` or `npx expo run:android` for an Expo managed app, and a development build instead of Expo Go;
- `cd ios && pod install` when it could not run;
- running the app on a simulator or device, then opening the `dashboardUrl` link from `common-verify-report.md`;
- optionally, symbol uploads for readable native crash stacks, with `expo-datadog` for Expo or `datadog-ci` for bare React Native.
