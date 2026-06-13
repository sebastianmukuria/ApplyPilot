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
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds_file = credentials_path()
    if not creds_file.exists():
        raise SystemExit(
            f"Missing {creds_file}.\n"
            "Create an OAuth 'Desktop app' client in Google Cloud Console "
            "(enable the Gmail API), download the JSON, and save it there. "
            "SETUP.md walks through it."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
    creds = flow.run_local_server(port=0)
    _write_token(creds)
    return token_path()
