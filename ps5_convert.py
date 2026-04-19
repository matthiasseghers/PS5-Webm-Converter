#!/usr/bin/env python3
"""ps5_convert.py — Convert PS5 4K .webm recordings to FCP-ready .mp4

Usage:
    python ps5_convert.py /path/to/folder [--mode sdr|hdr] [--overwrite|--skip]

FCP compatibility notes
-----------------------
Three issues plague PS5 → Final Cut Pro workflows if left unfixed:

  1. VFR (Variable Frame Rate) — PS5 recordings are VFR; FCP is CFR-only and
     lags / stutters on the timeline.  Fixed with -vsync cfr.

  2. Opus audio — WebM carries Opus audio which FCP cannot natively render fast,
     causing waveform generation to stall.  Fixed by re-encoding to AAC.

  3. Late moov atom — without -movflags +faststart the MP4 metadata sits at the
     end of the file and FCP must scan the entire file before it can import.
"""

import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

import click
import subprocess

# ── Constants ─────────────────────────────────────────────────────────────────

CACHE_DIR = Path.home() / ".ps5-converter" / "bin"
CRF = 18
PRESET = "slow"

# Static ffmpeg/ffprobe builds from evermeet.cx (macOS, x86_64 + arm64 universal)
FFMPEG_DOWNLOAD_URL = "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"
FFPROBE_DOWNLOAD_URL = "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"


# ── ffmpeg resolution ──────────────────────────────────────────────────────────

def _download_binary(url: str, dest: Path) -> None:
    """Download a single-binary zip from *url* and extract the binary to *dest*."""
    click.echo(f"  Downloading {dest.name} …")
    zip_path = dest.with_suffix(".zip")
    try:
        urllib.request.urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            if not names:
                raise RuntimeError(f"Downloaded zip for {dest.name} is empty")
            # evermeet.cx zips contain exactly one binary at the top level
            with zf.open(names[0]) as src, open(dest, "wb") as out:
                out.write(src.read())
        dest.chmod(0o755)
    finally:
        zip_path.unlink(missing_ok=True)


def _download_ffmpeg(to: Path) -> None:
    """Download ffmpeg and ffprobe static builds into the given directory."""
    to.mkdir(parents=True, exist_ok=True)
    _download_binary(FFMPEG_DOWNLOAD_URL, to / "ffmpeg")
    _download_binary(FFPROBE_DOWNLOAD_URL, to / "ffprobe")


def _has_zscale(ffmpeg: Path) -> bool:
    """Return True if the ffmpeg build includes the zscale filter (libzimg)."""
    result = subprocess.run(
        [str(ffmpeg), "-filters"],
        capture_output=True,
        text=True,
    )
    return "zscale" in (result.stdout + result.stderr)


def _prompt_and_download() -> tuple[Path, Path]:
    """Prompt the user, download static ffmpeg/ffprobe from evermeet.cx, return paths."""
    cached = CACHE_DIR / "ffmpeg"
    cached_probe = CACHE_DIR / "ffprobe"
    if not click.confirm(
        "Download a static build (includes zscale) to ~/.ps5-converter/bin/?",
        default=False,
    ):
        click.echo("Aborted — ffmpeg with zscale is required.")
        sys.exit(1)
    click.echo("Downloading ffmpeg static build from evermeet.cx …")
    _download_ffmpeg(to=CACHE_DIR)
    if not cached.exists() or not cached_probe.exists():
        click.echo(click.style("Error:", fg="red") + " download failed — binaries not found after extraction.")
        sys.exit(1)
    return cached, cached_probe


def find_or_download_ffmpeg() -> tuple[Path, Path]:
    """Return (ffmpeg, ffprobe) paths, downloading if necessary.

    Resolution order:
      1. ~/.ps5-converter/bin/  (cached download — assumed to have zscale)
      2. System $PATH           (only accepted if zscale is present)
      3. Prompt user → download static build from evermeet.cx → cache
    """
    cached = CACHE_DIR / "ffmpeg"
    cached_probe = CACHE_DIR / "ffprobe"

    if cached.exists() and cached_probe.exists():
        return cached, cached_probe

    system = shutil.which("ffmpeg")
    system_probe = shutil.which("ffprobe")
    if system and system_probe:
        if _has_zscale(Path(system)):
            return Path(system), Path(system_probe)
        click.echo(
            click.style("Warning:", fg="yellow", bold=True)
            + f" ffmpeg at {system} is missing zscale (libzimg).\n"
            "  HDR→SDR tone-mapping requires it.\n"
            "  Fix: brew install ffmpeg-full  OR  let this tool download a static build."
        )
        click.echo("")

    else:
        click.echo(click.style("ffmpeg not found.", fg="red"))
        click.echo("")

    return _prompt_and_download()


