#!/usr/bin/env python3
"""
Copy ID3 tags from a folder of source MP3s to matching MP3s in a target folder.

Matches files by stem name (case-insensitive). Useful for transferring tags
(title, artist, album, cover art, BPM, rating, etc.) from tagged MP3 files to
HQ-converted MP3 counterparts that lack metadata.

Tags transferred (all configurable via --rule):
  TIT2  title
  TPE1  artist
  TALB  album
  APIC  cover art (embedded image)
  TBPM  BPM
  COMM  comment
  TPUB  publisher
  TKEY  initial key
  POPM  popularimeter (star rating)

Rule options per tag (--rule TAG:RULE):
  always    — always overwrite (default)
  if_empty  — only copy if the target tag is absent
  skip      — never copy this tag

Requires: mutagen  (pip install mutagen)

Usage examples:

  # Interactive:
  python transfer_metadata.py

  # Non-interactive dry run:
  python transfer_metadata.py /mnt/music/mp3 /mnt/music/hq_mp3 --dry-run

  # Skip cover art, only fill BPM if target lacks it:
  python transfer_metadata.py /src /tgt --rule APIC:skip --rule TBPM:if_empty
"""

import argparse
import os
import sys

try:
    from mutagen.id3 import (
        ID3,
        ID3NoHeaderError,
    )
except ImportError:
    print("ERROR: mutagen is required.  Install it with:  pip install mutagen")
    sys.exit(1)

ALL_TAGS = ("TIT2", "TPE1", "TALB", "APIC", "TBPM", "COMM", "TPUB", "TKEY", "POPM")


# ── Core logic ────────────────────────────────────────────────────────────────


def _apply_tag(src_tags: ID3, tgt_tags: ID3, tag: str, rule: str) -> bool:
    """Copy one tag from src → tgt according to rule. Returns True if tgt changed."""
    if rule == "skip":
        return False

    if tag == "POPM":
        frames = src_tags.getall("POPM")
        if not frames:
            return False
        if rule == "if_empty" and tgt_tags.getall("POPM"):
            return False
        tgt_tags.delall("POPM")
        for frame in frames:
            tgt_tags.add(frame)
        return True
    else:
        frame = src_tags.get(tag)
        if frame is None:
            return False
        if rule == "if_empty" and tgt_tags.get(tag) is not None:
            return False
        tgt_tags.add(frame)
        return True


def transfer_metadata(
    src_dir: str,
    tgt_dir: str,
    rules: dict[str, str],
    dry_run: bool,
    verbose: bool,
) -> tuple[int, int, int]:
    """
    Transfer tags from src_dir MP3s to matching tgt_dir MP3s.
    Returns (matched, skipped, unmatched).
    """
    src_files = sorted(
        os.path.join(root, f)
        for root, _dirs, files in os.walk(src_dir)
        for f in files
        if f.lower().endswith(".mp3")
    )
    if not src_files:
        print(f"No source MP3s found in {src_dir}")
        return 0, 0, 0

    tgt_map: dict[str, str] = {}
    for root, _dirs, files in os.walk(tgt_dir):
        for f in files:
            if f.lower().endswith(".mp3"):
                stem = os.path.splitext(f)[0].lower()
                tgt_map[stem] = os.path.join(root, f)

    if not tgt_map:
        print(f"No target MP3s found in {tgt_dir}")
        return 0, 0, 0

    active = [t for t in ALL_TAGS if rules.get(t, "always") != "skip"]
    print(f"Source:  {os.path.abspath(src_dir)}  ({len(src_files)} MP3s)")
    print(f"Target:  {os.path.abspath(tgt_dir)}  ({len(tgt_map)} MP3s)")
    print(f"Tags:    {', '.join(active)}")
    if dry_run:
        print("Mode:    DRY RUN — no files will be modified\n")
    else:
        print()

    matched = skipped = unmatched = 0

    for src_path in src_files:
        stem = os.path.splitext(os.path.basename(src_path))[0].lower()
        tgt_path = tgt_map.get(stem)

        if tgt_path is None:
            if verbose:
                print(f"  ✗ no match: {os.path.basename(src_path)}")
            unmatched += 1
            continue

        try:
            try:
                src_tags = ID3(src_path)
            except ID3NoHeaderError:
                print(f"  ⚠ no tags in source: {os.path.basename(src_path)}")
                skipped += 1
                continue

            try:
                tgt_tags = ID3(tgt_path)
            except ID3NoHeaderError:
                tgt_tags = ID3()

            changed = False
            for tag in ALL_TAGS:
                if _apply_tag(src_tags, tgt_tags, tag, rules.get(tag, "always")):
                    changed = True

            if changed:
                if not dry_run:
                    tgt_tags.save(tgt_path)
                print(f"  {'[dry] ' if dry_run else ''}✓ {os.path.basename(src_path)}")
                matched += 1
            else:
                if verbose:
                    print(f"  — unchanged: {os.path.basename(src_path)}")
                skipped += 1

        except Exception as e:
            print(f"  ✗ error on {os.path.basename(src_path)}: {e}")
            skipped += 1

    return matched, skipped, unmatched


