# Installing JARVIS Android V2 on a phone

Everything below runs inside **Termux**. Install Termux from F-Droid, not from
the Play Store — the Play Store build is outdated and its package manager
fails.

---

## 1. Base system

```bash
pkg update && pkg upgrade -y
pkg install -y python git
```

That is all JARVIS itself needs. The backend uses only the Python standard
library, so there is no `pip install` step that can fail.

---

## 2. Get the project

```bash
cd ~
unzip jarvis-android-v2.zip -d jarvis
cd jarvis
chmod +x start.sh stop.sh install-termux.sh
```

or, from git:

```bash
git clone https://github.com/corneliuseg6-code/JARVIS-Android- jarvis
cd jarvis
git checkout claude/new-session-doztv0
```

Optional guided setup (it asks before each optional component):

```bash
./install-termux.sh
```

### Updating an install you already have

This branch is kept separate from `main`, so it never touches the copy already
on your phone unless you ask for it. To pull the redesigned interface into an
existing clone:

```bash
cd ~/jarvis
./stop.sh                                   # if it is running
git fetch origin claude/new-session-doztv0
git checkout claude/new-session-doztv0
git pull origin claude/new-session-doztv0
chmod +x start.sh stop.sh install-termux.sh
./start.sh
```

To try it **beside** your current install instead of upgrading it, clone it to
a second folder and run it on a second port — both can be installed as PWAs:

```bash
cd ~
git clone -b claude/new-session-doztv0 https://github.com/corneliuseg6-code/JARVIS-Android- jarvis-v21
cd jarvis-v21
chmod +x start.sh stop.sh install-termux.sh
JARVIS_PORT=8788 ./start.sh                 # then open http://127.0.0.1:8788
```

Your settings, conversations and voices live in `~/.jarvis/`, outside the
project folder, so both copies read the same data and neither can delete it.

---

## 3. First run

```bash
./start.sh
```

Then open Chrome on the phone:

```
http://127.0.0.1:8787
```

Chrome menu → **Add to home screen** installs it as a PWA (full screen, own
icon, works offline).

Stop it with `Ctrl-C`, or from another Termux session:

```bash
./stop.sh
```

---

## 3b. Finding your way around

* **☰** (top left) opens the navigation drawer: Home, Tool Armory, Eyes,
  Settings, the language picker, the magnifier and EXIT.
* The **visualiser** fills the middle of the screen. **RINGS / FACE** directly
  below it switches between the two, and the choice is saved. RINGS is the
  instrument dial; FACE is the holographic bust, drawn with WebGL.
* **Tap the visualiser** and it takes the whole screen, with every control
  folded away. Tap again — or press the ✕ in the corner — to bring them back.
  JARVIS keeps listening and talking the whole time.
* **MUTE** and **ACTIVE / STANDBY** are the two wide buttons under that. The
  microphone is muted every time JARVIS starts, and nothing re-opens it on its
  own.
* The five small switches are **EYES** (camera), **WATCH** (screen capture —
  greyed out on Android, see LIMITATIONS.md), **HOLO** (drag or tilt to turn
  the projection), **FOCUS** (a timed session that counts how often you leave
  JARVIS) and **REFLEX** (hands-free: the mic stays open and only a sentence
  containing the wake word is acted on).
* The **bar at the bottom** is the chat. Type in it, or drag its handle up to
  open the full conversation with History and New.

Settings → Assistant holds the wake word, the length of a FOCUS session and the
text in the middle of the rings.

---

## 4. Ollama (the AI answers)

JARVIS talks to Ollama over HTTP. In a **second Termux session**:

```bash
pkg install ollama          # if your Termux repository provides it
ollama serve                # leave this running
```

In a third session, pull a model that fits a phone:

```bash
ollama pull llama3.2:3b     # ~2 GB, a good default
# or smaller:
ollama pull qwen2.5:1.5b    # ~1 GB
```

Then set the model in **Settings → Models → Chat model**. The field offers the
models Ollama reports, so you can see what is actually installed.

Without Ollama, the interface still works — chat answers report
"Ollama is not responding" instead of pretending.

---

## 5. Offline voices (optional)

### 5.1 The engine

```bash
pip install sherpa-onnx==1.13.8
pkg install espeak-ng-data        # or put the folder in ~/.jarvis/voices/espeak-ng-data
```

> **Read this before you start.** `pip install sherpa-onnx` serves wheels built
> for *manylinux*, and Termux uses Android's bionic C library, so a wheel that
> downloads may still refuse to import. If that happens you have three options,
> in order of effort:
>
> 1. use the Android browser voices instead (nothing to install — see 5.3);
> 2. install Ollama/Termux's own espeak-ng and use it as a system voice;
> 3. build sherpa-onnx from source:
>    `pkg install cmake ninja clang binutils && pip install --no-binary :all: sherpa-onnx==1.13.8`
>    (long: expect 20-40 minutes on a phone).
>
> Settings → Voice always shows the real state of the engine, including the
> exact import error, so you never have to guess.

### 5.2 Voice models

