# Tested, and not tested

Nothing below is a guess: "verified" means it was executed in this session and
the result was observed.

---

## Verified working

### Backend (Python 3.13, Linux x86_64)

| What | How it was verified |
|---|---|
| Server starts, serves the PWA | all static routes return 200, unknown paths 404 |
| `/api/health` | reports the real state of each subsystem, including failures |
| Settings persist | written, server restarted, values still there |
| `mic_muted` / `power_state` | cannot be changed through `/api/config`; forced back on every load |
| `avatar_mode: "cube"` | rejected by the backend, falls back to `rings` |
| Conversations | created, appended, listed and reloaded after a restart |
| Chat streaming (SSE) | full token stream, answer saved to the conversation |
| Ollama down | streams a clean `ollama_error` event; the interface does not hang |
| Atomic writes | `config.json` written via temp file + rename |
| `/api/tts` with no voice | returns 409; the frontend falls back instead of failing |
| Standby | `/api/tts` and vision refuse with 409 while in standby |

### Tool Armory / MCP — the work that was interrupted

| What | How it was verified |
|---|---|
| stdio transport | real handshake against `tools/mock_mcp_server.py` |
| `initialize` + `tools/list` | server info and 2 tools returned and displayed |
| `tools/call` | `echo` called through the API, correct result returned |
| Credential injection | the mock server refuses to start without its variable; it starts only when the stored secret is injected |
| Missing credential | reports `missing credential: ABSENT_KEY`, state `error`, **not** connected |
| Bad command | reports `command not found`, state `error` |
| Disconnect | process terminated, tool list cleared |
| Autoconnect | connection re-established automatically after a server restart |
| Credentials never leak | `/api/armory`, `/api/config`, `/api/health`, `/api/diagnostics` and `connections.json` all checked for the secret value — clean; `secrets.json` is mode 600 |

### Voice — including the sherpa-onnx question

| What | How it was verified |
|---|---|
| sherpa-onnx 1.13.8 | installed and imported; the API this code uses (`OfflineTtsConfig`, `OfflineTtsVitsModelConfig(model, tokens, data_dir)`, `OfflineTts.generate(text, sid, speed)`) matches exactly |
| Real synthesis | a Piper voice produced 2.81 s of audible speech (peak 20322, RMS 2797 — not silence) |
| Through the API | `POST /api/tts` returned 92 606 bytes of `audio/wav` |
| Generated `tokens.txt` | Piper models from Hugging Face ship no `tokens.txt`; the one generated from `phoneme_id_map` is **identical** to the official file (130 lines, 0 mismatches) |
| Legacy voice names | a model named `en_US-alan-medium` that is not in the catalog is still found and used |

### Frontend (Chromium, mobile user agent, fake mic/camera devices)

Checked at **412×915** and **360×800** against the running backend:

* loads with zero JavaScript errors, zero missing translation keys (177 keys in
  each of the six languages)
* RINGS renders; FACE renders; no cube button; legacy `cube` falls back to rings
* **nothing off screen** in either size — every visible control's rectangle is
  inside the viewport — and the settings sheet does not scroll sideways
  (`scrollWidth == clientWidth`)
* the visualiser is the element that grows: 392×645 of a 412×915 screen,
  344×530 of a 360×800 one
* all six languages translate the drawer, the function switches, buttons,
  placeholders, status and the mic/power labels; the choice reaches the backend
* microphone muted at startup; MIC ON opens a live track; MUTE ends the track
  (`readyState: "ended"`), clears recognition and the restart flag; still muted
  after a reload; cannot be enabled in standby
* standby pauses the animation loop, shows the veil and switches all five
  function switches off; resume restarts the animation and does **not** reopen
  the microphone
* the drawer opens and closes; every panel opens in the sheet; the armory card
  shows CONNECTED with its tools and no credential value; chat streams an
  answer into the bubble; the dock expands and collapses
* camera starts, indicator appears, stopping ends the video track
* HOLO moves the projection on a drag and on a `deviceorientation` event;
  FOCUS counts down and renders the drift tally; REFLEX opens the microphone
  and its gate returns `null` for "hello there" and `"what time is it"` for
  "Jarvis, what time is it"

---

## Not tested — and why

