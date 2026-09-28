import Foundation

/// After a handled "Deck" command, speech is accepted without the wake word for `window` seconds.
public struct WakeSession {
    public var window: TimeInterval = 8
    public private(set) var until: Date?

    public init() {}

    public func followupActive(_ now: Date) -> Bool {
        guard let until else { return false }
        return now < until
    }

    public mutating func commandHandled(_ now: Date) {
        until = now.addingTimeInterval(window)
    }

    public mutating func end() {
        until = nil
    }
}
