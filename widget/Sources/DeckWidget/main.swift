import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var model: AppModel!
    private var panel: WidgetPanel!
    private var menuBar: MenuBar!

    func applicationDidFinishLaunching(_ notification: Notification) {
        model = AppModel()
        panel = WidgetPanel(root: AnyView(WidgetView(model: model)))
        if !model.widgetHidden { panel.orderFrontRegardless() }
        model.onWidgetHidden = { [weak panel] hidden in hidden ? panel?.orderOut(nil) : panel?.orderFrontRegardless() }
        menuBar = MenuBar(model: model, panel: panel)
        model.start()
        deckLog("Deck widget started")
    }
}

// main.swift runs on the main thread; tell the compiler so it can create the main-actor delegate.
MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.accessory)
    app.run()
}
