"""Shared Google OAuth helper for ``google_doc`` + ``gmail`` adapters.

Both adapters speak Google APIs and share a single OAuth consent
screen. Centralizing the credentials dance here means:

- One ``credentials.json`` (the OAuth client), one ``token.json``
  (the user's refresh/access tokens) — no per-adapter duplication.
- One ``marginalia auth google`` CLI command runs the device-or-browser
  flow once; both adapters then reuse the resulting token.
- Token refresh is centralized — adapters call ``load_credentials()``
  and get back live, refreshed credentials without thinking about it.

Files:

- ``credentials.json``: OAuth client secrets the user downloads from
  https://console.cloud.google.com/apis/credentials. Path:
  ``$XDG_CONFIG_HOME/marginalia/google_credentials.json`` (defaulting
  to ``~/.config/marginalia/...``).
- ``token.json``: cached user tokens written after the first successful
  auth. Same directory as the credentials file.

The test path bypasses both files: pass an explicit ``Credentials``
instance to the adapter constructors and skip OAuth entirely.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Scopes both adapters need. Read-only is sufficient for ingest;
# we never write to Drive or Gmail from the engine.
SCOPES = (
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
)
DEFAULT_CONFIG_DIRNAME = "marginalia"
CREDENTIALS_FILENAME = "google_credentials.json"
TOKEN_FILENAME = "google_token.json"


class GoogleAuthError(RuntimeError):
    """Raised when the OAuth flow can't proceed (missing creds, no token, etc.)."""


@dataclass(frozen=True)
class GoogleAuthPaths:
    """Resolved paths for the OAuth client + cached user token."""

    credentials_path: Path
    token_path: Path

    def __post_init__(self) -> None:
        # Ensure the parent dir exists — the OAuth flow will write the
        # token here on success.
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)


def default_config_dir() -> Path:
    """``$XDG_CONFIG_HOME/marginalia`` or ``~/.config/marginalia``."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / DEFAULT_CONFIG_DIRNAME


def default_paths() -> GoogleAuthPaths:
    """Resolve the standard locations for OAuth client + token files."""
    cfg = default_config_dir()
    return GoogleAuthPaths(
        credentials_path=cfg / CREDENTIALS_FILENAME,
        token_path=cfg / TOKEN_FILENAME,
    )


def load_credentials(paths: GoogleAuthPaths | None = None):
    """Return a live ``google.oauth2.credentials.Credentials`` instance.

    Reads ``token.json`` and refreshes if expired. Raises
    ``GoogleAuthError`` if no token exists yet — the caller (CLI)
    should guide the user to run ``marginalia auth google`` first
    rather than triggering an unexpected browser popup mid-ingest.

    Tests pass an explicit ``Credentials`` instance to the adapter
    constructors and never reach this function.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    paths = paths or default_paths()
    if not paths.token_path.is_file():
        raise GoogleAuthError(
            f"no Google token cached at {paths.token_path}; "
            "run `marginalia auth google` to grant access first"
        )

    creds = Credentials.from_authorized_user_file(str(paths.token_path), list(SCOPES))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Persist the refreshed token so we don't re-refresh next call.
        paths.token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def run_oauth_flow(paths: GoogleAuthPaths | None = None):
    """Run the InstalledAppFlow (browser-based) to mint a new token.

    Called by ``marginalia auth google`` (Phase 4 work; the helper
    exists now so the adapters can reference it without duplication).
    Requires ``credentials.json`` to already be in place.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    paths = paths or default_paths()
    if not paths.credentials_path.is_file():
        raise GoogleAuthError(
            f"no OAuth client at {paths.credentials_path}; download from "
            "https://console.cloud.google.com/apis/credentials and place "
            "it there first"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(paths.credentials_path), list(SCOPES))
    # run_local_server pops a browser; falls back to console flow on
    # headless machines via the user pasting the URL/code by hand.
    creds = flow.run_local_server(port=0)
    paths.token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


__all__ = [
    "CREDENTIALS_FILENAME",
    "DEFAULT_CONFIG_DIRNAME",
    "SCOPES",
    "TOKEN_FILENAME",
    "GoogleAuthError",
    "GoogleAuthPaths",
    "default_config_dir",
    "default_paths",
    "load_credentials",
    "run_oauth_flow",
]
