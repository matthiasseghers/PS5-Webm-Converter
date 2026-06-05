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
| Lifted blacks / washed-out SDR footage | Explicit color range preservation filter prevents ffmpeg from silently expanding TV range (16-235) to PC range (0-255) |
| Incorrect color interpretation | Full color matrix specification (colorspace, primaries, transfer, range) embedded in both container metadata and H.264/HEVC bitstream |
| Opus audio incompatibility | Re-encodes to AAC |
| VFR footage causing editor timeline issues | Forces constant frame rate (`-vsync cfr`) |
| Slow import in some editors | Moves MP4 metadata to the front of the file (`-movflags +faststart`) |

The output is a standard H.264 or HEVC `.mp4` that imports cleanly into Final Cut Pro, DaVinci Resolve, Premiere, and anything else that speaks MP4.

## Visual comparison

The following examples from Burnout Paradise Remastered gameplay (PS5, 4K HDR) show the difference between naive conversion (which lifts blacks and washes out colors) and ps5-convert's proper color handling.

> **Note:** While these examples use Burnout Paradise footage, the tool works with any PS5 WebM recording regardless of game or HDR/SDR mode. Contributions of comparison screenshots from other games are welcome!

### Example 1: Dark shadows and reflections

<table>
<tr>
  <td align="center"><b>Original WebM</b></td>
  <td align="center"><b>Naive conversion (lifted blacks)</b></td>
  <td align="center"><b>ps5-convert (correct)</b></td>
</tr>
<tr>
  <td><img src="examples/screenshots/burnout1-source.png" width="100%"></td>
  <td><img src="examples/screenshots/burnout1-bad.png" width="100%"></td>
  <td><img src="examples/screenshots/burnout1-fixed.png" width="100%"></td>
</tr>
</table>

### Example 2: Midday city driving

<table>
<tr>
  <td align="center"><b>Original WebM</b></td>
  <td align="center"><b>Naive conversion (lifted blacks)</b></td>
  <td align="center"><b>ps5-convert (correct)</b></td>
</tr>
<tr>
  <td><img src="examples/screenshots/burnout2-source.png" width="100%"></td>
  <td><img src="examples/screenshots/burnout2-bad.png" width="100%"></td>
  <td><img src="examples/screenshots/burnout2-fixed.png" width="100%"></td>
</tr>
</table>

### Example 3: Building shadows with bright road ahead

<table>
<tr>
  <td align="center"><b>Original WebM</b></td>
  <td align="center"><b>Naive conversion (lifted blacks)</b></td>
  <td align="center"><b>ps5-convert (correct)</b></td>
</tr>
<tr>
  <td><img src="examples/screenshots/burnout3-source.png" width="100%"></td>
  <td><img src="examples/screenshots/burnout3-bad.png" width="100%"></td>
  <td><img src="examples/screenshots/burnout3-fixed.png" width="100%"></td>
</tr>
</table>

### Example 4: Heavy clouds and overcast weather

<table>
<tr>
  <td align="center"><b>Original WebM</b></td>
  <td align="center"><b>Naive conversion (lifted blacks)</b></td>
  <td align="center"><b>ps5-convert (correct)</b></td>
</tr>
<tr>
  <td><img src="examples/screenshots/burnout4-source.png" width="100%"></td>
  <td><img src="examples/screenshots/burnout4-bad.png" width="100%"></td>
  <td><img src="examples/screenshots/burnout4-fixed.png" width="100%"></td>
</tr>
</table>

### Example 5: Hillside road with natural lighting

<table>
<tr>
  <td align="center"><b>Original WebM</b></td>
  <td align="center"><b>Naive conversion (lifted blacks)</b></td>
  <td align="center"><b>ps5-convert (correct)</b></td>
</tr>
<tr>
  <td><img src="examples/screenshots/burnout5-source.png" width="100%"></td>
  <td><img src="examples/screenshots/burnout5-bad.png" width="100%"></td>
  <td><img src="examples/screenshots/burnout5-fixed.png" width="100%"></td>
