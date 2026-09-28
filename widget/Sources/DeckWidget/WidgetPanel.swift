import AppKit
import SwiftUI

/// Borderless, non-activating panel that floats above all apps on every Space.
final class WidgetPanel: NSPanel {
    init(root: AnyView) {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 320, height: 76),
                   styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        backgroundColor = .clear
        isOpaque = false
        hasShadow = true
        hidesOnDeactivate = false
        let host = NSHostingController(rootView: root)
        host.sizingOptions = .preferredContentSize
        contentViewController = host
        if !setFrameUsingName("DeckWidgetPanel"), let screen = NSScreen.main?.visibleFrame {
            setFrameOrigin(NSPoint(x: screen.maxX - 340, y: screen.minY + 20))
        }
        setFrameAutosaveName("DeckWidgetPanel")
    }

    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}
