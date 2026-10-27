# JARVIS Android V2 — what this version contains

---

# V4 Phase 1c — three interface changes

## The clock is amber

`--hud-amber: #ffd60a` for the time, `--hud-amber-dim: #f7c948` for the line
below it, both with a warm glow. The weekday, day and month share one element
(`#hud-date`), so one rule covers all three. About 13:1 against the page
background — readable at a glance, and it now reads as an instrument rather
than as more chrome. Position, size and the ticking seconds are unchanged.

## The black rectangle behind the rings

Worth being precise about, because the answer was not where it was expected.

**The rings container was already transparent.** Measured in the running app:
`#stage` and both canvases compute to `rgba(0, 0, 0, 0)`, `rings.js` only ever
calls `clearRect`, and the particle field draws *more* densely inside the dial
than below it (2 509 lit pixels in the stage square). The box removed in
`766c3dd` has not come back.

What *is* a solid near-black rectangle over the rings is **the standby veil**.
`.standby-veil` is `position: absolute; inset: 0` inside `#stage`, filled with
a flat `rgba(2, 8, 14, .9)` — a 275×275 opaque box sitting exactly where the
rings are, shown whenever JARVIS is in standby. It is the only element in the
stage that has ever painted one.

It is now a radial fade with a matching mask: dark enough at the centre to
read the STANDBY notice, transparent at the corners, so the particle field
shows through and the dial never reads as a rectangle. The notice, the Resume
button and the blur all behave as before.

A test now asserts that **nothing** inside `#stage` computes a background with
alpha above 0.05, in both active and standby, so this cannot quietly return.

If a black box is still on screen after this update, it is a stale cached
build rather than a style: the service worker cache is bumped to
`jarvis-v4-3-ui`, and an installed PWA may need to be closed completely (not
just backgrounded) before the new shell is picked up.

## The conversation collapses

A grip sits at the top of the conversation panel. Drag it down and the panel
slides away; drag up, or tap it, and it comes back.

- **The input bar cannot move.** `.dock` is a sibling of the workspace, not a
  child of the panel, so collapsing the panel does not touch it. Verified: the
  dock's y is identical before and after (868 px both times) and both the
  input and Send stay visible.
- **Scrolling still works.** A drag that starts inside the message list is
  only treated as a collapse when the list is already scrolled to the top and
  the finger is moving down. Mid-conversation, a swipe scrolls and the panel
  does not move — verified with a 40-message list at `scrollTop 1454`.
- **Pointer events, not touch events**, so one code path serves a finger on
  Android and a mouse on a desktop. The grip carries `touch-action: none`;
  without it Android claims the vertical drag for a page pan before the panel
  sees it.
- The drag follows the finger, and a release under 44 px snaps back — a wobble
  is not a command.
- Collapsed, the console band centres in the freed space: the dial moves from
  y 103 to y 305 rather than leaving a hole at the bottom.
- Sending a message reopens the panel, so a reply never streams into a closed
  drawer.
- The state is remembered per device in `localStorage`, wrapped in try/catch
  so private mode cannot break the panel.
- `prefers-reduced-motion` turns the slide off.

## Verified

In the running app at 412×915 with touch emulation, driving real
`PointerEvent`s with `pointerType: 'touch'`:

| | Result |
|---|---|
| Clock colour | time `rgb(255, 214, 10)`, date `rgb(247, 201, 72)`, seconds still ticking |
| Nothing opaque in the stage | zero elements with background alpha > 0.05 |
| Particles behind the rings | 2 509 lit pixels inside the dial |
| Standby | radial fade plus mask, not a flat fill |
| Swipe down | panel 421 px → 18 px, state `collapsed` |
| Input bar | y 868 → 868, input and Send still visible |
| Swipe up | back to 421 px, state `open` |
| Tap the grip | collapses, and tapping again reopens |
| Swipe mid-scroll | `scrollTop` unchanged at 1454, panel stays open |
| Swipe from the top of the list | collapses |
| Send while collapsed | reopens |
| V3 layout | dial 275 px, RINGS/FACE bar matching, four shortcuts, no page scroll |

All five Python suites still pass (249 checks), as do the voice and
streaming browser suites.

