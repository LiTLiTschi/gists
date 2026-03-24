#!/usr/bin/env python3
"""
Populate the mymuma DuckDB with your SoundCloud library.

Fetches liked tracks, own sets, and saved playlists via the SoundCloud API,
then stores all unique tracks in soundcloud_tracks and collection memberships
in sc_collections + sc_collection_tracks.

Uses the ACTUAL marimo/backend/duckdb.py — point --backend at the file and
this script calls the exact same schema ops the notebooks call.

Requires: requests, aiohttp, duckdb  (pip install requests aiohttp duckdb)

Reads defaults from .env in the same directory as this script:
  MYMUMA_BACKEND  — path to marimo/backend/duckdb.py
  MYMUMA_DB       — DuckDB file path
  SC_CLIENT_ID    — SoundCloud client_id
  SC_AUTH_TOKEN   — SoundCloud OAuth token

Usage examples:

  # Interactive (prompts for everything):
  python populate_db.py

  # Non-interactive:
  python populate_db.py \\
    --backend ~/projects/mymuma/marimo/backend/duckdb.py \\
    --db /mnt/ssd/music/mymuma.duckdb \\
    --client-id abc123 --auth-token xyz
"""

import argparse
import asyncio
import importlib.util
import json
import os
import sys


# ── .env loader ───────────────────────────────────────────────────────────────


def _load_env() -> None:
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


# ── Load actual backend ───────────────────────────────────────────────────────


