#!/usr/bin/env python3
"""
Download SoundCloud likes, sets, or a full profile via scdl.

Replicates the exact scdl flags, folder layout, archive-file strategy, and
per-set subfolder rename detection used by mymuma.

Modes:
  likes    — downloads likes (MP3 + HQ) from a profile URL
  sets     — downloads one or more playlist/set URLs into per-set subfolders
  profile  — downloads the full profile (all tracks) from a profile URL

Format options:
  --format mp3   download 320k MP3 only      (--onlymp3)
  --format hq    download HQ original only   (--flac)
  --format both  download both (default)

Archive files prevent re-downloading already-fetched tracks:
  likes / profile  →  <out_dir>/archive.txt
  sets             →  <out_dir>/<set_name>/.scdl-archive-<hash12>.txt

Requires: scdl  (pip install scdl)

Usage examples:

  # Interactive (prompts for everything):
  python sc_download.py

  # Likes, both formats:
  python sc_download.py likes \\
    --client-id abc123 --auth-token xyz --profile-url https://soundcloud.com/myuser \\
    --output-dir ~/Music/SoundCloud

  # Specific sets, HQ only:
  python sc_download.py sets \\
    --client-id abc123 --auth-token xyz \\
    --urls https://soundcloud.com/user/sets/set-a https://soundcloud.com/user/sets/set-b \\
    --format hq --output-dir ~/Music/SoundCloud/sets-hq

  # Full profile dry-run (prints command, does not run):
  python sc_download.py profile \\
    --client-id abc123 --auth-token xyz --profile-url https://soundcloud.com/myuser \\
    --dry-run
"""

import argparse
import hashlib
import os
import re
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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def _sanitize(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"[\x00-\x1f]", "", name)
    return re.sub(r"\s+", " ", name).strip() or "set"


def _slug_to_name(url: str) -> str:
    m = re.search(r"/sets/([^/?#]+)", url)
    if m:
        return m.group(1).replace("-", " ").replace("_", " ").title()
    return ""


def _find_existing_sc_folder(output_dir: str, url: str) -> str | None:
    """Find a subfolder that already owns this URL's archive file."""
    archive_name = f".scdl-archive-{_url_hash(url)}.txt"
    if not os.path.isdir(output_dir):
        return None
    for entry in os.scandir(output_dir):
        if entry.is_dir() and os.path.exists(os.path.join(entry.path, archive_name)):
            return entry.path
    return None


def _shared_flags(
    out_dir: str,
    archive: str,
    client_id: str,
    auth_token: str,
) -> list[str]:
    return [
        "--client-id",        client_id,
        "--original-art",
        "-c",
        "--extract-artist",
        "--name-format",          "%(title)s",
        "--playlist-name-format", "%(title)s",
        "--auth-token",       auth_token,
        "--yt-dlp-args",
        "--sleep-request 0.1 --extractor-retries 20000 --fragment-retries 10 --postprocessor-args 'AtomicParsley:'",
        "--path",             out_dir,
        "--download-archive", archive,
    ]


def _build_mp3_cmd(
    url: str,
    out_dir: str,
    archive: str,
    client_id: str,
    auth_token: str,
    extra: list[str] | None = None,
) -> list[str]:
    return (
        ["scdl"]
        + _shared_flags(out_dir, archive, client_id, auth_token)
        + ["--onlymp3"]
        + (extra or [])
        + ["-l", url]
    )


def _build_hq_cmd(
    url: str,
    out_dir: str,
    archive: str,
    client_id: str,
    auth_token: str,
    extra: list[str] | None = None,
) -> list[str]:
    return (
        ["scdl"]
        + _shared_flags(out_dir, archive, client_id, auth_token)
        + ["--flac"]
        + (extra or [])
        + ["-l", url]
    )


def run_cmd(cmd: list[str], label: str, dry_run: bool) -> int:
    """Print and optionally run a scdl command. Returns exit code (0 on dry-run)."""
    print(f"\n[{label}] " + " ".join(cmd))
    if dry_run:
        print(f"  [dry-run] skipping execution")
        return 0
    try:
        result = subprocess.run(cmd)
        return result.returncode
    except FileNotFoundError:
        print("  ERROR: scdl not found — install with:  pip install scdl")
        sys.exit(1)


