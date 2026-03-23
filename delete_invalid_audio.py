#!/usr/bin/env python3
"""
Delete audio files (mp3, m4a, etc.) whose stem does not follow the naming rule:
  - The segment immediately before the file extension must be purely numeric.
  - It must contain at least MIN_DIGITS digits (default: 7, matches SoundCloud ID length).
  - There must be at least one non-whitespace, non-dot character before that number.

Valid:   My beautiful song....12451246.mp3
         Coooamo.1514651.mp3
         oaoaoa.123.36135615.mp3
         THE END OF ...1111111 ALL.134714714.m4a
         X.253235208.mp3
         '.1788350158.mp3

Invalid: ahuh11235.mp3
         oaoaoa.123.mp3
         .3613613.mp3
         THE END OF ...1111111 ALL.mp3
"""

import os
import re
import argparse

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".wav", ".aac", ".ogg"}
DEFAULT_MIN_DIGITS = 7


def build_pattern(min_digits: int) -> re.Pattern:
    # At least one non-whitespace non-dot char, then anything, then dot, then >=min_digits digits
    return re.compile(r".*[^\s.].*\.(\d{" + str(min_digits) + r",})$")


def is_valid_filename(stem: str, pattern: re.Pattern) -> bool:
    return bool(pattern.match(stem))


def scan_and_delete(
    directory: str,
    dry_run: bool,
    recursive: bool,
    extensions: set,
    pattern: re.Pattern,
) -> None:
    walker = os.walk(directory) if recursive else [(directory, [], os.listdir(directory))]
    invalid_files = []

    for root, _dirs, files in walker:
        for filename in files:
            stem, ext = os.path.splitext(filename)
            if ext.lower() not in extensions:
                continue
            if not is_valid_filename(stem, pattern):
                invalid_files.append(os.path.join(root, filename))

    if not invalid_files:
        print("\nNo invalid files found. Nothing to do.")
        return

    print(f"\nFound {len(invalid_files)} invalid file(s):")
    for path in invalid_files:
        print(f"  - {path}")

    if dry_run:
        print("\n[DRY RUN] No files were deleted.")
        confirm = input("Delete these files now? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted. No files deleted.")
            return

    deleted = 0
    for path in invalid_files:
        try:
            os.remove(path)
            print(f"  DELETED: {path}")
            deleted += 1
        except OSError as e:
            print(f"  ERROR deleting {path}: {e}")

    print(f"\nDone. {deleted}/{len(invalid_files)} file(s) deleted.")


def prompt_directory() -> str:
    while True:
        path = input("Enter directory to scan [default: current directory]: ").strip()
        if path == "":
            return "."
        if os.path.isdir(path):
            return path
        print(f"  Path not found: {path!r} — please try again.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete audio files missing a valid numeric ID segment before the extension."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=None,
        help="Directory to scan (omit to be prompted)",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Skip the confirmation prompt and delete immediately",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Only scan the top-level directory, not subdirectories",
    )
    parser.add_argument(
        "--min-digits",
        type=int,
        default=DEFAULT_MIN_DIGITS,
        metavar="N",
        help=f"Minimum digits required in the ID segment (default: {DEFAULT_MIN_DIGITS})",
    )
    parser.add_argument(
        "--ext",
        nargs="+",
        default=None,
        metavar="EXT",
        help="Extensions to check, e.g. --ext .mp3 .m4a (default: mp3 m4a flac wav aac ogg)",
    )
    args = parser.parse_args()

    directory = args.directory if args.directory else prompt_directory()

    extensions = (
        {e if e.startswith(".") else f".{e}" for e in args.ext}
        if args.ext
        else AUDIO_EXTENSIONS
    )

    pattern = build_pattern(args.min_digits)

    print(f"\nScanning: {os.path.abspath(directory)}")
    print(f"Extensions: {', '.join(sorted(extensions))}")
    print(f"Min ID digits: {args.min_digits}")
    print(f"Recursive: {not args.no_recursive}")

    scan_and_delete(
        directory=directory,
        dry_run=not args.delete,
        recursive=not args.no_recursive,
        extensions=extensions,
        pattern=pattern,
    )


if __name__ == "__main__":
    main()
