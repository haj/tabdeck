import XCTest
@testable import DeckCore

final class VADTests: XCTestCase {
    func feed(_ vad: inout VAD, _ value: Float, seconds: Double) -> [VADEvent] {
        var events: [VADEvent] = []
        for _ in 0..<Int((seconds / 0.02).rounded()) {
            if let e = vad.process(rms: value) { events.append(e) }
        }
        return events
    }

    func testSilenceProducesNothing() {
        var vad = VAD()
        XCTAssertEqual(feed(&vad, 0.001, seconds: 5), [])
        XCTAssertFalse(vad.inSpeech)
    }

    func testACommandEndsAfterAShortSilence() {
        var vad = VAD()
        _ = feed(&vad, 0.001, seconds: 1)
        XCTAssertEqual(feed(&vad, 0.1, seconds: 1), [.started])
        XCTAssertEqual(feed(&vad, 0.001, seconds: 0.7), [])
        XCTAssertEqual(feed(&vad, 0.001, seconds: 0.2), [.ended])  // ~0.8 s: "Hey Jarvis" isn't kept waiting
    }

    func testDictationAllowsLongerPauses() {
        var vad = VAD()
        vad.endSilenceFrames = VAD.dictationEndSilenceFrames
        _ = feed(&vad, 0.001, seconds: 1)
        XCTAssertEqual(feed(&vad, 0.1, seconds: 1), [.started])
        XCTAssertEqual(feed(&vad, 0.001, seconds: 1.4), [])   // a natural pause does not end the message
        XCTAssertEqual(feed(&vad, 0.001, seconds: 0.2), [.ended])
    }

    func testShortBlipIsDiscarded() {
        var vad = VAD()
        _ = feed(&vad, 0.001, seconds: 1)
        XCTAssertEqual(feed(&vad, 0.1, seconds: 0.1) + feed(&vad, 0.001, seconds: 2), [.started, .discarded])
    }

    func testOverlongUtteranceIsDiscarded() {
        var vad = VAD()
        _ = feed(&vad, 0.001, seconds: 1)
        // After the discard the floor becomes the run's average (0.1), so the same level no longer triggers.
        XCTAssertEqual(feed(&vad, 0.1, seconds: 31), [.started, .discarded])
    }

    func testLongDictationWithPausesStaysOneUtterance() {
        var vad = VAD()
        vad.endSilenceFrames = VAD.dictationEndSilenceFrames
        _ = feed(&vad, 0.001, seconds: 1)
        var events: [VADEvent] = []
        for _ in 0..<10 {
            events += feed(&vad, 0.1, seconds: 1.5) + feed(&vad, 0.001, seconds: 1.0)
        }
        XCTAssertEqual(events, [.started])
    }

    func testConstantNoiseStopsTriggeringAfterOneDiscard() {
        var vad = VAD()
        XCTAssertEqual(feed(&vad, 0.02, seconds: 31), [.started, .discarded])
        XCTAssertEqual(feed(&vad, 0.02, seconds: 20), [])
        XCTAssertEqual(feed(&vad, 0.3, seconds: 1), [.started])
    }

    func testRMS() {
        XCTAssertEqual(rms([0.5, -0.5, 0.5, -0.5][...]), 0.5, accuracy: 1e-6)
        XCTAssertEqual(rms([][...]), 0)
    }
}
