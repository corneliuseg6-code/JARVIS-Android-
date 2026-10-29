# JARVIS Android V2

A local, offline-first JARVIS assistant for Android (Termux + Chrome/PWA).
Python backend, no third-party Python packages required, no build step for the
frontend.

```
./install-termux.sh     # one-time setup (optional extras are each confirmed)
./start.sh              # start the server
                        # open http://127.0.0.1:8787 in Chrome
./stop.sh               # stop the server
```

---

## The interface

The whole screen is one column, and only the visualiser grows:

```
┌──────────────────────────────────────┐
│ ☰ AI☰ JARVIS         ● IDLE   ⛶  ⚙  │  top bar
│ ●SERVER ●OLLAMA ●VOICE ●TOOLS ●VISION│  status strip (scrolls sideways)
│                                      │
│                                      │
│            ╭──────────╮              │
│            │  RINGS   │              │  THE VISUALISER
│            │    or    │              │  (the only element that grows)
│            │   FACE   │              │
│            ╰──────────╯              │
│                                      │
│             ( RINGS | FACE )         │  mode, directly underneath
│     ┌───────────┬──────────────┐     │
│     │   MUTE    │    ACTIVE    │     │  mic + power
│     └───────────┴──────────────┘     │
│  ┌────┬─────┬─────┬──────┬────────┐  │
│  │EYES│WATCH│HOLO │FOCUS │ REFLEX │  │  the five functions
│  └────┴─────┴─────┴──────┴────────┘  │
│  ┌────────────────────────────────┐  │
│  │ ───                            │  │  chat bar — drag the handle
│  │ Write to JARVIS…         ➤     │  │  to expand the conversation
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘
```

The navigation is a **collapsible drawer** (☰) and the panels — Tool Armory,
Eyes, Settings — are a **bottom sheet**. Neither sits in the column, so neither
can take space from the visualiser or push a control off the screen.

---

## What it does

| Area | Status |
|---|---|
| **Two visualisers** | RINGS (a layered instrument dial: tick crowns, segmented arcs, orbiting satellites, two real 3D gyro rings, reactive core) and FACE (a real scanned human bust — head, neck and shoulders — rendered in WebGL as a blue hologram: fresnel rim, directional shading, scan sweep, particles, blinking, and a jaw that opens with your voice). The mode names are RINGS and FACE in every language; there is no cube and no third mode. |
| **Immersive mode** | Tap the visualiser and it fills the screen; every control folds away. Tap again to bring them back. |
| **EYES** | Camera with front/rear switch, a visible indicator, local vision through Ollama. |
| **WATCH** | Screen capture through `getDisplayMedia`. **Android browsers have no such API** — the switch says so and stays off rather than pretending. |
| **HOLO** | Drag the projection, or tilt the phone, and the visualiser turns with it. |
| **FOCUS** | A timed session. Leaving JARVIS counts as a drift; JARVIS tells you when you come back, and reports the tally at the end. |
| **REFLEX** | Hands-free. The microphone stays open and only an utterance containing the wake word is acted on. |
| **Six languages** | RO · EN · FR · DE · ES · IT, switched instantly, saved between sessions. |
| **Microphone** | Explicit ON / MUTE. **Muted at every start**, never re-enabled on its own. |
| **Active / Standby** | Standby stops mic, speech, camera, every function switch and the animation; the server and your data keep running. |
| **Magnifier** | In the drawer: a compact horizontal wedge that scales the whole interface. |
| **Tool Armory** | MCP connections over stdio or HTTP, with credentials kept on the backend. |
| **Chat** | Streaming answers, conversations saved on disk. |
| **AI TOOLS menu** | The **AI ☰** button (top left) opens a slide-out with every AI model, Claude Code, OpenArt and the MCP connections: live status, Use / Test / Connect, and a link to each one's settings. |
| **Four brains** | **Ollama** (local, free, offline — the default), **OpenAI**, **Claude** and **Gemini** (paid APIs, each off until switched on, acknowledged and keyed). Routing: Manual, Local only, Cloud only or Automatic, with fallback and a stated reason on every answer. MCP tools work on all four. Daily/monthly spending limits stop a paid brain before the call. Keys live only on the phone's backend. See **SETUP-V4-BRAIN.md**. |
| **Claude Code agent** | Develops, edits and repairs projects in one working directory through the official `claude` CLI, only after you approve the exact command. Shows live progress, cost and a diff; edit runs can be reverted. JARVIS can queue a task from the chat; it never runs one itself. |
| **OpenArt** | Image and video generation and editing through OpenArt's official MCP server, signed in on OpenArt's own page. Every credit-spending call waits for your approval; results are saved to the workspace and shown in the chat. |
| **Voice** | Offline Piper voices via sherpa-onnx, with a browser-voice fallback. |