# ── Prompts ───────────────────────────────────────────────────────────────────


def prompt_directory(label: str) -> str:
    while True:
        path = input(f"{label}: ").strip()
        expanded = os.path.expanduser(path)
        if os.path.isdir(expanded):
            return expanded
        print(f"  Path not found: {expanded!r} — please try again.")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy ID3 tags from source MP3s to matching target MP3s."
    )
    parser.add_argument(
        "src",
        nargs="?",
        default=None,
        help="Source directory (tagged MP3s, e.g. downloaded from SoundCloud)",
    )
    parser.add_argument(
        "tgt",
        nargs="?",
        default=None,
        help="Target directory (HQ MP3s to be tagged)",
    )
    parser.add_argument(
        "--rule",
        action="append",
        default=[],
        metavar="TAG:RULE",
        help=(
            "Per-tag rule override, e.g. --rule APIC:skip --rule TBPM:if_empty. "
            f"Tags: {', '.join(ALL_TAGS)}. Rules: always, if_empty, skip."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be transferred without writing any changes",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Also print unmatched and unchanged files",
    )
    args = parser.parse_args()

    # ── Parse rules ───────────────────────────────────────────────────────────
    rules: dict[str, str] = {t: "always" for t in ALL_TAGS}
    for spec in args.rule:
        if ":" not in spec:
            print(f"ERROR: invalid --rule format {spec!r} — expected TAG:RULE")
            sys.exit(1)
        tag, rule = spec.split(":", 1)
        tag = tag.upper()
        if tag not in ALL_TAGS:
            print(f"ERROR: unknown tag {tag!r}. Valid tags: {', '.join(ALL_TAGS)}")
            sys.exit(1)
        if rule not in ("always", "if_empty", "skip"):
            print(f"ERROR: unknown rule {rule!r}. Valid rules: always, if_empty, skip")
            sys.exit(1)
        rules[tag] = rule

    # ── Resolve directories ───────────────────────────────────────────────────
    src_dir = os.path.expanduser(args.src) if args.src else prompt_directory(
        "Source directory (tagged MP3s)"
    )
    tgt_dir = os.path.expanduser(args.tgt) if args.tgt else prompt_directory(
        "Target directory (HQ MP3s to be tagged)"
    )

    for label, path in [("Source", src_dir), ("Target", tgt_dir)]:
        if not os.path.isdir(path):
            print(f"ERROR: {label} directory not found: {path!r}")
            sys.exit(1)

    matched, skipped, unmatched = transfer_metadata(
        src_dir, tgt_dir, rules, dry_run=args.dry_run, verbose=args.verbose
    )

    print(f"\nDone: {matched} transferred, {skipped} skipped, {unmatched} unmatched.")
    if args.dry_run and matched:
        print("Run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
