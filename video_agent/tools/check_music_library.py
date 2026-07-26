"""Audit asset_library/music/ for usable tracks."""
import argparse
from pathlib import Path

MIN_TRACKS = 3


def audit(music_dir: Path) -> dict:
    music_dir = Path(music_dir)
    tracks = sorted(music_dir.glob("*.mp3")) if music_dir.exists() else []
    if not tracks:
        return {"track_count": 0, "ok": False,
                "message": f"No tracks in {music_dir} — composer will run music-free."}
    if len(tracks) < MIN_TRACKS:
        return {"track_count": len(tracks), "ok": True,
                "message": f"Only {len(tracks)} tracks — fewer than recommended ({MIN_TRACKS})."}
    return {"track_count": len(tracks), "ok": True,
            "message": f"{len(tracks)} tracks available."}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default="asset_library/music")
    args = p.parse_args()
    r = audit(Path(args.dir))
    print(f"[{'OK' if r['ok'] else 'WARN'}] {r['message']}")


if __name__ == "__main__":
    main()
