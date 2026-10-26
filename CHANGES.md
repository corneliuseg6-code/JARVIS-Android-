# JARVIS Android V2 — what this version contains

---

# V2.1 — the interface redesign

Built against the reference recording and its transcript, which were analysed
frame by frame in this session. The backend, the Termux launcher, the Ollama
integration, the six languages, the voice system, the camera, the MCP Tool
Armory and the microphone privacy rules are all unchanged and still working —
this release rebuilds the interface on top of them.

## The visualiser

**RINGS** was two tilted circles. It is now a layered instrument dial, drawn in
eight passes that each move at their own speed and direction:

1. an outer crown of 120 radial graduations, every fifth one long
2. four bright arc segments with gaps, turning slowly against the crown
3. a denser graduation ring running the other way
4. a thin orbit ring carrying six satellites
5. a sweep arc with a gradient tail that accelerates while JARVIS is thinking
6. an inner dashed ring
7. two real 3D circles, depth-sorted, for volume
8. a reactive core with the centre label, a breathing outline, and — while
   listening or speaking — a closed radial waveform built from the live
   microphone level

The label in the middle is a setting (`hud_label`), so it is yours to change.

**FACE** was a wireframe on a hemisphere, which is why it read as a mask. It is
now a real sculpted surface:

* the head is a stack of twelve measured cross-sections (half-width, half-depth
  and a z-centre at each height) interpolated with a Catmull-Rom spline, so it
  has a cranium, cheekbones, a jaw angle and a chin instead of an egg outline
* smooth deformations add the brow ridge, eye sockets, nose bridge and base,
  philtrum, lips, cheekbones and chin, all faded out towards the ears
* features sit at the usual proportions: eye line at mid height, base of the
  nose two thirds down the face, mouth between that and the chin
* every vertex carries a normal, and shading is **rim-weighted** — the segments
  turning away from you are the bright ones, which is what gives a wireframe
  the back-lit, glassy look instead of a flat net
* the outline is taken from the mesh itself, at the points where the normals
  stop facing the camera, so it stays correct as the head turns
* blinking, a lid crease, an iris that drifts while thinking and widens while
  listening, an easing jaw that opens with speech, and a scan sweep

Both renderers stay Canvas 2D and batch their geometry: the mesh is built once
with its normals, and the thousands of segments are stroked as four
brightness-bucketed paths per frame rather than one path each.

## Layout

* The navigation is a **collapsible drawer** behind ☰, with the magnifier, the
  language picker and EXIT inside it.
* The **visualiser is the central focus** and the only element that grows.
* **RINGS / FACE** sits directly below it, as a compact segmented control.
* **MUTE** and **ACTIVE / STANDBY** sit directly underneath that.
* **EYES · WATCH · HOLO · FOCUS · REFLEX** form one compact five-across group.
* The oversized chat panel is now a **small bottom bar**: a rounded input, a
  round send button, and the last thing JARVIS said shown as a subtitle. Drag
  the handle (or tap the input) and it expands into the full conversation.
* Tool Armory, Eyes and Settings became a **bottom sheet** instead of rows in
  the column — which is what used to squeeze everything else.
* Borders, padding and button sizes were cut throughout; the whole interface
  fits a 360×800 phone with nothing off screen and nothing to scroll.

## The five functions

| Switch | What it really does |
|---|---|
| **EYES** | Opens the Eyes panel and starts the camera. Real. |
| **WATCH** | `getDisplayMedia`. **Android browsers do not implement it**, so on a phone the switch is dimmed and says so; it works in the desktop/PWA build, where it also feeds the local vision model. |
| **HOLO** | Drag the stage, or tilt the phone, and the projection turns. Device orientation is eased and ignores the null readings a sensorless device reports. Real. |
| **FOCUS** | A session of `focus_minutes`. Leaving JARVIS for more than two seconds counts as a drift; JARVIS says so when you come back and gives the tally at the end. It can only see you leave *JARVIS* — a web page cannot watch other Android apps, and this does not claim to. |
| **REFLEX** | Opens the microphone and gates every transcript on the wake word, so "Jarvis, what time is it" reaches the model as "what time is it" and anything without the wake word is ignored. Real. |

Each switch reports its own state in its label. Standby, hiding the page and
EXIT all switch every one of them off.

## Backend

Three settings were added and are validated and clamped on load and on save:
`wake_word` (default `Jarvis`), `focus_minutes` (1–240, default 30) and
`hud_label` (default `J.A.R.V.I.S.`). Everything else is untouched.

## Bugs found and fixed while testing this release

1. **A malformed `\u` escape** in the Romanian translation stopped the whole
   module graph from parsing — the interface loaded blank. Found by the
   Chromium console check, not by reading.
2. **`[hidden]` did nothing** on any component whose CSS sets `display`: the
   Stop button sat next to Send permanently, and the camera indicator would
   have done the same. One global `[hidden] { display: none !important }`
   removes the entire class of bug.
3. **The dock handle was unreachable when the dock was closed**, because it
   lived inside the header that only appears when it is open. It is now the
   full top edge of the bar.
4. **Settings overflowed a 360px screen sideways**: `<select>` and
   `<input type=number>` outside a `.field` wrapper fell back to the platform's
   light theme at their natural width. Controls are now styled by element
   inside the sheet, and the voice rows wrap.

## Verified in this session

Chromium at 412×915 and 360×800 with a mobile user agent, against the real
backend:

* zero JavaScript errors, zero missing translation keys (177 in each of the six
  languages)
* no element off screen in either size; the sheet does not scroll sideways
* the visualiser takes 645px of 915 and 530px of 800
* HOLO turns the projection on drag and on device orientation; FOCUS counts
  down and reports drift; REFLEX opens the mic and its wake-word gate accepts
  "Jarvis, …" and rejects everything else; STANDBY switches all five off and
  mutes the microphone
* `tools/selftest.py`: 56 passed, 0 failed

---

# V2.0

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
