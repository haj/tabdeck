import AppKit
import SwiftUI

/// Everything configurable, in one window: the hub's shared settings (assistant, model, speech) and this Mac's.
/// Changes apply at once; deploy-time values are shown for reference (change them with `tabdeck setup`).
@MainActor
final class SettingsStore: ObservableObject {
    struct Field: Identifiable { let key, group, label: String; var id: String { key } }

    @Published var fields: [Field] = []
    @Published var values: [String: String] = [:]
    @Published var readOnly: [(String, String)] = []
    @Published var macTabs = true
    @Published var macSttURL = ""
    @Published var macSttModel = ""
    @Published var voices: [String] = []
    @Published var newKey = ""  // typed API key; empty keeps the current one
    @Published var clearKey = false
    @Published var keyIsSet = false
    @Published var wakeTest = ""
    @Published var wakeResult = ""
    @Published var message = ""
    @Published var busy = false

    private var loaded: [String: String] = [:]
    private var loadedMac: (Bool, String, String) = (true, "", "")
    let model: AppModel

    init(model: AppModel) { self.model = model }

    var groups: [String] { fields.reduce(into: [String]()) { if !$0.contains($1.group) { $0.append($1.group) } } }

    private static func text(_ v: Any?) -> String {
        switch v {
        case let s as String: return s
        case let d as Double: return d == d.rounded() ? String(Int(d)) : String(d)
        case let i as Int: return String(i)
        case let b as Bool: return b ? "yes" : "no"
        default: return ""
        }
    }

    private func show(hub r: [String: Any]) {
        fields = (r["fields"] as? [[String: String]] ?? []).compactMap {
            guard let k = $0["key"], let g = $0["group"], let l = $0["label"] else { return nil }
            return Field(key: k, group: g, label: l)
        }
        let v = r["values"] as? [String: Any] ?? [:]
        values = v.mapValues { Self.text($0) }
        keyIsSet = values["llm_key"] == "set"
        values["llm_key"] = nil
        loaded = values
        readOnly = (r["read_only"] as? [String: Any] ?? [:]).map { ($0.key, Self.text($0.value)) }.sorted { $0.0 < $1.0 }
        newKey = ""
        clearKey = false
    }

    private func show(mac r: [String: Any]) {
        let v = r["values"] as? [String: Any] ?? [:]
        macTabs = v["mac_tabs"] as? Bool ?? true
        macSttURL = v["stt_url"] as? String ?? ""
        macSttModel = v["stt_model"] as? String ?? ""
        loadedMac = (macTabs, macSttURL, macSttModel)
    }

    func load() async {
        busy = true
        defer { busy = false }
        var problems: [String] = []
        do { show(hub: try await model.client.settings()) } catch { problems.append("Hub: \(Self.describe(error))") }
        do { show(mac: try await model.client.settings(local: true)) } catch { problems.append("Mac agent: \(Self.describe(error))") }
        voices = await model.client.voices()
        message = problems.joined(separator: "\n")
    }

    func save() async {
        busy = true
        defer { busy = false }
        var hub: [String: Any] = [:]
        for (k, v) in values where loaded[k] != v {
            hub[k] = ["intent_timeout", "summary_timeout"].contains(k) ? (Double(v) ?? -1) as Any : v
        }
        if clearKey { hub["llm_key"] = "" } else if !newKey.isEmpty { hub["llm_key"] = newKey }
        var mac: [String: Any] = [:]
        if macTabs != loadedMac.0 { mac["mac_tabs"] = macTabs }
        if macSttURL != loadedMac.1 { mac["stt_url"] = macSttURL }
        if macSttModel != loadedMac.2 { mac["stt_model"] = macSttModel }
        if hub.isEmpty && mac.isEmpty { message = "Nothing changed."; return }
        var problems: [String] = []
        if !hub.isEmpty {
            do { show(hub: try await model.client.saveSettings(hub)) } catch { problems.append(Self.describe(error)) }
        }
        if !mac.isEmpty {
            do { show(mac: try await model.client.saveSettings(mac, local: true)) } catch { problems.append(Self.describe(error)) }
        }
        message = problems.isEmpty ? "Saved. Changes are in use now." : problems.joined(separator: "\n")
        if problems.isEmpty, hub["tts_voice"] != nil { model.say("Hello, this is my new voice.") }
    }

