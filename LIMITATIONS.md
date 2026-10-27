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

## The holographic bust (FACE)

| Claim | Status |
|---|---|
| It is a real 3D surface, shaded and animated in WebGL | **Verified.** The context comes up, the scan loads and renders, the jaw and lids animate in the vertex shader, and the fallback path was exercised by forcing a failure. |
| It is a real scanned human head | **Yes.** 9,279 vertices, 17,684 triangles, with neck and shoulders. |
| The licence was verified | **Yes** — CC BY 3.0, read from `LeePerrySmith_License.txt` as shipped beside the model in the three.js repository, and copied into this one. The authoritative publisher page (casual-effects.com) was **not** reachable from the build environment, so the verification rests on the licence file the redistributor ships, not on the original site. |
| **It is a real, identifiable person** | **Yes — read this.** The scan is of Lee Perry-Smith. CC BY 3.0 grants the copyright permission, with attribution, and this is how the model is distributed in three.js, Blender demos and graphics papers. It does **not** settle likeness or personality rights, which are a separate matter from copyright and vary by country. If you are uncomfortable giving your assistant a real person's face, set `avatar_mode` to `rings`, or delete `web/models/head.glb` — FACE then falls back to the original procedural bust automatically, with no code change. |
| It has hair | **Barely.** The scan is close-cropped, so the head reads as shaved rather than the styled hair in your reference. Nothing was added on top of it, because you asked for no more procedural face work. |
| It runs well on a phone GPU | **Not measured.** The only renderer available in this session was SwiftShader — Chromium's pure-CPU rasteriser, with no GPU at all — where the bust manages single-digit frames. That number says nothing about an Adreno: it is a software rasteriser running a shader with heavy overdraw. What *is* in the code is a frame-time watchdog that measures real frames and sheds the glow passes, the mesh overlay and the particles if they are not being hit, and a fallback to the Canvas 2D head if WebGL is refused outright. **Check it on your phone**: if FACE feels heavy, RINGS is the cheap mode and switching is one tap. |
| RINGS performance | **Verified at 60fps** in the same software renderer, so it has a very large margin on real hardware. |

The reference video never shows FACE mode — the visualiser is RINGS for its
whole 12 minutes — so the bust was built against the reference *image* and the
written description, not against footage of it animating.

## Immersive mode

Verified: a tap on the stage expands the visualiser to the full screen and
folds the top bar, status strip, HUD and chat dock away; a second tap, the ✕,
or Escape restores them; it works in both RINGS and FACE; and a drag of more
than 12px (a HOLO turn) does not trigger it.

## Galaxy S25 Ultra

The layout was checked at **412×915** and **360×800** CSS pixels with a mobile
user agent, which is the viewport Chrome reports on that class of device — not
on the device itself. Nothing was off screen, nothing scrolled sideways, and
focusing the chat input left the dock on screen. The physical 1440×3120 panel
is never used as a layout dimension; the device pixel ratio is capped at 2 for
the 2D canvas and 1.75 for WebGL, deliberately.

Not verified without the phone: the real on-screen keyboard, the real gesture
bar and status bar insets, 120Hz behaviour, and real battery drain.

## OpenAI — what was and was not verified

| Claim | Status |
|---|---|
| The key cannot reach the browser | **Verified.** A probe key was stored, then every public surface was searched for it: `/api/config`, `/api/health`, `/api/diagnostics`, `/api/llm/status` and the settings document — all clean. There is no endpoint that returns a key, and `/api/config` strips credential-shaped fields. `secrets.json` is mode 600. Re-checkable with `tools/test_provider.py`. |
| A paid provider cannot switch itself on | **Verified.** The switch is refused until the cost acknowledgement is set, and an unknown provider name falls back to Ollama. Enforced in `config.update_config()`, so this holds against a hand-edited `config.json` too. |
| Errors are translated into something actionable | **Verified against synthetic responses** — HTTP 401, 403, 404, 429 (both out-of-credit and rate-limited), 500 and an unexpected code each map to the right stable code, and the key never appears in the surfaced text. |
| The streaming path works end to end | **Partly.** The chat endpoint was driven with the provider set to OpenAI: it emitted `start` carrying the provider, then a structured `error` event with a code and detail, and the frontend rendered it. That exercises routing, streaming and error handling. |
| **A real completion from OpenAI** | **Verified on the phone**, not here. `api.openai.com` is blocked by this build environment's egress proxy, so no live call could be made during development — the request shape, SSE parsing and `[DONE]` handling were written against the documented API. They were then confirmed working on a Galaxy S25 Ultra under Termux with a real key: the provider connects and answers. |
| Cost figures | **Not quoted anywhere**, deliberately. Rates change; the interface points at OpenAI's pricing page instead of printing numbers that would go stale. |

Vision (EYES) routes through the same provider, so on OpenAI a described
frame is a billed request. The status says so.

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

---

## Claude Code on Termux — the honest answer

**Android is not an Anthropic-supported platform for Claude Code.** The
documented systems are macOS 13+, Windows 10 1809+, Ubuntu 20.04+, Debian 10+
and Alpine 3.19+. The published builds — native installer and npm alike — are
`linux-arm64` (glibc) and `linux-arm64-musl`; Termux is Android with Bionic
libc, so neither runs there unmodified.

