# ps5-convert

Convert PS5 4K `.webm` recordings to a properly colour-graded `.mp4` that works in any video editor.

## The problem

The PS5 can record gameplay in two formats:

- **MP4** — works everywhere, but capped at **1080p**
- **WebM** — the only way to get **4K**, but almost no editor handles it natively

The obvious fix is to just remux WebM into an MP4 container, but that breaks in two ways:

1. **Washed-out / faded colours** — WebM footage (especially from HDR captures) carries colour space metadata that gets ignored or misread during a naive conversion. The result looks desaturated and wrong.
2. **Incompatible audio** — WebM uses Opus audio. Stuffing Opus into an MP4 container produces a file that many editors either refuse to open or stall on.

This tool does the conversion properly:

| Problem | Fix applied |
|---|---|
| Faded colours on HDR footage | Detects HDR (`smpte2084` / `arib-std-b67`) and either tone-maps to BT.709 SDR or re-encodes as HEVC with HDR metadata preserved |
| Washed-out colours on SDR footage | Correct colour space pass-through — no accidental remapping |
| Opus audio incompatibility | Re-encodes to AAC |
| VFR footage causing editor timeline issues | Forces constant frame rate (`-vsync cfr`) |
| Slow import in some editors | Moves MP4 metadata to the front of the file (`-movflags +faststart`) |

The output is a standard H.264 or HEVC `.mp4` that imports cleanly into Final Cut Pro, DaVinci Resolve, Premiere, and anything else that speaks MP4.

## Requirements

- Python 3.11+
- `ffmpeg` with `libzimg` (required for HDR→SDR tone-mapping)
  - **macOS:** `brew install ffmpeg-full`
  - **Windows/Linux:** the tool will offer to download a static build automatically if needed

## Installation

### Option A — pip (recommended)

```bash
pip install ps5-convert
```

This gives you a `ps5-convert` command globally.

### Option B — run directly from the repo

```bash
git clone https://github.com/your-username/ps5-convert
cd ps5-convert
pip install .
```

## Usage

```
ps5-convert <directory> [OPTIONS]

Arguments:
  directory     Folder containing .webm files exported from your PS5

Options:
  --mode [auto|sdr|hdr]  Output mode (default: auto)
                         auto  Match the source — HDR→HEVC, SDR→H.264
                         sdr   Force tone-map to H.264 even on HDR files
                         hdr   Force HEVC output
  --output-dir <path>    Directory to write .mp4 files into
                         (default: same folder as the source .webm files)
  --overwrite            Overwrite existing .mp4 files without prompting
  --skip                 Skip existing .mp4 files without prompting
  -h, --help             Show this message and exit
```

### Examples

```bash
# Convert a folder — auto-detects HDR/SDR per file (recommended)
ps5-convert ~/Movies/PS5/Clips/Returnal

# Output converted files to a separate folder (created automatically if needed)
ps5-convert ~/Movies/PS5/Clips/Returnal --output-dir ~/Movies/PS5/Converted

# Force everything to SDR H.264 (e.g. you want one consistent format)
ps5-convert ~/Movies/PS5/Clips/Returnal --mode sdr

# Keep HDR, encode as HEVC
ps5-convert ~/Movies/PS5/Clips/Returnal --mode hdr --output-dir ~/Movies/PS5/Converted

# Batch re-run without prompts
ps5-convert ~/Movies/PS5/Clips/Returnal --skip
```

## What gets detected automatically

- **HDR source** (`smpte2084` / `arib-std-b67` colour transfer) — tone-mapped to BT.709 in `--mode sdr`, or preserved as HEVC in `--mode hdr`
- **SDR source** — straight H.264 re-encode with correct colour pass-through
- **Missing ffmpeg** — prompts to download a static build with zscale into `~/.ps5-converter/bin/` automatically

## Output quality

Videos are encoded at **CRF 18, preset slow** — visually lossless for most footage. Edit these constants at the top of `ps5_convert.py` if you want faster encodes (`preset fast`) or smaller files (higher CRF).

## Repo structure

```
ps5-convert/
├── ps5_convert.py   # Main CLI (Python, cross-platform)
├── pyproject.toml   # Package config — pip install . works
└── README.md
```

## License

MIT