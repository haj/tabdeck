import AVFoundation
import DeckCore

/// Mic → 16 kHz mono → VAD → whole utterances (with 0.3 s pre-roll).
final class AudioCapture {
    var onUtterance: (([Float]) -> Void)?
    var onSpeechStart: (() -> Void)?
    var onDiscard: ((String) -> Void)?

    private var engine = AVAudioEngine()
    private var lastBuffer = Date()
    private var watchdog: Timer?
    private let target = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1,
                                       interleaved: false)!
    private let queue = DispatchQueue(label: "deck.audio")
    private var converter: AVAudioConverter?
    private var vad = VAD()
    private var pending: [Float] = []
    private var preroll: [Float] = []
    private var utterance: [Float] = []
    private let lock = NSLock()
    private var _paused = false
    private var _dictation = false

    /// While dictating a message, allow longer pauses before the utterance ends.
    var dictation: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _dictation }
        set { lock.lock(); _dictation = newValue; lock.unlock() }
    }

    var paused: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _paused }
        set { lock.lock(); _paused = newValue; lock.unlock() }
    }

    private var observer: NSObjectProtocol?

    func start() throws {
        try startEngine()
        // Watchdog: if the mic delivers nothing for 10 s (while not paused), rebuild the engine.
        watchdog = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            guard let self, !self.paused, Date().timeIntervalSince(self.lastBuffer) > 10 else { return }
            self.rebuild(reason: "no audio for 10 s")
        }
    }

    /// A restarted engine can come back silent after a device change (headset in/out);
    /// a brand-new AVAudioEngine does not.
    private func rebuild(reason: String) {
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        engine = AVAudioEngine()
        do {
            try startEngine()
            deckLog("\(reason), audio engine rebuilt")
        } catch {
            deckLog("audio rebuild failed: \(error)")
        }
    }

    private func startEngine() throws {
        if let observer { NotificationCenter.default.removeObserver(observer) }
        observer = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main) { [weak self] _ in
            self?.rebuild(reason: "audio devices changed")
        }
        lastBuffer = Date()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        converter = AVAudioConverter(from: format, to: target)
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, _ in
            self?.queue.async { self?.handle(buffer) }
            DispatchQueue.main.async { self?.lastBuffer = Date() }
        }
        engine.prepare()
        try engine.start()
    }

    private func handle(_ buffer: AVAudioPCMBuffer) {
        guard let converter else { return }
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * target.sampleRate / buffer.format.sampleRate) + 32
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return }
        var fed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if fed {
                status.pointee = .noDataNow
                return nil
            }
            fed = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, let channel = out.floatChannelData else { return }
        if paused {
            vad = VAD(initialFloor: vad.floor)
            utterance = []
            pending = []
            return
        }
        pending += UnsafeBufferPointer(start: channel[0], count: Int(out.frameLength))
        let n = VAD.frameSamples
        vad.endSilenceFrames = dictation ? VAD.dictationEndSilenceFrames : VAD.commandEndSilenceFrames
        while pending.count >= n {
            let frame = Array(pending[0..<n])
            pending.removeFirst(n)
            process(frame)
        }
    }

    private func process(_ frame: [Float]) {
        switch vad.process(rms: rms(frame[...])) {
        case .started?:
            utterance = preroll + frame
            DispatchQueue.main.async { self.onSpeechStart?() }
        case .ended?:
            utterance += frame
            let samples = utterance
            utterance = []
            DispatchQueue.main.async { self.onUtterance?(samples) }
        case .discarded?:
            let why = String(format: "speech %.2fs of %.1fs, noise floor %.4f",
                             Double(vad.lastSpeechFrames) * 0.02, Double(utterance.count) / 16000, vad.floor)
            utterance = []
            DispatchQueue.main.async { self.onDiscard?(why) }
        case nil:
            if vad.inSpeech {
                utterance += frame
            } else {
                preroll = Array((preroll + frame).suffix(VAD.frameSamples * 15))
            }
        }
    }
}
