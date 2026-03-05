"""MX Bikes Export Auto Uploader (run on YOUR PC)

This watches your exports folder and uploads the newest .html file to your league website.

Usage (Windows example):
  python scripts/local_uploader.py --export-dir "C:\Users\YOU\Documents\PiBoSo\MX Bikes\exports" --server https://YOUR_WEB_DOMAIN --key YOUR_RESULTS_UPLOAD_KEY

Tip: Make a desktop shortcut / scheduled task.
"""

import argparse
import os
import time
from pathlib import Path
import requests

def newest_html(export_dir: Path):
    files = list(export_dir.glob("*.html")) + list(export_dir.glob("*.htm"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export-dir", required=True, help="Your MX Bikes exports folder")
    ap.add_argument("--server", required=True, help="Website base url (https://...)")
    ap.add_argument("--key", required=True, help="RESULTS_UPLOAD_KEY")
    ap.add_argument("--event-id", default="", help="Optional event id to attach results to")
    ap.add_argument("--poll", type=int, default=5, help="Seconds between checks")
    args = ap.parse_args()

    export_dir = Path(args.export_dir)
    if not export_dir.exists():
        raise SystemExit(f"Export dir not found: {export_dir}")

    server = args.server.rstrip("/")
    last = None
    print("Watching:", export_dir)
    while True:
        f = newest_html(export_dir)
        if f and (last is None or f != last):
            try:
                print("Uploading:", f.name)
                with f.open("rb") as fp:
                    files = {"file": (f.name, fp, "text/html")}
                    data = {"key": args.key}
                    if args.event_id:
                        data["event_id"] = args.event_id
                    r = requests.post(server + "/api/upload_results", data=data, files=files, timeout=30, headers={"X-RESULTS-KEY": args.key})
                print("Response:", r.status_code, r.text[:200])
                if r.ok:
                    last = f
            except Exception as e:
                print("Upload failed:", e)
        time.sleep(args.poll)

if __name__ == "__main__":
    main()
