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

# Tone mapping (HDR → SDR)
TONEMAP_NPL = 1000      # Peak luminance (nits) - PS5 HDR10 targets ~1000
TONEMAP_TYPE = "mobius" # Tone mapper: mobius, hable, reinhard
TONEMAP_PARAM = 0.3     # Mobius transition (0.1-0.5, lower = more highlights)
TONEMAP_DESAT = 0       # Desaturation (0 = none, 2 = heavy)

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


def _detect_color_range(ffprobe: Path, webm: Path) -> str:
    """
    Detect color range from source video.
    Returns "tv" (limited 16-235) or "pc" (full 0-255). Defaults to "tv".
    """
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
            return "tv"
        
        # Check color_range field
        color_range = streams[0].get("color_range", "")
        if color_range in ("tv", "pc"):
            return color_range
        
        # Fallback: check pixel format
        pix_fmt = streams[0].get("pix_fmt", "")
        if pix_fmt.startswith("yuvj"):
            return "pc"
        
        return "tv"  # Default to TV range
    except (json.JSONDecodeError, KeyError):
        return "tv"


# ── Comparison / validation ────────────────────────────────────────────────────

def _parse_timecode(tc: str) -> float:
    """
    Parse a timecode string into total seconds.
    Accepted formats:
        HH:MM:SS.mmm   e.g. 01:23:45.500
        HH:MM:SS       e.g. 01:23:45
        MM:SS.mmm      e.g. 23:45.500
        MM:SS          e.g. 23:45
        plain seconds  e.g. 83.5 or 83
    """
    tc = tc.strip()
    
    # Plain number (seconds)
    try:
        return float(tc)
    except ValueError:
        pass
    
    parts = tc.split(":")
    if len(parts) == 3:
        # HH:MM:SS[.mmm]
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        # MM:SS[.mmm]
        m, s = parts
        return int(m) * 60 + float(s)
    
    raise ValueError(
        f"Cannot parse timecode '{tc}'. "
        "Use HH:MM:SS.mmm, MM:SS.mmm, or plain seconds."
    )


def _show_comparison(ffprobe: Path, webm: Path, mp4: Path) -> None:
    """Display a side-by-side comparison of source vs output metadata."""
    click.echo(f"\n  {click.style('Comparison:', fg='cyan', bold=True)}")
    
    def get_metadata(file: Path) -> dict:
        result = subprocess.run(
            [
                str(ffprobe),
                "-v", "quiet",
                "-select_streams", "v:0",
                "-show_streams",
                "-of", "json",
                str(file),
            ],
            capture_output=True,
            text=True,
        )
        try:
            data = json.loads(result.stdout)
            return data.get("streams", [{}])[0]
        except (json.JSONDecodeError, IndexError, KeyError):
            return {}
    
    src = get_metadata(webm)
    dst = get_metadata(mp4)
    
    def fmt_val(val, default="N/A"):
        return str(val) if val else default
    
    # Display key properties
    props = [
        ("Codec", "codec_name"),
        ("Color Space", "color_space"),
        ("Color Primaries", "color_primaries"),
        ("Color Transfer", "color_transfer"),
        ("Color Range", "color_range"),
        ("Pixel Format", "pix_fmt"),
        ("Width", "width"),
        ("Height", "height"),
        ("Frame Rate", "r_frame_rate"),
    ]
    
    click.echo(f"\n  {'Property':<20} {'Source (WebM)':<25} {'Output (MP4)':<25}")
    click.echo(f"  {'-' * 20} {'-' * 25} {'-' * 25}")
    
    for label, key in props:
        src_val = fmt_val(src.get(key))
        dst_val = fmt_val(dst.get(key))
        
        # Highlight differences
        if src_val != dst_val and src_val != "N/A" and dst_val != "N/A":
            dst_val = click.style(dst_val, fg="yellow", bold=True)
        
        click.echo(f"  {label:<20} {src_val:<25} {dst_val}")
    
    click.echo("")


