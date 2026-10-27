#!/usr/bin/env python3
"""Checks for the voice system: detection, gender, and honest failure.

Run with: python3 tools/test_voices.py

Every voice here is a fake: a few zero bytes named like a model, with a
config beside it. That is enough to test *detection*, which is what was
broken — a correctly installed Alan was reported as missing because the scan
only looked at the top level of the voices folder.

Synthesis itself needs sherpa-onnx and a real 63 MB model, so it is not
exercised here; what is exercised is that a voice which cannot be synthesised
is reported rather than quietly replaced.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: Point the server's paths at a scratch home before importing it.
_HOME = Path(tempfile.mkdtemp(prefix="jarvis-voicetest-"))
os.environ["JARVIS_HOME"] = str(_HOME)
os.environ["JARVIS_VOICES"] = str(_HOME / "voices")

from server import config, voices  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  [{detail}]" if detail and not cond else ""))


def section(name: str) -> None:
    print(f"\n{name}")


VOICES = Path(os.environ["JARVIS_VOICES"])


def plant(relative: str, *, config_name: str | None = None) -> Path:
    """Put a fake voice on disk the way some real installer would."""
    model = VOICES / relative
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"\0" * 32)
    cfg = (model.parent / config_name) if config_name else Path(str(model) + ".json")
    cfg.write_text('{"phoneme_id_map": {}}', encoding="utf-8")
    return model


def clear() -> None:
    if VOICES.exists():
        shutil.rmtree(VOICES)
    VOICES.mkdir(parents=True)


# --------------------------------------------------------------------------

def test_detection() -> None:
    section("Detection — the four ways a voice lands on disk")
    clear()
    plant("ro_RO-mihai-medium.onnx")
    plant("en/en_GB/alan/medium/en_GB-alan-medium.onnx")
    plant("de_DE-thorsten-medium.onnx", config_name="de_DE-thorsten-medium.json")
    plant("sub/it_IT-paola-medium.ONNX")

    found = voices.installed_voices()
    ok("the flat layout is found", "ro_RO-mihai-medium" in found, str(found))
    ok("a voice nested in the publisher's tree is found",
       "en_GB-alan-medium" in found, str(found))
    ok("a config named .json instead of .onnx.json is accepted",
       "de_DE-thorsten-medium" in found, str(found))
    ok("an uppercase .ONNX is accepted", "it_IT-paola-medium" in found, str(found))
    ok("is_installed agrees with the scan",
       all(voices.is_installed(v) for v in found), str(found))

    # The bug this suite exists for: before the recursive scan, Alan was on
    # disk and working, and JARVIS reported him missing.
    ok("Alan is not reported missing while he is installed",
       voices.is_installed("en_GB-alan-medium"))

    section("Detection — what must NOT count")
    clear()
    lonely = VOICES / "en_GB-alan-medium.onnx"
    lonely.write_bytes(b"\0" * 32)
    ok("a model with no config is not offered", not voices.is_installed("en_GB-alan-medium"))
    diag = voices.diagnostics()
    ok("and it is reported as unusable, with the reason",
       any("alan" in u["path"] for u in diag["unusable"]), str(diag["unusable"]))
    ok("a path outside the id charset is refused",
       not voices.is_installed("../../etc/passwd"))
    ok("an empty id is refused", not voices.is_installed(""))


def test_diagnostics() -> None:
    section("Diagnostics name the real path")
    clear()
    plant("en/en_GB/alan/medium/en_GB-alan-medium.onnx")
    diag = voices.diagnostics()
    ok("the search folder is named", diag["root"] == str(VOICES), diag["root"])
    entry = next((v for v in diag["voices"] if v["id"] == "en_GB-alan-medium"), None)
    ok("the voice is listed", entry is not None)
    ok("with the path it was actually found at",
       entry and entry["path"] == "en/en_GB/alan/medium/en_GB-alan-medium.onnx",
       str(entry))
    ok("the engine report is included", "engine" in diag)
    ok("an empty folder says so plainly",
       (clear(), voices.diagnostics()["voices"] == [])[1])


def test_gender() -> None:
    section("Gender")
    clear()
    plant("ro_RO-mihai-medium.onnx")      # male, the only Romanian voice
    plant("en_GB-alan-medium.onnx")       # male
    plant("en_US-amy-low.onnx")           # female

    ok("Mihai is male", voices.gender_of("ro_RO-mihai-medium") == "male")
    ok("Amy is female", voices.gender_of("en_US-amy-low") == "female")
    ok("an unknown voice is not guessed at",
       voices.gender_of("xx_XX-nobody-medium") == "",
       voices.gender_of("xx_XX-nobody-medium"))
    # A voice kept from an older install has a different id but is still Alan.
    plant("en_US-alan-medium.onnx")
    ok("a legacy id is recognised by its speaker name",
       voices.gender_of("en_US-alan-medium") == "male")

    english = voices.genders_for("en")
    ok("English offers both genders",
       "male" in english and "female" in english, str(english))
    romanian = voices.genders_for("ro")
    ok("Romanian offers only male", list(romanian) == ["male"], str(romanian))

    ok("an uncatalogued installed voice is still selectable",
       (clear(), plant("fr_FR-someone-medium.onnx"),
        any(v["id"] == "fr_FR-someone-medium" for v in voices.voices_for("fr")))[2])


def test_resolution() -> None:
    section("Choosing a voice, and refusing to substitute")
    clear()
    plant("ro_RO-mihai-medium.onnx")
    plant("en_GB-alan-medium.onnx")
    plant("en_US-amy-low.onnx")

    chosen = voices.resolve_voice("en", gender_mapping={"en": "female"})
    ok("asking for a female English voice gets Amy",
       chosen["voice"] == "en_US-amy-low", str(chosen))
    ok("and it reports the gender it gave", chosen["gender"] == "female")

    chosen = voices.resolve_voice("en", gender_mapping={"en": "male"})
    ok("asking for male English gets Alan", chosen["voice"] == "en_GB-alan-medium", str(chosen))

    # The heart of the bug report: no female Romanian voice exists offline.
    chosen = voices.resolve_voice("ro", gender_mapping={"ro": "female"})
    ok("a female Romanian voice is NOT silently answered with the male one",
       chosen["voice"] == "", str(chosen))
    ok("the refusal says why", chosen["reason"] == "gender_unavailable", chosen["reason"])
    ok("and says what is available", chosen["available_genders"] == ["male"],
       str(chosen.get("available_genders")))

    # A specific choice is kept, as long as it matches the gender asked for.
    chosen = voices.resolve_voice(
        "en", mapping={"en": "en_US-amy-low"}, gender_mapping={"en": "female"})
    ok("an explicit pick is honoured within the gender",
       chosen["voice"] == "en_US-amy-low", str(chosen))
    chosen = voices.resolve_voice(
        "en", mapping={"en": "en_US-amy-low"}, gender_mapping={"en": "male"})
    ok("a pick of the wrong gender yields the requested gender, not the pick",
       chosen["voice"] == "en_GB-alan-medium", str(chosen))

    chosen = voices.resolve_voice("ro")
    ok("with no gender asked for, Romanian still works",
       chosen["voice"] == "ro_RO-mihai-medium" and chosen["installed"], str(chosen))
    ok("the label is reported for the interface", chosen["label"] == "Mihai", str(chosen))

    chosen = voices.resolve_voice("de")
    ok("a language with nothing installed reports missing, not a substitute",
       chosen["voice"] == "" and chosen["reason"] == "missing", str(chosen))

    chosen = voices.resolve_voice("en", override="en_US-amy-low")
    ok("a manual override is passed through", chosen["reason"] == "manual")

    clear()
    plant("en/en_GB/alan/medium/en_GB-alan-medium.onnx")
    chosen = voices.resolve_voice("en", mapping={"en": "en_GB-alan-medium"})
    ok("a configured voice found nested is used, not called missing",
       chosen["installed"] and chosen["reason"] == "configured", str(chosen))


def test_settings_persist() -> None:
    section("Settings survive a restart")
    config.update_config({
        "tts_speed": 0.45,
        "voice_gender_by_language": {"en": "female"},
        "tts_browser_fallback": True,
    })
    # Drop the in-memory copy, as a restart would.
    config._CACHE = None
    cfg = config.get_config()
    ok("a slow speed is kept", cfg["tts_speed"] == 0.45, str(cfg["tts_speed"]))
    ok("the chosen gender is kept",
       cfg["voice_gender_by_language"]["en"] == "female",
       str(cfg["voice_gender_by_language"]))
    ok("the device-voice switch is kept", cfg["tts_browser_fallback"] is True)
    ok("other languages are not clobbered by a single-language save",
       set(cfg["voice_gender_by_language"]) >= {"ro", "en", "fr", "de", "es", "it"},
       str(cfg["voice_gender_by_language"]))

    section("Bad values cannot leave a language mute")
    config.update_config({"tts_speed": 0.01})
    ok("a speed below the floor is clamped, not rejected",
       config.get_config()["tts_speed"] == 0.3, str(config.get_config()["tts_speed"]))
    config.update_config({"tts_speed": 99})
    ok("a speed above the ceiling is clamped",
       config.get_config()["tts_speed"] == 2.0, str(config.get_config()["tts_speed"]))
    config.update_config({"voice_gender_by_language": {"en": "robot"}})
    ok("a nonsense gender becomes 'any'",
       config.get_config()["voice_gender_by_language"]["en"] == "",
       str(config.get_config()["voice_gender_by_language"]))

    # The slider's own range must reach the backend's floor, or the extra
    # range is only theoretical.
    ui = (ROOT / "web/js/ui/settings.js").read_text()
    ok("the slider goes down to 0.4", "speed.min = '0.4'" in ui)
    ok("the slider step is fine enough to use", "speed.step = '0.05'" in ui)

    section("Speech is never silently replaced")
    js = (ROOT / "web/js/ui/voice.js").read_text()
    ok("the device voice is used only when switched on",
       "cfg.tts_browser_fallback" in js)
    ok("and when used, it says whose voice it is",
       "error.voiceDevice" in js)
    ok("a backend failure is explained, not swallowed", "_explain(" in js)

    keys = (ROOT / "web/js/i18n/en.js").read_text()
    for key in ("error.voiceDevice", "error.voiceGenderOnly", "settings.genderMale",
                "settings.preview", "settings.voiceDiagnostics", "settings.speedValue"):
        ok(f"{key} is translated", f'"{key}"' in keys)


def main() -> int:
    print("JARVIS — voice system self-test (offline, no models needed)")
    try:
        test_detection()
        test_diagnostics()
        test_gender()
        test_resolution()
        test_settings_persist()
    finally:
        shutil.rmtree(_HOME, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print("  failed: " + name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
