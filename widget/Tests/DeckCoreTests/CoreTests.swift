import XCTest
@testable import DeckCore

final class CoreTests: XCTestCase {
    func testWakeSessionWindow() {
        var w = WakeSession()
        let t0 = Date(timeIntervalSince1970: 1000)
        XCTAssertFalse(w.followupActive(t0))
        w.commandHandled(t0)
        XCTAssertTrue(w.followupActive(t0.addingTimeInterval(7.9)))
        XCTAssertFalse(w.followupActive(t0.addingTimeInterval(8.1)))
        w.commandHandled(t0.addingTimeInterval(5))
        XCTAssertTrue(w.followupActive(t0.addingTimeInterval(12)))
        w.end()
        XCTAssertFalse(w.followupActive(t0.addingTimeInterval(6)))
    }

    func testWAVHeaderAndSamples() {
        let d = wavData(samples: [1.0, -1.0, 0])
        XCTAssertEqual(d.count, 44 + 6)
        XCTAssertEqual(String(data: d[0..<4], encoding: .ascii), "RIFF")
        XCTAssertEqual(String(data: d[8..<12], encoding: .ascii), "WAVE")
        let rate = d[24..<28].withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) }
        XCTAssertEqual(UInt32(littleEndian: rate), 16000)
        let first = d[44..<46].withUnsafeBytes { $0.loadUnaligned(as: Int16.self) }
        XCTAssertEqual(Int16(littleEndian: first), 32767)
    }

    let stateJSON = """
    {"type":"state","iterm_connected":true,"is_local":true,"sessions":[
      {"id":"A","name":"Atlas","title":"claude","cwd":"/p/Atlas","status":"needs_you","since":1.5,
       "seen":false,"message":"Permission for Bash","urls":[{"url":"http://localhost:5173/","href":"http://localhost:5173/"}]}]}
    """

    func testDecodeState() throws {
        let m = try JSONDecoder().decode(StateMessage.self, from: Data(stateJSON.utf8))
        XCTAssertEqual(m.sessions?.first?.name, "Atlas")
        XCTAssertEqual(m.sessions?.first?.urls.first?.url, "http://localhost:5173/")
        XCTAssertNil(m.active)
        let withActive = try JSONDecoder().decode(StateMessage.self, from: Data(#"{"type":"state","active":"A","sessions":[]}"#.utf8))
        XCTAssertEqual(withActive.active, "A")
    }

    func testDecodeVoiceResponse() throws {
        let heard = try JSONDecoder().decode(VoiceResponse.self, from: Data(
            #"{"text":"approve","heard":true,"action":{"type":"keys","session_id":"A","key":"enter","speak":"Approved."}}"#.utf8))
        XCTAssertEqual(heard.action, Action(type: "keys", session_id: "A", key: "enter", speak: "Approved."))
        let ignored = try JSONDecoder().decode(VoiceResponse.self, from: Data(#"{"text":"","heard":false,"action":null}"#.utf8))
        XCTAssertEqual(ignored.heard, false)
        XCTAssertNil(ignored.action)
        let held = try JSONDecoder().decode(VoiceResponse.self, from: Data(
            #"{"text":"","heard":true,"action":null,"incomplete":true}"#.utf8))
        XCTAssertEqual(held.incomplete, true)
        XCTAssertNil(ignored.incomplete)
    }

    func tab(_ id: String, _ status: String, seen: Bool = false, name: String? = nil, message: String = "msg") -> TabState {
        TabState(id: id, name: name ?? id, title: "", cwd: "", status: status, since: 0, seen: seen,
                 message: message, urls: [])
    }

    func testAnnouncerOnlyOnTransitionsToAttention() {
        var a = Announcer()
        XCTAssertEqual(a.update([tab("A", "working"), tab("B", "working")], selected: "A"), [])
        XCTAssertEqual(a.update([tab("A", "done"), tab("B", "needs_you")], selected: "A"),
                       [.say("A is done. msg"), .say("B needs you.")])
        XCTAssertEqual(a.update([tab("A", "done"), tab("B", "needs_you")], selected: "A"), [])
    }

    func testAnnouncerSkipsSeenAndAfterReset() {
        var a = Announcer()
        _ = a.update([tab("A", "working")], selected: nil)
        XCTAssertEqual(a.update([tab("A", "done", seen: true)], selected: nil), [])
        a.reset()
        XCTAssertEqual(a.update([tab("A", "needs_you")], selected: nil), [])
    }

    func testSummariesAndPermissionDetails() {
        var a = Announcer()
        _ = a.update([tab("F", "working", name: "Atlas"), tab("S", "working", name: "Orbit")], selected: "F")
        let out = a.update([
            tab("F", "needs_you", name: "Atlas", message: "wants to run: npm install stripe"),
            tab("S", "done", name: "Orbit", message: "Added the login page. Tests pass."),
        ], selected: "F")
        XCTAssertEqual(out, [.say("Atlas wants to run: npm install stripe. Approve?"),
                             .say("Orbit finished. Added the login page.")])
    }

    func testSelectedNeedsYouSaysMessage() {
        var a = Announcer()
        _ = a.update([tab("A", "working", name: "Atlas")], selected: "A")
        XCTAssertEqual(a.update([tab("A", "needs_you", name: "Atlas")], selected: "A"),
                       [.say("Atlas needs you. msg")])
    }

    func testSplitSentences() {
        XCTAssertEqual(splitSentences("Atlas is done. It added the login page! Tests pass? Yes."),
                       ["Atlas is done.", "It added the login page!", "Tests pass? Yes."])
        XCTAssertEqual(splitSentences("No punctuation here"), ["No punctuation here"])
        XCTAssertEqual(splitSentences("  "), [])
        XCTAssertEqual(splitSentences("Ok. Sent."), ["Ok. Sent."])
        XCTAssertEqual(splitSentences("I updated package.json and bumped it to v2.0 today. Tests pass now."),
                       ["I updated package.json and bumped it to v2.0 today.", "Tests pass now."])
    }

    func testActionDecodesWhere() throws {
        let a = try JSONDecoder().decode(Action.self, from: Data(#"{"type":"new_session","project":"Atlas","where":"mac"}"#.utf8))
        XCTAssertEqual(a.where, "mac")
    }

    func testStateCarriesSharedVoice() throws {
        let m = try JSONDecoder().decode(StateMessage.self, from: Data(#"{"type":"state","tts_voice":"af_heart","sessions":[]}"#.utf8))
        XCTAssertEqual(m.tts_voice, "af_heart")
    }
}
