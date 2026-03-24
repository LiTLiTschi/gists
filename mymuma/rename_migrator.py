#!/usr/bin/env python3
"""
Rename audio files by appending the SoundCloud track ID to their stem.

  Before: Some Track Title.mp3
  After:  Some Track Title.1234567890.mp3

Matching strategy (in order):
  1. URL tag  — reads WOAF / TXXX:WWWAUDIOFILE (MP3/WAV/FLAC/OGG) or
                ----:com.apple.iTunes:WWWAUDIOFILE (M4A); looks up permalink_url
                in the DuckDB soundcloud_tracks table → confidence 1.0
  2. Fuzzy title+artist — TIT2/TPE1 (ID3) or ©nam/©ART (M4A) or Vorbis
                comments; case-fold + NFKC normalise; artist disambiguation
                when multiple title matches exist

Files with confidence below --threshold are reported as problems but not renamed
(unless --rename-problems is passed).

Uses the ACTUAL marimo/backend/duckdb.py — point --backend at the file and
this script calls the exact same functions the notebooks call.

Requires: mutagen  (pip install mutagen)

Usage examples:

  python rename_migrator.py \\
    --backend ~/projects/mymuma/marimo/backend/duckdb.py \\
    --db      /mnt/ssd/music/mymuma.duckdb \\
    --mp3     /mnt/ssd/music/scdl/mp3 \\
    --hq-mp3  /mnt/ssd/music/scdl/hq-mp3 \\
    --hq      /mnt/ssd/music/scdl/hq \\
    --dry-run

  # Interactive (prompts for everything):
  python rename_migrator.py
"""

import argparse
import importlib.util
import os
import re
import sys
import unicodedata


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


# ── Tag reading (mutagen) ─────────────────────────────────────────────────────


def _import_mutagen():
    try:
        import mutagen
        from mutagen.id3 import ID3, ID3NoHeaderError
        from mutagen.mp4 import MP4
        return mutagen, ID3, ID3NoHeaderError, MP4
    except ImportError:
        print("ERROR: mutagen is required.  Install it with:  pip install mutagen")
        sys.exit(1)


def extract_url_from_tags(filepath: str, mutagen, ID3, ID3NoHeaderError, MP4) -> str | None:
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".m4a":
        try:
            audio = MP4(filepath)
            if not audio.tags:
                return None
            val = audio.tags.get("----:com.apple.iTunes:WWWAUDIOFILE")
            if val:
                return bytes(val[0]).decode("utf-8").strip().rstrip("/").split("?")[0]
        except Exception:
            pass
        return None

    try:
        tags = ID3(filepath)
        woaf = tags.get("WOAF")
        if woaf:
            return str(woaf).strip().rstrip("/").split("?")[0]
        txxx = tags.get("TXXX:WWWAUDIOFILE")
        if txxx:
            return str(txxx).strip().rstrip("/").split("?")[0]
    except ID3NoHeaderError:
        pass
    except Exception:
        pass

    try:
        audio = mutagen.File(filepath)
        if audio and audio.tags:
            for key in ("WWWAUDIOFILE", "wwwaudiofile", "WOAF", "woaf"):
                val = audio.tags.get(key)
                if val:
                    raw = val[0] if isinstance(val, list) else str(val)
                    return str(raw).strip().rstrip("/").split("?")[0]
    except Exception:
        pass

    return None


def extract_title_artist(filepath: str, mutagen, ID3, ID3NoHeaderError, MP4) -> tuple[str, str]:
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".m4a":
        try:
            audio = MP4(filepath)
            if audio.tags:
                t = audio.tags.get("\xa9nam", [])
                a = audio.tags.get("\xa9ART", [])
                return (str(t[0]) if t else "", str(a[0]) if a else "")
        except Exception:
            pass
        return "", ""

    try:
        tags = ID3(filepath)
        tit2 = tags.get("TIT2")
        tpe1 = tags.get("TPE1")
        return (str(tit2) if tit2 else "", str(tpe1) if tpe1 else "")
    except Exception:
        pass

    try:
        audio = mutagen.File(filepath)
        if audio and audio.tags:
            t = audio.tags.get("title", [""])[0] if hasattr(audio.tags, "get") else ""
            a = audio.tags.get("artist", [""])[0] if hasattr(audio.tags, "get") else ""
            return str(t), str(a)
    except Exception:
        pass

    return "", ""


# ── Fuzzy lookup ──────────────────────────────────────────────────────────────


_RENAMED_RE = re.compile(r"\.\d{7,12}\.[^.]+$")
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aiff", ".aif"}


def already_renamed(filename: str) -> bool:
    return bool(_RENAMED_RE.search(filename))


