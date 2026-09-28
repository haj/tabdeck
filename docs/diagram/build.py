"""Builds docs/diagram/architecture{,-dark}.html: the README's "How it fits together" figure.

One geometry, two skins (the TabDeck profile: its dark terminal palette, and a light one for light-mode readers).
Run: python3 docs/diagram/build.py && uv run --with playwright python docs/diagram/export.py
"""
from pathlib import Path

SKINS = {
    "light": dict(paper="#f6f8fa", node="#ffffff", ink="#1f2632", muted="#4f5b6e", soft="#8290a6",
                  rule="31,38,50", accent="#0e8a94", tint="rgba(14,138,148,0.08)", link="#2f6fb5"),
    "dark": dict(paper="#0b0e14", node="#10141c", ink="#cdd6e4", muted="#8290a6", soft="#6b7689",
                 rule="205,214,228", accent="#56d4dd", tint="rgba(86,212,221,0.12)", link="#79c0ff"),
}
W, H = 1200, 600
FONTS = ("https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1"
         "&family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500;600&display=swap")


def svg(s: dict, slug: str) -> str:
    r = s["rule"]
    out = []
    add = out.append

    def zone(x, y, w, h, label):
        lw = -(-(len(label) * 6 + 16) // 4) * 4
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="rgba({r},0.02)" '
            f'stroke="rgba({r},0.16)" stroke-width="0.8"/>')
        add(f'<rect x="{x + 12}" y="{y + 4}" width="{lw}" height="12" rx="2" fill="{s["paper"]}"/>')
        add(f'<text x="{x + 20}" y="{y + 13}" fill="{s["soft"]}" font-size="8" font-family="\'Geist Mono\', monospace" '
            f'letter-spacing="0.14em">{label}</text>')

    def node(x, y, name, sub, tag, kind="backend", w=160, h=64):
        fill, stroke, dash = {
            "focal": (s["tint"], s["accent"], ""),
            "backend": (s["node"], s["ink"], ""),
            "input": (f"rgba({r},0.06)", s["soft"], ""),
            "optional": (f"rgba({r},0.02)", f"rgba({r},0.36)", ' stroke-dasharray="4,3"'),
        }[kind]
        tag_color = s["accent"] if kind == "focal" else s["soft"]
        tw = -(-(len(tag) * 6 + 12) // 4) * 4
        cx = x + w // 2
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{s["paper"]}"/>')
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1"{dash}/>')
        add(f'<rect x="{x + 8}" y="{y + 6}" width="{tw}" height="12" rx="2" fill="transparent" stroke="{tag_color}" '
            f'stroke-opacity="0.5" stroke-width="0.8"/>')
        add(f'<text x="{x + 8 + tw / 2:g}" y="{y + 15}" fill="{tag_color}" font-size="7" font-family="\'Geist Mono\', monospace" '
            f'text-anchor="middle" letter-spacing="0.08em">{tag}</text>')
        add(f'<text x="{cx}" y="{y + 38}" fill="{s["ink"]}" font-size="12" font-weight="600" '
            f'font-family="\'Geist\', sans-serif" text-anchor="middle">{name}</text>')
        add(f'<text x="{cx}" y="{y + 54}" fill="{s["muted"]}" font-size="9" font-family="\'Geist Mono\', monospace" '
            f'text-anchor="middle">{sub}</text>')

    def label(x, y, text, anchor="middle"):
        w = -(-(len(text) * 6 + 8) // 8) * 8  # a multiple of 8, so a centred mask stays on the 4px grid
        lx = x - w / 2 if anchor == "middle" else x
        tx = x if anchor == "middle" else x + 4
        add(f'<rect x="{lx:g}" y="{y}" width="{w}" height="12" rx="2" fill="{s["paper"]}"/>')
        add(f'<text x="{tx:g}" y="{y + 9}" fill="{s["soft"]}" font-size="8" font-family="\'Geist Mono\', monospace" '
            f'text-anchor="{"middle" if anchor == "middle" else "start"}" letter-spacing="0.06em">{text}</text>')

    def path(d, color="muted", marker="arrow", dash=False, start=False):
        stroke = s[color]
        extra = ' stroke-dasharray="4,3" stroke-width="1"' if dash else ' stroke-width="1.2"'
        ms = f' marker-start="url(#{marker}-start)"' if start else ""
        add(f'<path d="{d}" fill="none" stroke="{stroke}"{extra} marker-end="url(#{marker})"{ms}/>')

    add(f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-labelledby="{slug}-title {slug}-desc">')
    add(f'<title id="{slug}-title">How TabDeck fits together</title>')
    add(f'<desc id="{slug}-desc">TabDeck architecture: on the Mac, the widget and the Mac agent talk to an always-on '
        f'server (the hub), which runs the coding-agent sessions in tmux; iTerm2 shows those sessions as tabs over '
        f'ssh, a paired phone uses the hub\'s web app, and the hub can use optional model, speech-to-text and voice '
        f'services, which one ODS server can provide.</desc>')
    add("<defs>")
    for name, color in (("arrow", s["muted"]), ("arrow-accent", s["accent"]), ("arrow-link", s["link"])):
        add(f'<marker id="{name}" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">'
            f'<polygon points="0 0, 8 3, 0 6" fill="{color}"/></marker>')
        add(f'<marker id="{name}-start" markerWidth="8" markerHeight="6" refX="1" refY="3" orient="auto">'
            f'<polygon points="8 0, 0 3, 8 6" fill="{color}"/></marker>')
    add("</defs>")
    add(f'<rect width="100%" height="100%" fill="{s["paper"]}"/>')

    # Zones first.
    zone(40, 104, 240, 380, "YOUR MAC")
    zone(480, 104, 240, 380, "ALWAYS-ON SERVER · HUB")
    zone(920, 104, 240, 380, "OPTIONAL SERVICES")

    # Arrows before boxes.
    path("M 240,172 H 520", "accent", "arrow-accent")                                     # widget -> hub
    label(380, 152, "HTTPS · PRIVATE NET")
    path("M 160,204 V 264")                                                               # widget -> agent
    label(168, 226, "YOUR VOICE", "start")
    path("M 160,328 V 380", start=True)                                                   # agent <-> iTerm2
    label(168, 348, "READS TABS", "start")
    path("M 240,292 H 564 Q 572,292 572,284 V 204")                                       # agent -> hub
    label(380, 272, "YOUR ITERM TABS")
    path("M 240,412 H 520", "link", "arrow-link")                                         # iTerm2 -> sessions
    label(380, 392, "SSH + TMUX -CC")
    path("M 628,204 V 380")                                                               # hub -> sessions
    label(636, 284, "STARTS · READS · TYPES", "start")
    path("M 652,80 V 140")                                                                # phone -> hub
    label(660, 84, "HTTPS", "start")
    path("M 680,156 H 960")                                                               # hub -> model
    label(820, 136, "UNDERSTANDS · SUMS UP")
    path("M 680,172 H 812 Q 820,172 820,180 V 284 Q 820,292 828,292 H 960", dash=True)    # hub -> speech-to-text
    path("M 680,188 H 792 Q 800,188 800,196 V 404 Q 800,412 808,412 H 960")               # hub -> voice
    label(808, 392, "SPEAKS", "start")

    # Nodes.
    node(80, 140, "TabDeck widget", "menu bar · wake word", "APP", "focal")
    node(80, 264, "Mac agent", "tabdeck agent · Whisper", "SERVICE")
    node(80, 380, "iTerm2", "Python API on", "APP")
    node(520, 16, "Phone or browser", "paired web app", "CLIENT", "input")
    node(520, 140, "Hub", "tabdeck serve · :8765", "SERVER", "focal")
    node(520, 380, "Agent sessions", "tmux · claude | opencode", "TMUX")
    node(960, 140, "Language model", "Ollama · OpenAI-compatible", "LLM", "optional")
    node(960, 260, "Speech-to-text", "Whisper server", "STT", "optional")
    node(960, 380, "Neural voice", "Kokoro", "TTS", "optional")
    add(f'<text x="1040" y="472" fill="{s["muted"]}" font-size="14" font-style="italic" '
        f'font-family="\'Instrument Serif\', serif" text-anchor="middle">or all three from one ODS server</text>')

    # Legend strip.
    ly = 540
    add(f'<line x1="40" y1="{ly - 20}" x2="{W - 40}" y2="{ly - 20}" stroke="rgba({r},0.12)" stroke-width="0.8"/>')
    add(f'<text x="40" y="{ly + 8}" fill="{s["muted"]}" font-size="8" font-family="\'Geist Mono\', monospace" '
        f'letter-spacing="0.14em">LEGEND</text>')
    items = [
        ("box", s["tint"], s["accent"], "", "Where you talk to it"),
        ("box", s["node"], s["ink"], "", "Part of TabDeck"),
        ("box", f"rgba({r},0.02)", f"rgba({r},0.36)", ' stroke-dasharray="4,3"', "Optional service"),
        ("line", s["link"], "", "", "Shows sessions as tabs"),
        ("dash", s["muted"], "", "", "Only if set (stt_url)"),
    ]
    x = 120
    for kind, a, b, dash, text in items:
        if kind == "box":
            add(f'<rect x="{x}" y="{ly}" width="20" height="12" rx="2" fill="{a}" stroke="{b}" stroke-width="1"{dash}/>')
        else:
            d = ' stroke-dasharray="4,3"' if kind == "dash" else ""
            add(f'<line x1="{x}" y1="{ly + 6}" x2="{x + 20}" y2="{ly + 6}" stroke="{a}" stroke-width="1.2"{d}/>')
        add(f'<text x="{x + 28}" y="{ly + 9}" fill="{s["muted"]}" font-size="9" font-family="\'Geist\', sans-serif">{text}</text>')
        x += 200
    add("</svg>")
    return "\n".join(out)


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>How TabDeck fits together</title>
  <link href="{fonts}" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Geist', system-ui, sans-serif; background: {paper}; color: {ink}; min-height: 100vh;
      display: flex; align-items: center; justify-content: center; padding: 3rem 2rem; }}
    .frame {{ max-width: 1200px; width: 100%; }}
    .eyebrow {{ font-family: 'Geist Mono', monospace; font-size: 0.66rem; font-weight: 500; letter-spacing: 0.18em;
      text-transform: uppercase; color: {muted}; margin-bottom: 0.5rem; }}
    h1 {{ font-family: 'Instrument Serif', serif; font-size: 2rem; font-weight: 400; letter-spacing: -0.02em;
      margin-bottom: 1.5rem; }}
    svg {{ width: 100%; min-width: 900px; display: block; }}
  </style>
</head>
<body>
  <div class="frame">
    <p class="eyebrow">Architecture · TabDeck</p>
    <h1>How TabDeck fits together</h1>
    {svg}
  </div>
</body>
</html>
"""

if __name__ == "__main__":
    here = Path(__file__).parent
    for name, s in SKINS.items():
        slug = "architecture" if name == "light" else "architecture-dark"
        (here / f"{slug}.html").write_text(PAGE.format(fonts=FONTS, svg=svg(s, slug), **s))
        print(here / f"{slug}.html")
