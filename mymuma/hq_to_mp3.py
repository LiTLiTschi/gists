#!/usr/bin/env python3
"""
Convert HQ audio files (m4a, flac, aac, ogg, wav) to 320k MP3 using ffmpeg.

Walks a source directory recursively, converts each non-MP3 audio file to
a 320k MP3. The output file is written atomically (via a .mp3.part temp file
that is renamed on success). On failure, the temp file is cleaned up.

Two modes:
  inplace  — MP3 is written alongside the original in the same directory.
  separate — MP3 is written to a parallel destination directory tree.

Usage examples:

  # Interactive (prompts for all inputs):
  python hq_to_mp3.py

  # Source dir only (inplace mode):
  python hq_to_mp3.py /mnt/hq_likes

  # Source + dest (separate mode):
  python hq_to_mp3.py /mnt/hq_likes /mnt/mp3_likes

  # Non-interactive full run:
  python hq_to_mp3.py /mnt/hq_likes /mnt/mp3_likes --delete --ffmpeg /usr/bin/ffmpeg
"""

import argparse
import os
import subprocess
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

HQ_EXTENSIONS = {".m4a", ".flac", ".aac", ".ogg", ".wav"}


# ── Helpers ───────────────────────────────────────────────────────────────────


def prompt_directory(label: str, default: str | None = None) -> str:
    hint = f" [default: {default}]" if default else ""
    while True:
        path = input(f"{label}{hint}: ").strip()
        if path == "" and default is not None:
            return default
        if path == "":
            print("  A path is required — please try again.")
            continue
        expanded = os.path.expanduser(path)
        if os.path.isdir(expanded):
            return expanded
        print(f"  Path not found: {expanded!r} — please try again.")


def prompt_optional_directory(label: str) -> str | None:
    """Return a directory path or None if the user leaves the input blank."""
    while True:
        path = input(f"{label} [leave blank for inplace mode]: ").strip()
        if path == "":
            return None
        expanded = os.path.expanduser(path)
        if os.path.isdir(expanded):
            return expanded
        create = input(f"  {expanded!r} does not exist. Create it? [y/N] ").strip().lower()
        if create == "y":
            os.makedirs(expanded, exist_ok=True)
            return expanded


def collect_hq_files(src_dir: str) -> list[str]:
    results = []
    for root, _dirs, files in os.walk(src_dir):
        for filename in sorted(files):
            _, ext = os.path.splitext(filename)
            if ext.lower() in HQ_EXTENSIONS:
                results.append(os.path.join(root, filename))
    return results


def dest_path_for(src_file: str, src_dir: str, dest_dir: str | None) -> str:
    """Return the output .mp3 path for a given source file."""
    stem, _ = os.path.splitext(os.path.basename(src_file))
    mp3_name = stem + ".mp3"
    if dest_dir is None:
        return os.path.join(os.path.dirname(src_file), mp3_name)
    rel = os.path.relpath(os.path.dirname(src_file), src_dir)
    out_dir = os.path.join(dest_dir, rel)
    return os.path.join(out_dir, mp3_name)