def norm_artist(s: str) -> str:
    s = s.casefold().split(",")[0].strip()
    return re.sub(r"[^\w\s]", "", s).strip()


def lookup_by_title_artist(title: str, artist: str, title_index: dict) -> list[tuple[str, float]]:
    if not title:
        return []
    artist_norm = norm_artist(artist) if artist else ""

    def _score(rows, base):
        if not rows:
            return []
        if len(rows) == 1:
            db_artist = norm_artist(rows[0][2])
            conf = base if (artist_norm and db_artist == artist_norm) else base - 0.1
            return [(rows[0][0], conf)]
        if artist_norm:
            matched = [r for r in rows if norm_artist(r[2]) == artist_norm]
            if len(matched) == 1:
                return [(matched[0][0], base - 0.4)]
        return [(r[0], base - 0.5) for r in rows]

    rows = title_index.get(title.casefold(), [])
    if rows:
        return _score(rows, 1.0)

    nfkc = unicodedata.normalize("NFKC", title)
    if nfkc != title:
        rows = title_index.get(nfkc.casefold(), [])
        if rows:
            return _score(rows, 0.9)

    if " - " in title:
        short = title.split(" - ", 1)[1]
        rows = title_index.get(short.casefold(), [])
        if rows:
            return _score(rows, 0.75)
        short_nfkc = unicodedata.normalize("NFKC", short)
        if short_nfkc != short:
            rows = title_index.get(short_nfkc.casefold(), [])
            if rows:
                return _score(rows, 0.65)

    return []


# ── Prompts ───────────────────────────────────────────────────────────────────


def prompt(label: str, default: str | None = None) -> str:
    hint = f" [default: {default}]" if default else ""
    while True:
        val = input(f"{label}{hint}: ").strip()
        if val:
            return os.path.expanduser(val)
        if default is not None:
            return os.path.expanduser(default)
        print("  Required — please enter a value.")


