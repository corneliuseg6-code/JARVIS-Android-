# JARVIS Android V2 — what this version contains

## Important context

The repository `corneliuseg6-code/JARVIS-Android-` was **empty** when this
session started: no branches, no commits, no files. No ZIP and no reference
video reached the session either (`/mnt/user-data/uploads` was empty).

There was therefore nothing from the interrupted session to preserve, repair
or continue. What follows is a complete implementation of the requested V2,
written so that your existing data drops straight in:

* conversations → `~/.jarvis/conversations/*.json` (read as-is, unknown fields kept)
* settings → `~/.jarvis/config.json` (unknown keys ignored, defaults merged)
* voices → `~/.jarvis/voices/*.onnx` + `.onnx.json` (any filename starting with
  the language code is used, so Mihai and Alan work without renaming)
* credentials → `~/.jarvis/secrets.json`

None of these live inside the project folder, so unzipping this version over
an old one cannot destroy them.

---

## Backend (`server/`)

* **Zero third-party Python dependencies.** The HTTP layer is a small router on
  `http.server` (`app.py`). On Termux the usual cause of a failed install is a
  package that needs a compiler; there is none here.
* **`mcp_client.py`** — MCP JSON-RPC 2.0 client, two transports:
  * *stdio*: spawns the server process, reader threads for stdout and stderr,
    per-request id map, timeouts, clean shutdown, last stderr lines surfaced in
    the error message.
  * *http*: streamable HTTP, handles both JSON and `text/event-stream` replies,
    tracks `Mcp-Session-Id`.
  * Handshake: `initialize` → `notifications/initialized` → `tools/list` →
    `tools/call`.
* **`armory.py`** — connection storage, live sessions, verified status.
  A connection becomes `connected` only after `initialize` *and* `tools/list`
  have answered. Failure states carry the real error text.
* **`config.py`** — settings with merged defaults, plus a separate secret store
  written with mode 600. Secrets are referenced by name; the public shape of a
  connection reports `{name: true|false}`, never a value. Two invariants are
  re-applied on every load: `mic_muted = true`, `power_state = active`.
* **`store.py`** — atomic JSON writes (temp file + `os.replace` + `fsync`), so a
  process kill cannot truncate `config.json`. A corrupt file is moved aside
  rather than silently discarded.
* **`voices.py`** — sherpa-onnx + Piper. Reports the engine's real state
  (version, import error, espeak-ng-data location), finds installed models,
  generates the `tokens.txt` Piper models do not ship (verified identical to the
  official one), keeps exactly one voice resident, and downloads a voice only on
  an explicitly confirmed request.
* **`vision.py`** — EYES through an Ollama vision model; refuses politely when
  vision is off or the model is not pulled; writes a frame to disk only when
  `save_captures` is on.
* **`ollama.py`** — chat streaming over NDJSON, model list, health. Bypasses any
  system proxy so a local Ollama is always reachable.
* **`api.py`** — the HTTP API, including SSE chat streaming and a `/api/log`
  sink that prints frontend JS errors into the Termux console.

## Frontend (`web/`)

* **Two avatars only.** `rings.js` and `face.js` are Canvas 2D renderers — no
  WebGL, no external library, works offline.
  * RINGS: two real 3D circles projected to 2D, depth-sorted brightness, tick
    marks, reactive core, pulses while listening and speaking. Drawn as three
    continuous passes per ring instead of one stroke per segment.
  * FACE: a wireframe head on an ellipsoidal surface with a tapered jaw, nose
    ridge, brow shelf and cheekbones; blinking, mouth movement while speaking,
    and a scan sweep clipped to the silhouette.
* **CUBE removed.** No cube code, no cube button. `setMode()` maps the legacy
  values `cube` / `3d` / `box` back to `rings`, and the backend rejects
  `avatar_mode: "cube"`, so an old saved preference cannot blank the stage.
* **Six languages**, `js/i18n/`. 151 keys each, no missing key in any language.
  Markup carries `data-i18n` attributes; components that build their own text
  re-render on change. Switching is instant and saved to the backend.
  Conversation language can follow the interface or be set separately, and is
  passed to the model in the system prompt.
* **Microphone**: MUTE aborts recognition, clears the auto-restart flag, stops
  every track and closes the AudioContext. Muted at startup, on standby, when
  the page is hidden, and on exit.
* **ACTIVE / STANDBY**: stops mic, speech, camera, the animation loop and
  status polling; keeps the page and server alive; resuming never reopens the
  microphone. EXIT stays a separate control.
* **Magnifier**: moved under the main menu on the left, rotated from a vertical
  to a horizontal wedge, made smaller; it scales the interface through a
  `--zoom` custom property, and the wedge's widening shape shows the amount.
* **Tool Armory UI**: add/edit/delete, connect/disconnect, live status chips,
  tool list, credential fields that are write-only from the browser.
* **EYES**: start/stop, front↔rear switch, a visible CAMERA ON indicator, and
  a Describe button that is disabled with a reason when vision is unavailable.
* **Status chips** for server, Ollama, voice, tools and vision, in four
  translated states.
* **PWA**: manifest, icons, service worker that caches the shell but never an
  API response.

## Bugs found and fixed while testing

1. **Interface overflowed the screen.** The app grid had an implicit `auto`
   column sized to the widest item, pushing every row ~265px past the right
   edge of a 412px phone; controls on the right were unreachable. Fixed with
   `grid-template-columns: minmax(0, 1fr)` and `min-width: 0` on the children.
   This is exactly the "buttons do not respond" symptom described.
2. **Canvas resize feedback loop.** The avatar canvas was given an inline pixel
   size from its parent's measured width, which made it widen the parent, which
   the ResizeObserver measured again. Fixed by setting only the backing store,
   and making the canvas `position: absolute` with `pointer-events: none` so it
   can neither affect layout nor swallow taps.
3. **Menu labels clipped** in German and Romanian — now ellipsised, with shorter
   nav labels per language.
4. **Status chips squeezed** into the title row — moved to their own scrollable
   row.
5. **Face was barrel-shaped**: the depth support was a rectangle in parameter
   space, so the outline ended in flat edges at crown and chin. Replaced with a
   single ellipsoidal support.
6. **English voice id was wrong**: `en_US-alan-medium` pointed at the en_GB
   model file. Corrected to `en_GB-alan-medium`, and `resolve_voice()` now also
   accepts any installed model whose name starts with the language code, so an
   existing Alan or Mihai keeps working.