**Not verified: a real finger on a real Galaxy.** The gestures are driven by
synthesised pointer events in a touch-emulated Chromium, which exercises the
same handlers Android does but not Android's own scroll arbitration. Flick
momentum and the interaction with the system back-gesture at the screen edge
have to be felt on the phone.

---

# V4 Phase 1b — the voice system

Four symptoms were reported. They had three causes, and the most important
one explains the strangest symptom.

## Why JARVIS spoke in a female voice that could not be changed

The only installed Romanian voice is **Mihai, who is male**. So the female
voice being heard was never JARVIS's — it was Android's own.

`voice.js` used to call `_browserFallback()` automatically whenever backend
speech failed, and said nothing when it worked. Android then picked its own
Romanian voice, which on a Galaxy is female. No setting could change it,
because no setting in JARVIS controlled it.

The device voice is now **off by default** (`tts_browser_fallback`), and when
it is switched on and used, a notice names the voice and says it is the
device's, not JARVIS's. A backend failure is now explained in the words the
backend used — *"no voice of that gender is installed for this language;
available: male"* — instead of a flat "voice unavailable".

## Why Mihai and Alan showed as "Not installed"

The scan looked only at the **top level** of the voices folder and insisted on
exactly `<id>.onnx` + `<id>.onnx.json`. Three realistic layouts were therefore
invisible, which was verified by planting each one on disk:

| On disk | Before | Now |
|---|---|---|
| `ro_RO-mihai-medium.onnx` (flat) | found | found |
| `en/en_GB/alan/medium/en_GB-alan-medium.onnx` (as HuggingFace delivers it) | **invisible** | found |
| config named `<id>.json` instead of `<id>.onnx.json` | **invisible** | found |
| `.ONNX` from a manual copy | **invisible** | found |

The nested form is exactly what a HuggingFace download produces, which is why
Alan in particular went missing. The scan is now recursive, accepts either
config name, and is case-insensitive on the extension. A voice found anywhere
under the folder is used from where it actually is.

There is also a new **"Voices on this device"** section in Settings listing
the search folder, every voice found with its real path and size, and any
model skipped for a missing config. A voice reported missing that is in fact
installed cannot be diagnosed from a phone without that.

## Why the engine showed "—"

`settings.refresh()` set `this.voices = null` on *any* failed request, and the
engine line then rendered a bare dash. One slow request was enough to make a
working install look absent — the "sometimes" in the report. The last good
reading is now kept, the dash is gone, and the line always says something
real: the engine and version, or why it could not be checked, or that the
reading is stale.

## Male / Female

Every catalogue entry already carried a `gender`; nothing exposed it. There is
now a gender selector per language, and it **only offers genders that are
actually installed**. Where a language has just one, it says so — *"only male
available offline"* for Romanian — rather than offering a choice that cannot
be honoured. Asking for a gender with no installed voice returns
`gender_unavailable` and names what is available; it is never answered with
the other gender.

A voice kept from an older install is recognised by its speaker name, so a
legacy `en_US-alan-medium` is still Alan and still male, and selectable.

**Romanian is male-only offline.** Piper publishes no female Romanian voice
that this project has verified, and the Piper index is unreachable from the
build machine, so none was added — a download button that 404s would be worse
than an honest message.

## Preview, and a slower slider

Each installed voice has a **Preview** button that speaks a sample in that
voice at the rate currently on the slider, before it is saved. If it fails it
says which voice failed and why, and does not fall back to another.

The speed slider was 0.6–1.6 in steps of 0.1. It is now **0.4–1.6 in steps of
0.05**, with the value shown (`0.45×`) and marked *slow* at the bottom end.
The backend floor moved from 0.5 to 0.3 so the slider's range is real rather
than clamped away.

## Verified

`python3 tools/test_voices.py` — 53 checks, no models or sherpa-onnx needed:
all four on-disk layouts, a model with no config rejected *and reported*, path
traversal refused, gender resolution including the Romanian refusal, legacy
id recognition, clamping of bad speeds and genders, and settings surviving a
cache drop.

In the running app (Playwright, 412×915), with Mihai planted flat, Alan
planted **nested** and Amy flat:

- Romanian row: `Any / Male`, no female option, *"only male available
  offline"*, Mihai listed and marked installed;
