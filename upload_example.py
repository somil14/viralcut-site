"""ViralCut's YouTube upload, as a complete standalone script.

This is the same flow ViralCut runs when its owner posts a clip:
  1. Google's OAuth consent page (scopes youtube.upload + youtube.readonly), opened in the owner's browser.
  2. A resumable upload to videos.insert with the clip's title, description, tags, category and language.
  3. A videos.list call that reads the uploaded video's status and view count.

Run:
  pip install google-auth-oauthlib httpx
  python upload_example.py --client client_secret.json --file clip.mp4 --title "Title" --description "Text" --lang en

client_secret.json is the OAuth client of Google Cloud project viralcut-515210 (type "Web application",
redirect http://localhost:8765/oauth/youtube/callback), downloaded from Google Cloud Console.
The refresh token is kept in token.json next to this script, readable only by its owner.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
TOKEN = Path(__file__).with_name("token.json")


def sign_in(client_file: str) -> dict:
    """Google's own consent page; ViralCut never sees a password. Returns {"access_token", "refresh_token", ...}."""
    if TOKEN.exists():
        saved = json.loads(TOKEN.read_text())
        cfg = json.loads(Path(client_file).read_text())
        cfg = cfg.get("web") or cfg.get("installed")
        r = httpx.post("https://oauth2.googleapis.com/token", data={
            "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
            "refresh_token": saved["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
        if r.status_code == 200:
            return {**saved, **r.json()}
    flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
    creds = flow.run_local_server(port=8765, prompt="consent", access_type="offline",
                                  redirect_uri_trailing_slash=False,
                                  success_message="Signed in. You can close this tab.")
    saved = {"access_token": creds.token, "refresh_token": creds.refresh_token}
    TOKEN.write_text(json.dumps(saved))
    os.chmod(TOKEN, 0o600)
    return saved


def check(r: httpx.Response, what: str) -> httpx.Response:
    if r.status_code >= 400:
        sys.exit(f"{what} failed ({r.status_code}): {r.text[:500]}")
    return r


def upload(token: str, path: Path, title: str, description: str, tags: list, lang: str, privacy: str) -> str:
    """Resumable upload to videos.insert, exactly as ViralCut sends it. Returns the new video's ID."""
    snippet = {"title": re.sub(r"[<>]", "", title)[:100] or "Clip",
               "description": re.sub(r"[<>]", "", description)[:4900] + "\n\n#Shorts",
               "tags": tags[:15], "categoryId": "20"}
    if re.fullmatch(r"[a-z]{2}", lang or ""):
        snippet.update(defaultLanguage=lang, defaultAudioLanguage=lang)
    meta = {"snippet": snippet, "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    size = path.stat().st_size
    start = check(httpx.post("https://www.googleapis.com/upload/youtube/v3/videos",
                             params={"uploadType": "resumable", "part": "snippet,status"}, json=meta,
                             headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Type": "video/mp4",
                                      "X-Upload-Content-Length": str(size)}, timeout=60), "Starting the upload")
    with path.open("rb") as f:
        done = check(httpx.put(start.headers["Location"], content=f.read(),
                               headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"},
                               timeout=900), "Uploading").json()
    return done["id"]


def status(token: str, video_id: str) -> dict:
    """videos.list: the video's privacy status and view count (ViralCut shows the owner how each clip does)."""
    r = check(httpx.get("https://www.googleapis.com/youtube/v3/videos",
                        params={"part": "status,statistics", "id": video_id},
                        headers={"Authorization": f"Bearer {token}"}, timeout=30), "Reading the video").json()
    item = (r.get("items") or [{}])[0]
    return {"privacy": item.get("status", {}).get("privacyStatus"),
            "views": int(item.get("statistics", {}).get("viewCount", 0))}


def main():
    ap = argparse.ArgumentParser(description="Upload one ViralCut clip as a YouTube Short.")
    ap.add_argument("--client", required=True, help="OAuth client JSON from Google Cloud Console")
    ap.add_argument("--file", required=True, help="the vertical .mp4 clip")
    ap.add_argument("--title", required=True)
    ap.add_argument("--description", default="")
    ap.add_argument("--tags", default="", help="comma separated")
    ap.add_argument("--lang", default="", help="two letters, e.g. en or hi")
    ap.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    a = ap.parse_args()
    creds = sign_in(a.client)
    vid = upload(creds["access_token"], Path(a.file), a.title, a.description,
                 [t.strip() for t in a.tags.split(",") if t.strip()], a.lang, a.privacy)
    print(f"Uploaded: https://www.youtube.com/shorts/{vid}")
    print("Status:", status(creds["access_token"], vid))


if __name__ == "__main__":
    main()