1. **No real Android phone.** Everything above ran in a Linux container with
   Chromium. Chromium on Android is the same engine, but these need your
   device:
   * the actual Termux install path and `pkg` availability
   * real microphone and camera hardware and their permission prompts
   * Android's speech recognition (`webkitSpeechRecognition` needs network and
     Google services on most devices — if unavailable, MIC ON still records
     and shows a level, but no transcript appears, and the interface says so)
   * real battery behaviour

2. **sherpa-onnx on Termux (aarch64/bionic).** Verified on x86_64 glibc. PyPI
   wheels are built for manylinux; Termux uses Android's bionic libc, so the
   wheel may download and then fail to import. INSTALL-TERMUX.md §5.1 gives the
   three options. The code handles the failure cleanly — Settings → Voice shows
   the exact import error and speech falls back to the browser voice.

3. **Mihai and Alan are not in this package.** No ZIP reached the session, so
   the models could not be carried over, and voice models are too large to
   commit to git. Copy your existing files into `~/.jarvis/voices/` and they are
   picked up. The config already points at them by name.

4. **French, German, Spanish and Italian voices are not installed.** They are
   listed with sizes and a Download button; nothing downloads on its own. The
   download code path (HTTP fetch, progress polling, `.part` rename, cleanup on
   failure) is implemented but was only exercised against the catalog URLs for
   reachability, not run to completion for each voice.

5. **`qwen3-vl:2b-instruct` was not run.** No Ollama build was available here,
   so the chat path was verified against `tools/mock_ollama.py` instead. Whether
   your Termux Ollama supports that architecture has to be checked on the
   phone — `ollama pull qwen3-vl:2b-instruct` fails fast if not. Any vision
   model Ollama supports can be set in Settings instead. Vision is off by
   default so a second large model is never loaded behind your back.

6. **Third-party MCP servers are untested.** The client was verified against a
   mock server covering the full handshake. Real servers
   (`@modelcontextprotocol/server-filesystem`, GitHub, …) need Node.js on the
   phone and were not run here. The HTTP transport in particular was only
   exercised through unit-level paths, not against a live remote MCP server.

7. **PWA installation and offline start** were not tested on a phone: the
   service worker and manifest are correct and served with the right types, but
   "Add to home screen" behaviour is device-specific.

---

## The five function switches — what each one can actually do

| Switch | On Android | Why |
|---|---|---|
| **EYES** | Works | `getUserMedia` on a secure origin; `127.0.0.1` counts as one. |
| **WATCH** | **Does not work** | Android browsers do not implement `getDisplayMedia`. The switch detects this, dims itself, says so in its own label and refuses to turn on. It works in the desktop/PWA build, where it also feeds a still to the local vision model. |
| **HOLO** | Works | Pointer drag always; `deviceorientation` additionally where the phone exposes the sensor. It turns the visualiser — it is not hand tracking, and nothing here claims it is. |
| **FOCUS** | Works, within one real limit | A web page can only see that **JARVIS itself** lost focus (`visibilitychange`). It cannot see which other app you switched to, and it cannot watch a browser tab in a different app. So "drift" means "you left JARVIS for more than two seconds", and that is exactly what the message says. |
| **REFLEX** | Works where speech recognition works | It gates transcripts on the wake word. The transcripts come from the browser's own speech recognition, which on most Android devices needs Google speech services and a network connection (see point 1 above). With no recognition available, REFLEX turns on, the microphone opens and the level meter moves, but no transcript arrives — and the interface says so rather than looking broken. |

Standby, hiding the page and EXIT switch all five off and release every device
they hold.

---

## Known limitations by design

* Chat does not yet call MCP tools automatically. The Armory connects, lists
  and runs tools through the API, but the model is not given a tool loop —
  that is the obvious next step, and pretending otherwise would be the kind of
  claim this project is trying to avoid.
* The magnifier scales the interface (fonts, controls, panels). It does not
  magnify the visualiser canvas, which always fills its stage.
* The FACE head is an original parametric model, not a scan or a likeness of
  anyone. It is built from a measured cross-section profile and a handful of
  smooth deformations, so it is adjustable but it is not photoreal.
* Speech recognition uses the browser engine. There is no offline STT; on a
  device without Google speech services, MIC ON records and animates but
  produces no transcript — the interface says so rather than looking broken.
* The server is HTTP on localhost. Over `--lan`, Chrome will block the camera
  and microphone on other devices, because they require a secure context.
* `window.close()` after EXIT only works if the page was opened as a PWA or by
  a script; in a normal tab the interface closes and the tab stays.