- English row: `Any / Male / Female`, **Alan listed** despite being nested;
- preview buttons on both installed rows;
- slider reports `min 0.4, step 0.05`, readout `1.00×`;
- engine line reads *"sherpa-onnx: not importable: No module named
  'sherpa_onnx' · Install with: pip install sherpa-onnx==1.13.8"* — correct
  for this container and actionable;
- choosing Female for English and 0.45× speed **survived a page reload**;
- V3 interface untouched: dial 275 px, RINGS/FACE bar matching, clock
  running, four shortcuts, no page scroll.

**Not verified: audio actually coming out.** There is no sherpa-onnx and no
63 MB voice model on the build machine, so no sound was produced here —
every voice in the tests is a few zero bytes named like a model, which tests
detection and refusal but not synthesis. The preview button, the 0.4× rate
and Mihai's and Alan's real voices have to be heard on the phone. What the
tests do guarantee is that if synthesis fails there, JARVIS will say so
rather than quietly speaking in Android's voice.

---

# V4 Phase 1 — speed

The complaint was that local answers appear very slowly. Three causes were
found in the code, not guessed at, and the first one is most of it.

## The tool loop did not stream at all

`toolchat.run()` called `llm.chat_once_full()`, which is non-streaming,
because the loop needs `tool_calls` intact and a streamed turn delivers them
in pieces. So with **any** MCP server connected, the whole answer was withheld
until generation finished. That is why chat got dramatically slower the moment
a tool server was added — the thing the user noticed.

`ollama.chat_stream_full()` now does both: it yields tokens as they arrive and
assembles the complete message, tool calls included, from the same stream.

Measured with `tools/bench_chat.py`, against a stub that emits tokens on a
fixed schedule (300 ms to first token, 8 ms per token, 60 tokens — so the
floor is ttft 0.300 s):

| | ttft before | ttft after | token events before | after |
|---|---|---|---|---|
| plain chat | 0.344 s | 0.332 s | 60 | 60 |
| **with an MCP server connected** | **0.820 s** | **0.346 s** | **1** | **60** |

0.820 s was the entire generation time: one lump, at the end. The harness's
own share is now 33–46 ms on both paths. On a phone where a 3B model generates
for twenty seconds, this is the difference between a blank screen for twenty
seconds and the first words inside a second.

## The model was being unloaded between messages

No `keep_alive` was ever sent, so Ollama unloads after about five minutes idle
and the next message pays a multi-gigabyte reload before its first token. Now
sent on every request, default `30m`, configurable.

## No request tuning, and a window that could not see length

`num_ctx` and `num_predict` are now settable (0 and -1 mean "leave it alone",
so nothing changes until the user opts in), and history has a second brake:
`chat_char_budget`, default 6000 characters. A message count cannot tell
twenty short turns from twenty long ones, and the model re-reads all of it
every turn.

## Timings, visible

Each answer now carries a quiet line underneath: *first word 0.4s · 3.2s
total*. Two numbers because they mean different things — a long time to the
first word with a quick finish is the model loading or thinking, the reverse
would be JARVIS getting in the way.

## Transcript ordering

Because text now streams during tool rounds, a turn can produce several runs
of text around a tool card. Each run is its own bubble, in the order things
happened, instead of text being moved around a card that arrived after it.

## Not a cause

Voice was checked and left alone: speech already starts when the text is
complete, which is correct for a single TTS request.

## What was measured, and what was not

There is no Ollama on the build machine, so the *model's* generation speed
could not be measured and the table above is JARVIS's own overhead against a
constant-rate stub. That is the number these changes move. Real end-to-end
timings have to come from the phone — which is what the per-answer readout is
for.

Verified in the running app (Playwright, 412×915): the answer text grows in
9 observable steps with first paint at 215 ms, the timing line appears,
`list_directory` still runs for real and its output still reaches the answer,
and the V3 interface is untouched — dial 275 px, RINGS/FACE bar matching,
clock running, all four shortcuts present, no page scroll.

---

# V3.6 — internet research

JARVIS can now search the web, read public pages and answer with the links it
took the answer from. It rides the tool loop added in V3.5, so there is no
second code path: the research tools are an MCP server like any other.

## `tools/web_mcp_server.py` — "JARVIS Web"

Stdio MCP, stdlib only, four tools:

| tool | what it does | approval |
|---|---|---|
| `web_search` | DuckDuckGo (or your own SearXNG), returns title + URL + snippet | no |
| `wikipedia` | article extracts with their URLs | no |
| `fetch_url` | reads one public page as text | no |
| `download_file` | saves a file to `~/jarvis-downloads` | **yes** |

