#!/usr/bin/env python3
"""
Delete audio files (mp3, m4a, etc.) whose stem does not follow the naming rule:
  - The segment immediately before the file extension must be purely numeric.
  - It must contain at least MIN_DIGITS digits (default: 7, matches SoundCloud ID length).
  - There must be at least one non-whitespace, non-dot character before that number.

Optionally also deletes temporary download leftovers (.part, .temp, .tmp, .ytdl).

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
TEMP_EXTENSIONS = {".part", ".temp", ".tmp", ".ytdl"}
DEFAULT_MIN_DIGITS = 7


def build_pattern(min_digits: int) -> re.Pattern:
    return re.compile(r".*[^\s.].*\.(\d{" + str(min_digits) + r",})$")


def is_valid_filename(stem: str, pattern: re.Pattern) -> bool:
    return bool(pattern.match(stem))


def collect_files(
    directory: str,
    recursive: bool,
    extensions: set,
    pattern: re.Pattern,
    clean_temp: bool,
    temp_extensions: set,
) -> tuple[list[str], list[str]]:
    """Returns (invalid_audio, temp_files)."""
    walker = os.walk(directory) if recursive else [(directory, [], os.listdir(directory))]
    invalid_audio = []
    temp_files = []

    for root, _dirs, files in walker:
        for filename in files:
            stem, ext = os.path.splitext(filename)
            full_path = os.path.join(root, filename)
            if clean_temp and ext.lower() in temp_extensions:
                temp_files.append(full_path)
            elif ext.lower() in extensions:
                if not is_valid_filename(stem, pattern):
                    invalid_audio.append(full_path)

    return invalid_audio, temp_files


def print_and_confirm(label: str, files: list[str], dry_run: bool) -> bool:
    """Print file list and return True if deletion should proceed."""
    print(f"\n{label} ({len(files)} file(s)):")
    for path in files:
        print(f"  - {path}")
    if dry_run:
        confirm = input(f"\nDelete these {len(files)} file(s)? [y/N] ").strip().lower()
        return confirm == "y"
    return True


def delete_files(files: list[str]) -> None:
    deleted = 0
    for path in files:
        try:
            os.remove(path)
            print(f"  DELETED: {path}")
            deleted += 1
        except OSError as e:
            print(f"  ERROR deleting {path}: {e}")
    print(f"  {deleted}/{len(files)} file(s) deleted.")


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
        help="Skip confirmation prompts and delete immediately",
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
        help="Audio extensions to check, e.g. --ext .mp3 .m4a (default: mp3 m4a flac wav aac ogg)",
    )
    parser.add_argument(
        "--clean-temp",
        action="store_true",
        help=f"Also delete temporary download files ({', '.join(sorted(TEMP_EXTENSIONS))})",
    )
    parser.add_argument(
        "--temp-ext",
        nargs="+",
        default=None,
        metavar="EXT",
        help="Override temp extensions, e.g. --temp-ext .part .tmp (default: .part .temp .tmp .ytdl)",
    )
    args = parser.parse_args()

    directory = args.directory if args.directory else prompt_directory()

    extensions = (
        {e if e.startswith(".") else f".{e}" for e in args.ext}
        if args.ext
        else AUDIO_EXTENSIONS
    )
    temp_extensions = (
        {e if e.startswith(".") else f".{e}" for e in args.temp_ext}
        if args.temp_ext
        else TEMP_EXTENSIONS
    )

    pattern = build_pattern(args.min_digits)
    dry_run = not args.delete

    print(f"\nScanning: {os.path.abspath(directory)}")
    print(f"Audio extensions: {', '.join(sorted(extensions))}")
    print(f"Min ID digits: {args.min_digits}")
    print(f"Recursive: {not args.no_recursive}")
    if args.clean_temp:
        print(f"Temp extensions: {', '.join(sorted(temp_extensions))}")

    invalid_audio, temp_files = collect_files(
        directory=directory,
        recursive=not args.no_recursive,
        extensions=extensions,
        pattern=pattern,
        clean_temp=args.clean_temp,
        temp_extensions=temp_extensions,
    )

    anything_found = False

    if invalid_audio:
        anything_found = True
        if dry_run:
            print(f"\n[DRY RUN] Invalid audio files ({len(invalid_audio)}):")
            for path in invalid_audio:
                print(f"  - {path}")
            if print_and_confirm("Invalid audio files", invalid_audio, dry_run=True):
                delete_files(invalid_audio)
            else:
                print("  Skipped.")
        else:
            print(f"\nDeleting invalid audio files ({len(invalid_audio)}):")
            delete_files(invalid_audio)

    if temp_files:
        anything_found = True
        if dry_run:
            print(f"\n[DRY RUN] Temporary files ({len(temp_files)}):")
            for path in temp_files:
                print(f"  - {path}")
            if print_and_confirm("Temporary files", temp_files, dry_run=True):
                delete_files(temp_files)
            else:
                print("  Skipped.")
        else:
            print(f"\nDeleting temporary files ({len(temp_files)}):")
            delete_files(temp_files)

    if not anything_found:
        print("\nNo files to delete. All clean!")


if __name__ == "__main__":
    main()
