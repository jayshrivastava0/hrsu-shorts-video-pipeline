"""One-time interactive YouTube OAuth. Run: python -m scheduler.youtube_auth

Opens a browser, saves youtube_token.json next to client_secrets.json (HRSU_YOUTUBE_DIR or repo root).
The scheduled job never opens a browser; it fails fast if this has not been done.
"""
from __future__ import annotations

import sys

from scheduler.publish import auth_paths


def main() -> int:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from video_agent.config import YOUTUBE_UPLOAD_SCOPES

    secrets, token = auth_paths()
    if not secrets.exists():
        print(f"{secrets} not found. Download the OAuth client (Desktop app) JSON from Google Cloud "
              "Console and save it there.", file=sys.stderr)
        return 1
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), YOUTUBE_UPLOAD_SCOPES)
    creds = flow.run_local_server(port=0)
    token.write_text(creds.to_json(), encoding="utf-8")
    print(f"Saved {token}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
