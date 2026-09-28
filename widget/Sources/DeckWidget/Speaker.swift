import AVFoundation
import DeckCore

/// Speaks with a Kokoro neural voice from the TTS server when configured, else with a macOS voice.
/// Reports when it starts and stops so the mic can be paused meanwhile.
final class Speaker: NSObject, AVSpeechSynthesizerDelegate, AVAudioPlayerDelegate {
    private let synth = AVSpeechSynthesizer()
    private var player: AVAudioPlayer?
    private var fetch: Task<Void, Never>?
    private var queue: [String] = []
    /// The next sentence's audio, fetched while the current one plays.
    private var prefetched: (text: String, task: Task<Data?, Never>)?
    /// When the neural voice last failed: skip it for a minute instead of timing out every sentence.
    private var neuralFailedAt: Date?
    var onSpeaking: ((Bool) -> Void)?
    var muted = false
    private(set) var voice: AVSpeechSynthesisVoice?
    private var wantedVoice: String?
    /// e.g. "http://<kokoro-host>:8880" and "af_heart" (settings.json: tts_url, tts_voice).
    var ttsURL: URL?
    var ttsVoice = "af_heart"

    /// English Kokoro voices offered by the server, for the menu.
    func availableVoices() async -> [String] {
        guard let ttsURL,
              let (data, _) = try? await URLSession.shared.data(from: ttsURL.appendingPathComponent("v1/audio/voices")),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let list = json["voices"] as? [Any] else { return [] }
        let ids = list.compactMap { ($0 as? String) ?? (($0 as? [String: Any])?["id"] as? String) }
        return ids.filter { ["af_", "am_", "bf_", "bm_"].contains(String($0.prefix(3))) && !$0.contains("_v0") }.sorted()
    }

    override init() {
        super.init()
        synth.delegate = self
    }

    /// Fallback macOS voice by name ("Daniel") or identifier; the best installed quality of that name wins.
    /// Without this, AVSpeechSynthesizer ignores the System Voice setting and uses its own default.
    func setVoice(_ wanted: String) {
        wantedVoice = wanted
        let all = AVSpeechSynthesisVoice.speechVoices()
        voice = AVSpeechSynthesisVoice(identifier: wanted)
            ?? all.filter { $0.name.caseInsensitiveCompare(wanted) == .orderedSame }
                  .max { $0.quality.rawValue < $1.quality.rawValue }
        deckLog("fallback voice: \(voice.map { "\($0.name) (\($0.identifier))" } ?? "system default, \(wanted) not found")")
    }

    func say(_ text: String, interrupt: Bool = false) {
        guard !muted, !text.isEmpty else { return }
        if interrupt { halt() }
        queue += splitSentences(text)
        if player == nil && fetch == nil && !synth.isSpeaking { next() }
    }

    func stop() {
        halt()
        onSpeaking?(false)
    }

    /// Stop without reporting "done": used when new speech replaces the current one, so the mic
    /// is never unpaused between the two.
    private func halt() {
        queue.removeAll()
        fetch?.cancel()
        fetch = nil
        player?.stop()
        player = nil
        prefetched?.task.cancel()
        prefetched = nil
        synth.delegate = nil
        synth.stopSpeaking(at: .immediate)
        synth.delegate = self
    }

    private func next() {
        guard !queue.isEmpty else {
            onSpeaking?(false)
            return
        }
        let text = queue.removeFirst()
        onSpeaking?(true)
        let neuralDown = neuralFailedAt.map { Date().timeIntervalSince($0) < 60 } ?? false
        guard let ttsURL, !neuralDown else { return sayLocally(text) }
        let audioTask = audioTask(for: text, url: ttsURL)  // on the main thread: touches `prefetched`
        fetch = Task { [weak self] in
            let audio = await audioTask.value
            await MainActor.run {
                guard let self, !Task.isCancelled else { return }
                self.fetch = nil
                if let audio, let p = try? AVAudioPlayer(data: audio) {
                    self.neuralFailedAt = nil
                    deckLog("neural voice \(self.ttsVoice): \(audio.count / 1024) KB")
                    p.delegate = self
                    self.player = p
                    p.play()
                    self.prefetchNext()
                } else {
                    deckLog("neural voice unavailable, using fallback voice for a minute")
                    self.neuralFailedAt = Date()
                    self.prefetched?.task.cancel()
                    self.prefetched = nil
                    self.sayLocally(text)
                }
            }
        }
    }

    private func audioTask(for text: String, url: URL) -> Task<Data?, Never> {
        if let p = prefetched, p.text == text {
            prefetched = nil
            return p.task
        }
        let voice = ttsVoice
        return Task { await Self.synthesize(text, url: url, voice: voice) }
    }

    /// Fetch the next sentence while the current one plays.
    private func prefetchNext() {
        guard neuralFailedAt.map({ Date().timeIntervalSince($0) >= 60 }) ?? true, let url = ttsURL, let text = queue.first, prefetched?.text != text else { return }
        let voice = ttsVoice
        prefetched = (text, Task { await Self.synthesize(text, url: url, voice: voice) })
    }

    private static func synthesize(_ text: String, url: URL, voice: String) async -> Data? {
        // CPU synthesis time grows with length: allow ~2 s plus 1 s per 50 characters.
        var req = URLRequest(url: url.appendingPathComponent("v1/audio/speech"),
                             timeoutInterval: 2 + Double(text.count) / 50)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "model": "kokoro", "input": text, "voice": voice, "response_format": "mp3"])
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard (resp as? HTTPURLResponse)?.statusCode == 200, !data.isEmpty else {
                deckLog("neural voice: HTTP \((resp as? HTTPURLResponse)?.statusCode ?? 0)")
                return nil
            }
            return data
        } catch {
            deckLog("neural voice error: \(error.localizedDescription)")
            return nil
        }
    }

    private func sayLocally(_ text: String) {
        if voice == nil, let wanted = wantedVoice {
            setVoice(wanted)  // the voice catalog can load after launch
        }
        let u = AVSpeechUtterance(string: text)
        u.voice = voice
        u.rate = 0.52
        synth.speak(u)
    }

    func audioPlayerDidFinishPlaying(_ p: AVAudioPlayer, successfully flag: Bool) {
        DispatchQueue.main.async {
            guard p === self.player else { return }
            self.player = nil
            self.next()
        }
    }

    func speechSynthesizer(_ s: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        DispatchQueue.main.async { if !self.synth.isSpeaking { self.next() } }
    }
}
