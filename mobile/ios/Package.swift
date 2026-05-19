// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Sisoul",
    platforms: [
        .iOS(.v16),
        .macOS(.v13)
    ],
    products: [
        .library(name: "SisoulCore", targets: ["SisoulCore"]),
        .executable(name: "SisoulApp", targets: ["SisoulApp"])
    ],
    dependencies: [],
    targets: [
        .target(
            name: "SisoulCore",
            dependencies: [],
            path: "Sources/SisoulCore"
        ),
        .executableTarget(
            name: "SisoulApp",
            dependencies: ["SisoulCore"],
            path: "Sources/SisoulApp"
        ),
        .testTarget(
            name: "SisoulCoreTests",
            dependencies: ["SisoulCore"],
            path: "Tests/SisoulCoreTests"
        )
    ]
)
