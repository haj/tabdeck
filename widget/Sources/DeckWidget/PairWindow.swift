import AppKit
import CoreImage.CIFilterBuiltins
import SwiftUI

/// Pair a phone or browser: a QR code and link from the hub (through the Mac agent's token), no ssh needed.
struct PairView: View {
    let model: AppModel
    @State private var url = ""
    @State private var message = "Asking the hub for a pairing link…"

    private func qr(_ text: String) -> NSImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage?.transformed(by: CGAffineTransform(scaleX: 8, y: 8)),
              let cg = CIContext().createCGImage(output, from: output.extent) else { return nil }
        return NSImage(cgImage: cg, size: NSSize(width: output.extent.width / 2, height: output.extent.height / 2))
    }

    var body: some View {
        VStack(spacing: 14) {
            Text("Pair a phone or browser").font(.title3.bold())
            if let image = url.isEmpty ? nil : qr(url) {
                Image(nsImage: image).interpolation(.none).padding(10).background(.white, in: RoundedRectangle(cornerRadius: 8))
                Text("Scan with the phone's camera while it's on your private network, then add the page to the Home Screen.")
                    .font(.callout).multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                Text(url).font(.caption.monospaced()).textSelection(.enabled).foregroundStyle(.secondary)
                Text("The link works once, for 10 minutes.").font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("Open in this Mac's browser") { if let u = URL(string: url) { NSWorkspace.shared.open(u) } }
                    Button("New link") { Task { await load() } }
                }
            } else {
                Text(message).font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            Divider()
            Button("Unpair all phones and browsers", role: .destructive) {
                Task {
                    do { try await model.client.unpairAll(); message = "All phones and browsers unpaired."; url = "" }
                    catch { message = SettingsStore.describe(error) }
                }
            }
        }
        .padding(24)
        .frame(width: 380)
        .task { await load() }
    }

    private func load() async {
        do { url = try await model.client.pairingLink() }
        catch { url = ""; message = "Couldn't get a pairing link: \(SettingsStore.describe(error))" }
    }
}

@MainActor
enum PairWindow {
    private static var window: NSWindow?

    static func show(model: AppModel) {
        window?.close()  // a fresh link each time
        let w = NSWindow(contentViewController: NSHostingController(rootView: PairView(model: model)))
        w.title = "Pair a phone"
        w.styleMask = [.titled, .closable]
        w.isReleasedWhenClosed = false
        w.center()
        window = w
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}
