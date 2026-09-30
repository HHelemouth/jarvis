"""Count unread Outlook mails and unread Teams chats.

Two sources:
- Microsoft Graph (online, needs a one-time sign-in): mails + Teams, works with any Outlook.
- Classic Outlook on this PC (COM): mails only, no sign-in needed.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("jarvis")

TOKEN_CACHE = Path(__file__).resolve().parent / ".cache" / "microsoft_connexion.json"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.ReadBasic", "Chat.Read"]


# ---------------------------------------------------------------------------
# Microsoft Graph
# ---------------------------------------------------------------------------

def graph_configured() -> bool:
    return bool((os.environ.get("MICROSOFT_CLIENT_ID") or "").strip())


def _msal_app():
    import msal

    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE.is_file():
        cache.deserialize(TOKEN_CACHE.read_text(encoding="utf-8"))
    tenant = (os.environ.get("MICROSOFT_TENANT_ID") or "organizations").strip()
    app = msal.PublicClientApplication(
        os.environ["MICROSOFT_CLIENT_ID"].strip(),
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=cache,
    )
    return app, cache


def _save_cache(cache) -> None:
    if cache.has_state_changed:
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(cache.serialize(), encoding="utf-8")


def sign_in_interactive() -> bool:
    """One-time sign-in with a code shown in the console (device code flow)."""
    app, cache = _msal_app()
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print("Impossible de demarrer la connexion :", flow.get("error_description", flow))
        return False
    print()
    print("  1. Ouvre cette page :", flow["verification_uri"])
    print("  2. Tape ce code      :", flow["user_code"])
    print("  3. Connecte-toi avec ton compte pro et accepte les autorisations.")
    print()
    result = app.acquire_token_by_device_flow(flow)
    _save_cache(cache)
    if "access_token" in result:
        print("Connexion Microsoft reussie.")
        return True
    print("Connexion refusee :", result.get("error_description", result.get("error")))
    return False


def _graph_token() -> tuple[str, str] | None:
    """(access_token, my_user_id) or None if not signed in."""
    if not graph_configured():
        return None
    app, cache = _msal_app()
    accounts = app.get_accounts()
    if not accounts:
        log.warning("Microsoft : pas encore connecte. Lance 'Connexion-Microsoft.bat'.")
        return None
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache(cache)
    if not result or "access_token" not in result:
        log.warning("Microsoft : connexion expiree. Relance 'Connexion-Microsoft.bat'.")
        return None
    my_id = (accounts[0].get("local_account_id") or "").lower()
    return result["access_token"], my_id


def _get(token: str, url: str, params: dict | None = None) -> dict:
    import requests

    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=8)
    r.raise_for_status()
    return r.json()


def graph_unread_mails(token: str) -> int:
    data = _get(token, f"{GRAPH}/me/mailFolders/inbox", {"$select": "unreadItemCount"})
    return int(data.get("unreadItemCount", 0))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.rstrip("Z")
    if "." in v:
        head, frac = v.split(".", 1)
        v = f"{head}.{frac[:6].ljust(6, '0')}"
    try:
        return datetime.fromisoformat(v).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def graph_unread_teams_chats(token: str, my_id: str) -> int:
    """Number of Teams chats whose latest message (from someone else) I have not read."""
    params = {
        "$expand": "lastMessagePreview",
        "$top": "50",
        "$orderby": "lastMessagePreview/createdDateTime desc",
    }
    try:
        data = _get(token, f"{GRAPH}/me/chats", params)
    except Exception:
        params.pop("$orderby")
        data = _get(token, f"{GRAPH}/me/chats", params)

    unread = 0
    for chat in data.get("value", []):
        preview = chat.get("lastMessagePreview") or {}
        view = chat.get("viewpoint") or {}
        if not preview or preview.get("isDeleted") or view.get("isHidden"):
            continue
        if preview.get("messageType", "message") != "message":
            continue
        sender = (((preview.get("from") or {}).get("user")) or {}).get("id", "")
        if sender and sender.lower() == my_id:
            continue
        sent = _parse_time(preview.get("createdDateTime"))
        read = _parse_time(view.get("lastMessageReadDateTime"))
        if sent and (read is None or sent > read):
            unread += 1
    return unread


# ---------------------------------------------------------------------------
# Classic Outlook (COM)
# ---------------------------------------------------------------------------

def _com_inbox_unread(app, verbose: bool = True) -> int:
    """Unread mails in the inbox of every account set up in Outlook."""
    ns = app.GetNamespace("MAPI")
    total = 0
    seen: set[str] = set()
    for account in ns.Accounts:
        try:
            store = account.DeliveryStore
            if store.StoreID in seen:
                continue
            seen.add(store.StoreID)
            n = int(store.GetDefaultFolder(6).UnReadItemCount)  # 6 = inbox
        except Exception:
            continue
        if verbose:
            log.info("Outlook : %s -> %d non lu(s) dans la boite de reception", account.DisplayName, n)
        total += n
    if not seen:
        total = int(ns.GetDefaultFolder(6).UnReadItemCount)
    return total


def outlook_com_unread(wait_s: float, settle_s: float = 5.0, min_wait_s: float = 10.0) -> int | None:
    """Ask classic Outlook for the unread count.

    If Outlook was already open, answer at once. If it is just starting, it first has to
    download the mails received while it was closed: give it min_wait_s, then keep reading
    until the count stops moving for settle_s (or wait_s runs out), otherwise we would
    announce a stale number.
    """
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None
    pythoncom.CoInitialize()
    try:
        deadline = time.monotonic() + wait_s
        app = None
        first_try = True
        while app is None:
            try:
                app = win32com.client.GetActiveObject("Outlook.Application")
            except Exception:
                if time.monotonic() >= deadline:
                    return None
                first_try = False
                time.sleep(0.5)

        if first_try:
            return _com_inbox_unread(app)

        log.info("Outlook vient de demarrer : j'attends qu'il recupere les nouveaux mails...")
        try:
            app.GetNamespace("MAPI").SendAndReceive(False)
        except Exception:
            pass
        value: int | None = None
        started = changed_at = time.monotonic()
        while True:
            try:
                current = _com_inbox_unread(app, verbose=False)
            except Exception:
                current = value
            now = time.monotonic()
            if current != value:
                value, changed_at = current, now
                log.info("Outlook : %s non lu(s) pour l'instant...", value)
            elif value is not None and now - changed_at >= settle_s and now - started >= min_wait_s:
                return _com_inbox_unread(app)
            if now >= deadline:
                return value
            time.sleep(1.0)
    finally:
        pythoncom.CoUninitialize()


# ---------------------------------------------------------------------------

def get_counts(outlook_wait_s: float = 10.0) -> tuple[int | None, int | None]:
    """(unread mails, unread Teams chats); None means "could not find out"."""
    mails: int | None = None
    teams: int | None = None

    auth = None
    try:
        auth = _graph_token()
    except Exception as e:
        log.warning("Microsoft : %s", e)
    if auth:
        token, my_id = auth
        try:
            mails = graph_unread_mails(token)
        except Exception as e:
            log.warning("Mails via Microsoft : %s", e)
        try:
            teams = graph_unread_teams_chats(token, my_id)
        except Exception as e:
            log.warning("Teams via Microsoft : %s", e)

    if mails is None:
        mails = outlook_com_unread(outlook_wait_s)
        if mails is None:
            log.warning("Mails non lus : impossible de les compter (Outlook classique absent et pas de connexion Microsoft).")

    log.info("Non lus : mails=%s, conversations Teams=%s",
             "?" if mails is None else mails, "?" if teams is None else teams)
    return mails, teams