# ── HDR detection ──────────────────────────────────────────────────────────────

def _detect_hdr(ffprobe: Path, webm: Path) -> bool:
    """Return True if the first video stream has an HDR color transfer function."""
    result = subprocess.run(
        [
            str(ffprobe),
            "-v", "quiet",
            "-select_streams", "v:0",
            "-show_streams",
            "-of", "json",
            str(webm),
        ],
        capture_output=True,
        text=True,
    )
    try:
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if not streams:
            return False
        color_transfer = streams[0].get("color_transfer", "")
        return color_transfer in ("smpte2084", "arib-std-b67")
    except (json.JSONDecodeError, KeyError):
        return False


# ── ffmpeg argument builder ────────────────────────────────────────────────────

def _resolve_mode(is_hdr: bool, mode: str) -> str:
    """Resolve 'auto' to the actual encode mode based on source HDR flag."""
    if mode == "auto":
        return "hdr" if is_hdr else "sdr"
    return mode


def _build_ffmpeg_args(
    ffmpeg: Path,
    webm: Path,
    mp4: Path,
    is_hdr: bool,
    mode: str,
) -> list[str]:
    # Resolve auto → sdr or hdr before building args
    resolved = _resolve_mode(is_hdr, mode)

    args = [
        str(ffmpeg),
        "-hide_banner", "-loglevel", "error", "-stats",
        "-y",
        "-i", str(webm),
        # FIX 1 — Force constant frame rate.
        # PS5 WebM recordings are VFR (variable frame rate). FCP is CFR-only;
        # importing VFR footage causes timeline lag and playback stutter.
        "-vsync", "cfr",
    ]

    if is_hdr and resolved == "sdr":
        # HDR → SDR: zscale tone-map to BT.709
        args += [
            "-vf",
            (
                "zscale=t=linear:npl=100,"
                "format=gbrpf32le,"
                "zscale=p=bt709,"
                "tonemap=tonemap=hable:desat=2.0,"
                "zscale=t=bt709:m=bt709:r=tv,"
                "format=yuv420p"
            ),
            "-c:v", "libx264",
            "-crf", str(CRF),
            "-preset", PRESET,
        ]
    elif is_hdr and resolved == "hdr":
        # HDR → HDR: preserve metadata, encode HEVC
        args += [
            "-c:v", "libx265",
            "-crf", str(CRF),
            "-preset", PRESET,
            "-x265-params",
            "hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
        ]
    else:
        # SDR → SDR (or SDR forced with --mode hdr, which has no effect)
        args += [
            "-c:v", "libx264",
            "-crf", str(CRF),
            "-preset", PRESET,
        ]

    args += [
        # FIX 2 — Re-encode audio as AAC.
        # PS5 WebM carries Opus audio. Copying Opus into an MP4 container makes
        # FCP stall on waveform generation because it can't natively render it.
        # AAC is FCP's native audio format; waveforms appear immediately.
        "-c:a", "aac",
        "-b:a", "320k",
        # FIX 3 — Move moov atom to the start of the file.
        # Without faststart, MP4 metadata is written at the end of the file and
        # FCP must scan the entire file before it can begin importing.
        "-movflags", "+faststart",
        str(mp4),
    ]
    return args


