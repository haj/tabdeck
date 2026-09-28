import AppKit
import SwiftUI

/// Borderless, non-activating panel that floats above all apps on every Space.
final class WidgetPanel: NSPanel {
    init(root: AnyView) {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 272, height: 64),
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
            setFrameOrigin(NSPoint(x: screen.maxX - 290, y: screen.minY + 20))
        }
        setFrameAutosaveName("DeckWidgetPanel")
        keepOnScreen()  // a saved position can be behind the Dock or on a display that is gone
        let center = NotificationCenter.default
        for name in [NSWindow.didResizeNotification, NSWindow.didMoveNotification] {
            center.addObserver(forName: name, object: self, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.keepOnScreen() }  // queue: .main
            }
        }
        // Displays added or removed, resolution or Dock changes.
        center.addObserver(forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.keepOnScreen() }  // queue: .main
        }
    }

    /// Room left for an auto-hiding Dock at the bottom: macOS counts that edge as free, but the Dock pops up over it.
    static let dockMargin: CGFloat = 90

    /// Keep the whole widget inside the visible area of its screen: above the Dock, below the menu bar.
    func keepOnScreen() {
        guard let s = screen ?? NSScreen.main else { return }
        var visible = s.visibleFrame
        let bottomGap = visible.minY - s.frame.minY
        if bottomGap < Self.dockMargin {  // Dock auto-hidden (or not at the bottom): keep clear of where it appears
            visible.origin.y = s.frame.minY + Self.dockMargin
            visible.size.height -= Self.dockMargin - bottomGap
        }
        let area = visible.insetBy(dx: 8, dy: 8)
        var origin = frame.origin
        origin.x = min(max(origin.x, area.minX), max(area.minX, area.maxX - frame.width))
        origin.y = min(max(origin.y, area.minY), max(area.minY, area.maxY - frame.height))
        if origin != frame.origin { setFrameOrigin(origin) }
    }

    override func orderFrontRegardless() {
        super.orderFrontRegardless()
        keepOnScreen()
    }

    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}
