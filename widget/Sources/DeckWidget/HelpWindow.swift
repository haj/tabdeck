import AppKit
import SwiftUI

/// How to use the widget, start and stop it, what it needs, and where to look when something is wrong.
/// Uses the live assistant name and wake phrase, and this setup's data folder and launchd job.
struct HelpView: View {
    @ObservedObject var model: AppModel

    private var label: String { Bundle.main.bundleIdentifier ?? "com.tabdeck.widget" }
    private var instance: String {
        let suffix = String(Instance.dataDir.dropFirst(".tabdeck".count))
        return suffix.hasPrefix("-") ? "TABDECK_INSTANCE=\(suffix.dropFirst()) " : ""
    }
    private var data: String { "~/\(Instance.dataDir)" }

    private func section(_ title: String, _ lines: [String]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.headline)
            ForEach(lines, id: \.self) { line in
                Text(.init(line)).font(.callout).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            }
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("\(model.assistantName) — voice control for your coding agents").font(.title2.bold())
                Text("\(model.assistantName) watches your Claude Code or OpenCode sessions (on your servers and this Mac), tells you what they are doing, reads replies aloud, and approves, replies to or starts sessions — by voice or from the web page.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)

                section("Talk to it", [
                    "Say **“\(model.wakePhrase), …”** and then what you want. After it answers you can follow up for a few seconds without the wake word.",
                    "• “\(model.wakePhrase), what's going on?” — status of every session",
                    "• “\(model.wakePhrase), catch me up” — what happened since you last asked",
                    "• “\(model.wakePhrase), tell api to run the tests” — type into a session",
                    "• “\(model.wakePhrase), approve” / “deny” / “always allow” — answer a permission prompt",
                    "• “\(model.wakePhrase), read atlas” — read the last reply aloud",
                    "• “\(model.wakePhrase), start a session in myproject” — on the hub (add “on my Mac” or “on build-box”)",
                    "Before a message is typed it is shown with a short countdown: say “send it” or “cancel”.",
                ])

                section("The widget", [
                    "• The dot shows the state: **grey** listening, **blue** hearing you or working on it, **green** speaking, **red** paused, **hollow ring** offline. Drag it by the dot.",
                    "• Click the widget to show or hide your sessions; click a session to jump to it in iTerm.",
                    "• **⌃** tucks the widget into the menu bar (like Siri); menu → Show widget brings it back. It keeps listening and speaking.",
                    "• **Menu bar icon:** click it for the menu: **Show/Hide widget**, pause listening, voice, answer length, **Pair a phone…**, **Settings…**, **Reload config**, Help.",
                    "• **Phone:** menu → Pair a phone… shows a QR code; scan it on your private network and add the page to the Home Screen.",
                ])

                section("Start and stop", [
                    "It starts by itself when you log in (launchd job `\(label)`).",
                    "• Quit: menu → Quit \(model.assistantName). It stays off until you start it again or log in.",
                    "• Start again: `launchctl kickstart gui/$(id -u)/\(label)`",
                    "• Rebuild and install (from the TabDeck folder): `\(instance)uv run tabdeck install-widget`",
                    "• First time on a new Mac: `uv run tabdeck setup` sets up the hub, the Mac agent and this widget.",
                ])

                section("Requirements", [
                    "• macOS 14 or newer, and **microphone access** for \(model.assistantName) (System Settings → Privacy & Security → Microphone).",
                    "• **iTerm2 with its Python API enabled:** iTerm2 → Settings → General → Magic → *Enable Python API*. Server sessions open as iTerm tabs through it.",
                    "• On the hub server: **tmux**, **Claude Code or OpenCode** (logged in), **systemd lingering** (`sudo loginctl enable-linger $USER`) and **cron**. `tabdeck setup` checks all of these.",
                    "• The **Mac agent** running (`\(instance)uv run tabdeck install-agent`): it hears you and connects to the hub.",
                    "• The **hub** reachable over your private network (NetBird, Tailscale or WireGuard): \(model.client.hub.absoluteString)",
                    "• Speech-to-text: Whisper on this Mac, or a server set in Settings. A voice server (e.g. Kokoro) on the hub for the neural voice; otherwise the Mac's own voice is used.",
                ])

                section("When something is wrong", [
                    "• **Hollow ring / “can't reach the hub”:** check that the private network is connected, then menu → Reload config.",
                    "• **It doesn't react to you:** check microphone access, and use Settings → *Test the wake word* with what you say.",
                    "• **Old voice or old name:** menu → Reload config (after editing settings files by hand).",
                    "• Logs: `\(data)/widget.log` (this widget) and `\(data)/service.log` (the Mac agent).",
                ])

                HStack {
                    Button("Settings…") { model.openSettings() }
                    Button("Open the web page") { model.openWebPage() }
                    Button("Show logs") {
                        NSWorkspace.shared.open(FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(Instance.dataDir))
                    }
                }
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(minWidth: 560, minHeight: 600)
    }
}

@MainActor
enum HelpWindow {
    private static var window: NSWindow?

    static func show(model: AppModel) {
        if window == nil {
            let w = NSWindow(contentViewController: NSHostingController(rootView: HelpView(model: model)))
            w.styleMask = [.titled, .closable, .resizable]
            w.isReleasedWhenClosed = false
            w.setContentSize(NSSize(width: 620, height: 720))
            w.center()
            window = w
        }
        window?.title = "\(model.assistantName) Help"
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}
