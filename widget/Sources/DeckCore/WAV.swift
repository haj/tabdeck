import Foundation

/// Mono 16-bit PCM WAV.
public func wavData(samples: [Float], sampleRate: Int = 16000) -> Data {
    var d = Data()
    func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
    func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
    let dataSize = UInt32(samples.count * 2)
    d.append(contentsOf: Array("RIFF".utf8)); u32(36 + dataSize); d.append(contentsOf: Array("WAVE".utf8))
    d.append(contentsOf: Array("fmt ".utf8)); u32(16); u16(1); u16(1)
    u32(UInt32(sampleRate)); u32(UInt32(sampleRate * 2)); u16(2); u16(16)
    d.append(contentsOf: Array("data".utf8)); u32(dataSize)
    for s in samples {
        let v = Int16((max(-1, min(1, s)) * 32767).rounded())
        u16(UInt16(bitPattern: v))
    }
    return d
}
