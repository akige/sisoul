# sisoul Mobile PWA (Capacitor 8 wrap)

Native iOS + Android wrapper around the SolidJS PWA — **no JS rewrite** required.
Same code as `pwa/`, just packaged for App Store / Play Store + push notifications.

This is **path 2** to mobile (PWA wrap). Path 1 is `mobile/` (native Swift + Kotlin
skeleton, P3-6 ship). Both ship in parallel; users pick the one fitting their needs.

## Why Capacitor (not React Native / Flutter)

- **Reuse 100% of PWA**: SolidJS routes + components + styles work as-is in native.
- **No JS bridge re-implementation**: Capacitor uses platform WebView + native plugins.
- **App Store + Play Store distribution**: signs, archives, submits like any native app.
- **Web Push → APNs / FCM**: bridged via `@capacitor/push-notifications`.

## Quick start (Mac dev)

```bash
cd pwa && npm run build              # build SolidJS PWA → dist/
cd ../mobile-pwa
node copy-pwa.mjs                    # dist/ → www/
npx cap sync                          # plugins + web → ios/ + android/

# Open in native IDE
npx cap open ios                      # → Xcode (needs Xcode 15+)
npx cap open android                  # → Android Studio (needs Hedgehog+)
```

## Capacitor plugins installed (5)

| Plugin | Purpose |
|---|---|
| `@capacitor/push-notifications` | APNs (iOS) + FCM (Android) push delivery |
| `@capacitor/local-notifications` | In-app reminders (Goal-mode integration) |
| `@capacitor/preferences` | Native KV store (vault metadata, NOT secrets) |
| `@capacitor/network` | Online/offline state for daemon connection |
| `@capacitor/app` | Lifecycle: background / foreground / deep links |

## Config (`capacitor.config.json`)

- `appId`: `io.sisoul.pwa`
- `appName`: `sisoul`
- `webDir`: `www` (PWA build output, sync'd from `../pwa/dist/`)
- `iosScheme`: `sisoul` (deep linking `sisoul://`)
- `androidScheme`: `https` (HTTPS for daemon connection)
- `allowNavigation`: `*.sisoul.io` + `127.0.0.1:9876` + `localhost:9876`

## Build for distribution

### iOS (.ipa)

```bash
npx cap open ios                         # Open in Xcode
# In Xcode:
#  1. Select target → Signing & Capabilities → set Apple Developer team
#  2. Product → Archive → Distribute → App Store Connect
```

Requirements:
- Xcode 15+, macOS 14+
- Apple Developer account ($99/year for App Store distribution)
- APNs key for push notifications (free, in Apple Developer console)

### Android (.aab / .apk)

```bash
npx cap open android                     # Open in Android Studio
# In Android Studio:
#  1. Build → Generate Signed Bundle / APK → AAB → upload to Play Console
```

Requirements:
- Android Studio Hedgehog+
- JDK 17+
- Play Store developer account ($25 one-time for Play Store distribution)
- Firebase project for FCM push notifications (free tier)

## Push notification integration

PWA already has Service Worker (`pwa/public/sw.js`). For native:

1. iOS: Capacitor uses APNs. Configure in Xcode `Capabilities → Push Notifications`.
2. Android: Capacitor uses FCM. Drop `google-services.json` into `android/app/`.
3. PWA code calls `PushNotifications.register()` from `@capacitor/push-notifications`.
4. sisoul daemon (`/v1/push/register`) accepts device token, routes broadcasts via Waku.

See `docs/MOBILE.md` for full push notification integration guide.

## What's NOT in mobile-pwa (compared to mobile/)

- **No native crypto**: vault encryption runs in WebView's WebCrypto API. The
  `mobile/` skeleton has native CryptoKit / AndroidX security-crypto for users
  who want hardware-backed key storage.
- **No native UI components**: 100% SolidJS UI. Path 1 (`mobile/`) has Compose +
  SwiftUI for users wanting platform-idiomatic UI.

## CI/CD (post-alpha)

- GitHub Actions `mobile-pwa-ios.yml`: build .ipa on `git tag mobile-v*-ios`
- GitHub Actions `mobile-pwa-android.yml`: build .aab on `git tag mobile-v*-android`
- TestFlight / Play Store internal track auto-publish

Templates ship later (after first manual release verified).

## Update path

When PWA changes:

```bash
cd pwa && npm run build && cd ../mobile-pwa && npm run sync
npx cap open ios       # or android
# Build + upload new version
```

Native plugin updates:

```bash
cd mobile-pwa
npm update              # bump Capacitor packages
npx cap sync             # propagate to ios/ + android/
```

---

🤖 PWA wrap path complements `mobile/` native skeleton.
Users pick whichever fits their distribution + offline + UI needs.