Models go in `~/.jarvis/voices/` as **two files per voice**:

```
~/.jarvis/voices/ro_RO-mihai-medium.onnx
~/.jarvis/voices/ro_RO-mihai-medium.onnx.json
~/.jarvis/voices/en_GB-alan-medium.onnx
~/.jarvis/voices/en_GB-alan-medium.onnx.json
~/.jarvis/voices/espeak-ng-data/
```

If you already have Mihai and Alan from your previous JARVIS install, copy
those files into that folder and they are picked up automatically — including
voices whose filename is not in the catalog, as long as it starts with the
language code (`ro_`, `en_`, `fr_`, `de_`, `es_`, `it_`).

For French, German, Spanish and Italian, Settings → Voice lists free Piper
voices with their size and a **Download** button. Nothing downloads until you
confirm that specific voice:

| Language | Voice | Size |
|---|---|---|
| FR | fr_FR-siwis-medium / fr_FR-gilles-low | 63 / 21 MB |
| DE | de_DE-thorsten-medium / -low | 63 / 21 MB |
| ES | es_ES-davefx-medium / es_ES-sharvard-medium | 63 MB |
| IT | it_IT-paola-medium / it_IT-riccardo-x_low | 63 / 15 MB |

### 5.3 Fallback

If no offline voice is available for a language, JARVIS uses the Android
browser voice for it (Chrome exposes the system TTS engine). If the phone has
no voice for that language either, the answer is shown as text and you are
told once. It never fails silently.

---

## 6. Camera (EYES) and local vision

The camera needs a secure context. `http://127.0.0.1:8787` counts as secure, so
opening JARVIS on the phone itself works. Over `--lan` from another device,
Chrome will refuse the camera on plain HTTP — that is a browser rule, not a
JARVIS setting.

Local vision is **off by default** because it loads a second model. To enable:

```bash
ollama pull qwen3-vl:2b-instruct
```

then Settings → Models → *Enable local vision*. If the model is not present,
EYES says so and shows the exact `ollama pull` command instead of failing.

> Check that your Ollama build actually supports `qwen3-vl` before pulling two
> gigabytes: `ollama pull qwen3-vl:2b-instruct` fails fast with
> "unsupported architecture" on older Ollama builds. Any vision model Ollama
> supports works — set its name in the Vision model field.

Do not run the chat model and the vision model at the same time on a phone
with less than 8 GB of RAM.

---

## 7. Tool Armory (MCP)

Add a connection in the Tool Armory panel:

* **Local process (stdio)** — a command, e.g.
  `npx -y @modelcontextprotocol/server-filesystem /sdcard`
  (needs `pkg install nodejs`).
* **HTTP endpoint** — a URL for a streamable-HTTP MCP server.

Put credential **names** in the credential field (e.g. `GITHUB_TOKEN`), then
the values in the password boxes that appear. The values are written to
`~/.jarvis/secrets.json` with mode 600 and are never sent back to the browser.

Press **Connect**. The card turns CONNECTED only after the server answered the
handshake and listed its tools; otherwise it shows the exact error.

To try the mechanism without a real MCP server:

```
command: python3 ~/jarvis/tools/mock_mcp_server.py
```

---

## 8. Battery

* Keep JARVIS in **Standby** when you are not using it: animations, mic, camera
  and polling all stop.
* Turn on *Reduced animations* in Settings on an older phone.
* `./start.sh` takes a Termux wake-lock so Android does not suspend the server
  with the screen off. `./stop.sh` releases it.
* Exclude Termux from battery optimisation if the server keeps getting killed:
  Android Settings → Apps → Termux → Battery → Unrestricted.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `cannot bind 127.0.0.1:8787` | already running → `./stop.sh`, or use `JARVIS_PORT=9000 ./start.sh` |
| Page loads but nothing reacts | open Chrome DevTools (`chrome://inspect` from a PC) — JARVIS also posts JS errors to the Termux console |
| "Server: Disconnected" chip | the backend died; look at the Termux output |
| "Ollama: Disconnected" | `ollama serve` is not running, or the address in Settings is wrong |
| "Voice: Disconnected" | no sherpa-onnx or no model — Settings → Voice shows which |
| Microphone does nothing | Chrome blocks the mic on non-secure origins; use `127.0.0.1`, and grant the permission |
| Everything is tiny / too large | the magnifier in the drawer (☰), or its ↺ reset |
| WATCH is greyed out | Android browsers have no screen-capture API; the switch says so. It works in the desktop/PWA build. |
| FACE feels heavy or warm | It is a shaded 3D bust. JARVIS sheds its glow passes automatically below ~36fps, but RINGS is the cheap mode — switching is one tap. *Reduced animations* in Settings lowers both. |
| FACE looks like the old flat head | WebGL was refused, so it fell back to the Canvas 2D face. Check `chrome://gpu`, and that hardware acceleration is on. |
| REFLEX hears nothing | the browser's speech recognition needs Google speech services and a network connection on most devices (see LIMITATIONS.md) |

A full diagnosis in one command:

```bash
python3 tools/selftest.py
```