Community routes exist (a patched ELF loader, `glibc-runner`, or a proot
Ubuntu chroot that gives the normal installer a real glibc userland). They are
third-party and none of them is something this project can vouch for.

What JARVIS therefore does:

| Situation | What you see |
|---|---|
| `claude` on PATH and it runs | The card shows the real version and path, and the controls work |
| `claude` missing | The card states that, with no switch to press |
| `claude` present but won't execute | The card reports the actual `exec` error — the usual shape of the ABI mismatch |
| Any Termux install | An amber note explaining that the platform is unsupported and what the community routes are |

**Verified in this session:** detection against the real CLI (2.1.311 at
`/opt/node22/bin/claude`) on Linux x86_64. The run path — argument building,
subprocess, JSON parsing, the approval gate — is covered by
`tools/test_connections.py` against a stub `claude`, so the whole bridge is
exercised without a billed model call.

**Not verified:** a Claude Code run on an actual Termux install on the Galaxy
S25 Ultra. No Android device was available here, and per the above there is no
supported way to get the binary there.

## Claude (Anthropic API) — verified

Unlike `api.openai.com`, which this build environment's proxy blocks,
`api.anthropic.com` is reachable from here, so the Claude provider was tested
against the live API:

| What | How it was verified |
|---|---|
| Key storage | written to `~/.jarvis/secrets.json`, mode 600, absent from `/api/config`, the status payload and every error string |
| Rejected key | a deliberately invalid key produced a real 401, mapped to `auth`, and surfaced in the interface as "Neconectat: invalid x-api-key" |
| Cost gate | `llm_provider: "claude"` falls back to `ollama` unless the connection is enabled *and* the cost notice acknowledged — enforced in `config._clamp_v21()`, not only in the UI |
| Error mapping | all eight documented HTTP failures map to codes the interface translates |
| Message translation | system prompt hoisted, images as base64 blocks, consecutive same-role turns merged |

**Not verified:** a successful billed completion. No valid Anthropic key was
used, because spending money is the user's decision.

## FULL SCREEN

| What | How it was verified |
|---|---|
| Enter | `requestFullscreen({navigationUI:'hide'})` engages; `document.fullscreenElement` set |
| Button hides in fullscreen | asserted |
| EXIT pill | appears on entry, fades after ~3.2s, returns on any tap |
| Exit restores | `document.fullscreenElement` cleared, button returns, layout byte-identical |
| Immersive still works | FACE + tap-to-expand unaffected |
| No API | with `requestFullscreen` and `fullscreenEnabled` removed, `enter()` returns false, the button hides, and a toast explains — it never claims success |

**Not verified on hardware:** Android Chrome's own handling of the status bar
and gesture bar, and the installed PWA. Chromium desktop was used here.

## Shortcuts, clock and ambient layers — verified

| What | How it was verified |
|---|---|
| Shortcut targets | OpenAI → `https://chatgpt.com/`, GitHub → `https://github.com/`, both `target="_blank"` with `rel="noopener noreferrer"` |
| Custom shortcuts | typed into the real Settings fields, saved, and the top bar repainted with the new name and address |
| URL validation | `javascript:alert(1)` refused by the backend, slot stayed a `+`, and the interface explained why |
| Empty slot | shows `+` and opens Settings when tapped |
| Clock | `HH:MM:SS`, ticks, date beneath, and showed London time while the browser's own zone was Europe/Bucharest |
| Clock placement | centred on the dial's axis to within a pixel, above it, and the dial's geometry is unchanged |
| Particles | ~898 at full quality; mean displacement over 1.2s was 0.2px against ~4px travelled per mote, i.e. no net direction |
| Particle cost | 0.10 ms/frame measured against the same scene with the layer stopped |
| Edge pulse | `animation-name: edge-run` running in both modes |
| Chat panel | identical `background-color` in normal and full screen |
| Cinematic scene | off in normal mode, on in full screen, off again on exit while the particles keep running |

**Not verified on hardware:** real frame rates. This container has only
SwiftShader, a CPU rasteriser, where the dial alone measures 15fps — so the
absolute numbers mean nothing and only the *difference* between layers is
informative. The 0.10 ms figure is that difference.

## Particle depth and the status border — verified

| What | How it was verified |
|---|---|
| Count | 2,218 motes at 412×915, across all five depth bands |
| Depth | near band 1.24px at 4.47px/s against far band 0.44px at 1.24px/s — size and parallax both scale with depth |
| No net drift | mean displacement a fraction of a pixel against ~4px travelled per mote over 1.2s |
| Chat panel | canvas read back: 0 lit pixels inside the panel, 3,267 in a strip outside it |
| Cost | 0.10 ms/frame against the same scene with the layer stopped, unchanged from 900 motes |
| Border colour | red at idle, green for listening / thinking / speaking, red again in standby even with an activity still set |
| Border animation | `animation-name: edge-run` still running in every state |

## Shared RINGS/FACE background — verified

