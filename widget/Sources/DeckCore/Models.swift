import Foundation

public struct TabURL: Codable, Equatable {
    public let url: String
    public let href: String
}

public struct TabState: Codable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public let title: String
    public let cwd: String
    public let status: String
    public let since: Double
    public let seen: Bool
    public let message: String
    public let urls: [TabURL]

    public init(id: String, name: String, title: String, cwd: String, status: String, since: Double,
                seen: Bool, message: String, urls: [TabURL]) {
        self.id = id; self.name = name; self.title = title; self.cwd = cwd; self.status = status
        self.since = since; self.seen = seen; self.message = message; self.urls = urls
    }
}

public struct StateMessage: Codable {
    public let type: String
    public let iterm_connected: Bool?
    public let sessions: [TabState]?
    /// The tab currently selected in iTerm.
    public let active: String?
    /// The voice shared by the widget and the web page (chosen on the hub).
    public let tts_voice: String?
}

public struct Action: Codable, Equatable {
    public let type: String
    public var text: String?
    public var session_id: String?
    public var key: String?
    public var speak: String?
    public var project: String?
    public var index: Int?
    public var task: String?
    public var `where`: String?

    public init(type: String, text: String? = nil, session_id: String? = nil, key: String? = nil,
                speak: String? = nil, project: String? = nil, index: Int? = nil) {
        self.type = type; self.text = text; self.session_id = session_id; self.key = key
        self.speak = speak; self.project = project; self.index = index
    }
}

public struct VoiceResponse: Codable {
    public let text: String
    public let heard: Bool?
    public let action: Action?
    /// The speech sounded unfinished; keep listening and send it again together with what follows.
    public let incomplete: Bool?
}
