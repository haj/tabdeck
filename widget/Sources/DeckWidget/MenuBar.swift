import AppKit
import Combine

@MainActor
final class MenuBar: NSObject {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let model: AppModel
    private let panel: NSPanel

    init(model: AppModel, panel: NSPanel) {
        self.model = model
        self.panel = panel
        super.init()
        if let button = item.button {
            button.target = self
            button.action = #selector(clicked(_:))
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }
        updateIcon()
        // The icon shows what the assistant is doing, which matters most while the widget is hidden.
        observer = model.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async { self?.updateIcon() }
        }
    }

    private var observer: AnyCancellable?

    /// Click: show or hide the widget (like Siri). Right-click or ⌥/⌃-click: the menu.
    @objc private func clicked(_ sender: NSStatusBarButton) {
        let event = NSApp.currentEvent
        if event?.type == .rightMouseUp || event?.modifierFlags.contains(.option) == true
            || event?.modifierFlags.contains(.control) == true {
            let menu = NSMenu()
            menuNeedsUpdate(menu)
            item.menu = menu
            item.button?.performClick(nil)
            item.menu = nil
        } else {
            model.setWidgetHidden(panel.isVisible)
        }
    }

    private func updateIcon() {
        let symbol: String
        switch model.light {
        case .paused: symbol = "mic.slash"
        case .offline: symbol = "wifi.slash"
        case .speaking: symbol = "speaker.wave.2.fill"
        case .active: symbol = "waveform.circle.fill"
        case .listening: symbol = model.needsYou > 0 ? "exclamationmark.bubble.fill" : "waveform"
        }
        if item.button?.image?.accessibilityDescription != symbol {
            item.button?.image = NSImage(systemSymbolName: symbol, accessibilityDescription: symbol)
        }
        item.button?.toolTip = "\(model.assistantName): click to \(panel.isVisible ? "hide" : "show"), right-click for the menu"
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        add(menu, model.micPaused ? "Resume listening" : "Pause listening", #selector(togglePause))
        add(menu, panel.isVisible ? "Hide widget" : "Show widget", #selector(toggleWidget))
        add(menu, "Open TabDeck page", #selector(openPage))
        add(menu, "Pair a phone…", #selector(openPair))
        menu.addItem(.separator())
        menu.addItem(submenu("Voice", options: model.voices, current: model.speaker.ttsVoice, action: #selector(pickVoice(_:))))
        menu.addItem(submenu("Answer length", options: ["short", "normal", "detailed"], current: model.answerLength,
                             action: #selector(pickLength(_:))))
        if model.micDenied { add(menu, "Grant microphone access…", #selector(openMicSettings)) }
        menu.addItem(.separator())
        add(menu, "Settings…", #selector(openSettings))
        add(menu, "Reload config", #selector(reloadConfig))
        add(menu, "Help…", #selector(openHelp))
        menu.addItem(.separator())
        add(menu, "Quit \(model.assistantName)", #selector(quit))
    }

    private func submenu(_ title: String, options: [String], current: String, action: Selector) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        let sub = NSMenu()
        if options.isEmpty { sub.addItem(NSMenuItem(title: "Voice server not reachable", action: nil, keyEquivalent: "")) }
        for o in options {
            let i = NSMenuItem(title: o.capitalized == o ? o : o, action: action, keyEquivalent: "")
            i.target = self
            i.representedObject = o
            i.state = o == current ? .on : .off
            sub.addItem(i)
        }
        item.submenu = sub
        return item
    }

    @objc private func pickVoice(_ sender: NSMenuItem) { if let v = sender.representedObject as? String { model.chooseVoice(v) } }
    @objc private func pickLength(_ sender: NSMenuItem) { if let l = sender.representedObject as? String { model.chooseAnswerLength(l) } }

    private func add(_ menu: NSMenu, _ title: String, _ action: Selector) {
        let i = NSMenuItem(title: title, action: action, keyEquivalent: "")
        i.target = self
        menu.addItem(i)
    }

    @objc private func togglePause() { model.togglePause() }
    @objc private func toggleWidget() { model.setWidgetHidden(panel.isVisible) }
    @objc private func openPage() { model.openWebPage() }
    @objc private func openSettings() { model.openSettings() }
    @objc private func reloadConfig() { model.reloadConfig() }
    @objc private func openHelp() { model.openHelp() }
    @objc private func openPair() { PairWindow.show(model: model) }
    @objc private func openMicSettings() {
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")!)
    }
    @objc private func quit() { NSApp.terminate(nil) }
}
