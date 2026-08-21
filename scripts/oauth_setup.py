"""
oauth_setup.py — one-time local setup for the durable PDF archive (ADR 007).

Runs the OAuth consent flow as Trisha, creates the Drive mirror folder, and
prints the four values to store as GitHub secrets. Run it ONCE, locally, on a
machine with a browser.

Prereqs (in the GCP console, project arbor-hills-monitor):
  1. APIs & Services -> Library -> enable the Google Drive API.
  2. OAuth consent screen -> add the scope `.../auth/drive.file`, add yourself as
     a user, and PUBLISH to "In production" (this removes the 7-day refresh-token
     expiry; for a single user you just click through an "unverified app" notice
     once during consent).
  3. Credentials -> Create credentials -> OAuth client ID -> type "Desktop app".
     Download its JSON.

Then:
    pip install -r requirements.txt        # provides google-auth-oauthlib
    python scripts/oauth_setup.py ~/Downloads/client_secret_XXXX.json

A browser opens; sign in as Trisha and approve. The script prints the refresh
token + folder ID and the exact `gh secret set` commands. Finally, share the new
mirror folder in Drive as "Anyone with the link -> Viewer" so anyone with the
link can open Archive Links (these are already-public EGLE filings).

To add ANOTHER app-only mirror folder later (e.g. the GFL air exhibit, ADR 026),
reuse this flow with an explicit name + secret:
    python scripts/oauth_setup.py <client.json> --folder-name "Arbor Hills GFL Air Exhibit" --secret-name GOAUTH_GFL_AIR_FOLDER_ID
Only the printed folder-ID secret is new; CLIENT_ID/SECRET/REFRESH_TOKEN are
already set from the first run. The exhibit is already-public GFL fenceline data,
so share the folder the same way (Anyone with the link -> Viewer). The folder is
created at your Drive root (the drive.file scope can't place it inside a hand-made
parent) — move it under your public-records parent afterward; the app tracks it
by its stable ID.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import archive_client as ac
from config_loader import load_config


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description=(
            "One-time OAuth setup: consent as Trisha, create an app-only Drive "
            "folder, and print the GitHub secrets to store. Reuse it to add a NEW "
            "mirror folder for another stream (e.g. the GFL air exhibit) via "
            "--folder-name + --secret-name — only the folder-ID secret is new; the "
            "CLIENT_ID/SECRET/REFRESH_TOKEN it prints are already set from the "
            "first run and can be left as-is."))
    ap.add_argument("client_json", help="path to the OAuth client JSON (Desktop app)")
    ap.add_argument(
        "--folder-name", default=None,
        help="name of the folder to create (default: config archive.folder_name)")
    ap.add_argument(
        "--secret-name", default="GOAUTH_ARCHIVE_FOLDER_ID",
        help="the folder-ID GitHub secret to print in the `gh secret set` line "
             "(e.g. GOAUTH_GFL_AIR_FOLDER_ID for the GFL air exhibit)")
    args = ap.parse_args()

    client_json = os.path.expanduser(args.client_json)
    if not os.path.exists(client_json):
        print(f"OAuth client JSON not found: {client_json}")
        return 2

    from google_auth_oauthlib.flow import InstalledAppFlow

    cfg = load_config()
    folder_name = args.folder_name or (cfg.get("archive") or {}).get(
        "folder_name", "Arbor Hills Case File Mirror")

    flow = InstalledAppFlow.from_client_secrets_file(client_json, ac.OAUTH_SCOPES)
    # run_local_server (not the deprecated console/OOB flow): spins a localhost
    # redirect, opens the browser, captures the code automatically.
    creds = flow.run_local_server(port=0)

    if not creds.refresh_token:
        print("\nNo refresh token returned. Revoke prior access at "
              "https://myaccount.google.com/permissions and re-run (Google only "
              "issues a refresh token on first consent).")
        return 1

    # Create the mirror folder now so the archiver references a fixed ID, not a
    # name search (drive.file listing semantics are narrower than full drive).
    from googleapiclient.discovery import build
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    folder = (
        drive.files()
        .create(
            body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"},
            fields="id",
        )
        .execute()
    )
    fid = folder["id"]

    client_id = flow.client_config.get("client_id", "")
    client_secret = flow.client_config.get("client_secret", "")

    print("\n" + "=" * 72)
    print("OAuth setup complete. Mirror folder created:")
    print(f"  {folder_name}  (id: {fid})")
    print("=" * 72)
    print("\nSet these four GitHub secrets (the prompt form keeps them out of "
          "shell history):\n")
    print("  gh secret set GOAUTH_CLIENT_ID")
    print(f"      -> {client_id}")
    print("  gh secret set GOAUTH_CLIENT_SECRET")
    print(f"      -> {client_secret}")
    print("  gh secret set GOAUTH_REFRESH_TOKEN")
    print(f"      -> {creds.refresh_token}")
    print(f"  gh secret set {args.secret_name}")
    print(f"      -> {fid}")
    print("\nThese values are SENSITIVE. After copying them into the secrets, "
          "clear your terminal scrollback.")
    print("\nLAST STEP: in Google Drive, right-click the new "
          f"'{folder_name}' folder -> Share -> General access -> "
          "'Anyone with the link' -> Viewer, so readers can open "
          "Archive Links. (Folder sharing cascades to the PDFs inside it.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