def prompt_optional(label: str) -> str | None:
    val = input(f"{label} [leave blank to skip]: ").strip()
    return os.path.expanduser(val) if val else None


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rename audio files by appending their SoundCloud track ID."
    )
    parser.add_argument("--backend",  default=None, metavar="PATH",
                        help="Path to marimo/backend/duckdb.py (prompted if omitted)")
    parser.add_argument("--db",       default=None, metavar="PATH",
                        help="DuckDB database path (prompted if omitted)")
    parser.add_argument("--mp3",      default=None, metavar="DIR",  help="MP3 root dir")
    parser.add_argument("--hq-mp3",   default=None, metavar="DIR",  help="HQ-MP3 root dir")
    parser.add_argument("--hq",       default=None, metavar="DIR",  help="HQ root dir")
    parser.add_argument("--threshold", type=float, default=0.6,
                        help="Min confidence to auto-rename (default: 0.6)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Preview renames without applying them")
    parser.add_argument("--rename-problems", action="store_true",
                        help="Also rename files below threshold (use with care)")
    parser.add_argument("--verbose",  action="store_true",
                        help="Print already-renamed and no-match files too")
    args = parser.parse_args()

    backend_path = (args.backend
                    or os.environ.get("MYMUMA_BACKEND")
                    or prompt("Path to marimo/backend/duckdb.py",
                              default="~/projects/mymuma/marimo/backend/duckdb.py"))
    db_path      = (args.db
                    or os.environ.get("MYMUMA_DB")
                    or prompt("DuckDB path", default="/mnt/ssd/music/mymuma.duckdb"))

    mp3_root    = args.mp3    or os.environ.get("MYMUMA_MP3_DIR")    or prompt_optional("MP3 root dir    (blank to skip)")
    hq_mp3_root = args.hq_mp3 or os.environ.get("MYMUMA_HQ_MP3_DIR") or prompt_optional("HQ-MP3 root dir (blank to skip)")
    hq_root     = args.hq     or os.environ.get("MYMUMA_HQ_DIR")     or prompt_optional("HQ root dir     (blank to skip)")

    roots = [(r, n) for r, n in [
        (mp3_root,    "mp3"),
        (hq_mp3_root, "hq-mp3"),
        (hq_root,     "hq"),
    ] if r and os.path.isdir(r)]

    if not roots:
        print("ERROR: No valid audio directories specified.")
        sys.exit(1)

    db = load_backend(backend_path)
    mutagen, ID3, ID3NoHeaderError, MP4 = _import_mutagen()

    print(f"\nConnecting to DB: {db_path}")
    con = db.connect(db_path, read_only=True)
    url_index   = db.build_url_index(con)
    title_index = db.build_title_index(con)
    con.close()
    print(f"  {len(url_index)} URLs indexed, {len(title_index)} titles indexed")

    # ── Collect files ─────────────────────────────────────────────────────────
    all_files: list[tuple[str, str]] = []
    for root, label in roots:
        for dirpath, subdirs, files in os.walk(root):
            subdirs.sort()
            for fname in sorted(files):
                if os.path.splitext(fname)[1].lower() in AUDIO_EXTS:
                    all_files.append((os.path.join(dirpath, fname), label))

    print(f"  {len(all_files)} audio files found across {len(roots)} dir(s)\n")

    # ── Resolve ───────────────────────────────────────────────────────────────
    results = []
    counts = dict(already_done=0, woaf=0, txxx=0, m4a=0,
                  fuzzy=0, url_not_in_db=0, no_match=0)

    for fpath, root_label in all_files:
        fname = os.path.basename(fpath)

        if already_renamed(fname):
            counts["already_done"] += 1
            if args.verbose:
                print(f"  ✓ already done: {fname}")
            continue

        track_id = None
        method   = "no_match"
        conf     = 0.0

        url = extract_url_from_tags(fpath, mutagen, ID3, ID3NoHeaderError, MP4)
        if url:
            track_id = url_index.get(url)
            if track_id:
                ext = os.path.splitext(fpath)[1].lower()
                if ext == ".m4a":
                    method = "m4a"; counts["m4a"] += 1
                else:
                    try:
                        from mutagen.id3 import ID3 as _ID3
                        _tags = _ID3(fpath)
                        if _tags.get("WOAF"):
                            method = "woaf"; counts["woaf"] += 1
                        else:
                            method = "txxx"; counts["txxx"] += 1
                    except Exception:
                        method = "woaf"; counts["woaf"] += 1
                conf = 1.0
            else:
                method = "url_not_in_db"; counts["url_not_in_db"] += 1

        if not track_id and method != "url_not_in_db":
            title, artist = extract_title_artist(fpath, mutagen, ID3, ID3NoHeaderError, MP4)
            candidates = lookup_by_title_artist(title, artist, title_index)
            if candidates:
                best_id, best_conf = max(candidates, key=lambda x: x[1])
                track_id = best_id
                method   = "fuzzy"
                conf     = best_conf
                counts["fuzzy"] += 1
            else:
                counts["no_match"] += 1

        stem, ext = os.path.splitext(fpath)
        new_path = f"{stem}.{track_id}{ext}" if track_id else None

        results.append(dict(
            path=fpath, fname=fname, track_id=track_id,
            new_path=new_path, method=method, conf=conf,
        ))

    # ── Summary ───────────────────────────────────────────────────────────────
    thresh      = args.threshold
    will_rename = [r for r in results if r["track_id"] and r["conf"] >= thresh]
    problems    = [r for r in results if not r["track_id"] or r["conf"] < thresh]

    print(f"{'DRY RUN — ' if args.dry_run else ''}Results:")
    print(f"  ✅ Already renamed : {counts['already_done']}")
    print(f"  🔗 WOAF matched    : {counts['woaf']}")
    print(f"  🔗 TXXX matched    : {counts['txxx']}")
    print(f"  🍎 M4A matched     : {counts['m4a']}")
    print(f"  🔤 Fuzzy matched   : {counts['fuzzy']}")
    print(f"  ⚠  URL not in DB   : {counts['url_not_in_db']}")
    print(f"  ❌ No match        : {counts['no_match']}")
    print(f"  → Ready to rename (conf ≥ {thresh}): {len(will_rename)}")
    print(f"  → Problems                         : {len(problems)}")

    if problems:
        print("\nProblems (low confidence or unresolved):")
        for r in problems:
            proposed = os.path.basename(r["new_path"]) if r["new_path"] else "—"
            print(f"  [{r['method']} {r['conf']:.2f}] {r['fname']}  →  {proposed}")

    # ── Rename ────────────────────────────────────────────────────────────────
    to_rename = will_rename[:]
    if args.rename_problems:
        to_rename += [r for r in problems if r["track_id"]]

    if not to_rename:
        print("\nNothing to rename.")
        return

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Renaming {len(to_rename)} file(s):")
    renamed = errors = 0
    for r in to_rename:
        old = r["path"]
        new = r["new_path"]
        if not new or old == new:
            continue
        if args.dry_run:
            print(f"  [dry] {r['fname']}  →  {os.path.basename(new)}")
            renamed += 1
            continue
        if os.path.exists(new):
            print(f"  ❌ target exists, skipped: {os.path.basename(new)}")
            errors += 1
            continue
        try:
            os.rename(old, new)
            print(f"  ✅ {r['fname']}  →  {os.path.basename(new)}")
            renamed += 1
        except Exception as e:
            print(f"  ❌ {r['fname']}: {e}")
            errors += 1

    print(f"\nDone: {renamed} renamed, {errors} errors.")
    if args.dry_run:
        print("Run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