def convert_file(
    src: str,
    dest: str,
    ffmpeg: str,
    delete_original: bool,
) -> bool:
    """
    Convert src to dest as a 320k MP3. Returns True on success.

    Writes atomically via dest + '.part', renamed on success.
    """
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)

    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-err_detect", "ignore_err",
                "-i", src,
                "-vn",
                "-b:a", "320k",
                "-map_metadata", "0",
                "-f", "mp3",
                "-y",
                part,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        print(f"  ERROR: ffmpeg not found at {ffmpeg!r}. Install ffmpeg or use --ffmpeg.")
        sys.exit(1)

    if result.returncode == 0:
        os.replace(part, dest)
        if delete_original:
            try:
                os.remove(src)
            except OSError as e:
                print(f"  WARN: could not delete original: {e}")
        return True
    else:
        err = result.stderr.decode(errors="replace").strip()
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass
        print(f"  FAILED: {err[-300:]}")
        return False


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert HQ audio files to 320k MP3 using ffmpeg."
    )
    parser.add_argument(
        "src",
        nargs="?",
        default=None,
        help="Source directory containing HQ audio files (omit to be prompted)",
    )
    parser.add_argument(
        "dest",
        nargs="?",
        default=None,
        help=(
            "Destination directory for MP3 output (omit for inplace mode, "
            "where MP3s are written beside the originals)"
        ),
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the HQ original after a successful conversion",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Only scan the top-level source directory, not subdirectories",
    )
    parser.add_argument(
        "--ffmpeg",
        default="ffmpeg",
        metavar="PATH",
        help="Path to ffmpeg binary (default: ffmpeg from PATH)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be converted without doing anything",
    )
    args = parser.parse_args()

    # ── Apply env-var defaults (CLI wins, then .env, then prompt) ─────────────
    if not args.src and os.environ.get("HQ_TO_MP3_SRC"):
        args.src = os.environ["HQ_TO_MP3_SRC"]
    if not args.dest and os.environ.get("HQ_TO_MP3_DEST"):
        args.dest = os.environ["HQ_TO_MP3_DEST"]
    if args.ffmpeg == "ffmpeg" and os.environ.get("FFMPEG_PATH"):
        args.ffmpeg = os.environ["FFMPEG_PATH"]

    # ── Resolve source dir ────────────────────────────────────────────────────
    src_dir = os.path.expanduser(args.src) if args.src else prompt_directory("Source directory (HQ files)")
    if not os.path.isdir(src_dir):
        print(f"Source directory not found: {src_dir!r}")
        sys.exit(1)

    # ── Resolve dest dir ──────────────────────────────────────────────────────
    if args.dest is not None:
        dest_dir: str | None = os.path.expanduser(args.dest)
        os.makedirs(dest_dir, exist_ok=True)
    elif args.src is not None:
        # src was given on CLI but no dest → inplace
        dest_dir = None
    else:
        dest_dir = prompt_optional_directory("Destination directory for MP3 output")

    mode = "inplace" if dest_dir is None else f"separate → {dest_dir}"

    # ── Collect files ─────────────────────────────────────────────────────────
    if args.no_recursive:
        hq_files = [
            os.path.join(src_dir, f)
            for f in sorted(os.listdir(src_dir))
            if os.path.splitext(f)[1].lower() in HQ_EXTENSIONS
        ]
    else:
        hq_files = collect_hq_files(src_dir)

    print(f"\nSource:     {os.path.abspath(src_dir)}")
    print(f"Mode:       {mode}")
    print(f"Delete HQ:  {args.delete}")
    print(f"ffmpeg:     {args.ffmpeg}")
    print(f"Found:      {len(hq_files)} HQ file(s)\n")

    if not hq_files:
        print("No HQ audio files found. Nothing to do.")
        return

    if args.dry_run:
        for f in hq_files:
            print(f"  [dry-run] {f}")
        return

    # ── Convert ───────────────────────────────────────────────────────────────
    done = errors = skipped = 0
    total = len(hq_files)

    for i, src_file in enumerate(hq_files, 1):
        dest_file = dest_path_for(src_file, src_dir, dest_dir)
        stem = os.path.basename(src_file)
        prefix = f"[{i}/{total}]"

        if os.path.exists(dest_file):
            print(f"{prefix} SKIP (MP3 exists): {stem}")
            skipped += 1
            continue

        print(f"{prefix} Converting: {stem}")
        ok = convert_file(src_file, dest_file, args.ffmpeg, args.delete)
        if ok:
            print(f"  → {os.path.basename(dest_file)}")
            done += 1
        else:
            errors += 1

    print(f"\nDone: {done} converted, {skipped} skipped, {errors} errors (out of {total} total).")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
