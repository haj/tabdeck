import Foundation

/// Split text into sentences for sentence-by-sentence speech. A piece shorter than
/// 12 characters is joined with what follows, so there are no choppy one-word clips.
public func splitSentences(_ text: String) -> [String] {
    var raw: [String] = []
    var current = ""
    let chars = Array(text)
    for (i, ch) in chars.enumerated() {
        current.append(ch)
        // A sentence ends at . ! ? followed by whitespace or the end ("package.json" and "v2.0" stay whole).
        if ".!?".contains(ch) && (i + 1 == chars.count || chars[i + 1].isWhitespace) {
            raw.append(current)
            current = ""
        }
    }
    raw.append(current)
    var out: [String] = []
    var buffer = ""
    for piece in raw {
        let p = piece.trimmingCharacters(in: .whitespacesAndNewlines)
        if p.isEmpty { continue }
        buffer = buffer.isEmpty ? p : buffer + " " + p
        if buffer.count >= 12 {
            out.append(buffer)
            buffer = ""
        }
    }
    if !buffer.isEmpty { out.append(buffer) }
    return out
}