Add it in **Tool Armory → Add → JARVIS Web**. The preset fills in the command
with your real install path, so there is nothing to type.

To use your own search instance instead of DuckDuckGo, set
`JARVIS_SEARXNG_URL=https://your-instance` before starting JARVIS.

## The address guard

This is the part worth reading. The model chooses the URL, so `fetch_url` is a
confused deputy waiting to happen — "read `http://127.0.0.1:8787/api/config`"
would hand it your own settings, and `http://192.168.1.1/` would reach your
router. So every hostname is resolved, and **every address it resolves to** is
checked before the socket is opened:

- loopback, private, link-local (where cloud metadata lives), multicast,
  reserved, unspecified and carrier-grade NAT ranges are all refused;
- `http` and `https` only — no `file://`, no `ftp://`;
- redirects are followed by hand, up to five hops, and **each hop is checked
  again**, because a public host is free to redirect to `127.0.0.1`;
- a response is capped at 2 MB and the extracted text at 20 000 characters.

A refusal says which address it was and why. Verified: asking it to read
JARVIS's own API over loopback is refused, as is a public page that redirects
into a private network.

## Sources in the answer

A model handed a search tool will cheerfully answer from memory and cite
nothing. When the catalogue contains a tool that can reach the web,
`toolchat` appends an instruction to the system message: search rather than
recall for anything that may have changed, and list the URLs under
`Sources:`. With no web tool connected, nothing is added.

## Read-only, honestly decided

`web_search` reads, but "web" is not a read verb, so the V3.5 name heuristic
would have held it behind an approval prompt forever. Tool names now carry the
MCP spec's `annotations`, and `toolchat.is_mutating()` consults them — but not
blindly, because the spec is explicit that annotations may not describe a tool
faithfully:

- a write verb anywhere in the name wins over any annotation, so a server
  claiming `delete_everything` is `readOnlyHint: true` is not believed;
- `destructiveHint` or `readOnlyHint: false` always means approval;
- `readOnlyHint: true` is taken only when the name itself says nothing either
  way — which is exactly the `web_search` case.

## A failed call no longer reads as a success

An MCP server that answers with `isError` used to produce a green "ran in
240ms" card with the error message inside it. Network failures are routine for
a web tool, so `execute()` now reports `ran: false` for those, and the card
shows them as not-run.

## What was *not* verified here

The live search engines are unreachable from the machine this was built on —
its egress proxy answers 403 to `CONNECT` for `html.duckduckgo.com`,
`lite.duckduckgo.com`, `api.duckduckgo.com`, `en.wikipedia.org` and the
SearXNG instance tried. So **no live search was run**.

What *was* run: `tools/test_websearch.py` (88 checks) against a local fixture
serving the exact shapes DuckDuckGo, SearXNG and the Wikipedia API return —
result parsing, redirect unwrapping, HTML-to-text, size caps, the address
guard unpatched, redirect re-checking, filename sanitising and a real file
written to disk. Plus the real stdio handshake, and the browser suite driving
the Armory UI: preset → save → connect → catalogue → loopback refusal.

The first live search is therefore yours to make. On the phone:

```
JARVIS → Tool Armory → Add → JARVIS Web → Save → Connect
then ask: "search the web for the current Ollama release and give me the link"
```

If DuckDuckGo changes its HTML, `web_search` returns "No results" rather than
wrong ones, and `_DDG_LINK` / `_DDG_SNIPPET` in `tools/web_mcp_server.py` are
the two lines to adjust.

---

# V3.5 — MCP tools the model can actually use

The Armory could already connect to an MCP server, list its tools and call
them. Nothing joined that to the conversation: `ollama.chat_stream()` never
sent a `tools` field, so the model was never told the tools existed, and
nothing watched its replies for a request to use one. The server connected,
and chat could do nothing with it.

## The loop

`server/toolchat.py` is the join:

1. gather the tools every **connected** server exposes;
2. ask the model, handing it those tools;
3. if it asked for one, run it through `armory.call_tool` and append the
   result as a `tool` message;
4. back to 2, up to six rounds.

