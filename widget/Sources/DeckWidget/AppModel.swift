import AppKit
import AVFoundation
import DeckCore

enum Light {
    case listening, active, speaking, paused, offline
}

func deckLog(_ message: String) {
    FileHandle.standardError.write(Data("[\(Date())] \(message)\n".utf8))
}

@MainActor
final class AppModel: ObservableObject {
    struct Pending: Equatable {
        let sessionId: String
        let name: String
        var text: String
    }

    @Published var sessions: [TabState] = []
    @Published var selected: String? = UserDefaults.standard.string(forKey: "selected")
    @Published var line = "Say “\(Instance.wakePhrase), status”"
    @Published var online = false
    @Published var itermOK = true
    @Published var speaking = false
    @Published var busy = false
    @Published var micPaused = false
    @Published var micDenied = false
    @Published var pending: Pending?
    @Published var expanded = false
    @Published private var followupTick = 0
    @Published var answerLength = AppModel.setting("answer_length") ?? "normal"
    @Published var voices: [String] = []
    /// Tucked into the menu bar (like Siri): still listening and speaking, just not on screen.
    @Published private(set) var widgetHidden = UserDefaults.standard.bool(forKey: "widgetHidden")
    var onWidgetHidden: ((Bool) -> Void)?

    func setWidgetHidden(_ hidden: Bool) {
        widgetHidden = hidden
        UserDefaults.standard.set(hidden, forKey: "widgetHidden")
        onWidgetHidden?(hidden)
    }
    /// The assistant's name and wake phrase: from the hub's live settings (the build's values until it answers).
    @Published var assistantName = Instance.assistant
    @Published var wakePhrase = Instance.wakePhrase
    var idleLine: String { "Say “\(wakePhrase), status”" }
    /// The hint Whisper gets: 'Friday ("Hey Friday")', or just 'Jarvis'.
    var nameHint: String { wakePhrase.caseInsensitiveCompare(assistantName) == .orderedSame ? assistantName : "\(assistantName) (\"\(wakePhrase)\")" }

    let client = ServiceClient()
    let speaker = Speaker()
    let capture = AudioCapture()
    private var wake = WakeSession()
    private var announcer = Announcer()
    private var offlineNotice = OfflineNotice()  // "can't reach the server", at most every 20 s
    private var chunks: [String: [String]] = [:]
    private var readIdx: [String: Int] = [:]
    private var pendingTask: Task<Void, Never>?
    private var pendingPaused = false
    private var selectAfter: String?
    private var lastActive: String?
    /// Follow-up state captured when speech *starts*: a long sentence begun inside the window still counts.
    private var followupAtStart = false
    /// Jarvis asked "What should I send?" and the next utterance is the message.
    private var composing = false { didSet { capture.dictation = composing } }
    /// Jarvis asked "Did you mean ...?" about this message.
    private var clarifying: Action?
    /// Unfinished speech waiting for the rest of the sentence.
    private var held: [Float] = []
    private var heldTask: Task<Void, Never>?
    private static let holdSeconds = 3.0
    private static let maxSamples = 16000 * 30
    private static let pendingSeconds = 2.5

    var needsYou: Int { sessions.filter { $0.status == "needs_you" }.count }

    var light: Light {
        _ = followupTick
        if micDenied || micPaused { return .paused }
        if !online { return .offline }
        if speaking { return .speaking }
        if busy || !held.isEmpty || wake.followupActive(Date()) { return .active }
        return .listening
    }

    // MARK: startup

    /// the setup's settings.json (Instance.settingsURL), shared with the service ("voice": name or identifier).
    static func setting(_ key: String) -> String? {
        let url = Instance.settingsURL
        guard let data = try? Data(contentsOf: url),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return json[key] as? String
    }

