#!/usr/bin/env python3
"""
Delete audio files (mp3, m4a, etc.) whose stem does not follow the naming rule:
  - The segment immediately before the file extension must be purely numeric.
  - There must also be at least one non-empty segment BEFORE that number
    (i.e. the file needs an actual name, not just a bare number).

Valid:   My beautiful song....12451246.mp3      -> stem ends in .<digits>, has name before it
         Coooamo.1514651.mp3
         oaoaoa.123.36135615.mp3
         THE END OF ...1111111 ALL.134714714.m4a

Invalid: ahuh11235.mp3                          -> no dot before digits at end
         oaoaoa.123.mp3                         -> only one segment (the number), nothing before it
         THE END OF ...1111111 ALL.mp3          -> no numeric segment before extension
"""

import os
import re
import argparse

# File extensions to scan
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".wav", ".aac", ".ogg"}

# Stem must have:
#   - at least one character of "name" content before the final dot-number
#   - a dot separating name from the numeric tag
#   - one or more digits as the last dot-segment
# Pattern: anything (non-empty, at least one char) + dot + digits_only at end
VALID_PATTERN = re.compile(r".+\.(\d+)$")


def is_valid_filename(stem: str) -> bool:
    """Return True if the stem has content before a trailing dot-number segment."""
    return bool(VALID_PATTERN.match(stem))


def scan_and_delete(
    directory: str,
    dry_run: bool = True,
    recursive: bool = True,
    extensions: set = AUDIO_EXTENSIONS,
) -> None:
    walker = os.walk(directory) if recursive else [(directory, [], os.listdir(directory))]

    invalid_files = []

    for root, _dirs, files in walker:
        for filename in files:
            stem, ext = os.path.splitext(filename)
            if ext.lower() not in extensions:
                continue
            if not is_valid_filename(stem):
                invalid_files.append(os.path.join(root, filename))

    if not invalid_files:
        print("No invalid files found.")
        return

    print(f"Found {len(invalid_files)} invalid file(s):")
    for path in invalid_files:
        print(f"  {'[DRY RUN] ' if dry_run else ''}DELETE: {path}")
        if not dry_run:
            try:
                os.remove(path)
            except OSError as e:
                print(f"    ERROR deleting {path}: {e}")

    if dry_run:
        print("\n[DRY RUN] No files were deleted. Pass --delete to actually remove them.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete audio files that don't end their stem with a numeric segment preceded by actual name content."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory to scan (default: current directory)",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Actually delete the files (default is dry-run / preview only)",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Only scan the top-level directory, not subdirectories",
    )
    parser.add_argument(
        "--ext",
        nargs="+",
        default=None,
        metavar="EXT",
        help="File extensions to check, e.g. --ext .mp3 .m4a  (default: mp3 m4a flac wav aac ogg)",
    )
    args = parser.parse_args()

    extensions = (
        {e if e.startswith(".") else f".{e}" for e in args.ext}
        if args.ext
        else AUDIO_EXTENSIONS
    )

    scan_and_delete(
        directory=args.directory,
        dry_run=not args.delete,
        recursive=not args.no_recursive,
        extensions=extensions,
    )


if __name__ == "__main__":
    main()