`ollama.chat_full()` is new: one non-streamed turn returning the whole
assistant message. The loop needs `tool_calls` intact, and a streamed turn
hands those back in pieces whose assembly differs between Ollama versions.

**Nothing about the old path changed.** With no connected server, or on a
provider that cannot take tools, chat streams exactly as before.

## Never claiming a tool ran

Every `tool_result` carries what `armory.call_tool` actually returned — its
failures included. A tool that was refused, errored, or hit a disconnected
server says so in the transcript *the model reads*, so the model cannot report
success over a failure it was told about. The interface shows a card per call:
blue while running, green with the elapsed time when it ran, red and "did not
run" when it did not, with the real output underneath.

## Writes wait for you

Tools are classified read-only or mutating by their verb, and **an unknown
verb counts as mutating** — a read wrongly held is an inconvenience, a write
wrongly allowed is not. A mutating call does not run. It raises an approval
bound to that exact tool and those exact arguments, consumed the first time it
is used, so an approval for one path cannot be spent on another.

## New: a real filesystem server

`tools/fs_mcp_server.py` — "JARVIS Files", stdio, dependency-free, confined to
one root that `..` cannot escape. Tools: `list_directory`, `read_file`,
`get_file_info` (read-only), `write_file`, `create_directory` (mutating).

```
name:      JARVIS Files
transport: stdio
command:   python3 ~/jarvis-v2/tools/fs_mcp_server.py ~/projects
```

## Tested for real

`tools/test_toolcall.py` — 27 checks. The MCP server, the stdio handshake,
`tools/list`, `tools/call`, the catalogue, the parsing, the execution and the
result fed back are all real. The *model* is stubbed, because there is no
Ollama in a test environment — and the stub reads the tool output back, so a
check claiming a directory listing arrived only passes if it did.

Verified through the browser as well: asking for `list_directory` produced a
green card holding the real contents of the directory, and an answer quoting
them. Asking for `write_file` produced a red "did not run" card, an approval
control, and **no file on disk**; after approving, the same request wrote
`notes/new.txt` with the right contents — confirmed on the filesystem.

---

# V3.4 — no box behind the rings

The dial sat on a soft disc of radius `R * 1.25`. The canvas's half-size is
`R * 1.087`, so that disc was **wider than the canvas it was drawn into** and
got clipped by the square edge: visible along the four sides, absent at the
corners, which is exactly the shape of a box. On a phone's OLED, in the dark,
a two-level step along a straight edge is plainly a rectangle.

`.stage` carried a second backing layer of its own — a radial gradient behind
the canvas. It was not being clipped, but it was still a panel the rings were
floating on rather than part of the interface.

Both are gone. The rings now sit straight on the interface background, so the
ambient particle field runs right through where the box used to be.

**The ring design is untouched.** All eight layers are exactly as they were —
tick crown, segment ring, graduation ring, orbit, sweep, dashed ring, the two
3D gyro rings, and the reactive core with its centre label. The core's own
glow stays, which is what keeps the middle of the dial holographic; what went
was only the wash *behind* the whole thing.

**Verified by pixel sampling.** Strips crossing the stage's left and bottom
edges, with the motes and the dial frozen so nothing else could vary, read
flat across the boundary: maximum step between neighbouring pixels is **1**,
which is the page's own gradient. Before, the boundary was a visible step.

---

# V3.3 — one background for both visualisers

FACE used to paint its own background: a full-canvas volumetric wash behind
the bust, plus its own field of motes drifting through the head's volume.
RINGS painted neither — it cleared its canvas and drew the dial, letting the
stage's own gradient and the ambient particle field show through. Switching
mode therefore changed the backdrop as well as the visualiser.

Both are now gone from `face3d.js`:

* the `WASH` program, its shaders, its buffer and the `_wash()` pass;
* the GL particle buffers and their draw call.

The irises stay — they are part of the face, not of the background — and so
does everything else: the scan band, the interference, the depth tint, the
mesh overlay, the jaw and the lids.

What remains behind the visualiser is identical in both modes: the page
gradient, the ambient particle field, and the stage's own radial. Switching
RINGS ↔ FACE now swaps one canvas for the other and changes nothing else.

**Verified by pixel comparison.** With the visualiser canvas hidden, a region
strictly inside the stage and clear of the mode label, the hologram card and
the control rail is **byte-identical** between the two modes — diff bounding
box `None`, maximum channel delta `0`.