# ── CLI ────────────────────────────────────────────────────────────────────────

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--mode",
    type=click.Choice(["auto", "sdr", "hdr"]),
    default="auto",
    show_default=True,
    help="Output mode — auto: match source (default), sdr: force tone-map to H.264, hdr: force HEVC output.",
)
@click.option(
    "--overwrite",
    "overwrite",
    is_flag=True,
    default=False,
    help="Overwrite existing .mp4 files without prompting.",
)
@click.option(
    "--skip",
    "skip",
    is_flag=True,
    default=False,
    help="Skip existing .mp4 files without prompting.",
)
@click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory to write .mp4 files into (default: same folder as source .webm).",
)
def main(directory: Path, mode: str, overwrite: bool, skip: bool, output_dir: Path | None) -> None:
    """Convert PS5 4K .webm recordings in DIRECTORY to FCP-ready .mp4."""

    if overwrite and skip:
        raise click.UsageError("--overwrite and --skip are mutually exclusive")

    conflict = "overwrite" if overwrite else ("skip" if skip else "prompt")

    # Resolve output directory — create it if it doesn't exist
    out_dir = output_dir or directory
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Resolve ffmpeg ─────────────────────────────────────────────────────────
    ffmpeg, ffprobe = find_or_download_ffmpeg()

    # ── Find .webm files (non-recursive, case-insensitive, sorted α → oldest first) ──
    webm_files = sorted(
        f for f in directory.iterdir() if f.suffix.lower() == ".webm"
    )
    total = len(webm_files)

    if total == 0:
        click.echo(
            click.style("No .webm files found in:", fg="yellow") + f" {directory}"
        )
        sys.exit(0)

    # ── Header ────────────────────────────────────────────────────────────────
    click.echo(click.style("PS5 WebM → MP4 Converter", fg="cyan", bold=True))
    click.echo(f"Input     : {directory}")
    click.echo(f"Output    : {out_dir}")
    mode_label = f"{mode} (match source)" if mode == "auto" else mode
    click.echo(f"Mode      : {click.style(mode_label, bold=True)}")
    click.echo(
        f"Quality   : CRF {click.style(str(CRF), bold=True)}, "
        f"preset {click.style(PRESET, bold=True)}"
    )
    click.echo(f"Conflicts : {click.style(conflict, bold=True)}")
    click.echo(f"Found     : {click.style(str(total), bold=True)} .webm file(s)\n")

    # ── Process files ─────────────────────────────────────────────────────────
    skipped = 0
    converted = 0
    failed = 0

    for webm in webm_files:
        current = converted + skipped + failed + 1
        mp4 = out_dir / (webm.stem + ".mp4")

        click.echo(click.style(f"[{current}/{total}]", bold=True) + f" {webm.name}")

        # Conflict handling
        if mp4.exists():
            if conflict == "skip":
                click.echo(
                    f"  {click.style('⚠ Skipped', fg='yellow')} — {mp4}"
                )
                skipped += 1
                click.echo("")
                continue
            elif conflict == "prompt":
                do_overwrite = click.confirm(
                    f"  {click.style('⚠', fg='yellow')} {mp4.name} already exists."
                    " Overwrite?",
                    default=False,
                )
                if not do_overwrite:
                    click.echo(f"  {click.style('Skipped', fg='yellow')} — {mp4}")
                    skipped += 1
                    click.echo("")
                    continue
            # conflict == "overwrite": fall through to encode

        # HDR detection
        is_hdr = _detect_hdr(ffprobe, webm)
        source_label = "HDR" if is_hdr else "SDR"
        resolved_mode = _resolve_mode(is_hdr, mode)
        click.echo(
            f"  {click.style('→ Converting...', fg='cyan')}"
            f" ({source_label}→{resolved_mode.upper()})"
        )

        if not is_hdr and mode == "hdr":
            click.echo(
                f"  {click.style('⚠ Source is SDR — HDR mode has no effect, encoding as SDR', fg='yellow')}"
            )

        # Encode
        args = _build_ffmpeg_args(ffmpeg, webm, mp4, is_hdr, mode)
        result = subprocess.run(args)

        if result.returncode == 0:
            click.echo(f"\n  {click.style('✓ Done', fg='green')} → {mp4}")
            converted += 1
        else:
            click.echo(
                f"\n  {click.style('✗ Failed', fg='red')} — check the error above"
            )
            if mp4.exists():
                mp4.unlink()
            failed += 1

        click.echo("")

    # ── Summary ───────────────────────────────────────────────────────────────
    click.echo(click.style("─────────────────────────────", bold=True))
    click.echo(click.style(f"Converted : {converted}", fg="green"))
    click.echo(
        click.style(f"Skipped   : {skipped}", fg="yellow") + " (already existed)"
    )
    if failed > 0:
        click.echo(click.style(f"Failed    : {failed}", fg="red"))
    click.echo(click.style("─────────────────────────────", bold=True))


if __name__ == "__main__":
    main()