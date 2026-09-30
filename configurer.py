"""Setup assistant: asks for the keys in the console and writes the .env file.

    python configurer.py             -> ElevenLabs voice, then (optional) Microsoft
    python configurer.py voix        -> ElevenLabs voice only
    python configurer.py microsoft   -> Microsoft sign-in only (unread mails + Teams)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"')
    return values


def write_env(updates: dict[str, str]) -> None:
    values = read_env()
    values.update({k: v for k, v in updates.items() if v is not None})
    lines = ["# Reglages personnels de Jarvis (ne jamais partager ce fichier)"]
    lines += [f"{k}={v}" for k, v in values.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for k, v in values.items():
        os.environ[k] = v


def ask(question: str, current: str = "", secret: bool = False) -> str:
    if current:
        shown = current[:4] + "..." if secret else current
        answer = input(f"{question}\n  (Entree pour garder : {shown})\n> ").strip()
        return answer or current
    while True:
        answer = input(f"{question}\n> ").strip()
        if answer:
            return answer


def title(text: str) -> None:
    print()
    print("=" * 60)
    print(" " + text)
    print("=" * 60)


def setup_voice() -> bool:
    title("VOIX (ElevenLabs)")
    env = read_env()
    key = ask("Colle ta cle API ElevenLabs (clic droit pour coller) :", env.get("ELEVENLABS_API_KEY", ""), secret=True)
    voice = ask("Colle le Voice ID de la voix choisie :", env.get("ELEVENLABS_VOICE_ID", ""))
    write_env({"ELEVENLABS_API_KEY": key, "ELEVENLABS_VOICE_ID": voice})

    print("\nTest de la voix...")
    import jarvis_voice

    try:
        jarvis_voice.play_file(jarvis_voice.synthesize("Bon retour ! Test de la voix réussi."))
    except jarvis_voice.VoiceError as e:
        print("\nECHEC :", e)
        print("Relance ce programme pour corriger la cle ou le Voice ID.")
        return False
    print("Voix OK ! Tu as du l'entendre.")
    return True


def setup_microsoft() -> bool:
    title("MICROSOFT (mails et Teams non lus)")
    env = read_env()
    client_id = ask("Colle l'ID d'application (client) :", env.get("MICROSOFT_CLIENT_ID", ""))
    tenant_id = ask("Colle l'ID de l'annuaire (locataire) :", env.get("MICROSOFT_TENANT_ID", ""))
    write_env({"MICROSOFT_CLIENT_ID": client_id, "MICROSOFT_TENANT_ID": tenant_id})

    import jarvis_unread

    if not jarvis_unread.sign_in_interactive():
        print("Jarvis marchera quand meme, sans le compteur Teams.")
        return False
    print("\nTest du comptage...")
    mails, teams = jarvis_unread.get_counts(outlook_wait_s=3)
    print(f"  Mails non lus           : {'?' if mails is None else mails}")
    print(f"  Conversations Teams     : {'?' if teams is None else teams}")
    return True


def main() -> int:
    load_dotenv(ENV_FILE)
    step = sys.argv[1] if len(sys.argv) > 1 else "tout"
    try:
        if step in ("tout", "voix"):
            setup_voice()
        if step == "tout":
            answer = input("\nConfigurer maintenant le compteur Microsoft (mails + Teams) ? (o/n)\n> ").strip().lower()
            if answer.startswith("o"):
                setup_microsoft()
            else:
                print("Tu pourras le faire plus tard avec Connexion-Microsoft.bat.")
        if step == "microsoft":
            setup_microsoft()
    except (KeyboardInterrupt, EOFError):
        print("\nAnnule.")
        return 1
    print("\nConfiguration terminee.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
