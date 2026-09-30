"""Jarvis: double clap -> wake up the desk.

On a double clap:
  1. the voice greets you, then tells you how many mails and Teams chats are unread;
  2. Claude opens in Edge, maximized on the middle screen;
  3. Teams (left half) and Outlook (right half) open on the right screen;
  4. FIP Groove starts playing in a minimized Edge window.

Settings that are personal (API keys, microphone...) live in the .env file.
Set JARVIS_DEBUG=1 to print the measured level and the threshold at every peak.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

from clap_detector import ClapDetector, ClapSettings

load_dotenv(Path(__file__).resolve().parent / ".env")

# ---------------------------------------------------------------------------
# What opens, and where. Screens are counted from the left: 0 = left, 1 = middle, 2 = right.
# ---------------------------------------------------------------------------

CLAUDE_URL = "https://claude.ai/new"
CLAUDE_SCREEN = 1

TEAMS_SCREEN = 2
TEAMS_PART = "left"      # "full", "left", "right", "top" or "bottom"
OUTLOOK_SCREEN = 2
OUTLOOK_PART = "right"

# Direct FIP Groove audio stream: plays without a click, unlike the radiofrance.fr page.
RADIO_URL = "https://icecast.radiofrance.fr/fipgroove-midfi.mp3"

# ---------------------------------------------------------------------------
# What the voice says. {items} is replaced by e.g. "3 mails non lus et 2 conversations Teams".
# ---------------------------------------------------------------------------

GREETING = "Bon retour !"
PROGRAM = "Au programme : {items}. On attaque ?"
NOTHING_UNREAD = "Boîte vide, rien ne t'arrête aujourd'hui."
COUNTS_UNKNOWN = "On attaque ?"

COUNTS_TIMEOUT_S = 30.0     # max wait for the unread counts (Outlook needs time to sync when it was closed)
WINDOW_TIMEOUT_S = 40.0     # max wait for an app window to appear (Teams can be slow)

# ---------------------------------------------------------------------------
# Clap detection (the thresholds can be overridden in .env, see README)
# ---------------------------------------------------------------------------

BLOCK_MS = 10


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip().replace(",", ".")
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


DEBUG = (os.environ.get("JARVIS_DEBUG") or "").strip().lower() in ("1", "true", "oui", "yes")
NO_ACTIONS = (os.environ.get("JARVIS_SANS_ACTIONS") or "").strip().lower() in ("1", "true", "oui", "yes")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("jarvis")
logging.getLogger("httpx").setLevel(logging.WARNING)  # hide one line per ElevenLabs request


# ---------------------------------------------------------------------------
# Sentence
# ---------------------------------------------------------------------------

def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def counts_sentence(mails: int | None, teams: int | None) -> str:
    if mails is None and teams is None:
        return COUNTS_UNKNOWN
    if not mails and not teams:
        return NOTHING_UNREAD
    items = []
    if mails:
        items.append(_plural(mails, "mail non lu", "mails non lus"))
    if teams:
        items.append(_plural(teams, "conversation Teams", "conversations Teams"))
    return PROGRAM.format(items=" et ".join(items))


# ---------------------------------------------------------------------------
# Microphone
# ---------------------------------------------------------------------------

def _rms(block) -> float:
    import numpy as np

    b = block.astype(np.float64)
    if b.ndim > 1:
        b = b.mean(axis=1)
    return float(np.sqrt(np.mean(b * b))) if b.size else 0.0


def _choose_microphone():
    import sounddevice as sd

    spec = (os.environ.get("JARVIS_INPUT_DEVICE") or "").strip()
    if spec:
        if spec.isdigit():
            idx = int(spec)
        else:
            matches = [
                i for i, d in enumerate(sd.query_devices())
                if d["max_input_channels"] > 0 and spec.lower() in d["name"].lower()
            ]
            if not matches:
                log.error("Aucun micro ne correspond a JARVIS_INPUT_DEVICE=%r. Lance Micros.bat pour la liste.", spec)
                raise SystemExit(1)
            idx = matches[0]
    else:
        idx = sd.default.device[0]
        if idx is None or idx < 0:
            log.error("Aucun micro par defaut dans Windows (Parametres > Son > Entree).")
            raise SystemExit(1)

    dev = sd.query_devices(idx)
    rate = int(_env_float("JARVIS_SAMPLE_RATE", 0)) or int(dev["default_samplerate"])
    return idx, dev["name"], rate


def list_microphones() -> None:
    import sounddevice as sd

    default = sd.default.device[0]
    print("Micros disponibles (numero : nom, frequence) :")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            mark = "  <- micro par defaut" if i == default else ""
            print(f"  {i:3d} : {d['name']}  ({int(d['default_samplerate'])} Hz){mark}")


def wait_for_double_clap() -> bool:
    import sounddevice as sd

    idx, name, rate = _choose_microphone()
    block = max(1, int(rate * BLOCK_MS / 1000))
    settings = ClapSettings(
        min_rms=_env_float("JARVIS_MIN_RMS", 0.28),
        decay_ratio=_env_float("JARVIS_DECAY_RATIO", 0.35),
        min_gap_s=_env_float("JARVIS_MIN_GAP_S", 0.12),
        max_gap_s=_env_float("JARVIS_MAX_GAP_S", 0.35),
    )
    detector = ClapDetector(settings, debug=DEBUG)

    log.info("Micro : [%d] %s a %d Hz", idx, name, rate)
    if DEBUG:
        log.info("MODE DEBUG : seuil minimum %.2f, retombee < %d %% du pic en 100 ms, ecart %.2f-%.2f s",
                 settings.min_rms, round(settings.decay_ratio * 100), settings.min_gap_s, settings.max_gap_s)
    log.info("J'ecoute... Claque deux fois des mains. (Ctrl+C pour arreter)")

    try:
        with sd.InputStream(device=idx, samplerate=rate, channels=1, dtype="float32", blocksize=block) as stream:
            t = 0.0
            while True:
                data, overflowed = stream.read(block)
                if overflowed and DEBUG:
                    log.info("(micro : quelques echantillons perdus)")
                t += block / rate
                if detector.feed(_rms(data), t) == "double":
                    if NO_ACTIONS:
                        log.info(">>> DOUBLE CLAP DETECTE (mode test : aucune action, j'ecoute encore)")
                        continue
                    log.info(">>> DOUBLE CLAP DETECTE")
                    return True
    except KeyboardInterrupt:
        log.info("Arrete.")
        return False
    except sd.PortAudioError as e:
        log.error("Erreur micro : %s", e)
        log.error("Essaie une autre frequence avec JARVIS_SAMPLE_RATE=48000 (ou 44100) dans .env.")
        return False


# ---------------------------------------------------------------------------
# Wake-up sequence
# ---------------------------------------------------------------------------

def voice_sequence(counts: dict, counts_ready: threading.Event) -> None:
    import jarvis_voice

    if not jarvis_voice.is_configured():
        log.warning("Voix desactivee : ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID manquants dans .env.")
        counts_ready.wait(COUNTS_TIMEOUT_S)
        return
    jarvis_voice.say(GREETING)
    counts_ready.wait(COUNTS_TIMEOUT_S)
    jarvis_voice.say(counts_sentence(counts.get("mails"), counts.get("teams")))


def fetch_counts(counts: dict, counts_ready: threading.Event) -> None:
    import jarvis_unread

    try:
        counts["mails"], counts["teams"] = jarvis_unread.get_counts(outlook_wait_s=COUNTS_TIMEOUT_S - 2)
    except Exception as e:
        log.error("Comptage des non lus : %s", e)
    finally:
        counts_ready.set()


def open_and_place(label: str, launch, exes, screen: int, part: str, exclude=None, title_hint=None):
    import jarvis_windows as win

    launch()
    hwnd = win.wait_for_window(exes, WINDOW_TIMEOUT_S, exclude=exclude, title_hint=title_hint)
    if not hwnd:
        log.warning("%s : fenetre introuvable, je la laisse ou Windows l'a mise.", label)
        return None
    rect = win.monitor_rect(screen)
    if rect is None:
        return hwnd
    target = win.split_rect(rect, part)
    # Apps often resize themselves while loading: place once, then again once settled.
    win.place_window(hwnd, target, maximize=(part == "full"))
    time.sleep(2.5)
    win.place_window(hwnd, target, maximize=(part == "full"))
    log.info("%s : place sur l'ecran %d (%s).", label, screen + 1, part)
    return hwnd


def wake_up_desk() -> None:
    import jarvis_windows as win

    win.enable_dpi_awareness()

    counts: dict = {}
    counts_ready = threading.Event()
    threading.Thread(target=fetch_counts, args=(counts, counts_ready), daemon=True).start()
    voice = threading.Thread(target=voice_sequence, args=(counts, counts_ready))
    voice.start()

    edge_before = {w[0] for w in win.app_windows(win.EDGE_EXES)}
    results: dict = {}

    def _claude():
        results["claude"] = open_and_place(
            "Claude", lambda: win.open_edge_window(CLAUDE_URL), win.EDGE_EXES,
            CLAUDE_SCREEN, "full", exclude=edge_before, title_hint="claude",
        )

    apps = [
        threading.Thread(target=_claude),
        threading.Thread(target=open_and_place, args=(
            "Teams", win.launch_teams, win.TEAMS_EXES, TEAMS_SCREEN, TEAMS_PART)),
        threading.Thread(target=open_and_place, args=(
            "Outlook", win.launch_outlook, win.OUTLOOK_EXES, OUTLOOK_SCREEN, OUTLOOK_PART)),
    ]
    for th in apps:
        th.start()
        time.sleep(0.3)
    for th in apps:
        th.join()

    # Music comes in once the voice is done, so the voice stays clear.
    voice.join()
    edge_now = {w[0] for w in win.app_windows(win.EDGE_EXES)}
    win.open_edge_window(RADIO_URL)
    radio = win.wait_for_window(win.EDGE_EXES, 15, exclude=edge_now, title_hint="fipgroove")
    if radio:
        win.minimize_window(radio)
        log.info("FIP Groove : en lecture (fenetre Edge reduite).")
    else:
        log.warning("FIP Groove : fenetre non trouvee, la radio s'ouvre quand meme dans Edge.")

    if results.get("claude"):
        win.bring_to_front(results["claude"])
    log.info("Bureau pret. Bonne session !")


def warm_voice_cache() -> None:
    """Generate the fixed sentences in advance so the first clap answers instantly."""
    import jarvis_voice

    if not jarvis_voice.is_configured():
        return
    for text in (GREETING, NOTHING_UNREAD, COUNTS_UNKNOWN):
        try:
            jarvis_voice.synthesize(text)
        except Exception as e:
            log.warning("Pre-generation de la voix : %s", e)
            return


def main() -> int:
    if "--micros" in sys.argv:
        list_microphones()
        return 0
    if sys.platform != "win32":
        log.warning("Ce projet est configure pour Windows : seule l'ecoute des claps fonctionnera ici.")

    threading.Thread(target=warm_voice_cache, daemon=True).start()
    if not wait_for_double_clap():
        return 0
    if sys.platform == "win32":
        wake_up_desk()
    return 0


if __name__ == "__main__":
    sys.exit(main())
