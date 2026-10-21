"""Read-only YouTube Analytics for published shorts: views, watch time and retention.

Uses its OWN token (youtube_analytics_token.json, read-only scopes) next to the publishing
token, so authorising analytics can never disturb the scheduled upload job.

  python -m scheduler.youtube_analytics auth                  one-time browser sign-in
  python -m scheduler.youtube_analytics fetch [--out FILE]    per-video stats + retention curves

Output (default data/youtube_analytics.json): one record per channel video with title,
published date, duration, views, averageViewDuration, averageViewPercentage, and the
audience-retention curve (elapsedVideoTimeRatio -> audienceWatchRatio).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from scheduler.publish import REPO_ROOT, youtube_dir

SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]
TOKEN_NAME = "youtube_analytics_token.json"
DEFAULT_OUT = REPO_ROOT / "data" / "youtube_analytics.json"


def paths() -> tuple[Path, Path]:
    base = youtube_dir()
    return base / "client_secrets.json", base / TOKEN_NAME


def auth() -> int:
    from google_auth_oauthlib.flow import InstalledAppFlow

    secrets, token = paths()
    if not secrets.exists():
        print(f"{secrets} not found.", file=sys.stderr)
        return 1
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    creds = flow.run_local_server(port=0)
    token.write_text(creds.to_json(), encoding="utf-8")
    print(f"Saved {token}")
    return 0


def credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    _, token = paths()
    if not token.exists():
        raise SystemExit(f"{token} not found -- run: python -m scheduler.youtube_analytics auth")
    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def iso_duration_seconds(iso: str) -> int:
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def channel_videos(yt) -> list[dict]:
    """Every upload on the authorised channel: id, title, published, duration_s."""
    ch = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, page = [], None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50,
                                    pageToken=page).execute()
        ids += [i["contentDetails"]["videoId"] for i in r["items"]]
        page = r.get("nextPageToken")
        if not page:
            break
    out = []
    for k in range(0, len(ids), 50):
        r = yt.videos().list(part="snippet,contentDetails", id=",".join(ids[k:k + 50])).execute()
        for v in r["items"]:
            out.append({
                "video_id": v["id"],
                "title": v["snippet"]["title"],
                "published": v["snippet"]["publishedAt"][:10],
                "duration_s": iso_duration_seconds(v["contentDetails"]["duration"]),
            })
    return out


def video_stats(ya, video_id: str, start: str, end: str) -> dict:
    r = ya.reports().query(
        ids="channel==MINE", startDate=start, endDate=end,
        metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage",
        filters=f"video=={video_id}").execute()
    rows = r.get("rows") or [[0, 0, 0, 0]]
    names = [h["name"] for h in r["columnHeaders"]]
    return dict(zip(names, rows[0]))


def retention_curve(ya, video_id: str, start: str, end: str) -> list[list[float]]:
    r = ya.reports().query(
        ids="channel==MINE", startDate=start, endDate=end,
        dimensions="elapsedVideoTimeRatio", metrics="audienceWatchRatio",
        filters=f"video=={video_id}").execute()
    return r.get("rows") or []


def fetch(out: Path) -> int:
    from googleapiclient.discovery import build

    creds = credentials()
    yt = build("youtube", "v3", credentials=creds)
    ya = build("youtubeAnalytics", "v2", credentials=creds)
    end = dt.date.today().isoformat()
    records = []
    for v in channel_videos(yt):
        rec = dict(v)
        try:
            rec.update(video_stats(ya, v["video_id"], v["published"], end))
            rec["retention"] = retention_curve(ya, v["video_id"], v["published"], end)
        except Exception as exc:  # noqa: BLE001 -- keep the other videos
            rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
        records.append(rec)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"fetched": end, "videos": records}, indent=1), encoding="utf-8")
    for r in records:
        print(f"{r['published']}  {r['duration_s']:>4}s  views={r.get('views', '?'):>5}  "
              f"avg%={r.get('averageViewPercentage', '?')}  {r['title'][:60]}"
              + (f"  ERROR {r['error'][:80]}" if "error" in r else ""))
    print(f"Saved {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth")
    f = sub.add_parser("fetch")
    f.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    return auth() if a.cmd == "auth" else fetch(a.out)


if __name__ == "__main__":
    sys.exit(main())
