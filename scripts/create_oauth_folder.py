"""
create_oauth_folder.py — create a NEW app-only Drive folder using the EXISTING
GOAUTH creds (no browser, no client JSON).

Reuses GOAUTH_CLIENT_ID / GOAUTH_CLIENT_SECRET / GOAUTH_REFRESH_TOKEN (already set
as GitHub secrets) to create one folder and print its ID — for provisioning an
additional mirror folder (e.g. the GFL air exhibit, ADR 026) when the original
OAuth client JSON isn't on hand. Unlike scripts/oauth_setup.py, this does NOT run
the browser consent flow; it only mints a Drive service from the stored refresh
token and creates a folder.

Run it via the `create-oauth-folder` workflow (where the GOAUTH_* secrets are
available), or locally with those three env vars set. Read-only w.r.t. all data:
it creates one empty folder and prints its id, nothing else.

    FOLDER_NAME="Arbor Hills GFL Air Exhibit" python scripts/create_oauth_folder.py

Optionally set PARENT_FOLDER_ID to create the new folder directly INSIDE an
existing folder (e.g. a parent Trisha already created and gave the ID for)
instead of at Drive root — this works even though that parent wasn't created
by this OAuth app: drive.file scope permits creating a new file/folder as a
child of an existing folder ID, the same mechanism archive_client.upload_file
already relies on to upload into Trisha's hand-created MMPC folder. Omitting
it keeps the original root-level behavior (manual move-under-parent after).

    FOLDER_NAME="DPW" PARENT_FOLDER_ID="1hqh...J897" python scripts/create_oauth_folder.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import archive_client as ac


def main() -> int:
    name = os.environ.get("FOLDER_NAME") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not name:
        print("Usage: FOLDER_NAME='...' python scripts/create_oauth_folder.py "
              "(or pass the name as the first argument)")
        return 2
    parent_id = os.environ.get("PARENT_FOLDER_ID") or (sys.argv[2] if len(sys.argv) > 2 else "")
    missing = [k for k in ("GOAUTH_CLIENT_ID", "GOAUTH_CLIENT_SECRET",
                           "GOAUTH_REFRESH_TOKEN") if not os.environ.get(k)]
    if missing:
        print(f"Missing GOAUTH creds ({', '.join(missing)}) — cannot create the "
              "folder. In GitHub Actions these come from the repo secrets.")
        return 2

    drive = ac.oauth_drive_service()
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [parent_id]
    folder = drive.files().create(body=body, fields="id").execute()
    fid = folder["id"]

    print("=" * 68)
    print(f"CREATED app-only Drive folder: {name}")
    print(f"FOLDER_ID: {fid}")
    if parent_id:
        print(f"Created directly under parent: {parent_id}")
    print("")
    print("Next: store this as the folder-ID secret, e.g.")
    print(f"  gh secret set GOAUTH_GFL_AIR_FOLDER_ID    ->  {fid}")
    print("")
    if not parent_id:
        print("Then in Drive, move the new folder under your public-records parent and")
        print("share 'Anyone with the link -> Viewer' (already-public data). The app")
        print("tracks the folder by this stable ID, so moving/renaming never breaks it.")
    else:
        print("Already placed under the given parent — just share 'Anyone with the")
        print("link -> Viewer' if it should be publicly visible like the rest.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
