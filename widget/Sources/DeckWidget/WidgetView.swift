import AppKit
import DeckCore
import SwiftUI

/// An AppKit view that drags the window, since SwiftUI gestures would swallow the drag in a borderless panel.
struct DragHandle: NSViewRepresentable {
    final class HandleView: NSView {
        override func mouseDown(with event: NSEvent) { window?.performDrag(with: event) }
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    }
    func makeNSView(context: Context) -> NSView { HandleView() }
    func updateNSView(_ nsView: NSView, context: Context) {}
}

func statusColor(_ status: String) -> Color {
    switch status {
    case "needs_you": return .orange
    case "working": return .blue
    case "done": return .green
    default: return .gray
    }
}

struct LightView: View {
    let light: Light
    var body: some View {
        switch light {
        case .offline:
            Circle().stroke(Color.gray, lineWidth: 2).frame(width: 14, height: 14)
        default:
            Circle().fill(color).frame(width: 14, height: 14)
                .shadow(color: color.opacity(0.7), radius: light == .listening ? 0 : 5)
        }
    }
    var color: Color {
        switch light {
        case .listening: return .gray
        case .active: return .blue
        case .speaking: return .green
        case .paused: return .red
        case .offline: return .gray
        }
    }
}

struct WidgetView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            pill
            if let p = model.pending { pendingView(p) }
            if model.expanded { tabList }
        }
        .padding(10)
        .frame(width: 300)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(Color.primary.opacity(0.1)))
    }

    private var pill: some View {
        HStack(spacing: 10) {
            ZStack {
                DragHandle().frame(width: 26, height: 36)
                LightView(light: model.light).allowsHitTesting(false)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 13, weight: .semibold))
                Text(model.line).font(.system(size: 12)).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 0)
            if model.needsYou > 0 {
                Text("\(model.needsYou)")
                    .font(.system(size: 12, weight: .bold)).foregroundStyle(.black)
                    .padding(.horizontal, 7).padding(.vertical, 2)
                    .background(Color.orange, in: Capsule())
            }
            Button { model.setWidgetHidden(true) } label: {
                Image(systemName: "chevron.up.circle.fill").font(.system(size: 16)).foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .help("Hide in the menu bar (click the menu bar icon to bring it back)")
        }
        .contentShape(Rectangle())
        .onTapGesture { model.expanded.toggle() }
    }

    private var title: String {
        if !model.online { return "TabDeck offline" }
        if !model.itermOK { return "iTerm not connected" }
        if model.micDenied { return "Microphone access needed" }
        if model.needsYou > 0 { return "\(model.needsYou) need\(model.needsYou == 1 ? "s" : "") you" }
        if let id = model.selected, let s = model.sessions.first(where: { $0.id == id }) { return s.name }
        return model.assistantName
    }

    private func pendingView(_ p: AppModel.Pending) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Sending to \(p.name)").font(.system(size: 12, weight: .semibold))
            Text(p.text).font(.system(size: 12)).lineLimit(3)
            HStack {
                Button("Cancel") { model.cancelPending("Cancelled.") }
                Button("Send now") { Task { await model.commitPending() } }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(8)
        .background(Color.accentColor.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
    }

    private var tabList: some View {
        ScrollView {
            VStack(spacing: 4) {
                ForEach(Array(model.sessions.enumerated()), id: \.element.id) { i, s in
                    Button { model.pick(s.id) } label: {
                        HStack(spacing: 8) {
                            Text("\(i + 1)").font(.system(size: 11)).foregroundStyle(.secondary).frame(width: 18)
                            Circle().fill(statusColor(s.status)).frame(width: 9, height: 9)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(s.name).font(.system(size: 12, weight: s.seen ? .regular : .semibold))
                                if !s.message.isEmpty {
                                    Text(s.message).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                                }
                            }
                            Spacer(minLength: 0)
                        }
                        .padding(6)
                        .background(s.id == model.selected ? Color.accentColor.opacity(0.18) : .clear,
                                    in: RoundedRectangle(cornerRadius: 8))
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .frame(maxHeight: 320)
    }
}
