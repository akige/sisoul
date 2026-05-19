# sisoul mobile · iOS + Android native skeleton (P3-6)

跨平台 mobile 客户端 skeleton. 主要功能在 PC daemon 跑, mobile 调 HTTP API.

## iOS (Swift Package · SisoulCore + SisoulApp)

```
cd ios
swift build           # build SisoulCore lib + SisoulApp executable
swift test            # 跑 14 单元测试 (Vault 6 + Identity 5 + DaemonClient 3)
```

要求: Xcode 15+ / Swift 5.9+ / iOS 17+ deployment target.
依赖: CryptoKit (vault), CommonCrypto (PBKDF2), URLSession (HTTP).

## Android (Gradle · Compose + Kotlin)

```
cd android
./gradlew :app:test       # 跑 14 单元测试 (Vault 6 + Identity 5 + DaemonClient 3)
./gradlew :app:assembleDebug
```

要求: Android Studio Hedgehog+ / Kotlin 2.0 / minSdk 26 / targetSdk 34.
依赖: AndroidX security-crypto (vault AES-GCM), BouncyCastle (PBKDF2), OkHttp (HTTP), Compose (UI).

## 跨平台对齐

| 模块 | iOS | Android | PC (权威) |
|---|---|---|---|
| Vault 加密 | CryptoKit ChaChaPoly (algo=0x01) | AES-GCM (algo=0x02) | libsodium SecretBox (algo=0x01) |
| BIP-39 PBKDF2 | CommonCrypto SHA-512 100k 轮 | BouncyCastle SHA-512 100k 轮 | hashlib SHA-512 100k 轮 |
| Subkey derive | HMAC-SHA256 | HMAC-SHA256 | HMAC-SHA256 |
| Daemon HTTP | URLSession | OkHttp | FastAPI |
| Sync 协议 | SyncRequest/SyncResponse JSON | 同 | 同 |

`algo` byte 不同是因为 Android AES 硬件加速比 ChaCha 快, iOS/PC 用 ChaCha 因 libsodium 默认.
Phase 5 加 AES↔ChaCha 兼容层 (vault file 同 key 跨平台读). 本 skeleton 各端独立 vault.

## TODO Phase 5 (skeleton 之外)
- BIP-39 全 2048 词 wordlist 嵌入 (本 skeleton 只验长度)
- iOS↔Android↔PC vault 跨格式互读
- LAN 自动发现 (Bonjour / mDNS NSD)
- TLS pin + 自签证书