def run_fmt(
    fmt: str,
    url: str,
    out_dir: str,
    archive_base: str,
    client_id: str,
    auth_token: str,
    extra: list[str] | None,
    label: str,
    dry_run: bool,
) -> None:
    """Run mp3 and/or hq download based on format flag."""
    if fmt in ("mp3", "both"):
        archive = archive_base if fmt == "mp3" else archive_base.replace(".txt", "-mp3.txt")
        if fmt == "both":
            mp3_dir = out_dir + "-mp3" if not out_dir.endswith("-mp3") else out_dir
            os.makedirs(mp3_dir, exist_ok=True)
            archive = os.path.join(mp3_dir, "archive.txt")
        else:
            mp3_dir = out_dir
        run_cmd(_build_mp3_cmd(url, mp3_dir, archive, client_id, auth_token, extra), label + " MP3", dry_run)

    if fmt in ("hq", "both"):
        if fmt == "both":
            hq_dir = out_dir + "-hq" if not out_dir.endswith("-hq") else out_dir
            os.makedirs(hq_dir, exist_ok=True)
            archive = os.path.join(hq_dir, "archive.txt")
        else:
            hq_dir = out_dir
            archive = archive_base
        run_cmd(_build_hq_cmd(url, hq_dir, archive, client_id, auth_token, extra), label + " HQ", dry_run)


# ── Mode implementations ──────────────────────────────────────────────────────


def do_likes(
    client_id: str,
    auth_token: str,
    profile_url: str,
    output_dir: str,
    fmt: str,
    dry_run: bool,
) -> None:
    base = os.path.expanduser(output_dir)
    if fmt == "both":
        mp3_dir = os.path.join(base, "likes-mp3")
        hq_dir  = os.path.join(base, "likes-hq")
        os.makedirs(mp3_dir, exist_ok=True)
        os.makedirs(hq_dir,  exist_ok=True)
        if fmt in ("mp3", "both"):
            archive = os.path.join(mp3_dir, "archive.txt")
            run_cmd(_build_mp3_cmd(profile_url, mp3_dir, archive, client_id, auth_token, ["--no-playlist"]),
                    "likes MP3", dry_run)
        if fmt in ("hq", "both"):
            archive = os.path.join(hq_dir, "archive.txt")
            run_cmd(_build_hq_cmd(profile_url, hq_dir, archive, client_id, auth_token, ["--no-playlist"]),
                    "likes HQ", dry_run)
    else:
        out = os.path.join(base, "likes-mp3" if fmt == "mp3" else "likes-hq")
        os.makedirs(out, exist_ok=True)
        archive = os.path.join(out, "archive.txt")
        cmd = (_build_mp3_cmd if fmt == "mp3" else _build_hq_cmd)(
            profile_url, out, archive, client_id, auth_token, ["--no-playlist"]
        )
        run_cmd(cmd, f"likes {fmt.upper()}", dry_run)


def do_sets(
    client_id: str,
    auth_token: str,
    urls: list[str],
    output_dir: str,
    fmt: str,
    dry_run: bool,
) -> None:
    for url in urls:
        name = _slug_to_name(url) or _url_hash(url)
        sanitized = _sanitize(name)

        if fmt == "both":
            bases = [
                (os.path.join(os.path.expanduser(output_dir), "sets-mp3"), "mp3"),
                (os.path.join(os.path.expanduser(output_dir), "sets-hq"),  "hq"),
            ]
        else:
            suffix = "sets-mp3" if fmt == "mp3" else "sets-hq"
            bases = [(os.path.join(os.path.expanduser(output_dir), suffix), fmt)]

        for base_dir, mode in bases:
            # Rename detection: find existing subfolder that already has this URL's archive
            existing = _find_existing_sc_folder(base_dir, url)
            expected = os.path.join(base_dir, sanitized)
            if existing and existing != expected:
                print(f"  Renaming: {os.path.basename(existing)!r} → {sanitized!r}")
                if not dry_run:
                    os.rename(existing, expected)
            os.makedirs(expected, exist_ok=True)

            archive = os.path.join(expected, f".scdl-archive-{_url_hash(url)}.txt")
            cmd = (_build_mp3_cmd if mode == "mp3" else _build_hq_cmd)(
                url, expected, archive, client_id, auth_token, ["--no-playlist-folder"]
            )
            run_cmd(cmd, f"set {sanitized} {mode.upper()}", dry_run)


