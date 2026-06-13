"""Google OAuth for application tracking — read-only Gmail, local token.

Adapted from the user's daily-os-bot auth flow. Credentials (the OAuth client
downloaded from Google Cloud Console) live at APP_DIR/google_credentials.json;
the granted token at APP_DIR/google_token.json (0600). Scope is strictly
read-only: we fetch message metadata, never modify, never send.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class TrackingNotConfigured(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Gmail tracking is not connected. Run `applypilot track auth` "
            "(needs google_credentials.json in ~/.applypilot — see SETUP.md)."
        )


def _app_dir() -> Path:
    return Path(os.environ.get("APPLYPILOT_DIR", Path.home() / ".applypilot"))


def credentials_path() -> Path:
    return _app_dir() / "google_credentials.json"


def _bundled_credentials_path() -> Path:
    # An OAuth client shipped with the app, so end users never touch Google
    # Cloud Console — one click connects. Installed-app client secrets are not
    # confidential (Google's own docs say so). User-supplied creds win.
    return Path(__file__).resolve().parent.parent / "google_credentials.json"


def resolve_credentials() -> Path | None:
    if credentials_path().exists():
        return credentials_path()
    if _bundled_credentials_path().exists():
        return _bundled_credentials_path()
    return None


def has_credentials() -> bool:
    return resolve_credentials() is not None


def token_path() -> Path:
    return _app_dir() / "google_token.json"


def is_configured() -> bool:
    return token_path().exists()


def _write_token(creds) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_credentials():
    """Load (and refresh if needed) the stored token. Raises TrackingNotConfigured."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not token_path().exists():
        raise TrackingNotConfigured()
    creds = Credentials.from_authorized_user_file(str(token_path()), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _write_token(creds)
    return creds


def get_service():
    """An authorized Gmail API service. Raises TrackingNotConfigured."""
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=load_credentials(), cache_discovery=False)


def run_auth_flow() -> Path:
    """Interactive browser OAuth (run from the CLI). Returns the token path."""
    creds_file = resolve_credentials()
    if creds_file is None:
        raise SystemExit(
            f"Missing {credentials_path()}.\n"
            "Create an OAuth 'Desktop app' client in Google Cloud Console "
            "(enable the Gmail API), download the JSON, and save it there. "
            "SETUP.md walks through it."
        )
    return _run_flow(creds_file)


class CredentialsMissing(RuntimeError):
    """No OAuth client available (neither user-supplied nor bundled)."""


def _run_flow(creds_file: Path) -> Path:
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    _write_token(creds)
    return token_path()


# --- one-click connect from the web app ------------------------------------
# run_local_server blocks until the user approves in their browser, so the
# connect runs in a daemon thread and the UI polls the phase.

_connect_thread = None  # type: ignore[var-annotated]
_connect_phase = {"phase": "idle", "error": None}  # idle|opening|connected|error


def connect_phase() -> dict:
    global _connect_phase
    if is_configured() and _connect_phase["phase"] not in ("opening",):
        return {"phase": "connected", "error": None}
    return dict(_connect_phase)


def start_connect() -> dict:
    """Open the Google consent screen in the user's browser (one click).

    Returns the current phase immediately; the UI polls /api/tracking/status.
    """
    global _connect_thread, _connect_phase
    import threading

    if _connect_thread is not None and _connect_thread.is_alive():
        return dict(_connect_phase)
    creds_file = resolve_credentials()
    if creds_file is None:
        raise CredentialsMissing(
            "No Google OAuth client is configured. Add google_credentials.json "
            "to ~/.applypilot (or bundle one with the app)."
        )

    def _run() -> None:
        global _connect_phase
        _connect_phase = {"phase": "opening", "error": None}
        try:
            _run_flow(creds_file)
            _connect_phase = {"phase": "connected", "error": None}
        except Exception as e:  # noqa: BLE001 — surfaced to the UI
            logger.warning("Gmail connect failed: %s", e)
            _connect_phase = {"phase": "error", "error": str(e)[:200]}

    _connect_phase = {"phase": "opening", "error": None}
    _connect_thread = threading.Thread(target=_run, name="ap-gmail-connect", daemon=True)
    _connect_thread.start()
    return dict(_connect_phase)


def disconnect() -> None:
    """Forget the stored token (revoke locally)."""
    global _connect_phase
    token_path().unlink(missing_ok=True)
    _connect_phase = {"phase": "idle", "error": None}