The file is ~1.7KB smaller and FACE is two draw calls lighter per frame.

---

# V3.2 — depth in the particle field, status in the border

## Thousands of motes, with depth

The field is now ~2,200 motes on a Galaxy S25 Ultra across five depth bands.
Depth is assigned once and never changes — nothing moves toward or away from
the viewer — and it sets three things at once: size, brightness and drift
speed. Near motes measure 1.24px and travel at 4.47px/s; far ones 0.44px at
1.24px/s. That difference is the parallax that makes a flat canvas read as a
volume.

They still wander: each mote has its own heading and a slow turn rate, so the
field has no preferred direction. No streams, no falling, no forward motion.

Cost is unchanged at **0.10 ms/frame** despite 2.5x the count, because the two
farthest bands draw as squares rather than circles — under a pixel the two are
indistinguishable and a rect is much cheaper to path — and the whole field is
five `Path2D` fills rather than thousands of draw calls.

## Never inside the conversation

The backdrop is drawn inside an even-odd clip that cuts out the chat panel and
the chat bar, so not one mote is painted under them. Verified by reading the
canvas back: **0 lit pixels** inside the panel, 3,267 in a strip outside it.
Clipping rather than clearing afterwards also saves the fill.

The masked rectangles are cached and refreshed by a `ResizeObserver` rather
than measured every frame — reading a layout box 120 times a second is how an
ambient layer becomes a janky one.

## The border reports what JARVIS is doing

| State | Border |
|---|---|
| Listening, thinking, speaking | **green** |
| Standing by, or in standby | **red** |

The travelling animation is unchanged; only the colour switches. It is driven
from `applyEdgeState()`, which reads the same two pieces of state the activity
pill does, from the same place — so the border and the pill cannot disagree.
Standby wins over a lingering activity.

---

# V3.0 — shortcuts, a clock, and ambient light

This branch also carries V2.8 (AI Connections), which was built on `main`
while the console layout sat on its own branch. The two were merged here so
there is one branch with everything rather than two that each lack half.

## Four shortcuts on the top bar

OpenAI (chatgpt.com) and GitHub are fixed; the other two are yours, named and
pointed from **Settings → Top-bar shortcuts**. They are real `<a
target="_blank" rel="noopener noreferrer">` links, so Android Chrome treats
them as an ordinary navigation and a long press offers its usual menu. An
unconfigured slot shows a `+` and opens Settings — never a dead button.

Addresses are validated on the backend: anything that is not `http://` or
`https://` is dropped, the slot stays a `+`, and the interface says so. A
`javascript:` URL in the field is refused rather than rendered as a link.

They sit on the header's **second line**. The first line is already full at
412px — menu, brand, activity, full screen, settings — and squeezing four more
controls in would have meant shrinking the brand or the activity pill, which
is a redesign and not what was asked for.

## A clock above the rings

24-hour `HH:MM:SS` with the date beneath, fixed to **Europe/London** rather
than the phone's own zone, so it stays right wherever the phone is and follows
British summer time by itself. It ticks on a timer re-aligned to each whole
second instead of every 1000ms from an arbitrary start, so the seconds change
when the second changes.

It sits in grid column 2 at the top of the console band — the dial's own axis,
in space the band already had. The rings keep their size and their position to
the pixel, and nothing below moves.

## Ambient light, in every mode

- **Suspended particles.** ~900 motes on a Galaxy S25 Ultra, each wandering on
  its own slowly turning heading. They do not fall and they do not stream:
  measured over 1.2s the field's mean displacement is a fraction of a pixel in
  both axes while each mote travels ~4px. One batched `Path2D` and three fills
  per frame rather than a thousand draw calls — **measured cost 0.10 ms/frame**.
  They shed with the dial's existing frame-time watchdog, so a slow phone
  drops to ~270 motes on its own.
- **A travelling edge pulse.** A thin neon arc running the outer edge, as an
  SVG stroke with `pathLength="1000"` and a CSS `stroke-dashoffset` animation:
  one composited element, no JavaScript per frame, and one dash pattern that
  works at every screen size without measuring anything.

The conversation panel is untouched — same background in every mode.

## The cinematic scene, full screen only

