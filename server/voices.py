"""Offline text-to-speech through sherpa-onnx + Piper voice models.

Layering (each step degrades gracefully, nothing is ever claimed to work
before it has answered):

  1. sherpa-onnx installed AND a voice model present  -> backend WAV synthesis
  2. otherwise                                        -> the frontend falls back
     to the browser's Web Speech API, or to silent text if that has no voice
     for the language either.

Voice models are NEVER downloaded automatically: `catalog()` only reports what
is recommended and how big it is, and the download endpoint requires an
explicit, per-voice user action.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.request
from pathlib import Path
from typing import Any

from . import paths

_LOCK = threading.RLock()
_ENGINE_CACHE: dict[str, Any] = {}
_DOWNLOADS: dict[str, dict[str, Any]] = {}

PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# Recommended free, offline Piper voices per interface language.
# "bundled" marks the two voices the project has always shipped with.
CATALOG: dict[str, list[dict[str, Any]]] = {
    "ro": [
        {
            "id": "ro_RO-mihai-medium",
            "label": "Mihai",
            "gender": "male",
            "quality": "medium",
            "bundled": True,
            "size_mb": 63,
            "url": f"{PIPER_BASE}/ro/ro_RO/mihai/medium/ro_RO-mihai-medium.onnx",
            "config_url": f"{PIPER_BASE}/ro/ro_RO/mihai/medium/ro_RO-mihai-medium.onnx.json",
        }
    ],
    "en": [
        {
            # Alan is published by Piper as an en_GB voice; the id matches the
            # real model filename so a downloaded file lands where it is looked
            # for. A legacy en_US-alan-medium.onnx already on disk still works,
            # because resolve_voice() accepts any installed voice for the
            # language (see _installed_for_language).
            "id": "en_GB-alan-medium",
            "label": "Alan",
            "gender": "male",
            "quality": "medium",
            "bundled": True,
            "size_mb": 63,
            "url": f"{PIPER_BASE}/en/en_GB/alan/medium/en_GB-alan-medium.onnx",
            "config_url": f"{PIPER_BASE}/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json",
        },
        {
            "id": "en_US-amy-low",
            "label": "Amy (light)",
            "gender": "female",
            "quality": "low",
            "size_mb": 21,
            "url": f"{PIPER_BASE}/en/en_US/amy/low/en_US-amy-low.onnx",
            "config_url": f"{PIPER_BASE}/en/en_US/amy/low/en_US-amy-low.onnx.json",
        },
    ],
    "fr": [
        {
            "id": "fr_FR-siwis-medium",
            "label": "Siwis",
            "gender": "female",
            "quality": "medium",
            "size_mb": 63,
            "url": f"{PIPER_BASE}/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx",
            "config_url": f"{PIPER_BASE}/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json",
        },
        {
            "id": "fr_FR-gilles-low",
            "label": "Gilles (light)",
            "gender": "male",
            "quality": "low",
            "size_mb": 21,
            "url": f"{PIPER_BASE}/fr/fr_FR/gilles/low/fr_FR-gilles-low.onnx",
            "config_url": f"{PIPER_BASE}/fr/fr_FR/gilles/low/fr_FR-gilles-low.onnx.json",
        },
    ],
    "de": [
        {
            "id": "de_DE-thorsten-medium",
            "label": "Thorsten",
            "gender": "male",
            "quality": "medium",
            "size_mb": 63,
            "url": f"{PIPER_BASE}/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx",
            "config_url": f"{PIPER_BASE}/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx.json",
        },
        {
            "id": "de_DE-thorsten-low",
            "label": "Thorsten (light)",
            "gender": "male",
            "quality": "low",
            "size_mb": 21,
            "url": f"{PIPER_BASE}/de/de_DE/thorsten/low/de_DE-thorsten-low.onnx",
            "config_url": f"{PIPER_BASE}/de/de_DE/thorsten/low/de_DE-thorsten-low.onnx.json",
        },
    ],
    "es": [
        {
            "id": "es_ES-davefx-medium",
            "label": "DaveFX",
            "gender": "male",
            "quality": "medium",
            "size_mb": 63,
            "url": f"{PIPER_BASE}/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx",
            "config_url": f"{PIPER_BASE}/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx.json",
        },
        {
            "id": "es_ES-sharvard-medium",
            "label": "Sharvard",
            "gender": "female",
            "quality": "medium",
            "size_mb": 63,
            "url": f"{PIPER_BASE}/es/es_ES/sharvard/medium/es_ES-sharvard-medium.onnx",
            "config_url": f"{PIPER_BASE}/es/es_ES/sharvard/medium/es_ES-sharvard-medium.onnx.json",
        },
    ],
    "it": [
        {
            "id": "it_IT-paola-medium",
            "label": "Paola",
            "gender": "female",
            "quality": "medium",
            "size_mb": 63,
            "url": f"{PIPER_BASE}/it/it_IT/paola/medium/it_IT-paola-medium.onnx",
            "config_url": f"{PIPER_BASE}/it/it_IT/paola/medium/it_IT-paola-medium.onnx.json",
        },
        {
            "id": "it_IT-riccardo-x_low",
            "label": "Riccardo (light)",
            "gender": "male",
            "quality": "x_low",
            "size_mb": 15,
            "url": f"{PIPER_BASE}/it/it_IT/riccardo/x_low/it_IT-riccardo-x_low.onnx",
            "config_url": f"{PIPER_BASE}/it/it_IT/riccardo/x_low/it_IT-riccardo-x_low.onnx.json",
        },
    ],
}

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")


# --------------------------------------------------------------------------
# engine discovery
# --------------------------------------------------------------------------

def engine_info() -> dict[str, Any]:
    """Report the sherpa-onnx situation truthfully, including the known
    1.12.x regression that breaks Piper models built with espeak-ng data."""
    info: dict[str, Any] = {
        "available": False,
        "version": None,
        "error": None,
        "espeak_data": None,
        "recommended_version": "1.13.8",
        "note": "",
    }
    try:
        import sherpa_onnx  # type: ignore
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"sherpa-onnx not importable: {exc}"
        info["note"] = "Install with: pip install sherpa-onnx==1.13.8"
        return info

    info["available"] = True
    info["version"] = getattr(sherpa_onnx, "__version__", "unknown")
    espeak = _find_espeak_data()
    info["espeak_data"] = str(espeak) if espeak else None
    if espeak is None:
        info["note"] = (
            "espeak-ng-data not found. Piper voices need it; place it in "
            f"{paths.VOICES_DIR / 'espeak-ng-data'}"
        )
    return info


def _find_espeak_data() -> Path | None:
    candidates = [
        paths.VOICES_DIR / "espeak-ng-data",
        paths.JARVIS_HOME / "espeak-ng-data",
        Path("/data/data/com.termux/files/usr/share/espeak-ng-data"),
        Path("/usr/share/espeak-ng-data"),
    ]
    for path in candidates:
        if path.is_dir():
            return path
    return None


# --------------------------------------------------------------------------
# installed voices
# --------------------------------------------------------------------------

def _model_paths(voice_id: str) -> tuple[Path, Path]:
    base = paths.VOICES_DIR / voice_id
    return base.with_suffix(".onnx"), base.with_suffix(".onnx.json")


def is_installed(voice_id: str) -> bool:
    if not voice_id or not _SAFE_ID.match(voice_id):
        return False
    model, cfg = _model_paths(voice_id)
    return model.is_file() and cfg.is_file()


def installed_voices() -> list[str]:
    paths.VOICES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for model in sorted(paths.VOICES_DIR.glob("*.onnx")):
        if model.with_suffix(".onnx.json").is_file():
            out.append(model.stem)
    return out


def catalog() -> dict[str, Any]:
    present = set(installed_voices())
    out: dict[str, Any] = {}
    for lang, voices in CATALOG.items():
        out[lang] = [
            {
                **{k: v for k, v in voice.items() if k not in ("url", "config_url")},
                "installed": voice["id"] in present,
            }
            for voice in voices
        ]
    extra = present - {v["id"] for group in CATALOG.values() for v in group}
    out["custom"] = [{"id": vid, "label": vid, "installed": True} for vid in sorted(extra)]
    return out


def _installed_for_language(language: str) -> list[str]:
    """Any installed model whose filename starts with the language code.

    This is what keeps a voice from an earlier JARVIS install working after an
    upgrade, even when its exact id is not in the catalog.
    """
    prefix = f"{language}_"
    return [vid for vid in installed_voices() if vid.lower().startswith(prefix)]


def resolve_voice(language: str, override: str = "", mapping: dict[str, str] | None = None) -> dict[str, Any]:
    """Pick the voice to use, and say exactly why, so the UI can show a
    truthful fallback message instead of failing silently."""
    mapping = mapping or {}
    if override:
        return {
            "voice": override,
            "installed": is_installed(override),
            "reason": "manual",
        }
    preferred = mapping.get(language) or ""
    if preferred and is_installed(preferred):
        return {"voice": preferred, "installed": True, "reason": "configured"}
    for voice in CATALOG.get(language, []):
        if is_installed(voice["id"]):
            return {"voice": voice["id"], "installed": True, "reason": "auto"}
    # Nothing from the catalog: fall back to any voice the user already has for
    # this language (e.g. a model kept from a previous JARVIS install).
    existing = _installed_for_language(language)
    if existing:
        return {"voice": existing[0], "installed": True, "reason": "existing"}
    return {"voice": preferred, "installed": False, "reason": "missing"}


# --------------------------------------------------------------------------
# synthesis
# --------------------------------------------------------------------------

def _get_engine(voice_id: str):
    with _LOCK:
        engine = _ENGINE_CACHE.get(voice_id)
        if engine is not None:
            return engine
        import sherpa_onnx  # type: ignore

        model, cfg = _model_paths(voice_id)
        espeak = _find_espeak_data()
        if espeak is None:
            raise RuntimeError("espeak-ng-data not found")
        tts_config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(model),
                    tokens=str(_tokens_for(cfg, model)),
                    data_dir=str(espeak),
                ),
                num_threads=1,
                provider="cpu",
            ),
            max_num_sentences=1,
        )
        # Only one voice stays resident: loading several at once is the fastest
        # way to run a phone out of RAM.
        _ENGINE_CACHE.clear()
        engine = sherpa_onnx.OfflineTts(tts_config)
        _ENGINE_CACHE[voice_id] = engine
        return engine


def _tokens_for(cfg_path: Path, model_path: Path) -> Path:
    tokens = model_path.with_name(model_path.stem + ".tokens.txt")
    if tokens.is_file():
        return tokens
    # Piper .onnx.json carries the phoneme map; materialise it once.
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    id_map = cfg.get("phoneme_id_map") or {}
    lines = []
    for symbol, ids in id_map.items():
        if not ids:
            continue
        lines.append(f"{symbol} {ids[0]}")
    tokens.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tokens


def synthesize(text: str, voice_id: str, speed: float = 1.0) -> bytes:
    """Return WAV bytes, or raise RuntimeError with a human-readable reason."""
    text = (text or "").strip()
    if not text:
        raise RuntimeError("empty text")
    if not is_installed(voice_id):
        raise RuntimeError(f"voice not installed: {voice_id}")
    info = engine_info()
    if not info["available"]:
        raise RuntimeError(info["error"] or "sherpa-onnx unavailable")
    engine = _get_engine(voice_id)
    audio = engine.generate(text[:1200], sid=0, speed=max(0.5, min(2.0, speed)))
    return _wav_bytes(audio.samples, audio.sample_rate)


def _wav_bytes(samples, sample_rate: int) -> bytes:
    import array
    import io
    import wave

    pcm = array.array("h", (max(-32768, min(32767, int(s * 32767))) for s in samples))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return buf.getvalue()


# --------------------------------------------------------------------------
# explicit, user-approved downloads
# --------------------------------------------------------------------------

def download_status(voice_id: str) -> dict[str, Any]:
    return _DOWNLOADS.get(voice_id, {"state": "idle", "progress": 0, "error": ""})


def start_download(voice_id: str) -> dict[str, Any]:
    """Download one voice. Called only from an explicit user action."""
    entry = None
    for voices in CATALOG.values():
        for voice in voices:
            if voice["id"] == voice_id:
                entry = voice
    if entry is None:
        return {"state": "error", "error": "unknown voice"}
    if is_installed(voice_id):
        return {"state": "done", "progress": 100}
    with _LOCK:
        if _DOWNLOADS.get(voice_id, {}).get("state") == "running":
            return _DOWNLOADS[voice_id]
        _DOWNLOADS[voice_id] = {"state": "running", "progress": 0, "error": ""}

    def worker() -> None:
        model, cfg = _model_paths(voice_id)
        try:
            paths.VOICES_DIR.mkdir(parents=True, exist_ok=True)
            _fetch(entry["config_url"], cfg)
            _fetch(entry["url"], model, progress_key=voice_id)
            _DOWNLOADS[voice_id] = {"state": "done", "progress": 100, "error": ""}
        except Exception as exc:  # noqa: BLE001
            for leftover in (model, cfg):
                try:
                    leftover.unlink()
                except OSError:
                    pass
            _DOWNLOADS[voice_id] = {"state": "error", "progress": 0, "error": str(exc)[:200]}

    threading.Thread(target=worker, daemon=True).start()
    return _DOWNLOADS[voice_id]


def _fetch(url: str, dest: Path, progress_key: str | None = None) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-android/2.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(262144)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if progress_key and total:
                _DOWNLOADS[progress_key] = {
                    "state": "running",
                    "progress": int(done * 100 / total),
                    "error": "",
                }
    tmp.replace(dest)
