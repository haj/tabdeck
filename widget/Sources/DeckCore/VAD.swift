import Foundation

public enum VADEvent: Equatable {
    case started, ended, discarded
}

/// Energy-based voice activity detector over fixed 20 ms frames.
public struct VAD {
    public static let frameSamples = 320  // 20 ms at 16 kHz
    public var startFactor: Float = 3
    public var minFloor: Float = 0.002
    public var minSpeechFrames = 20      // 0.4 s
    /// Silence that ends a command: short, so "Hey Jarvis" is answered quickly. A command cut off
    /// mid-sentence is fine: the hub hears it trail off and the widget keeps listening.
    public static let commandEndSilenceFrames = 40      // 0.8 s
    /// Silence that ends a dictated message: people pause mid-sentence.
    public static let dictationEndSilenceFrames = 75    // 1.5 s
    public var endSilenceFrames = VAD.commandEndSilenceFrames
    public var maxFrames = 1500          // 30 s: room for real dictation

    public private(set) var floor: Float
    public private(set) var inSpeech = false
    private var speechFrames = 0
    /// Speech frames of the utterance that just ended or was discarded (for diagnostics).
    public private(set) var lastSpeechFrames = 0
    private var silenceFrames = 0
    private var totalFrames = 0
    private var rmsSum: Float = 0

    public init(initialFloor: Float = 0.005) {
        floor = initialFloor
    }

    var threshold: Float { max(floor, minFloor) * startFactor }

    public mutating func process(rms value: Float) -> VADEvent? {
        if !inSpeech {
            if value > threshold {
                inSpeech = true
                speechFrames = 1
                silenceFrames = 0
                totalFrames = 1
                rmsSum = value
                return .started
            }
            floor = floor * 0.95 + value * 0.05
            return nil
        }
        totalFrames += 1
        rmsSum += value
        if value > threshold * 0.6 {
            speechFrames += 1
            silenceFrames = 0
        } else {
            silenceFrames += 1
        }
        if totalFrames >= maxFrames {
            floor = rmsSum / Float(totalFrames)  // it was background noise, not speech
            reset()
            return .discarded
        }
        if silenceFrames >= endSilenceFrames {
            let long = speechFrames >= minSpeechFrames
            reset()
            return long ? .ended : .discarded
        }
        return nil
    }

    private mutating func reset() {
        lastSpeechFrames = speechFrames
        inSpeech = false
        speechFrames = 0
        silenceFrames = 0
        totalFrames = 0
        rmsSum = 0
    }
}

public func rms(_ x: ArraySlice<Float>) -> Float {
    guard !x.isEmpty else { return 0 }
    var sum: Float = 0
    for v in x { sum += v * v }
    return (sum / Float(x.count)).squareRoot()
}
