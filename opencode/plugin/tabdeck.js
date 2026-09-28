// TabDeck status plugin for OpenCode: reports what this session is doing to the TabDeck hub, in the
// same shape as Claude Code hook events, so the hub's status, summaries and read-aloud work unchanged.
// Lives in <data dir>/opencode/plugin/ and is loaded through OPENCODE_CONFIG_DIR (set by <data dir>/agent.sh);
// does nothing outside tmux.
import { appendFileSync, mkdirSync, readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// <data dir>/opencode/plugin/tabdeck.js -> <data dir> (~/.tabdeck, or ~/.tabdeck-<instance>)
const DATA = dirname(dirname(dirname(fileURLToPath(import.meta.url))))

const TOOLS = { bash: "Bash", edit: "Edit", write: "Write", read: "Read", patch: "Edit", multiedit: "MultiEdit" }

export const TabDeck = async ({ client }) => {
  const pane = process.env.TMUX_PANE
  if (!pane) return {}
  const url = process.env.TABDECK_HOOK_URL || `https://127.0.0.1:${process.env.TABDECK_PORT || 8765}/hook`
  const replies = join(DATA, "replies")
  try { mkdirSync(replies, { recursive: true }) } catch {}
  const transcript = (sessionID) => join(replies, `${sessionID || "session"}.jsonl`)
  const remember = (sessionID, entry) => { try { appendFileSync(transcript(sessionID), JSON.stringify(entry) + "\n") } catch {} }

  // The hub's certificate comes from the Mac's mkcert CA; deploy-hub.sh copies that CA's public cert here.
  let ca
  try { ca = readFileSync(join(DATA, "ca.pem"), "utf8") } catch {}
  // Never block OpenCode on the hub: short timeout, errors ignored.
  const post = (event) => fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Tmux-Pane": pane },
    body: JSON.stringify(event.session_id ? { ...event, transcript_path: transcript(event.session_id) } : event),
    ...(ca ? { tls: { ca } } : {}),
    signal: AbortSignal.timeout(2000),
  }).catch(() => {})

  const lastReply = async (sessionID) => {
    try {
      const res = await client.session.messages({ path: { id: sessionID } })
      const messages = res.data || []
      for (let i = messages.length - 1; i >= 0; i--) {
        const m = messages[i]
        if (m.info && m.info.role === "assistant") {
          return (m.parts || []).filter((p) => p.type === "text" && p.text).map((p) => p.text).join("\n\n").trim()
        }
      }
    } catch {}
    return ""
  }

  post({ hook_event_name: "SessionStart" })
  return {
    "chat.message": async (input, output) => {
      const text = (output.parts || []).filter((p) => p.type === "text").map((p) => p.text).join("\n")
      remember(input.sessionID, { type: "user", message: { content: text } })
      post({ hook_event_name: "UserPromptSubmit", session_id: input.sessionID, prompt: text })
    },
    "tool.execute.before": async (input, output) => {
      const args = { ...(output.args || {}) }
      if (args.filePath && !args.file_path) args.file_path = args.filePath
      post({ hook_event_name: "PreToolUse", session_id: input.sessionID, tool_name: TOOLS[input.tool] || input.tool, tool_input: args })
    },
    event: async ({ event }) => {
      const p = event.properties || {}
      if (event.type === "session.idle") {
        const text = await lastReply(p.sessionID)
        if (text) remember(p.sessionID, { type: "assistant", message: { content: [{ type: "text", text }] } })
        post({ hook_event_name: "Stop", session_id: p.sessionID, last_assistant_message: text })
      } else if (event.type === "permission.asked" || event.type === "permission.updated") {
        post({ hook_event_name: "Notification", notification_type: "permission_prompt", session_id: p.sessionID,
               message: p.title ? `wants to ${p.title}` : "needs your permission" })
      } else if (event.type === "session.error") {
        post({ hook_event_name: "Notification", session_id: p.sessionID, message: "ran into an error" })
      }
    },
  }
}
