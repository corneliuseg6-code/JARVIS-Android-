# JARVIS Android V2 — what this version contains

---

# V2.6 — console and conversation

The home screen is now two things: an instrument band at the top and the
conversation under it. UI only — no backend file is touched.

| # | Asked for | What it is |
|---|-----------|------------|
| 1 | Avatar smaller, upper-right | A 64–92px panel in the console's top-right corner, on its own WebGL surface |
| 2 | Rings larger, higher, centred | 284px on a Galaxy S25 Ultra (was 229px), centred on the screen's axis and sitting under both rails |
| 3 | Status indicators down the left | SERVER, OLLAMA (the active brain), VOICE, TOOLS, VISION, stacked |
| 4 | Controls down the right | RINGS/FACE, MUTE, ACTIVE, EYES, WATCH, HOLO, FOCUS, REFLEX — eight 26px rows, none wider than a third of the screen |
| 5 | A large conversation panel | Always open, 482px on an S25 Ultra: eight messages at once, momentum scrolling |
| 6 | Only the chat bar at the bottom | The dock is the composer and nothing else — grip, header and ticker are gone |

## The dial runs behind the rails

A three-column band on a 412px screen cannot both centre a larger dial and
keep it clear of the rails. The rails are therefore translucent
(`--rail-fill`) and the dial is laid across the whole console and centred in
it, so the rings read as continuous behind the controls — as in the
reference — instead of being chopped off by a solid edge.

## Reading a long answer

The message list follows a streaming answer only while you are already at the
bottom of it. Scroll up to re-read and the stream no longer yanks you back;
sending the next message opts you back in.

## Typing

While the keyboard is up the hologram gives its corner back and the control
rail moves up into it, so the conversation keeps 257px rather than 162px on
an S25 Ultra (142px rather than 58px on a 360×800 phone). Both kinds of
Android are covered: the viewport-shrinking kind through a media query, the
older kind through the `--kb` measurement the app already made.

## One bug fixed on the way

Immersive mode came up as a black screen. The dial is now a grid item centred
in its area, and Chromium shrink-to-fits a `position: fixed` box that carries
a self-alignment other than `stretch` — with absolutely positioned children
that meant a 0×0 stage. The immersive rule resets `place-self` as well.

---

# V2.5 — the annotated reference layout

The home screen is now stacked in the order of the annotated reference, top
to bottom, with the navigation menu staying on the left and nothing but the
chat bar at the bottom.

| # | Block | What changed |
|---|-------|--------------|
| 1 | Avatar / hologram | A framed panel at the top of the column showing the holographic bust on its own WebGL surface, so the head and the dial are both on screen at once. |
| 2 | Timer / focus | A compact pause / time / stop block directly under the avatar, shown **only while a FOCUS session is running**. |
| 3 | Rings | Unchanged dial, re-budgeted to `clamp(150px, 25dvh, 240px)` to make room above it. |
| 4 | Status / model | The slim connection chips, unchanged. |
| 5 | Mode buttons | RINGS / FACE, unchanged. |
| 6 | Function buttons | EYES / WATCH / HOLO / FOCUS / REFLEX, the existing vertical compact list. |
| 7 | Chat bar | The bottom dock, unchanged. |

## The focus timer

* Pause shifts the deadline by however long the pause lasted, so a session
  still runs for the full time it was asked for, and the paused minutes are
  excluded from the summary.
* Looking away during a pause is a deliberate break, so it no longer counts
  as a drift.
* The drift count moved into the block's caption. The note under the dial is
  now only used for the drift message and the closing summary, and both clear
  themselves after eight seconds instead of sitting over the visualiser.
* New strings in all six languages: `fn.focusPause`, `fn.focusResume`,
  `fn.focusStop`, `fn.focusDrifts` (replacing the now-unused
  `fn.focusRunning`).

## Two fixes found while testing this layout

* **Immersive mode had stopped expanding.** The stage carries an explicit
  height, which beat `inset: 0` on the fixed element, so a tap switched the
  screen to immersive and left the dial card-sized in the middle of it. The
  immersive rule now resets `height` and `max-height` as well as `width`.
* **The avatar card could vanish on load.** A WebGL context can be refused
  transiently while another is being created; that was treated as permanent
  and the card was hidden for the session. It now retries once on the next
  frame and only gives the space back if the second attempt fails too, with a
  console warning rather than silence.

The avatar card hides itself when the main stage is already in FACE mode —
rendering the same head twice would cost a second draw for nothing — and in
landscape, where height is the scarce axis.

---

# V2.4 — a second brain: OpenAI alongside Ollama

Ollama stays the default: local, free, offline, private. OpenAI is now
available beside it, chosen in **Settings → Brain**.

## Modular by construction

* `server/openai_client.py` — the API client, standard library only, so
  Termux still needs no `pip install` that could fail.
* `server/llm.py` — a router over both providers. Everything above it (the
  chat endpoint, the health report, EYES) asks the router, never a provider,
  so a third one means writing a client and adding two lines here.
