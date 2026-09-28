import DeckCore
import Foundation

enum ServiceError: Error {
    case http(Int, String)
}

/// Talks to the local TabDeck service (mkcert certificate is trusted via the system keychain).
final class ServiceClient {
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

    func voice(wav: Data, selected: String?, followup: Bool, compose: Bool, confirming: Bool, final: Bool, length: String) async throws -> VoiceResponse {
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

    func newSession(_ project: String, task: String? = nil, where place: String? = nil) async throws -> String {
        struct Created: Decodable { let id: String }
        let data = try await request("api/new_session", method: "POST",
                                     json: ["project": project, "task": task ?? "", "where": place ?? ""])  // "": on the hub
        return try JSONDecoder().decode(Created.self, from: data).id
    }
}