def do_profile(
    client_id: str,
    auth_token: str,
    profile_url: str,
    output_dir: str,
    fmt: str,
    dry_run: bool,
) -> None:
    base = os.path.expanduser(output_dir)
    if fmt == "both":
        mp3_dir = os.path.join(base, "profile-mp3")
        hq_dir  = os.path.join(base, "profile-hq")
        os.makedirs(mp3_dir, exist_ok=True)
        os.makedirs(hq_dir,  exist_ok=True)
        archive = os.path.join(mp3_dir, "archive.txt")
        run_cmd(_build_mp3_cmd(profile_url, mp3_dir, archive, client_id, auth_token,
                               ["-f", "--no-playlist-folder"]), "profile MP3", dry_run)
        archive = os.path.join(hq_dir, "archive.txt")
        run_cmd(_build_hq_cmd(profile_url, hq_dir, archive, client_id, auth_token,
                              ["-f", "--no-playlist-folder"]), "profile HQ", dry_run)
    else:
        out = os.path.join(base, "profile-mp3" if fmt == "mp3" else "profile-hq")
        os.makedirs(out, exist_ok=True)
        archive = os.path.join(out, "archive.txt")
        cmd = (_build_mp3_cmd if fmt == "mp3" else _build_hq_cmd)(
            profile_url, out, archive, client_id, auth_token, ["-f", "--no-playlist-folder"]
        )
        run_cmd(cmd, f"profile {fmt.upper()}", dry_run)


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
            return val
        if default is not None:
            return default
        print("  Required — please enter a value.")


def prompt_mode() -> str:
    while True:
        val = input("Mode [likes/sets/profile]: ").strip().lower()
        if val in ("likes", "sets", "profile"):
            return val
        print("  Enter one of: likes, sets, profile")


def prompt_urls() -> list[str]:
    print("Enter set URLs (one per line, blank line to finish):")
    urls = []
    while True:
        line = input("  URL: ").strip()
        if not line:
            if urls:
                return urls
            print("  At least one URL is required.")
        else:
            urls.append(line)


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download SoundCloud likes, sets, or profile via scdl."
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["likes", "sets", "profile"],
        help="Download mode: likes, sets, or profile (omit to be prompted)",
    )
    parser.add_argument("--client-id",    default=None, help="SoundCloud client ID")
    parser.add_argument("--auth-token",   default=None, help="SoundCloud OAuth token")
    parser.add_argument("--profile-url",  default=None, help="SoundCloud profile URL (likes/profile mode)")
    parser.add_argument(
        "--urls",
        nargs="+",
        default=None,
        metavar="URL",
        help="Set URLs to download (sets mode only)",
    )
    parser.add_argument(
        "--format",
        choices=["mp3", "hq", "both"],
        default="both",
        help="Download MP3 only, HQ only, or both (default: both)",
    )
    parser.add_argument(
        "--output-dir",
        default="~/Music/SoundCloud",
        metavar="DIR",
        help="Base output directory (default: ~/Music/SoundCloud)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print scdl commands without running them",
    )
    args = parser.parse_args()

    mode       = args.mode       or prompt_mode()
    client_id  = (args.client_id  or os.environ.get("SC_CLIENT_ID")
                  or prompt("SoundCloud client ID"))
    auth_token = (args.auth_token or os.environ.get("SC_AUTH_TOKEN")
                  or prompt("SoundCloud auth token", secret=True))
    if args.output_dir == "~/Music/SoundCloud" and os.environ.get("SC_OUTPUT_DIR"):
        args.output_dir = os.environ["SC_OUTPUT_DIR"]

    if mode == "sets":
        urls = args.urls or prompt_urls()
        do_sets(client_id, auth_token, urls, args.output_dir, args.format, args.dry_run)
    else:
        profile_url = args.profile_url or prompt("SoundCloud profile URL")
        if mode == "likes":
            do_likes(client_id, auth_token, profile_url, args.output_dir, args.format, args.dry_run)
        else:
            do_profile(client_id, auth_token, profile_url, args.output_dir, args.format, args.dry_run)


if __name__ == "__main__":
    main()
