import DeckCore
import Foundation

enum ServiceError: Error {
    case http(Int, String)

    /// What to show the user: the hub's list of refused settings, or its message.
    var message: String {
        guard case .http(let code, let body) = self else { return "" }
        if let json = try? JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: Any] {
            if let detail = json["detail"] as? [String: Any], let errors = detail["errors"] as? [String] {
                return errors.joined(separator: "\n")
            }
            if let detail = json["detail"] as? String { return detail }
        }
        return code == 0 ? "Not reachable" : "HTTP \(code)"
    }
}

/// Talks to the local TabDeck service (mkcert certificate is trusted via the system keychain).
/// Its settings (hub, token) change only on the main thread; URLSession itself is thread-safe.
final class ServiceClient: @unchecked Sendable {
    /// The TabDeck hub (state and actions). Defaults to this Mac for the old single-machine setup.
    var hub = Instance.localURL
    /// The local agent: speech recognition runs here, on the Mac.
    var voiceBase = Instance.localURL
    /// Bearer token for the hub (settings.json agent_token).
    var token: String?
    private let session = URLSession(configuration: .default)
    private var socket: URLSessionWebSocketTask?
    var onState: ((StateMessage) -> Void)?
    var onConnection: ((Bool) -> Void)?

    func connect() {
        var components = URLComponents(url: hub.appendingPathComponent("ws"), resolvingAgainstBaseURL: false)!
        components.scheme = "wss"
        var wsRequest = URLRequest(url: components.url!)
        if let token { wsRequest.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        let task = session.webSocketTask(with: wsRequest)
        socket = task
        task.resume()
        receive(task)
    }

    /// Connect again, e.g. after the hub address or token changed.
    func reconnect() {
        let old = socket
        socket = nil
        old?.cancel(with: .goingAway, reason: nil)
        connect()
    }

    private func receive(_ task: URLSessionWebSocketTask) {
        task.receive { [weak self] result in
            guard let self, task === self.socket else { return }
            switch result {
            case .success(let message):
                if case .string(let text) = message,
                   let m = try? JSONDecoder().decode(StateMessage.self, from: Data(text.utf8)),
                   m.type == "state" {
                    DispatchQueue.main.async {
                        self.onConnection?(true)
                        self.onState?(m)
                    }
                }
                self.receive(task)
            case .failure:
                DispatchQueue.main.async { self.onConnection?(false) }
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.connect() }
            }
        }
    }