def load_backend(path: str):
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        print(f"ERROR: backend not found: {path!r}")
        print("  Pass --backend /path/to/marimo/backend/duckdb.py")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("duckdb_backend", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── SC API helpers ────────────────────────────────────────────────────────────


def _headers(auth_token: str) -> dict:
    return {
        "Authorization": f"OAuth {auth_token}",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Origin": "https://soundcloud.com",
        "Referer": "https://soundcloud.com/",
    }


def _paginate(path: str, client_id: str, auth_token: str, extra: dict | None = None, max_pages: int = 50) -> list:
    try:
        import requests
    except ImportError:
        print("ERROR: requests is required.  Install with:  pip install requests")
        sys.exit(1)

    items = []
    url = f"https://api-v2.soundcloud.com{path}"
    params = {"client_id": client_id, "limit": 200, **(extra or {})}
    page = 0
    while url and page < max_pages:
        r = requests.get(url, params=params if page == 0 else None,
                         headers=_headers(auth_token), timeout=30)
        if not r.ok:
            print(f"  WARNING: {path} page {page} returned {r.status_code}")
            break
        data = r.json()
        items.extend(data.get("collection", []))
        url = data.get("next_href")
        page += 1
    return items


def _get_full_playlist(playlist_id: int, client_id: str, auth_token: str) -> dict | None:
    try:
        import requests
    except ImportError:
        sys.exit(1)
    r = requests.get(
        f"https://api-v2.soundcloud.com/playlists/{playlist_id}",
        params={"client_id": client_id, "representation": "full"},
        headers=_headers(auth_token),
        timeout=30,
    )
    return r.json() if r.ok else None


async def _fetch_tracks_async(track_ids: list[str], client_id: str, auth_token: str) -> dict[str, dict | None]:
    try:
        import aiohttp as _aiohttp
    except ImportError:
        print("ERROR: aiohttp is required.  Install with:  pip install aiohttp")
        sys.exit(1)

    hdrs = _headers(auth_token)
    sem = asyncio.Semaphore(20)
    results: dict[str, dict | None] = {}

    async def _fetch_one(session, tid: str):
        async with sem:
            try:
                async with session.get(
                    f"https://api-v2.soundcloud.com/tracks/{tid}",
                    params={"client_id": client_id},
                    headers=hdrs,
                ) as resp:
                    resp.raise_for_status()
                    return tid, await resp.json()
            except Exception:
                return tid, None

    async with _aiohttp.ClientSession() as session:
        tasks = [_fetch_one(session, tid) for tid in track_ids]
        total = len(tasks)
        done = 0
        for coro in asyncio.as_completed(tasks):
            tid, data = await coro
            results[tid] = data
            done += 1
            if done % 50 == 0 or done == total:
                print(f"  fetched {done}/{total}...", end="\r", flush=True)
    print()
    return results


# ── Prompts ───────────────────────────────────────────────────────────────────


def prompt(label: str, default: str | None = None, secret: bool = False) -> str:
    hint = f" [default: {default}]" if default else ""
    while True:
        if secret:
            import getpass
            val = getpass.getpass(f"{label}{hint}: ").strip()
        else:
            val = input(f"{label}{hint}: ").strip()
        if val:
            return os.path.expanduser(val)
        if default is not None:
            return os.path.expanduser(default) if not secret else default
        print("  Required — please enter a value.")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Populate mymuma DuckDB with SoundCloud library data."
    )
    parser.add_argument("--backend",    default=None, metavar="PATH",
                        help="Path to marimo/backend/duckdb.py (env: MYMUMA_BACKEND)")
    parser.add_argument("--db",         default=None, metavar="PATH",
                        help="DuckDB file path (env: MYMUMA_DB)")
    parser.add_argument("--client-id",  default=None, help="SoundCloud client_id (env: SC_CLIENT_ID)")
    parser.add_argument("--auth-token", default=None, help="SoundCloud OAuth token (env: SC_AUTH_TOKEN)")
    args = parser.parse_args()

    backend_path = (args.backend
                    or os.environ.get("MYMUMA_BACKEND")
                    or prompt("Path to marimo/backend/duckdb.py",
                              default="~/projects/mymuma/marimo/backend/duckdb.py"))
    db_path      = (args.db
                    or os.environ.get("MYMUMA_DB")
                    or prompt("DuckDB path", default="/mnt/ssd/music/mymuma.duckdb"))
    client_id    = (args.client_id
                    or os.environ.get("SC_CLIENT_ID")
                    or prompt("SoundCloud client_id"))
    auth_token   = (args.auth_token
                    or os.environ.get("SC_AUTH_TOKEN")
                    or prompt("SoundCloud OAuth token", secret=True))

    db = load_backend(backend_path)

    # ── 1. Verify auth + get user info ────────────────────────────────────────
    try:
        import requests
    except ImportError:
        print("ERROR: requests is required.  Install with:  pip install requests")
        sys.exit(1)

    print("\nVerifying credentials...")
    me_r = requests.get(
        "https://api-v2.soundcloud.com/me",
        params={"client_id": client_id},
        headers=_headers(auth_token),
        timeout=10,
    )
    if not me_r.ok:
        print(f"ERROR: /me failed ({me_r.status_code}) — check your auth token")
        sys.exit(1)
    me = me_r.json()
    user_id   = str(me["id"])
    username  = me.get("username", "")
    print(f"  Logged in as: {username} (ID: {user_id})")

    # ── 2. Liked tracks ───────────────────────────────────────────────────────
    print("\nFetching liked tracks...")
    raw_likes = _paginate(f"/users/{user_id}/track_likes", client_id, auth_token)
    liked_tracks = [
        item["track"] for item in raw_likes
        if item.get("track") and item["track"].get("id")
    ]
    print(f"  {len(liked_tracks)} liked tracks")

    # ── 3+4. Own sets + saved playlists ───────────────────────────────────────
    print("\nFetching playlists...")
    raw_pls = _paginate(
        f"/users/{user_id}/playlists/liked_and_owned",
        client_id, auth_token,
        extra={"limit": 50}, max_pages=10,
    )
    own_playlists   = []
    saved_playlists = []
    for i, item in enumerate(raw_pls, 1):
        pl = item.get("playlist")
        if not pl or not pl.get("id"):
            continue
        # fetch full playlist if track list is incomplete
        if pl.get("track_count", 0) > len(pl.get("tracks") or []):
            full = _get_full_playlist(pl["id"], client_id, auth_token)
            pl = full if full else pl
        if item.get("type") == "playlist":
            own_playlists.append(pl)
        else:
            saved_playlists.append(pl)
        print(f"  {i}/{len(raw_pls)} playlists fetched...", end="\r", flush=True)
    print()
    print(f"  {len(own_playlists)} own sets, {len(saved_playlists)} saved playlists")

    # ── 5. Build unique track map ─────────────────────────────────────────────
    all_tracks_map: dict[str, dict] = {}
    for t in liked_tracks:
        if t.get("id"):
            all_tracks_map[str(t["id"])] = t
    for pl in own_playlists + saved_playlists:
        for t in pl.get("tracks") or []:
            if t.get("id") and str(t["id"]) not in all_tracks_map:
                all_tracks_map[str(t["id"])] = t

    print(f"\n{len(all_tracks_map)} unique tracks across all collections")

    # ── 6. Open DB, check what's already there ────────────────────────────────
    print(f"\nOpening DB: {db_path}")
    con = db.connect(db_path)
    db.ensure_schema(con)
    db.ensure_collections_schema(con)

    known_ids    = db.get_known_ids(con)
    to_fetch_ids = [tid for tid in all_tracks_map if tid not in known_ids]
    print(f"  {len(known_ids)} tracks already in DB, {len(to_fetch_ids)} new to fetch")

    # ── 7. Async fetch new tracks ─────────────────────────────────────────────
    fetched: dict[str, dict | None] = {}
    if to_fetch_ids:
        print(f"\nFetching {len(to_fetch_ids)} new tracks...")
        fetched = asyncio.run(_fetch_tracks_async(to_fetch_ids, client_id, auth_token))

    # ── 8. Upsert tracks ──────────────────────────────────────────────────────
    inserted, failed = db.bulk_upsert_tracks(con, fetched)
    if failed:
        print(f"  WARNING: {len(failed)} tracks failed to insert:")
        for f in failed[:10]:
            print(f"    {f}")

    # ── 9. Upsert collections ─────────────────────────────────────────────────
    likes_cid = f"likes:{user_id}"
    db.upsert_collection(con, {
        "id": likes_cid, "type": "likes", "title": "Liked Tracks",
        "track_count": len(liked_tracks), "user_id": int(user_id),
        "user_username": None, "raw": None,
    })
    db.upsert_collection_tracks(con, likes_cid, [
        (str(t["id"]), i) for i, t in enumerate(liked_tracks) if t.get("id")
    ])

    for pl in own_playlists:
        db.upsert_collection(con, {
            "id": str(pl["id"]), "type": "set",
            "title": pl.get("title"),
            "permalink": pl.get("permalink"),
            "permalink_url": pl.get("permalink_url"),
            "track_count": pl.get("track_count"),
            "user_id": (pl.get("user") or {}).get("id"),
            "user_username": (pl.get("user") or {}).get("username"),
            "raw": json.dumps(pl),
        })
        db.upsert_collection_tracks(con, str(pl["id"]), [
            (str(t["id"]), i) for i, t in enumerate(pl.get("tracks") or []) if t.get("id")
        ])

    for pl in saved_playlists:
        db.upsert_collection(con, {
            "id": str(pl["id"]), "type": "playlist",
            "title": pl.get("title"),
            "permalink": pl.get("permalink"),
            "permalink_url": pl.get("permalink_url"),
            "track_count": pl.get("track_count"),
            "user_id": (pl.get("user") or {}).get("id"),
            "user_username": (pl.get("user") or {}).get("username"),
            "raw": json.dumps(pl),
        })
        db.upsert_collection_tracks(con, str(pl["id"]), [
            (str(t["id"]), i) for i, t in enumerate(pl.get("tracks") or []) if t.get("id")
        ])

    con.close()

    print(f"\nDone!")
    print(f"  {inserted} new tracks stored" + (f" | {len(failed)} failed" if failed else ""))
    print(f"  {len(liked_tracks)} likes indexed")
    print(f"  {len(own_playlists)} own sets indexed")
    print(f"  {len(saved_playlists)} saved playlists indexed")
    print(f"  {len(all_tracks_map)} total unique tracks in collections")


if __name__ == "__main__":
    main()