# ──  ffmpeg argument builder ────────────────────────────────────────────────────

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
    source_color_range: str = "tv",
    test_duration: float | None = None,
    start_time: float | None = None,
    end_time: float | None = None,
) -> list[str]:
    """
    Build the complete ffmpeg command-line arguments for conversion.
    
    Args:
        ffmpeg: Path to ffmpeg binary
        webm: Input .webm file path
        mp4: Output .mp4 file path
        is_hdr: Whether source is HDR (smpte2084/arib-std-b67)
        mode: Output mode ('auto', 'sdr', or 'hdr')
        source_color_range: Detected color range ('tv' or 'pc')
        test_duration: If set, only convert first N seconds
        start_time: If set (with end_time), seek to this timestamp
        end_time: If set (with start_time), convert until this timestamp
    
    Returns:
        Complete ffmpeg argument list ready for subprocess.run()
    """
    # Resolve auto → sdr or hdr before building args
    resolved = _resolve_mode(is_hdr, mode)

    args = [
        str(ffmpeg),
        "-hide_banner", "-loglevel", "error", "-stats",
        "-y",
    ]
    
    # Seek to start time (fast seek before input)
    if start_time is not None:
        args += ["-ss", str(start_time)]
    
    args += [
        "-i", str(webm),
        # FIX 1 — Force constant frame rate.
        # PS5 WebM recordings are VFR (variable frame rate). FCP is CFR-only;
        # importing VFR footage causes timeline lag and playback stutter.
        "-vsync", "cfr",
    ]
    
    # Duration or end time (after input, before output options)
    if test_duration is not None:
        args += ["-t", str(test_duration)]
    elif end_time is not None and start_time is not None:
        # Use duration from start to end
        duration = end_time - start_time
        args += ["-t", str(duration)]

    if is_hdr and resolved == "sdr":
        # HDR → SDR: zscale tone-map to BT.709
        args += [
            "-vf",
            (
                f"zscale=t=linear:npl={TONEMAP_NPL},"
                "format=gbrpf32le,"
                "zscale=p=bt709,"
                f"tonemap=tonemap={TONEMAP_TYPE}:param={TONEMAP_PARAM}:desat={TONEMAP_DESAT},"
                "zscale=t=bt709:m=bt709:r=tv,"
                "format=yuv420p"
            ),
            "-c:v", "libx264",
            "-crf", str(CRF),
            "-preset", PRESET,
            "-color_range", source_color_range,
            # Explicit output colorspace tags (container level)
            "-colorspace", "bt709",
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            # Full color specification to prevent player misinterpretation
            "-x264-params", f"colorprim=bt709:transfer=bt709:colormatrix=bt709:range={source_color_range}",
        ]
    elif is_hdr and resolved == "hdr":
        # HDR → HDR: preserve metadata, encode HEVC
        x265_params = (
            "hdr-opt=1:repeat-headers=1:"
            "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:"
            f"range={source_color_range}"
        )
        args += [
            "-c:v", "libx265",
            "-crf", str(CRF),
            "-preset", PRESET,
            "-x265-params", x265_params,
        ]
    else:
        # SDR → SDR: preserve original color characteristics
        args += [
            "-c:v", "libx264",
            "-crf", str(CRF),
            "-preset", PRESET,
        ]
        
        # Apply color range settings based on source
        if source_color_range == "tv":
            args += [
                # Explicit filter to prevent range conversion (TV→TV)
                "-vf", "scale=in_range=tv:out_range=tv",
                "-pix_fmt", "yuv420p",
                "-color_range", "tv",
                # Explicit output colorspace tags (container level)
                "-colorspace", "bt709",
                "-color_primaries", "bt709",
                "-color_trc", "bt709",
                # Full color specification to prevent player misinterpretation
                "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=tv",
            ]
        elif source_color_range == "pc":
            args += [
                # Explicit filter to prevent range conversion (PC→PC)
                "-vf", "scale=in_range=pc:out_range=pc",
                "-pix_fmt", "yuvj420p",
                "-color_range", "pc",
                # Explicit output colorspace tags (container level)
                "-colorspace", "bt709",
                "-color_primaries", "bt709",
                "-color_trc", "bt709",
                # Full color specification to prevent player misinterpretation
                "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=pc",
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
    "path",
    type=click.Path(exists=True, path_type=Path),
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
@click.option(
    "--test",
    "test_duration",
    type=float,
    default=None,
    metavar="SECONDS",
    help="Test mode: only convert the first N seconds (e.g., --test 10 for quick validation).",
)
@click.option(
    "--start",
    "start_time",
    type=str,
    default=None,
    metavar="TIME",
    help="Start time for conversion (e.g., --start 01:23:45 or --start 83.5). Use with --end.",
)
@click.option(
    "--end",
    "end_time",
    type=str,
    default=None,
    metavar="TIME",
    help="End time for conversion (e.g., --end 01:24:00 or --end 100). Use with --start.",
)
@click.option(
    "--compare",
    "show_compare",
    is_flag=True,
    default=False,
    help="Show source vs output metadata comparison after conversion.",
)
def main(path: Path, mode: str, overwrite: bool, skip: bool, output_dir: Path | None, test_duration: float | None, start_time: str | None, end_time: str | None, show_compare: bool) -> None:
    """Convert PS5 4K .webm recordings to FCP-ready .mp4.
    
    PATH can be either a directory containing .webm files or a single .webm file.
    """

    if overwrite and skip:
        raise click.UsageError("--overwrite and --skip are mutually exclusive")
    
    # Validate time range options
    if test_duration and (start_time or end_time):
        raise click.UsageError("--test cannot be used with --start/--end")
    
    if (start_time and not end_time) or (end_time and not start_time):
        raise click.UsageError("--start and --end must be used together")
    
    # Parse and validate start/end times
    start_seconds = None
    end_seconds = None
    if start_time and end_time:
        try:
            start_seconds = _parse_timecode(start_time)
            end_seconds = _parse_timecode(end_time)
            if start_seconds >= end_seconds:
                raise click.UsageError(f"--start ({start_seconds}s) must be before --end ({end_seconds}s)")
        except ValueError as e:
            raise click.UsageError(str(e))

    conflict = "overwrite" if overwrite else ("skip" if skip else "prompt")

    # ── Determine if path is a file or directory ──────────────────────────────
    is_single_file = path.is_file()
    
    if is_single_file:
        if path.suffix.lower() != ".webm":
            click.echo(
                click.style("Error:", fg="red") + f" {path.name} is not a .webm file"
            )
            sys.exit(1)
        webm_files = [path]
        input_dir = path.parent
    else:
        # Directory mode
        webm_files = sorted(
            f for f in path.iterdir() if f.suffix.lower() == ".webm"
        )
        input_dir = path
        if len(webm_files) == 0:
            click.echo(
                click.style("No .webm files found in:", fg="yellow") + f" {path}"
            )
            sys.exit(0)
    
    total = len(webm_files)

    # Resolve output directory — create it if it doesn't exist
    out_dir = output_dir or input_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Resolve ffmpeg ─────────────────────────────────────────────────────────
    ffmpeg, ffprobe = find_or_download_ffmpeg()

    # ── Header ────────────────────────────────────────────────────────────────
    click.echo(click.style("PS5 WebM → MP4 Converter", fg="cyan", bold=True))
    if is_single_file:
        click.echo(f"Input     : {path.name} (single file)")
    else:
        click.echo(f"Input     : {path}")
    click.echo(f"Output    : {out_dir}")
    mode_label = f"{mode} (match source)" if mode == "auto" else mode
    click.echo(f"Mode      : {click.style(mode_label, bold=True)}")
    click.echo(
        f"Quality   : CRF {click.style(str(CRF), bold=True)}, "
        f"preset {click.style(PRESET, bold=True)}"
    )
    if test_duration:
        click.echo(
            f"Test Mode : {click.style(f'first {test_duration}s only', fg='yellow', bold=True)}"
        )
    if start_seconds is not None and end_seconds is not None:
        duration = end_seconds - start_seconds
        click.echo(
            f"Time Range: {click.style(f'{start_seconds}s to {end_seconds}s ({duration}s)', fg='magenta', bold=True)}"
        )
    click.echo(f"Conflicts : {click.style(conflict, bold=True)}")
    if show_compare:
        click.echo(f"Compare   : {click.style('enabled', fg='cyan', bold=True)}")
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
        
        # Color range detection
        source_color_range = _detect_color_range(ffprobe, webm)
        
        click.echo(
            f"  {click.style('→ Converting...', fg='cyan')}"
            f" ({source_label}→{resolved_mode.upper()}, color range: {source_color_range})"
        )

        if not is_hdr and mode == "hdr":
            click.echo(
                f"  {click.style('⚠ Source is SDR — HDR mode has no effect, encoding as SDR', fg='yellow')}"
            )

        # Encode
        args = _build_ffmpeg_args(ffmpeg, webm, mp4, is_hdr, mode, source_color_range, test_duration, start_seconds, end_seconds)
        
        try:
            result = subprocess.run(args)
        except KeyboardInterrupt:
            click.echo(f"\n\n  {click.style('⚠ Conversion interrupted', fg='yellow')}")
            if mp4.exists():
                if click.confirm(f"  Delete incomplete file {mp4.name}?", default=True):
                    mp4.unlink()
                    click.echo(f"  {click.style('✓ Deleted', fg='green')} {mp4.name}")
                else:
                    click.echo(f"  {click.style('Kept', fg='yellow')} {mp4.name} (incomplete)")
            raise  # Re-raise to exit the program

        if result.returncode == 0:
            click.echo(f"\n  {click.style('✓ Done', fg='green')} → {mp4}")
            
            # Show comparison if requested
            if show_compare:
                _show_comparison(ffprobe, webm, mp4)
            
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