A nebula wash and a hooded figure, drawn onto the same canvas and only while
full screen is on. It is drawn, not photographed: the brief asked for a face
that is not clearly visible and a silhouette has none at all, nothing has to
be licensed or cached, and it can be tinted to JARVIS's own cyan rather than
graded towards it. A scrim over the whole thing keeps the rings, the rails and
the text readable.

---

# V2.8 — AI Connections, and a full-screen button

## A new AI CONNECTIONS section in the menu

Three brains in one panel, in the order of what they cost you.

| | Free | Key | Cost gate | Test |
|---|---|---|---|---|
| **Ollama** | yes, local and offline | — | — | yes |
| **OpenAI** | no | backend only | yes | yes |
| **Claude** | no | backend only | yes | yes |

Ollama stays the default and the fallback. Neither paid provider can become
the active brain until it is **switched on** *and* its **cost notice is
acknowledged** — and that is enforced in `config._clamp_v21()`, so a
hand-edited `config.json` or a POST that skipped the interface still cannot
start spending. The notice carries the real per-million-token prices.

Keys go to `~/.jarvis/secrets.json` (mode 600) on the Termux backend. There is
no GET for them, here or anywhere: the browser is told whether a key exists,
never what it is. `tools/test_connections.py` asserts the key is absent from
the settings document, the status payload and every error string.

`server/anthropic_client.py` is the new Claude client — the Messages API over
`urllib`, so Termux still needs no `pip install`. The project takes no pip
dependencies at all, which is why the official `anthropic` SDK is not used.

## Claude Code — a real bridge, or an honest "no"

JARVIS does not reimplement Claude Code. It looks for the `claude` binary and
drives it headlessly, behind an approval gate:

1. **Propose.** JARVIS records a request and returns the exact argument
   vector. Nothing runs.
2. **Review.** The panel shows that command verbatim, and the directory.
3. **Approve.** Only then does a process start.

Plan mode is the default and passes `--permission-mode plan --restricted`: it
cannot write, run commands or deploy. Edits need a settings switch *and* a
per-request confirmation, and run under `acceptEdits`.
`--dangerously-skip-permissions` is never passed. Requests cannot reach
outside the one configured working directory, and JARVIS never touches Claude
Code's credentials — `claude` signs in on its own.

**Android is not an Anthropic-supported platform for Claude Code.** Where the
binary is missing or will not execute, the card says so with the real reason
and offers no switch. See LIMITATIONS.md.

## FULL SCREEN

A compact button at the top of the interface hands the whole screen to JARVIS
through the Fullscreen API. It hides itself once full screen; a tap anywhere
brings back an EXIT pill for a few seconds. Where the browser has no
Fullscreen API the button is hidden and a message explains why, rather than a
control that silently does nothing. This is separate from immersive mode —
they compose, and neither switches the other on.

---

# V2.7 — tightening the console

Layout refinements only; nothing else moved.

* **The dial is centred between the rails, not on the band.** It used to be
  centred on the whole console, and because the control rail is wider than
  the status rail that put it visibly right of the space it sits in. Its axis
  is now the axis of the clear span, which moves it left by half the
  difference between the two rail widths.
* **RINGS / FACE moved out of the control rail** and into a row of its own
  directly under the dial, exactly as wide as it and on the same axis. The
  bottom row belongs to it alone, so neither rail can cover its ends.
* **The empty band under the rings is gone.** The dial is sized from the
  hologram and the rail above it, so what is left under it is the toggle and
  a 5px margin rather than ~50px of background.
* **The hologram took the space the toggle left**, growing from 92px to
  119px on a Galaxy S25 Ultra — the right column now has no dead height.

The dial's width is the clear span between the rails plus a 28px bleed each
side, capped by the height of the hologram and rail together. The bleed is
what keeps the rings reading as continuous behind the translucent rails
without the rails landing on their labels; the cap is what leaves no gap.

## One subtlety worth recording

The console's sizes are derived with `calc()` from `--rail-row` and
`--avatar-h`. Those derived properties are declared on `.app`, not on
`:root`, because a custom property is resolved on the element that declares
it: deriving them at the root would freeze them against the root's inputs and
the keyboard and short-screen overrides would silently do nothing. That is
exactly what happened on the first attempt — the conversation stayed at 103px
with the keyboard up until the declarations moved down one level. It now gets
281px on an S25 Ultra and 166px on a 360×800 phone, better than before this
change rather than worse.

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
