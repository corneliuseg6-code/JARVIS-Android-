# JARVIS Android — project status

Written for the next Claude Code session. Read this instead of the whole
conversation. Last updated 2026-10-28.

---

## Where the code is

| | |
|---|---|
| Repository | `corneliuseg6-code/JARVIS-Android-` |
| **Development branch** | **`claude/v4-agent-system`** — all V4 work goes here |
| Version | V4, Phase 1 complete (speed, voices, UI, workspace + folder network) |
| `main` | `c7c1fde` — **do not touch** |
| `jarvis-v3-backup` | `766c3dd` — the frozen, working V3. **Never modify or delete.** |

Earlier branches (`claude/web-research`, `claude/mcp-tool-calling`,
`claude/ai-connections`, `claude/v3-shortcuts-clock`, `claude/ui-*`) are all
ancestors of the V4 branch. Nothing is left behind on them.

Target device: Samsung Galaxy S25 Ultra, Chrome/PWA, portrait, 412×915 CSS
pixels, 120 Hz. Every browser test runs at that viewport.

---

## Run it on the phone

```bash
# first time
pkg install python git
git clone https://github.com/corneliuseg6-code/JARVIS-Android-.git ~/jarvis-v3
cd ~/jarvis-v3
git checkout claude/v4-agent-system

# speech (optional; JARVIS runs without it, it just will not talk)
pip install sherpa-onnx==1.13.8
pkg install espeak-ng          # provides espeak-ng-data

# the local model (optional; without it, chat needs a cloud provider)
pkg install ollama
ollama serve &
ollama pull llama3.2:3b

# every time
cd ~/jarvis-v3 && ./start.sh      # then open http://127.0.0.1:8787
./stop.sh
```

Updating:

```bash
cd ~/jarvis-v3 && ./stop.sh
git fetch origin claude/v4-agent-system
git checkout claude/v4-agent-system && git pull
./start.sh
```

**After any update, the installed PWA must be closed completely** (not just
backgrounded) or it keeps serving the old cached shell. The service-worker
cache name is bumped every release — currently `jarvis-v4-6-layout` in
`web/sw.js`. Bump it whenever `web/` changes.

---

## Architecture

Python **standard library only** — no pip packages for the server. Termux has
no compiler for most wheels, so a dependency is a broken install. The frontend
is plain ES modules: no bundler, no build step, no framework.

Application code lives in the repo. **Everything the user owns lives under
`~/.jarvis`** and is never inside the source tree, so re-cloning cannot
destroy data.

```
server/
  app.py          tiny HTTP router + SSE helper
  api.py          every endpoint; the chat stream lives here
  llm.py          provider router: ollama | openai | claude
  ollama.py       /api/chat, streaming and full, with tools + keep_alive
  openai_client.py, anthropic_client.py, claude_code.py
  toolchat.py     the MCP tool loop: catalogue, approvals, execution
  armory.py       MCP connections; mcp_client.py is the stdio/HTTP transport
  workspace.py    JARVIS's own folders (NEW)
  voices.py       sherpa-onnx discovery, gender, synthesis
  config.py       defaults + validation; paths.py  the filesystem layout
  conversations.py, vision.py, store.py

web/
  index.html      one screen; the console band over the chat panel
  css/jarvis.css  all styling, token-driven
  js/app.js       boot and wiring; window.__jarvis exposes live instances
  js/core/api.js  every fetch + the SSE client
  js/ui/          chat, folders (NEW), backdrop, status, settings, armory,
                  connections, voice, clock, shortcuts, modes, power, …
  js/avatar/      rings.js (canvas 2D), face3d.js (WebGL)
  js/i18n/        six languages, 346 keys each, all must stay in sync

tools/            MCP servers and the test suites
```

### Things that will bite you

- **CSS custom properties resolve on the element that declares them.** The
  derived values (`--dial`, `--rail-h`) are declared on `.app`, not `:root`.
  Moving them to `:root` freezes them against root's inputs.
- **`transition` is not additive.** A later rule listing the same element
  replaces the whole declaration. This silently killed the chat panel's
  collapse; see `.chat-panel` near the immersive-mode block in the CSS.
- **One context type per canvas.** RINGS is canvas 2D, FACE is WebGL, so they
  are separate canvases. The folder network is canvas 2D for the same reason.
- **A canvas in a hidden panel measures 0×0.** `folders.js` watches its canvas
  with a `ResizeObserver` because a single measurement at boot is zero.
- **Chromium shrink-to-fits a fixed box** that carries a self-alignment other
  than `stretch` — this is why `.stage` uses `place-self: stretch` in
  immersive mode.
- The layout invariants every browser test re-checks: dial **275 px**, the
  RINGS/FACE bar the same width as the dial, four shortcuts, and
  `scrollHeight <= 916` (the page must never scroll).