    func reload() async {
        model.reloadConfig()
        await load()
        if message.isEmpty { message = "Reloaded from the settings files." }
    }

    func testWake() async {
        guard !wakeTest.isEmpty else { return }
        do {
            let r = try await model.client.testWake(wakeTest)
            wakeResult = r.matches ? "✓ Wake word heard. Command: “\(r.rest)”" : "✗ No wake word at the start"
        } catch { wakeResult = Self.describe(error) }
    }

    static func describe(_ error: Error) -> String {
        (error as? ServiceError)?.message ?? error.localizedDescription
    }
}

struct SettingsView: View {
    @ObservedObject var store: SettingsStore

    private func binding(_ key: String) -> Binding<String> {
        Binding(get: { store.values[key] ?? "" }, set: { store.values[key] = $0 })
    }

    @ViewBuilder private func row(_ f: SettingsStore.Field) -> some View {
        switch f.key {
        case "tts_voice" where !store.voices.isEmpty:
            Picker(f.label, selection: binding(f.key)) {
                ForEach(store.voices, id: \.self) { Text($0).tag($0) }
            }
        case "llm_api":
            Picker(f.label, selection: binding(f.key)) {
                Text("Ollama").tag("ollama")
                Text("OpenAI-compatible").tag("openai")
            }
        case "llm_key":
            SecureField(store.keyIsSet ? "\(f.label) (set; type to replace)" : f.label, text: $store.newKey)
                .disabled(store.clearKey)
            if store.keyIsSet { Toggle("Remove the API key", isOn: $store.clearKey) }
        default:
            TextField(f.label, text: binding(f.key))
        }
    }

    var body: some View {
        Form {
            ForEach(store.groups, id: \.self) { group in
                Section(group) {
                    ForEach(store.fields.filter { $0.group == group }) { row($0) }
                }
            }
            Section("This Mac") {
                Toggle("Show this Mac's own iTerm tabs on the hub", isOn: $store.macTabs)
                TextField("Speech-to-text URL (empty: Whisper on this Mac)", text: $store.macSttURL)
                TextField("Speech-to-text model", text: $store.macSttModel)
            }
            Section("Test the wake word") {
                HStack {
                    TextField("Type what you'd say, e.g. “Hey Friday, status”", text: $store.wakeTest)
                    Button("Test") { Task { await store.testWake() } }
                }
                if !store.wakeResult.isEmpty { Text(store.wakeResult).font(.callout) }
            }
            if !store.readOnly.isEmpty {
                Section("Set when deploying (tabdeck setup)") {
                    ForEach(store.readOnly, id: \.0) { k, v in
                        LabeledContent(k.replacingOccurrences(of: "_", with: " ").capitalized, value: v)
                    }
                }
            }
            if !store.message.isEmpty {
                Text(store.message).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
            }
        }
        .formStyle(.grouped)
        .disabled(store.busy)
        .toolbar {
            ToolbarItemGroup(placement: .confirmationAction) {
                Button("Reload config") { Task { await store.reload() } }
                Button("Save") { Task { await store.save() } }.keyboardShortcut(.defaultAction)
            }
        }
        .frame(minWidth: 520, minHeight: 560)
        .task { await store.load() }
    }
}

@MainActor
enum SettingsWindow {
    private static var window: NSWindow?

    static func show(model: AppModel) {
        if let w = window {
            w.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let w = NSWindow(contentViewController: NSHostingController(rootView: SettingsView(store: SettingsStore(model: model))))
        w.title = "\(model.assistantName) Settings"
        w.styleMask = [.titled, .closable, .resizable]
        w.isReleasedWhenClosed = false
        w.setContentSize(NSSize(width: 560, height: 680))
        w.center()
        window = w
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}
