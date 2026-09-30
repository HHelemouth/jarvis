"""ElevenLabs text-to-speech, cached as MP3 files and played natively on Windows."""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

log = logging.getLogger("jarvis")

CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "voix"
DEFAULT_MODEL = "eleven_multilingual_v2"
OUTPUT_FORMAT = "mp3_44100_128"

_mci_lock = threading.Lock()
_mci_counter = 0


class VoiceError(Exception):
    pass


def _config() -> tuple[str, str, str]:
    key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    voice = (os.environ.get("ELEVENLABS_VOICE_ID") or "").strip()
    model = (os.environ.get("ELEVENLABS_MODEL_ID") or DEFAULT_MODEL).strip()
    return key, voice, model


def is_configured() -> bool:
    key, voice, _ = _config()
    return bool(key and voice)


def cache_path(text: str) -> Path:
    _, voice, model = _config()
    digest = hashlib.sha256(f"{voice}|{model}|{OUTPUT_FORMAT}|{text}".encode()).hexdigest()[:24]
    return CACHE_DIR / f"{digest}.mp3"


def synthesize(text: str) -> Path:
    """Return the MP3 for this sentence, generating it with ElevenLabs only if not cached yet."""
    path = cache_path(text)
    if path.is_file() and path.stat().st_size > 0:
        return path

    key, voice, model = _config()
    if not key or not voice:
        raise VoiceError("ELEVENLABS_API_KEY ou ELEVENLABS_VOICE_ID manquant dans le fichier .env")

    from elevenlabs.client import ElevenLabs

    try:
        client = ElevenLabs(api_key=key)
        audio = b"".join(
            client.text_to_speech.convert(
                voice_id=voice, text=text, model_id=model, output_format=OUTPUT_FORMAT
            )
        )
    except Exception as e:
        raise VoiceError(_explain_error(e)) from e
    if not audio:
        raise VoiceError("ElevenLabs a renvoye un son vide.")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(audio)
    tmp.replace(path)
    return path


def _explain_error(e: Exception) -> str:
    status = getattr(e, "status_code", None)
    if status == 401:
        return "Cle ElevenLabs refusee (401) : verifie ELEVENLABS_API_KEY dans .env."
    if status == 402:
        return (
            "ElevenLabs refuse cette voix (402). Sur le plan gratuit, les voix de la Voice Library "
            "ne marchent pas par l'API : choisis une voix native (onglet 'Default' / voix ElevenLabs)."
        )
    if status == 404:
        return "Voice ID introuvable (404) : verifie ELEVENLABS_VOICE_ID dans .env."
    if status == 429:
        return "Quota ElevenLabs depasse (429) : plus de caracteres disponibles ce mois-ci."
    return f"Erreur ElevenLabs : {e}"


def play_file(path: Path) -> None:
    """Play an MP3 and wait until it is finished."""
    if sys.platform == "win32":
        _play_mci(path)
    elif sys.platform == "darwin":
        subprocess.run(["afplay", str(path)], check=False)
    else:
        subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)], check=False)


def _play_mci(path: Path) -> None:
    # Windows' built-in media API (winmm) plays MP3 without ffmpeg or any extra install.
    import ctypes

    global _mci_counter
    winmm = ctypes.windll.winmm
    with _mci_lock:
        _mci_counter += 1
        alias = f"jarvis{_mci_counter}"

    def mci(cmd: str) -> None:
        err = winmm.mciSendStringW(cmd, None, 0, None)
        if err:
            buf = ctypes.create_unicode_buffer(256)
            winmm.mciGetErrorStringW(err, buf, 256)
            raise VoiceError(f"Lecture audio impossible : {buf.value}")

    mci(f'open "{path}" type mpegvideo alias {alias}')
    try:
        mci(f"play {alias} wait")
    finally:
        winmm.mciSendStringW(f"close {alias}", None, 0, None)


def say(text: str) -> bool:
    try:
        play_file(synthesize(text))
        return True
    except VoiceError as e:
        log.error("%s", e)
    except Exception as e:  # never let the voice break the rest of the wake-up
        log.error("Voix : erreur inattendue : %s", e)
    return False
