// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "DeckWidget",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "DeckCore"),
        .executableTarget(name: "DeckWidget", dependencies: ["DeckCore"]),
        .testTarget(name: "DeckCoreTests", dependencies: ["DeckCore"]),
    ]
)
