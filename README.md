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
│ ☰  JARVIS            ● IDLE      ⚙  │  top bar
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
| **Two visualisers** | RINGS (a layered instrument dial: tick crowns, segmented arcs, orbiting satellites, two real 3D gyro rings, reactive core) and FACE (a sculpted holographic head with rim-lit wireframe, blinking eyes and a jaw that moves with speech). There is no cube. |
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
| **Chat** | Streaming answers from Ollama, conversations saved on disk. |
| **Voice** | Offline Piper voices via sherpa-onnx, with a browser-voice fallback. |

---

## Layout

```
server/      Python backend (standard library only)
  main.py        entry point            app.py       tiny HTTP framework
  api.py         all HTTP routes        config.py    settings + secret store
  mcp_client.py  MCP JSON-RPC client    armory.py    Tool Armory
  voices.py      sherpa-onnx TTS        vision.py    EYES / local vision
  ollama.py      Ollama client          conversations.py
web/         Frontend (plain ES modules, no bundler)
  index.html  css/jarvis.css  sw.js  manifest.webmanifest
  js/avatar/   rings.js  face.js  index.js
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
* A tool connection is shown as CONNECTED only after the MCP handshake and
  `tools/list` have both answered.
* No model and no voice is ever downloaded without you confirming it.

---

## Documentation

* [INSTALL-TERMUX.md](INSTALL-TERMUX.md) — Android install, step by step
* [CHANGES.md](CHANGES.md) — what this version contains
* [LIMITATIONS.md](LIMITATIONS.md) — what is tested, and what is not