* Both clients already yield chunks in the same shape, so there is no format
  translation above the router.

## The key never reaches the browser

* It is stored in `~/.jarvis/secrets.json` (chmod 600) under a name, using
  the same secret store the Tool Armory already uses.
* `POST /api/llm/key` sets it. There is deliberately **no GET**: the API says
  whether a key exists, never what it is.
* `/api/config` strips any field that looks like a credential, so a key
  cannot be smuggled through the settings document the browser can read.
* `tools/test_provider.py` asserts the key is absent from the settings
  document, the provider status and the surfaced error text.

## Costs are explained before anything can be billed

Settings shows an amber notice — what OpenAI charges for, that JARVIS cannot
see your balance, that each turn resends the recent conversation so longer
conversations cost more per reply, and a pointer to OpenAI's pricing page —
with an acknowledgement that must be ticked first.

That rule is enforced in `config.update_config()`, not only in the UI: a
hand-edited `config.json` or a POST that skipped the interface still falls
back to Ollama. The new `chat_history_limit` setting (default 20) makes the
resend window adjustable, because on a metered provider it multiplies cost.

## Errors say what to do

Failures are mapped to stable codes and translated into all six languages:
no key, key rejected, out of credit, rate limited, model does not exist,
region or model not permitted, server error, timeout. The provider's own
words are appended, which is what makes a rejected key diagnosable from a
phone. Verified against synthetic HTTP 401/403/404/429/500 responses.

## Everything else still works

Romanian and the other five languages (the conversation language reaches
OpenAI through the same system prompt), voice, conversations on disk, and
EYES — vision routes through the provider too, so the camera works on either
brain, and says so when a frame will be billed.

The status chip now names whichever brain is active and turns amber when it
is connected but pointed at a model that is not there. Only the provider in
use is contacted: polling a metered API every twelve seconds to colour a chip
would be both slow and rude.

**No interface layout changed.**

## Fixes found in the closing audit

1. **The service worker cache name had not been bumped.** An already-installed
   PWA would have kept serving the previous JavaScript, so the Brain panel
   would simply never have appeared. This is the second time that bite has
   drawn blood; the name now changes with every interface release.
2. **The status chip was polling OpenAI every twelve seconds.** `/api/health`
   calls the router, and on a metered provider that meant a request across the
   internet, on mobile data, six times a minute to colour one chip. Provider
   health is now held for 60 seconds, keyed by a hash of the endpoint and the
   key so changing either invalidates it at once — without retaining any key
   material. Opening the settings panel still checks for real.
3. **The model fields were ambiguous.** "Chat model" and "Vision model" are
   Ollama's, and editing them while OpenAI was active did nothing. They are
   now named "Ollama chat model" and "Ollama vision model", under an
   "Ollama & local" heading, in all six languages.

`tools/test_provider.py` grew two checks for the polling behaviour: four
background polls must make one request, and the settings panel must bypass
the cache. 20 checks, still offline, still free.

---

# V2.3 — FACE is a real scanned head

The procedural bust is gone from the default path. FACE now renders an actual
3D human head scan through the hologram shader.

## The model

* **“Infinite”, 3D head scan by Lee Perry-Smith**
* **Creative Commons Attribution 3.0 Unported (CC BY 3.0)** — permissive,
  attribution required
* Based on a work at www.triplegangers.com
* Obtained from the three.js project (MIT), `examples/models/gltf/LeePerrySmith/`
* Licence verified from `LeePerrySmith_License.txt`, shipped alongside the
  model in that repository, and copied into this one at
  `web/models/head-LICENSE.txt`
* 9,279 vertices / 17,684 triangles / 395 KB, head **and neck and shoulders**

The attribution appears in **Settings → Credits** with a link to the full
licence. CC BY requires it, so the self-test now fails the build if either the
licence file or the in-app credit goes missing.

## Loading it

`glb.js` is a ~150-line GLB reader: container header, JSON and BIN chunks,
accessors, interleaving, and normals derived from the triangles if a file
ships without them. No 3D library is vendored — pulling in 600KB of one to
read three attributes out of a single mesh would cost more than it saves, and
it would have to be cached and maintained too.

`fitBust()` then measures the scan and rigs it, without touching the geometry:

* the width profile finds the skull's widest slice, then the neck below it,
  then the **top** of the flat neck run — which is the chin. Looking for "the
  narrowest slice from the top" instead finds the crown, which is also narrow,
  and collapses the fit to nothing. That bug made the bust invisible once.
* the scan is scaled and centred so the crown is y = +1 and the chin y = −1
* the nose is located as the front-most point near the centre line, and the
  eye line and jaw hinge are placed relative to it rather than assuming the
  scan shares the old procedural model's proportions
* per-vertex jaw and eyelid weights are derived, with the jaw falling back to
  zero below the chin so the mouth opens without dragging the throat

## Depth prepass

A hologram is additive, which is why it looks translucent — but a scanned head
has interior geometry, and additively every bit of it shone straight through
the face: eyeballs as spheres on the cheeks, the inside of the mouth as a slab
across the chin. The fix is a depth prepass — fill the depth buffer with the
nearest surface writing no colour, then run the additive colour pass against
it — so only the surface actually facing you contributes. The context now
requests a depth buffer for this.