| What | How it was verified |
|---|---|
| Same background | with the visualiser hidden, a pure-background region of the stage is byte-identical in both modes (diff bbox `None`, max channel delta 0) |
| Stage styling | identical computed `background-image` in both modes |
| Mode switch | RINGS shows the 2D canvas with the GL one hidden, FACE the reverse |
| The face still renders | bust, irises, scan band and mesh overlay all present after the wash was removed |

The first comparison attempt failed on a region that included the stage's
top-right corner, where the hologram card and the control rail overlap it —
those differ between frames because they animate, not because the background
does. The region was narrowed to one only the background occupies.

## The box behind the rings — verified gone

| What | How it was verified |
|---|---|
| Stage backing layer | computed `background-image` is `none` |
| Left edge | strip crossing it, clear of the status rail, reads flat — max step 1 |
| Bottom edge | strip crossing it reads flat — max step 1 |
| The dial still draws | 1,693 lit samples in the rings canvas after the change |
| RINGS/FACE still match | the shared-background comparison still passes |

The first left-edge probe was placed over the status rail and read its chips
rather than the background; it was moved below the rail before the result was
trusted.

## MCP tool calling — verified

| What | How it was verified |
|---|---|
| stdio handshake | real `initialize` + `tools/list` against `tools/fs_mcp_server.py` |
| Catalogue | all 5 tools reach the model, namespaced per connection, server schema passed through |
| The model is handed them | the stub recorded 5 tool definitions in the request body |
| `list_directory` | ran for real; the card and the answer both carry the actual directory contents |
| A mutating call | refused, approval raised, **nothing written to disk** — checked by listing the directory before and after |
| After approval | the write ran and `notes/new.txt` existed with the right contents |
| Approval is single-use | the second identical request was refused again |
| Path traversal | `../../../../etc/passwd` refused by the server's root check |
| A missing file | reported as `ERROR`, never as success |
| A disconnected server | reported as "the tool did not run — not connected" |
| Layout | dial 275, toggle 275, no scrolling — unchanged |

**Stubbed, and why:** the language model. There is no Ollama in this
container, so a small HTTP server answers `/api/chat` the way Ollama does with
tools. Everything this project owns runs for real; the one part that cannot
run locally is the only part faked. The stub reads the tool output back into
its reply, so a check asserting the listing arrived cannot pass without it.

**Not verified on hardware:** a real Ollama choosing to call a tool. Whether
`llama3.2:3b` reliably emits `tool_calls` for a given prompt is a property of
the model, not of this code. The wiring is proven; the model's judgement is
not something a test here can establish.

## Web research — verified, and not

Run `python3 tools/test_websearch.py` (88 checks).

| What | How it was verified |
|---|---|
| stdio handshake | real `initialize` + `tools/list` against `tools/web_mcp_server.py`; 4 tools, annotations intact |
| Search parsing | DuckDuckGo HTML, SearXNG JSON and Wikipedia API fixtures; redirect wrapper unwrapped, snippets paired to their links, `javascript:` links dropped, `limit` honoured |
| Page reading | title and prose extracted; `<script>`, `<style>`, `<nav>` and `<footer>` contents dropped; `&nbsp;` read as a space; 2 MB and `max_chars` caps enforced |
| Redirects | followed, and the **final** URL reported rather than the one asked for |
| The address guard | tested **unpatched** against loopback, `localhost`, `[::1]`, 10/172.16/192.168, 169.254, 100.64, 0.0.0.0, `file://`, `ftp://` |
| Redirect re-checking | a page redirecting to `http://10.0.0.1/` is refused at the second hop |
| Through the running app | the Armory UI: preset → save → **real connect** → catalogue; `fetch_url` on `http://127.0.0.1:8787/api/config` refused as loopback |
| Approval gating | `web_search` / `wikipedia` / `fetch_url` read-only; `download_file` gated — read off the live `/api/chat/tools` catalogue |
| A lying annotation | `readOnlyHint: true` on `delete_everything`, `write_file`, `run_shell` is ignored; all three still need approval |
| Downloads | a real file written to disk; `../../escape.sh` stayed inside the folder; shell characters stripped from the name |
| A failed call | an `isError` result reports `ran: false`, not a green "ran in Nms" card |
| Layout | dial still 275px, RINGS/FACE bar still matches it, page still does not scroll |

**Not verified: any live search or fetch.** The build machine's egress proxy
answers 403 to `CONNECT` for `html.duckduckgo.com`, `lite.duckduckgo.com`,
`api.duckduckgo.com`, `en.wikipedia.org` and the SearXNG instance tried, so
every network call went to a local fixture. The fixture returns the documented
shapes, not a recording of a live response — if DuckDuckGo's markup has
changed since, `web_search` will return "No results" rather than wrong ones,
and `_DDG_LINK` / `_DDG_SNIPPET` are the two lines to adjust.

The address guard could not be tested against the fixture and the real guard
at once: the fixture can only live on loopback, which is what the guard
exists to refuse. So the guard is tested unpatched against literal addresses,
and the fetching and parsing are tested with loopback temporarily permitted.
Both halves are covered; neither patch can hide the other.