    /// Write one key to the setup's settings.json (Instance.settingsURL), keeping the others.
    static func saveSetting(_ key: String, _ value: String) {
        let url = Instance.settingsURL
        var json = ((try? Data(contentsOf: url)).flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]) ?? [:]
        json[key] = value
        if let data = try? JSONSerialization.data(withJSONObject: json, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: url)
        }
    }

    func chooseVoice(_ v: String) {
        speaker.ttsVoice = v
        Task { await client.setVoice(v) }  // kept on the hub: the web page and phone use it too
        say("Hello, this is my new voice.")
    }

    func chooseAnswerLength(_ l: String) {
        answerLength = l
        Self.saveSetting("answer_length", l)
    }

    /// This Mac's settings.json: the hub to use, its token, and the fallback macOS voice. Returns whether the hub changed.
    @discardableResult
    func loadMacSettings() -> Bool {
        speaker.setVoice(Self.setting("voice") ?? "Daniel")
        let before = (client.hub, client.token)
        if let h = Self.setting("hub_url"), let u = URL(string: h.hasSuffix("/") ? h : h + "/") { client.hub = u }
        client.token = Self.setting("agent_token")
        deckLog("hub: \(client.hub.absoluteString)")
        return before.0 != client.hub || before.1 != client.token
    }

    /// Re-read every setting: this Mac's settings.json, the Mac agent's and the hub's (e.g. after editing files).
    func reloadConfig() {
        Task {
            if loadMacSettings() { client.reconnect() }
            var notes: [String] = []
            for local in [false, true] {
                do {
                    let r = try await client.reloadSettings(local: local)
                    notes += r["restart_needed"] as? [String] ?? []
                } catch {
                    notes.append("\(local ? "Mac agent" : "hub") not reachable")
                }
            }
            voices = await client.voices()
            deckLog("config reloaded\(notes.isEmpty ? "" : "; " + notes.joined(separator: ", "))")
            say(notes.isEmpty ? "Settings reloaded." : "Settings reloaded. Still needed: \(notes.joined(separator: ", ")).")
        }
    }

    func openSettings() {
        SettingsWindow.show(model: self)
    }

    func openHelp() {
        HelpWindow.show(model: self)
    }

    func start() {
        loadMacSettings()
        let client = self.client
        speaker.neural = { await client.tts($0) }  // the hub's voice server and voice
        Task { voices = await client.voices() }
        client.onState = { [weak self] m in self?.apply(m) }
        client.onConnection = { [weak self] ok in
            guard let self else { return }
            if !ok && self.online { self.announcer.reset() }
            if ok { self.offlineNotice.reset() }
            self.online = ok
        }
        client.connect()

        speaker.onSpeaking = { [weak self] on in
            guard let self else { return }
            deckLog("speaking: \(on)")
            self.speaking = on
            if on {
                self.capture.paused = true
            } else {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                    if !self.speaking && !self.micPaused { self.capture.paused = false }
                }
                // The window should start when Jarvis stops talking, not when it started answering.
                if self.wake.followupActive(Date()) || self.clarifying != nil || self.composing {
                    self.wake.commandHandled(Date())
                    self.refreshFollowupLight()
                }
            }
        }
        capture.onSpeechStart = { [weak self] in
            guard let self else { return }
            deckLog("speech started")
            self.followupAtStart = self.wake.followupActive(Date()) || self.pending != nil || !self.held.isEmpty
            self.heldTask?.cancel()
            self.pausePending()
        }
        capture.onDiscard = { [weak self] why in deckLog("utterance discarded (\(why))"); self?.resumePending() }
        capture.onUtterance = { [weak self] samples in
            guard let self else { return }
            let joined = self.held + samples
            self.held = []
            Task { await self.handleUtterance(joined, final: joined.count >= Self.maxSamples) }
        }

        AVCaptureDevice.requestAccess(for: .audio) { granted in
            DispatchQueue.main.async {
                guard granted else {
                    self.micDenied = true
                    deckLog("microphone access denied")
                    return
                }
                do {
                    try self.capture.start()
                    deckLog("listening")
                } catch {
                    self.micDenied = true
                    deckLog("audio engine failed: \(error)")
                }
            }
        }
    }

    // MARK: state from the service

    private func apply(_ m: StateMessage) {
        let old = Dictionary(uniqueKeysWithValues: sessions.map { ($0.id, $0.status) })
        sessions = m.sessions ?? []
        itermOK = m.iterm_connected ?? true
        if let v = m.tts_voice, v != speaker.ttsVoice {
            speaker.ttsVoice = v  // one voice everywhere: the hub decides
            deckLog("voice from hub: \(v)")
        }
        if let n = m.assistant_name, let w = m.wake_phrase, (n, w) != (assistantName, wakePhrase) {
            let wasIdle = line == idleLine
            assistantName = n
            wakePhrase = w
            if wasIdle { line = idleLine }
            deckLog("assistant from hub: \(n) (\(w))")
        }
        for s in sessions where old[s.id] != nil && old[s.id] != s.status {
            chunks[s.id] = nil
        }
        for a in announcer.update(sessions, selected: selected) {
            switch a {
            case .say(let text):
                speaker.say(text)
            case .readReply(let id, let prefix):
                Task { await read(id, 0, prefix: prefix) }
            }
        }
        if let sel = selected, !sessions.contains(where: { $0.id == sel }) {
            if pending?.sessionId == sel { cancelPending("That tab is gone. Nothing was sent.") }
            select(nil)
        }
        // Follow iTerm: the tab you click there becomes Jarvis's current tab.
        if m.active != lastActive {
            lastActive = m.active
            if let a = m.active, a != selected { select(a) }
        }
        if let id = selectAfter, sessions.contains(where: { $0.id == id }) {
            selectAfter = nil
            select(id)
        }
    }

    // MARK: voice

    private func holdForMore(_ samples: [Float]) {
        held = samples
        followupTick += 1
        heldTask?.cancel()
        heldTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Self.holdSeconds * 1_000_000_000))
            guard !Task.isCancelled, let self, !self.held.isEmpty else { return }
            let samples = self.held
            self.held = []
            self.heldTask = nil
            await self.handleUtterance(samples, final: true)
        }
    }

    private func handleUtterance(_ samples: [Float], final: Bool = false) async {
        let followup = followupAtStart || wake.followupActive(Date()) || pending != nil
        let composeNow = composing && followup
        let confirmingNow = clarifying != nil && followup
        followupAtStart = false
        busy = true
        defer { busy = false }
        do {
            let r = try await client.voice(wav: wavData(samples: samples), selected: selected, followup: followup,
                                           compose: composeNow, confirming: confirmingNow, final: final,
                                           length: answerLength, hint: nameHint)
            if r.offline == true {
                deckLog("hub unreachable (heard: \(r.heard == true))")
                if r.heard == true { unreachable("I can't reach the TabDeck server. Check your network.") }
                resumePending()
                return
            }
            if r.incomplete == true && !final {
                deckLog("sounds unfinished, listening for more")
                holdForMore(samples)
                return
            }
            guard r.heard != false, let action = r.action else {
                deckLog("utterance \(String(format: "%.1f", Double(samples.count) / 16000))s ignored (no wake word)")
                resumePending()
                return
            }
            deckLog("heard: \(r.text) -> \(action.type)")
            composing = false
            let asked = clarifying
            clarifying = nil
            line = "“\(r.text)”"
            wake.commandHandled(Date())
            refreshFollowupLight()
            if action.type == "confirmed", let asked {
                await run(Action(type: "reply", text: asked.text, session_id: asked.session_id,
                                 speak: "Sending to \(name(asked.session_id ?? ""))."))
            } else {
                await run(action)
            }
        } catch is URLError {
            deckLog("voice failed: the TabDeck agent on this Mac isn't answering")
            unreachable("I can't reach TabDeck on this Mac. Is the agent running?")
            resumePending()
        } catch {
            deckLog("voice failed: \(error)")
            say("Sorry, I didn't get that.")
            resumePending()
        }
    }

    /// Say why nothing happens, but not on every utterance while the link is down.
    private func unreachable(_ message: String) {
        line = message
        if offlineNotice.shouldSpeak() { say(message) }
    }

    private func refreshFollowupLight() {
        followupTick += 1
        DispatchQueue.main.asyncAfter(deadline: .now() + wake.window + 0.1) { self.followupTick += 1 }
    }

    // MARK: actions

    /// Actions about a specific tab also switch iTerm to that tab and bring iTerm to the front.
    static let focusingActions: Set<String> = ["select", "keys", "read", "read_more", "reply", "compose", "clarify"]

    func run(_ a: Action) async {
        if let id = a.session_id, Self.focusingActions.contains(a.type) {
            Task { await client.focus(id) }
        }
        switch a.type {
        case "speak", "ask":
            say(a.text ?? "")
        case "select":
            if let id = a.session_id { select(id) }
            say(a.speak ?? "")
        case "keys":
            if let id = a.session_id, let key = a.key { await sendKey(id, key) }
            say(a.speak ?? "")
        case "reply":
            startPending(a)
        case "clarify":
            if let id = a.session_id { select(id) }
            clarifying = a
            say(a.speak ?? "Did you mean: \(a.text ?? "")?")
        case "confirmed":
            say("Nothing to confirm.")
        case "compose":
            if let id = a.session_id { select(id) }
            composing = true
            say(a.speak ?? "What should I send?")
        case "read":
            if let id = a.session_id { await read(id, 0) }
        case "read_more":
            if let id = a.session_id { await read(id, (readIdx[id] ?? -1) + 1) }
        case "open_url":
            openURL(a.session_id, a.index ?? 0)
        case "new_session":
            if let p = a.project { await newSession(p, task: a.task, where: a.where, speak: a.speak) }
        case "cancel":
            if pending != nil { cancelPending("Cancelled.") } else { say("Nothing to cancel.") }
        case "send":
            if pending != nil { await commitPending() } else { say("Nothing to send.") }
        default:
            say("I'm not sure what to do.")
        }
        if !["reply", "cancel", "send"].contains(a.type) { resumePending() }
    }

    func say(_ text: String) {
        guard !text.isEmpty else { return }
        line = text
        speaker.say(text, interrupt: true)
    }

    func select(_ id: String?) {
        selected = id
        UserDefaults.standard.set(id, forKey: "selected")
        if let id { Task { await client.seen(id) } }
    }

    /// Clicked in the widget's tab list.
    func pick(_ id: String) {
        select(id)
        Task { await client.focus(id) }
    }

    private func name(_ id: String) -> String {
        sessions.first { $0.id == id }?.name ?? "that tab"
    }

    private func sendKey(_ id: String, _ key: String) async {
        do {
            try await client.keys(id, key)
        } catch ServiceError.http(404, _) {
            say("That tab is gone.")
        } catch {
            say("Could not send that key.")
        }
    }

    private func read(_ id: String, _ i: Int, prefix: String = "") async {
        // Start of a read: always fetch, a cached reply can be from an earlier turn.
        if i == 0 || chunks[id] == nil {
            chunks[id] = (try? await client.reply(id)) ?? []
        }
        let list = chunks[id] ?? []
        if i >= list.count {
            say("\(prefix) \(list.isEmpty ? "There is no reply to read." : "That's the end.")"
                .trimmingCharacters(in: .whitespaces))
            return
        }
        readIdx[id] = i
        say("\(prefix) \(list[i])".trimmingCharacters(in: .whitespaces))
    }

    private func openURL(_ id: String?, _ index: Int) {
        guard let id, let s = sessions.first(where: { $0.id == id }), index < s.urls.count,
              let url = URL(string: s.urls[index].url) else {
            say("No app URL for this tab.")
            return
        }
        NSWorkspace.shared.open(url)
        say("Opening \(s.name).")
    }

    private func newSession(_ project: String, task: String? = nil, where place: String? = nil,
                            speak: String? = nil) async {
        do {
            selectAfter = try await client.newSession(project, task: task, where: place)
            say(speak ?? "Starting Claude in \(project).")
        } catch ServiceError.http(502, let body) {
            let detail = (try? JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: Any])?["detail"] as? String
            say(detail ?? "Could not start a session in \(project).")
        } catch {
            say("Could not start a session in \(project).")
        }
    }

    func openWebPage() {
        NSWorkspace.shared.open(client.hub)
    }

    func togglePause() {
        micPaused.toggle()
        capture.paused = micPaused
        line = micPaused ? "Mic paused" : idleLine
    }

    // MARK: pending reply

    private func startPending(_ a: Action) {
        cancelPending(nil)
        guard let id = a.session_id, sessions.contains(where: { $0.id == id }) else {
            say("That tab is gone.")
            return
        }
        pending = Pending(sessionId: id, name: name(id), text: a.text ?? "")
        say(a.speak ?? "Sending.")
        runCountdown()
    }

    private func runCountdown() {
        pendingTask?.cancel()
        pendingPaused = false
        pendingTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Self.pendingSeconds * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            // Drop our own handle first: commitPending cancels pendingTask, which would otherwise
            // cancel this very task and its in-flight send (URLSession error -999).
            self.pendingTask = nil
            await self.commitPending()
        }
    }

    private func pausePending() {
        guard pending != nil else { return }
        pendingTask?.cancel()
        pendingPaused = true
    }

    private func resumePending() {
        if pending != nil && pendingPaused { runCountdown() }
    }

    func commitPending() async {
        guard let p = pending else { return }
        pending = nil
        pendingTask?.cancel()
        let text = p.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        do {
            try await client.send(p.sessionId, text)
            deckLog("sent to \(p.name)")
            say("Sent.")
        } catch ServiceError.http(404, _) {
            deckLog("send failed: tab gone")
            say("That tab is gone. Nothing was sent.")
        } catch ServiceError.http(409, _) {
            deckLog("send refused: no Claude running in \(p.name)")
            say("Claude isn't running in \(p.name), so I didn't type anything.")
        } catch {
            deckLog("send failed: \(error)")
            say("Sending failed.")
        }
    }

    func cancelPending(_ message: String?) {
        guard pending != nil else { return }
        pending = nil
        pendingTask?.cancel()
        if let message { say(message) }
    }
}