---

## What is done, and what was actually verified

### Tests

```bash
python3 tools/test_workspace.py     # 78
python3 tools/test_voices.py        # 53
python3 tools/test_toolcall.py      # 29
python3 tools/test_websearch.py     # 88
python3 tools/test_connections.py   # 44
python3 tools/test_provider.py      # 35   → 327 checks, all passing
python3 tools/bench_chat.py         # latency against a fixed-rate stub
```

Browser suites are Playwright scripts in the scratchpad, not in the repo —
**rewrite them when needed**; the invariants above are what they assert.

### Shipped

| Feature | State | Verified by |
|---|---|---|
| MCP tool calling end to end | done | real stdio server + `list_directory`, 29 checks |
| Approval gate on mutating tools | done | writes refused, then run once approved, single-use |
| Web research (`web_search`, `wikipedia`, `fetch_url`, `download_file`) | done | 88 checks against a local fixture |
| SSRF guard (loopback/private/link-local refused, redirects re-checked) | done | unpatched, plus live refusal through the app |
| OpenAI + Claude providers, keys server-side, cost guard | done | 35 checks |
| Streaming through the tool loop | done | TTFT 0.820 s → 0.346 s, 1 → 60 token events |
| `keep_alive`, `num_ctx`, `num_predict`, char budget | done | 35 checks |
| Per-answer timing readout | done | browser |
| Voice detection (recursive, nested, `.json`, `.ONNX`) | done | 53 checks |
| Male/Female selector, preview, 0.4× speed floor | done | browser, survives reload |
| No silent fallback to the device voice | done | off by default, announced when used |
| Amber clock | done | `rgb(255,214,10)` / `rgb(247,201,72)` |
| Collapsible chat panel (swipe/tap), input fixed | done | real PointerEvents, 421→18 px |
| Continuous background behind the collapsed panel | done | seam ≤ 1.3 brightness levels |
| **Workspace** (`~/.jarvis/workspace`, 7 sections) | done | 78 checks |
| **Holographic folder network** | done | draws the real tree, tap reveals real files |

### Not verified — be honest about these

- **No audio has ever been produced.** No sherpa-onnx and no voice model on
  any build machine. Detection and refusal are tested; synthesis is not.
- **No live web search.** Every search host is blocked by the build
  container's proxy (403 on CONNECT). Parsing is tested against a fixture
  that returns the documented shapes, not a recording.
- **No Ollama.** The latency figures are JARVIS's own overhead against a
  constant-rate stub, not model speed.
- **No real touchscreen.** Gestures are synthesised `PointerEvent`s in a
  touch-emulated Chromium. Flick momentum and Android's scroll arbitration
  are untested.
- **api.openai.com is blocked** from the build container; api.anthropic.com
  is reachable.

---

## Known gaps and next steps

### Bugs / rough edges

- The folder network's labels still crowd when many sections sit on the near
  side of the sphere. Layout is in `_layout()` in `web/js/ui/folders.js`.
- `folders.js` auto-highlight fires on a folder **name appearing in text**
  (`workspace.detect()`); a project called "Notes" would match loosely.
  Names under three characters are already ignored.
- Claude Code CLI cannot run on Termux (Android/Bionic; published builds are
  glibc/musl). The bridge detects this and offers no switch — intentional.

### V4 work still to do

1. **Phase 2 — the AI brain.** `Cloud / Local / Automatic` routing mode;
   **tools for OpenAI and Claude** (`llm.supports_tools()` is Ollama-only
   today, which blocks all orchestration); Gemini as a fourth provider;
   per-provider cost limits.
2. **Phase 3 — AI Control / Command Center.** Hierarchical dashboard with
   JARVIS as commander, per-agent ON/OFF, autonomy setting, live "who is
   doing what", honest labelling of model vs agent vs tool.
3. **Phase 4 — long-term memory.** SQLite at `~/.jarvis/memory.db`:
   decisions, preferences, open tasks, what JARVIS was doing when the app
   closed. The workspace half of Phase 4 is **done**.
4. **Phase 5** — multi-source comparison in research; integration tests.

The full plan, with the reasoning behind each phase, is in `V4-PLAN.md`.
Per-release detail is in `CHANGES.md`; everything that could not be tested is
listed in `LIMITATIONS.md`.

### Standing instructions from the user

- Never modify `main` or `jarvis-v3-backup`.
- Preserve the V3 interface: console band, rings, face, particles, border
  pulse, shortcuts, clock, voices.
- Ollama stays the free default. Paid providers off until configured.
- Never claim a tool ran unless it actually ran; never claim untested
  functionality works.
- Approval required before writing files outside the workspace, sending
  anything, installing software, or running dangerous commands.
- Work on a branch, commit, push. Do not open a PR unless asked.
