#!/usr/bin/env bash
# JARVIS Android V2 — Termux setup.
#
# Installs only what JARVIS genuinely needs. The server itself runs on plain
# CPython with no third-party packages; everything below is optional extra
# capability, and each step is skippable.
set -uo pipefail

say() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m[!] %s\033[0m\n' "$*"; }

say "1/5  Base packages (python, git)"
pkg install -y python git || warn "pkg install failed - run 'pkg update' and retry"

say "2/5  Storage access (needed only if JARVIS should read /sdcard)"
if [ ! -d "$HOME/storage" ]; then
  termux-setup-storage || warn "storage permission not granted"
fi

say "3/5  Data folders"
JARVIS_HOME="${JARVIS_HOME:-$HOME/.jarvis}"
mkdir -p "$JARVIS_HOME"/{voices,conversations,logs,run,captures}
chmod 700 "$JARVIS_HOME"
echo "    data: $JARVIS_HOME"

say "4/5  Offline voice engine (optional, ~40 MB)"
cat <<'TXT'
    The offline voices (Mihai / Alan) need sherpa-onnx. Version 1.13.8 is the
    one these Piper models were verified against:

        pip install sherpa-onnx==1.13.8

    Piper voices also need espeak-ng data:

        pkg install espeak-ng-data   (or place it in ~/.jarvis/voices/espeak-ng-data)

    Skip this and JARVIS falls back to the Android browser voices, or to text.
TXT
read -r -p "    Install sherpa-onnx 1.13.8 now? [y/N] " answer
case "$answer" in
  [yY]*) pip install "sherpa-onnx==1.13.8" || warn "sherpa-onnx install failed - see LIMITATIONS.md" ;;
  *) echo "    skipped" ;;
esac

say "5/5  Ollama (optional, for the AI answers)"
cat <<'TXT'
    JARVIS talks to an Ollama server. In Termux:

        pkg install ollama        # if your Termux repo has it
        ollama serve &            # keep running in a second session
        ollama pull llama3.2:3b   # a 2 GB model that fits most phones

    Vision (EYES) additionally needs a vision model, for example:

        ollama pull qwen3-vl:2b-instruct

    No model is ever downloaded by JARVIS without you asking.
TXT

say "Done"
cat <<'TXT'
    Start :  ./start.sh          then open  http://127.0.0.1:8787
    Stop  :  ./stop.sh
TXT