Curves, particles and irises still ignore depth, because they are meant to
read through the surface.

## Fallbacks

Unchanged in spirit, now three deep: the scan, then the procedural bust if the
model is missing or unreadable, then the Canvas 2D head if WebGL is refused.
FACE never ends up blank because an asset failed. The procedural bust renders
immediately on entry and the scan swaps in when it has downloaded, so there is
no empty stage while a 395 KB file arrives.

The service worker caches the model and its licence, so FACE works offline.

## Also

* **"CHIP" was never a mode.** It was `avatar.face` translated into Romanian.
  RINGS and FACE are now untranslated product labels in all six languages.
* The latitude-grid overlay is gone — a regular grid over a face is what read
  as "wireframe mask".
* Scan lines coarsened and dimmed; they were aliasing into banding.

---

# V2.2 — the holographic bust, immersive mode, S25 Ultra

## FACE is now WebGL, not a wireframe

The Canvas 2D face read as a flat mask no matter how much the geometry was
improved, and the reason was structural: a wireframe has no surface, so it has
no shading, and shading is what makes a head look like a head. It has been
replaced rather than adjusted.

`head-mesh.js` builds the anatomy; `face3d.js` renders it.

* **Two swept surfaces** — head, and neck-into-shoulders — so the chin can sit
  in front of the throat. A single sweep cannot do that: both would need the
  same height.
* **Measured cross-sections.** Fourteen rings from crown to chin, nine more
  down to the chest, each with a half-width, a half-depth, a z-centre and a
  flattening exponent, interpolated with a Catmull-Rom spline.
* **Twenty-two anatomical deformers** on the face alone: brow ridge, glabella,
  temple hollow, eye sockets, eyeballs, upper and lower lids, nasion, dorsum,
  tip, alar wings, nostril sills, philtrum, both lips, the line between them,
  zygomatic arch, buccal hollow, nasolabial fold, masseter, mental
  protuberance and the crease above it — plus ears, placed sideways rather
  than forwards.
* **Short hair as 96 strands** sweeping back from a parting, stopping at a
  hairline that dips over the temples and running lower behind the ears.
* **Feature curves**: brows (four strands each), the almond of each eye and
  its crease, the bridge of the nose and its wings, the fold beside the mouth,
  the three lines of the lips, the jawline, the cords of the neck, the
  collarbones. These are what the eye reads a face by.
* **Hologram material**: fresnel rim light for volume, a key and fill
  direction for the relief (without it a nose pointing at the camera is
  invisible), drifting scan lines, a travelling scan band, interference
  flicker and occasional tearing, depth tint, drifting particles and a
  volumetric wash. Additive, no depth buffer, so front and back add together
  and the bust is genuinely translucent.
* **Animation in the vertex shader**: the jaw is a real rotation about a hinge
  behind the ear, driven by a per-vertex weight and the live voice level; the
  lids close over the eyeballs the same way. Idle yaw and pitch, and HOLO
  tilt, ride on top.
* WebGL 1, no extensions, no library, no build step — about five draw calls.

RINGS stays Canvas 2D. The two live on separate canvases, because a canvas can
only ever hold one kind of context. If WebGL is refused, FACE falls back to the
old Canvas 2D head rather than leaving the stage blank.

**What it is not:** an original stylised model, not a photoreal scan and not a
likeness of anyone. There is no licensed head model in the repository and none
is downloaded — voice models are already the largest thing here, and a
photoreal head would dwarf them.

## Immersive mode

Tap the visualiser and it fills the screen: the top bar, the status strip, the
HUD and the chat dock collapse to nothing and the stage, the only element with
flex-grow, expands into the space. Tap again, press Escape, or use the ✕ to
come back. The rows animate their height rather than switching off, so the
canvas — watched by a ResizeObserver — grows with the stage instead of jumping
at the end. A press only counts as a tap if it moved less than 12px and ended
within 400ms, so it never fires on a HOLO drag. The backend, the microphone,
the voice and the conversation all keep running.

## Galaxy S25 Ultra

* `interactive-widget=resizes-content` makes Chrome shrink the layout viewport
  when the keyboard opens, so the chat bar stays above it.
* A `visualViewport` listener covers browsers that do not, setting a `--kb`
  inset — only while an input is focused, because a pinch-zoom also shrinks
  the visual viewport and must not be mistaken for a keyboard.
* The device pixel ratio is capped: 2 for the 2D canvas, 1.75 for WebGL. The
  S25 Ultra reports 3.5, and a full-ratio buffer is around 12× the fill cost
  of a 1× one for a difference invisible on a 6.9" panel. It is the single
  biggest thing the visualiser can do to a battery.
* A frame-time watchdog measures real frames: under ~36fps it drops the bust's
  glow passes, then its mesh overlay and particles; over ~55fps it takes them
  back, with hysteresis so it cannot oscillate.

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
