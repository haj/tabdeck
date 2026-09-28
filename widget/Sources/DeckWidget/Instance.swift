import Foundation

/// Which TabDeck setup this widget belongs to. bundle.sh writes these keys into Info.plist
/// (from TABDECK_INSTANCE and that setup's settings.json), so one codebase builds "Jarvis", "ODS", ….
enum Instance {
    private static func value(_ key: String) -> String? {
        Bundle.main.object(forInfoDictionaryKey: key) as? String
    }
    /// The data folder under the home folder: ".tabdeck" or ".tabdeck-<instance>".
    static let dataDir = value("TabDeckDataDir") ?? ".tabdeck"
    /// The local agent's port (voice and hooks), e.g. 8765.
    static let port = value("TabDeckPort") ?? "8765"
    /// What the assistant is called, e.g. "Jarvis".
    static let assistant = value("TabDeckAssistant") ?? "Jarvis"
    /// How to address it, e.g. "Jarvis" or "Hey ODS".
    static let wakePhrase = value("TabDeckWakePhrase") ?? "Jarvis"

    static var settingsURL: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(dataDir + "/settings.json")
    }
    static var localURL: URL { URL(string: "https://localhost:\(port)/")! }
}