</tr>
</table>

**Note the difference:** In the naive conversion, blacks appear gray/washed out, reducing contrast and color depth. ps5-convert preserves the original's true blacks and color accuracy by explicitly maintaining the TV color range (16-235) throughout the conversion process.

## Requirements

- Python 3.11+
- `ffmpeg` with `libzimg` (required for HDR→SDR tone-mapping)
  - **macOS:** `brew install ffmpeg-full`
  - **Windows/Linux:** the tool will offer to download a static build automatically if needed

## Installation

### Option 1: Using venv (recommended for macOS)

```bash
# Clone the repository
git clone https://github.com/your-username/ps5-convert
cd ps5-convert

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the package
pip install -e .

# Now you can use it (remember to activate the venv each time)
ps5-convert --help
```

**Usage:** Activate the venv before each use:
```bash
source /path/to/ps5-convert/.venv/bin/activate
ps5-convert /path/to/clips
```

### Option 2: Using pipx (install once, use everywhere)

```bash
# Install pipx first
brew install pipx

# Install ps5-convert
pipx install /path/to/ps5-convert

# Now available globally, no activation needed
ps5-convert /path/to/clips
```

### Option 3: Quick alias (no activation needed)

Add to your `~/.zshrc`:
```bash
alias ps5-convert='/path/to/ps5-convert/.venv/bin/ps5-convert'
```

Then reload: `source ~/.zshrc`

## Usage

```
ps5-convert <path> [OPTIONS]

Arguments:
  path          A .webm file or a folder containing .webm files

Options:
  --mode [auto|sdr|hdr]  Output mode (default: auto)
                         auto  Match the source — HDR→HEVC, SDR→H.264
                         sdr   Force tone-map to H.264 even on HDR files
                         hdr   Force HEVC output
  --output-dir <path>    Directory to write .mp4 files into
                         (default: same folder as source)
  --test SECONDS         Test mode: only convert first N seconds (e.g., --test 10)
  --start TIME           Start time for conversion (e.g., --start 01:23:45 or --start 83.5)
                         Use with --end to extract specific segments
  --end TIME             End time for conversion (e.g., --end 01:24:00 or --end 100)
                         Use with --start to extract specific segments
  --compare              Show source vs output metadata comparison after conversion
  --overwrite            Overwrite existing .mp4 files without prompting
  --skip                 Skip existing .mp4 files without prompting
  -h, --help             Show this message and exit
```

### Examples

**Convert a single file:**
```bash
# Convert one clip with auto-detected HDR/SDR mode
ps5-convert ~/Movies/PS5/Clips/MyClip.webm

# Convert with custom output location
ps5-convert ~/Movies/PS5/Clips/MyClip.webm --output-dir ~/Desktop/Converted

# Force SDR tone-mapping
ps5-convert ~/Movies/PS5/Clips/MyClip.webm --mode sdr
```

**Convert a whole directory:**
```bash
# Convert a folder — auto-detects HDR/SDR per file (recommended)
ps5-convert ~/Movies/PS5/Clips/Returnal

# Output converted files to a separate folder (created automatically if needed)
ps5-convert ~/Movies/PS5/Clips/Returnal --output-dir ~/Movies/PS5/Converted

# Force everything to SDR H.264
ps5-convert ~/Movies/PS5/Clips/Returnal --mode sdr

# Keep HDR, encode as HEVC
ps5-convert ~/Movies/PS5/Clips/Returnal --mode hdr

# Batch re-run without prompts
ps5-convert ~/Movies/PS5/Clips/Returnal --skip
```