    private func request(_ path: String, method: String = "GET", json: [String: Any]? = nil,
                         body: Data? = nil, contentType: String? = nil, local: Bool = false) async throws -> Data {
        var req = URLRequest(url: URL(string: path, relativeTo: local ? voiceBase : hub)!)
        req.httpMethod = method
        if !local, let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let json {
            req.httpBody = try JSONSerialization.data(withJSONObject: json)
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        } else if let body {
            req.httpBody = body
            req.setValue(contentType, forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await session.data(for: req)
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            throw ServiceError.http(code, String(data: data, encoding: .utf8) ?? "")
        }
        return data
    }

    func voice(wav: Data, selected: String?, followup: Bool, compose: Bool, confirming: Bool, final: Bool, length: String,
               hint: String) async throws -> VoiceResponse {
        let boundary = "deck-\(UUID().uuidString)"
        var body = Data()
        func field(_ name: String, _ value: String) {
            body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n".utf8))
        }
        field("selected", selected ?? "")
        field("wake", "1")
        field("followup", followup ? "1" : "0")
        field("compose", compose ? "1" : "0")
        field("confirming", confirming ? "1" : "0")
        field("final", final ? "1" : "0")
        field("length", length)
        field("hint", hint)  // the hub's current name, so Whisper expects the right wake word
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"speech.wav\"\r\nContent-Type: audio/wav\r\n\r\n".utf8))
        body.append(wav)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        let data = try await request("api/voice", method: "POST", body: body,
                                     contentType: "multipart/form-data; boundary=\(boundary)", local: true)
        return try JSONDecoder().decode(VoiceResponse.self, from: data)
    }

    func keys(_ id: String, _ key: String) async throws {
        _ = try await request("api/sessions/\(id)/keys", method: "POST", json: ["key": key])
    }

    func send(_ id: String, _ text: String) async throws {
        _ = try await request("api/sessions/\(id)/send", method: "POST", json: ["text": text])
    }

    func reply(_ id: String) async throws -> [String] {
        struct Reply: Decodable { let chunks: [String] }
        return try JSONDecoder().decode(Reply.self, from: try await request("api/sessions/\(id)/reply")).chunks
    }

    func setVoice(_ voice: String) async {
        _ = try? await request("api/voice-setting", method: "POST", json: ["voice": voice])
    }

    func focus(_ id: String) async {
        _ = try? await request("api/sessions/\(id)/focus", method: "POST")
    }

    func seen(_ id: String) async {
        _ = try? await request("api/sessions/\(id)/seen", method: "POST")
    }

    // MARK: settings (hub: shared by every device; local: this Mac's agent)

    private func json(_ data: Data) throws -> [String: Any] {
        (try JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    func settings(local: Bool = false) async throws -> [String: Any] {
        try json(await request(local ? "api/local-settings" : "api/settings", local: local))
    }

    func saveSettings(_ changes: [String: Any], local: Bool = false) async throws -> [String: Any] {
        try json(await request(local ? "api/local-settings" : "api/settings", method: "POST", json: changes, local: local))
    }

    func reloadSettings(local: Bool = false) async throws -> [String: Any] {
        try json(await request(local ? "api/local-settings/reload" : "api/settings/reload", method: "POST", local: local))
    }

    func testWake(_ text: String) async throws -> (matches: Bool, rest: String) {
        let r = try json(await request("api/settings/test-wake", method: "POST", json: ["text": text]))
        return (r["matches"] as? Bool ?? false, r["rest"] as? String ?? "")
    }

    /// A one-time pairing link for a phone or browser (the hub accepts this Mac agent's token for it).
    func pairingLink() async throws -> String {
        let r = try json(await request("api/pair", method: "POST"))
        guard let url = r["url"] as? String else { throw ServiceError.http(0, "no link") }
        return url
    }

    func unpairAll() async throws {
        _ = try await request("api/revoke", method: "POST")
    }

    /// English voices of the hub's voice server.
    func voices() async -> [String] {
        guard let data = try? await request("api/tts/voices"), let r = try? json(data) else { return [] }
        return r["voices"] as? [String] ?? []
    }

    /// One sentence in the hub's voice (its voice server and chosen voice), or nil if it is unavailable.
    func tts(_ text: String) async -> Data? {
        var components = URLComponents(url: hub.appendingPathComponent("api/tts"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "text", value: text)]
        // Synthesis time grows with length: allow ~3 s plus 1 s per 50 characters.
        var req = URLRequest(url: components.url!, timeoutInterval: 3 + Double(text.count) / 50)
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        do {
            let (data, resp) = try await session.data(for: req)
            guard (resp as? HTTPURLResponse)?.statusCode == 200, !data.isEmpty else {
                deckLog("hub voice: HTTP \((resp as? HTTPURLResponse)?.statusCode ?? 0)")
                return nil
            }
            return data
        } catch {
            deckLog("hub voice error: \(error.localizedDescription)")
            return nil
        }
    }

    func newSession(_ project: String, task: String? = nil, where place: String? = nil) async throws -> String {
        struct Created: Decodable { let id: String }
        let data = try await request("api/new_session", method: "POST",
                                     json: ["project": project, "task": task ?? "", "where": place ?? ""])  // "": on the hub
        return try JSONDecoder().decode(Created.self, from: data).id
    }
}