---

## Layout

```
server/      Python backend (standard library only)
  main.py        entry point            app.py       tiny HTTP framework
  api.py         all HTTP routes        config.py    settings + secret store
  llm.py         provider router + routing    openai_client.py  OpenAI (stdlib only)
  anthropic_client.py, gemini_client.py       Claude and Gemini (stdlib only)
  usage.py       spend ledger + limits        oauth.py     MCP sign-in (OAuth 2.1 + PKCE)
  claude_code.py Claude Code bridge           media.py     tool media → workspace
  mcp_client.py  MCP JSON-RPC client    armory.py    Tool Armory
  voices.py      sherpa-onnx TTS        vision.py    EYES / local vision
  ollama.py      Ollama client          conversations.py
web/         Frontend (plain ES modules, no bundler)
  index.html  css/jarvis.css  sw.js  manifest.webmanifest
  js/avatar/   index.js  rings.js        (Canvas 2D dial)
               face3d.js glb.js          (WebGL hologram + .glb reader)
               head-mesh.js face.js      (fallbacks: procedural, then 2D)
  models/      head.glb  head-LICENSE.txt
  js/i18n/     ro en fr de es it + index.js
  js/ui/       mic power modes magnifier chat armory eyes settings status voice toast
  js/core/     api.js  state.js  bus.js
tools/       selftest.py  mock_mcp_server.py  mock_ollama.py
```

Your data never lives in the project folder:

```
~/.jarvis/
  config.json        settings (safe to read)
  secrets.json       API keys, chmod 600, never sent to the browser
  connections.json   MCP connections (names only, no credential values)
  conversations/     one JSON file per conversation
  voices/            Piper .onnx + .onnx.json models, espeak-ng-data/
  captures/          only if you explicitly turn capture saving on
```

Re-installing or unzipping a new version therefore cannot delete your
conversations, settings or voices.

---

## Commands

| Action | Command |
|---|---|
| Start (phone only) | `./start.sh` |
| Start, reachable from your Wi-Fi | `./start.sh --lan` |
| Start on another port | `JARVIS_PORT=9000 ./start.sh` |
| Start without opening MCP connections | `./start.sh --no-autoconnect` |
| Stop | `./stop.sh` (or Ctrl-C in the same session) |
| Check what works | `python3 tools/selftest.py` |
| Check the AI providers | `python3 tools/test_provider.py` (offline, costs nothing) |
| Test without Ollama | `python3 tools/mock_ollama.py` in a second session |

The server binds to `127.0.0.1` by default: nothing on your network can reach
it until you pass `--lan`.

---

## Privacy rules the code enforces

* The microphone is muted at startup, on standby, when the page is hidden and
  on exit — and every audio track is stopped, not just ignored.
* Resuming from standby never re-opens the microphone.
* REFLEX only ever opens the microphone on your tap, and closes it again when
  you switch REFLEX off.
* The camera stops on disable, on standby, when the page is hidden, and when
  you leave the EYES view.
* Screen capture stops when the page is hidden, when you stop it from the
  browser's own bar, and on standby.
* Camera frames are never written to storage unless you turn that on.
* Credentials live in `~/.jarvis/secrets.json` (chmod 600). The browser can
  set a value and is told whether one exists — it never receives the value.
  That includes the OpenAI API key: there is deliberately no endpoint that
  returns it, and `/api/config` rejects any attempt to carry one.
* A paid provider cannot be switched on until its cost notice is
  acknowledged. That rule is enforced in the backend, so a hand-edited
  `config.json` cannot start spending money either.
* A tool connection is shown as CONNECTED only after the MCP handshake and
  `tools/list` have both answered.
* No model and no voice is ever downloaded without you confirming it.

---

## Credits

The FACE bust is **“Infinite”, a 3D head scan by Lee Perry-Smith**, licensed
**Creative Commons Attribution 3.0 Unported (CC BY 3.0)**, based on a work at
www.triplegangers.com and obtained from the [three.js](https://github.com/mrdoob/three.js)
project (MIT), `examples/models/gltf/LeePerrySmith/`.

The licence travels with the model at `web/models/head-LICENSE.txt` and the
credit is shown in **Settings → Credits**. CC BY requires that attribution, so
neither may be dropped from a build.

Everything else — the rings, the shaders, the interface and the backend — is
original to this project.

---

## Documentation

* [INSTALL-TERMUX.md](INSTALL-TERMUX.md) — Android install, step by step
* [CHANGES.md](CHANGES.md) — what this version contains
* [LIMITATIONS.md](LIMITATIONS.md) — what is tested, and what is not