**Testing and validation:**
```bash
# Quick test: convert first 10 seconds only
ps5-convert ~/Movies/PS5/Clips/MyClip.webm --test 10

# Extract specific segment (night scene from 1:23:45 to 1:25:30)
ps5-convert ~/Movies/PS5/Clips/MyClip.webm --start 01:23:45 --end 01:25:30

# Extract segment using plain seconds (from 83.5s to 100s)
ps5-convert ~/Movies/PS5/Clips/MyClip.webm --start 83.5 --end 100

# Extract segment with MM:SS format
ps5-convert ~/Movies/PS5/Clips/MyClip.webm --start 05:30 --end 06:15.5

# Compare source vs output metadata
ps5-convert ~/Movies/PS5/Clips/MyClip.webm --compare

# Combine test + compare for rapid iteration
ps5-convert ~/Movies/PS5/Clips/MyClip.webm --test 10 --compare
```

## What gets detected automatically

- **HDR source** (`smpte2084` / `arib-std-b67` colour transfer) — tone-mapped to BT.709 in `--mode sdr`, or preserved as HEVC in `--mode hdr`
- **SDR source** — straight H.264 re-encode with correct colour pass-through
- **Color range** — auto-detects TV (16-235) vs PC (0-255) range and preserves it correctly
- **Missing ffmpeg** — prompts to download a static build with zscale into `~/.ps5-converter/bin/` automatically

## Interrupt handling

If you interrupt a conversion (Ctrl+C), the tool will:
1. Stop the conversion immediately
2. Prompt whether to delete the incomplete MP4 file (defaults to yes)
3. Allow you to keep the partial file if needed for inspection

This prevents incomplete files from cluttering your output directory.

## Output quality

Videos are encoded at **CRF 18, preset slow** — visually lossless quality ideal for editing in Final Cut Pro. This balances high quality with reasonable file sizes and encoding time.

**To adjust quality settings**, edit these constants at the top of `ps5_convert.py`:
```python
CRF = 18        # Lower = higher quality (18 = visually lossless, 23 = default)
PRESET = "slow" # slow/medium/fast - slower = better compression efficiency
```

### HDR→SDR tone mapping

The tool uses optimized tone mapping settings for PS5 HDR content:

- **Mobius tone mapper** — preserves highlight detail better than alternatives
- **1000 nits peak luminance** — matches PS5 HDR10 target brightness
- **No desaturation** — maintains original color accuracy and contrast
- **Smooth highlight transitions** — prevents crushed bright areas

These settings produce SDR output that closely matches the original HDR appearance when viewed on an SDR display.

**To fine-tune tone mapping**, edit these constants at the top of `ps5_convert.py`:
```python
TONEMAP_NPL = 1000      # Peak luminance (600-1500 nits)
TONEMAP_TYPE = "mobius" # Try: mobius, hable, reinhard
TONEMAP_PARAM = 0.3     # Mobius transition (0.1-0.5, lower = more highlights)
TONEMAP_DESAT = 0       # Desaturation (0-2, 0 = none)
```

**Testing workflow:**
1. Test with current settings: `ps5-convert clip.webm --test 10 --compare`
2. Adjust tone mapping constants if needed
3. Test again until satisfied
4. Convert full file(s)

### Color accuracy technical details

The tool implements multiple layers of color specification to ensure accurate black levels and color reproduction:

1. **Auto-detection** — Detects source color range (TV limited vs PC full) from metadata
2. **Explicit range preservation** — Uses `scale=in_range=tv:out_range=tv` filter to prevent ffmpeg from silently expanding pixel values during encoding
3. **Container-level metadata** — Sets `-colorspace`, `-color_primaries`, `-color_trc`, and `-color_range` flags
4. **Bitstream-level metadata** — Embeds color information directly in H.264/HEVC via x264/x265 params
5. **Pixel format enforcement** — Uses `yuv420p` (TV range) or `yuvj420p` (PC range) based on source

This comprehensive approach ensures players like Final Cut Pro, IINA, and VLC correctly interpret colors without lifted blacks or washed-out appearance.

## Repo structure

```
ps5-convert/
├── ps5_convert.py         # Main CLI (Python, cross-platform)
├── pyproject.toml         # Package config — pip install . works
├── requirements.txt       # Dependencies
├── .github/workflows/     # CI automation
│   └── ci.yml            # Build validation on push
└── README.md
```

## License

MIT