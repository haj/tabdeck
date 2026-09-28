import Foundation

public enum Announcement: Equatable {
    case say(String)
    case readReply(id: String, prefix: String)
}

/// Same rules as the web page: announce a tab only when it newly becomes done / needs you and is unseen.
public struct Announcer {
    private var previous: [String: String] = [:]

    public init() {}

    /// Forget history, e.g. after a reconnect, so the next update only records.
    public mutating func reset() {
        previous = [:]
    }

    static func firstSentence(_ text: String) -> String {
        guard let end = text.firstIndex(where: { ".!?".contains($0) }) else { return text }
        return String(text[...end])
    }

    public mutating func update(_ sessions: [TabState], selected: String?) -> [Announcement] {
        var out: [Announcement] = []
        for s in sessions {
            defer { previous[s.id] = s.status }
            guard let before = previous[s.id], before != s.status, !s.seen,
                  s.status == "needs_you" || s.status == "done" else { continue }
            let permission = s.status == "needs_you" && s.message.hasPrefix("wants to ")
            if permission {
                out.append(.say("\(s.name) \(s.message). Approve?"))
            } else if s.id == selected {
                out.append(.say(s.status == "done" ? "\(s.name) is done. \(s.message)" : "\(s.name) needs you. \(s.message)"))
            } else if s.status == "done" {
                let headline = Self.firstSentence(s.message)
                out.append(.say(headline.isEmpty ? "\(s.name) is done." : "\(s.name) finished. \(headline)"))
            } else {
                out.append(.say("\(s.name) needs you."))
            }
        }
        return out
    }
}
