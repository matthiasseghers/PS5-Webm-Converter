#!/bin/bash
# ps5_convert.sh — Convert PS5 4K .webm recordings to .mp4
# Usage: ./ps5_convert.sh /path/to/game/folder [--mode sdr|hdr] [--overwrite|--skip]
# Example: ./ps5_convert.sh ~/Movies/PS5/CREATE/Video\ Clips/Burnout\ Paradise\ Remastered --mode sdr --skip

set -euo pipefail

# ── Colors for output ────────────────────────────────────────────────────────
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Check arguments ───────────────────────────────────────────────────────────
if [ "$#" -lt 1 ]; then
  echo -e "${BOLD}Usage:${RESET} $0 <directory> [--mode sdr|hdr] [--overwrite|--skip]"
  echo -e "  --mode sdr   Convert to SDR H.264 .mp4 (default)"
  echo -e "  --mode hdr   Preserve HDR, encode as HEVC H.265 .mp4"
  echo -e "  --overwrite  Always overwrite existing .mp4 files without prompting"
  echo -e "  --skip       Always skip existing .mp4 files without prompting"
  echo -e "  Example: $0 ~/Movies/PS5/CREATE/Video\ Clips/Burnout\ Paradise\ Remastered --mode sdr --skip"
  exit 1
fi

INPUT_DIR="$1"
MODE="sdr"
CONFLICT="prompt"
CRF=18
PRESET="slow"

# Parse optional flags
shift
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      if [[ -z "${2:-}" || ("$2" != "sdr" && "$2" != "hdr") ]]; then
        echo -e "${RED}Error:${RESET} --mode must be 'sdr' or 'hdr'"
        exit 1
      fi
      MODE="$2"
      shift 2
      ;;
    --overwrite)
      if [[ "$CONFLICT" == "skip" ]]; then
        echo -e "${RED}Error:${RESET} --overwrite and --skip are mutually exclusive"
        exit 1
      fi
      CONFLICT="overwrite"
      shift
      ;;
    --skip)
      if [[ "$CONFLICT" == "overwrite" ]]; then
        echo -e "${RED}Error:${RESET} --overwrite and --skip are mutually exclusive"
        exit 1
      fi
      CONFLICT="skip"
      shift
      ;;
    *)
      echo -e "${RED}Error:${RESET} Unknown argument: $1"
      exit 1
      ;;
  esac
done

if [ ! -d "$INPUT_DIR" ]; then
  echo -e "${RED}Error:${RESET} Directory not found: $INPUT_DIR"
  exit 1
fi

# ── Check ffmpeg-full is installed with libzimg ───────────────────────────────
if ! command -v ffmpeg &>/dev/null; then
  echo -e "${RED}Error:${RESET} ffmpeg not found. Install with: brew install ffmpeg-full"
  exit 1
fi

# if ! ffmpeg -filters 2>&1 | grep -q zscale; then
#   echo -e "${RED}Error:${RESET} Your ffmpeg build is missing 'zscale' (libzimg)."
#   echo -e "  Fix with: brew uninstall ffmpeg && brew install ffmpeg-full"
#   exit 1
# fi

# ── Find all .webm files ──────────────────────────────────────────────────────
WEBM_FILES=()
while IFS= read -r -d '' file; do
  WEBM_FILES+=("$file")
done < <(find "$INPUT_DIR" -maxdepth 1 -iname "*.webm" -print0 | sort -z)

TOTAL=${#WEBM_FILES[@]}

if [ "$TOTAL" -eq 0 ]; then
  echo -e "${YELLOW}No .webm files found in:${RESET} $INPUT_DIR"
  exit 0
fi

echo -e "${BOLD}${CYAN}PS5 WebM → MP4 Converter${RESET}"
echo -e "Directory : $INPUT_DIR"
echo -e "Mode      : ${BOLD}$MODE${RESET}"
echo -e "Quality   : CRF ${BOLD}$CRF${RESET}, preset ${BOLD}$PRESET${RESET}"
echo -e "Conflicts : ${BOLD}$CONFLICT${RESET}"
echo -e "Found     : ${BOLD}$TOTAL${RESET} .webm file(s)\n"

# ── Process each file ─────────────────────────────────────────────────────────
SKIPPED=0
CONVERTED=0
FAILED=0

for WEBM in "${WEBM_FILES[@]}"; do
  BASENAME=$(basename "$WEBM" .webm)
  MP4="${INPUT_DIR}/${BASENAME}.mp4"

  echo -e "${BOLD}[$((CONVERTED + SKIPPED + FAILED + 1))/$TOTAL]${RESET} ${BASENAME}.webm"

  # Handle existing .mp4
  if [ -f "$MP4" ]; then
    if [[ "$CONFLICT" == "skip" ]]; then
      echo -e "  ${YELLOW}⚠ Skipped${RESET} — $MP4"
      SKIPPED=$((SKIPPED + 1))
      echo ""
      continue
    elif [[ "$CONFLICT" == "prompt" ]]; then
      echo -e "  ${YELLOW}⚠ ${BASENAME}.mp4 already exists.${RESET} Overwrite? [y/N] \c"
      read -r REPLY
      if [[ "$REPLY" != "y" && "$REPLY" != "Y" ]]; then
        echo -e "  ${YELLOW}Skipped${RESET} — $MP4"
        SKIPPED=$((SKIPPED + 1))
        echo ""
        continue
      fi
    fi
    # --overwrite: fall through to encode
  fi

  COLOR_TRANSFER=$(ffprobe -v quiet -select_streams v:0 \
    -show_entries stream=color_transfer -of default=nw=1:nk=1 "$WEBM")

  IS_HDR=false
  if [[ "$COLOR_TRANSFER" == "smpte2084" || "$COLOR_TRANSFER" == "arib-std-b67" ]]; then
    IS_HDR=true
  fi

  MODE_UPPER=$(echo "$MODE" | tr '[:lower:]' '[:upper:]')
  SOURCE_LABEL=$([[ "$IS_HDR" == true ]] && echo "HDR" || echo "SDR")
  echo -e "  ${CYAN}→ Converting...${RESET} (${SOURCE_LABEL}→${MODE_UPPER})"

  # Build ffmpeg command based on source and target mode
  FFMPEG_ARGS=(-hide_banner -loglevel error -stats -y -i "$WEBM")

  if [[ "$IS_HDR" == true && "$MODE" == "sdr" ]]; then
    # HDR→SDR: tone-map with zscale
    FFMPEG_ARGS+=(
      -vf "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=2.0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
      -c:v libx264 -crf "$CRF" -preset "$PRESET"
    )
  elif [[ "$IS_HDR" == true && "$MODE" == "hdr" ]]; then
    # HDR→HDR: preserve HDR metadata, use HEVC
    FFMPEG_ARGS+=(
      -c:v libx265 -crf "$CRF" -preset "$PRESET"
      -x265-params "hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc"
    )
  else
    # SDR→SDR or SDR→HDR (no tone mapping possible): straight re-encode
    if [[ "$MODE" == "hdr" ]]; then
      echo -e "  ${YELLOW}⚠ Source is SDR — HDR mode has no effect, encoding as SDR${RESET}"
    fi
    FFMPEG_ARGS+=(-c:v libx264 -crf "$CRF" -preset "$PRESET")
  fi

  FFMPEG_ARGS+=(-c:a copy "$MP4")

  if ffmpeg "${FFMPEG_ARGS[@]}" 2>&1; then
    echo -e "\n  ${GREEN}✓ Done${RESET} → $MP4"
    CONVERTED=$((CONVERTED + 1))
  else
    echo -e "\n  ${RED}✗ Failed${RESET} — check the error above"
    # Remove partial output file if it exists
    [ -f "$MP4" ] && rm "$MP4"
    FAILED=$((FAILED + 1))
  fi

  echo ""
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo -e "${BOLD}─────────────────────────────${RESET}"
echo -e "${GREEN}Converted : $CONVERTED${RESET}"
echo -e "${YELLOW}Skipped   : $SKIPPED${RESET} (already existed)"
[ "$FAILED" -gt 0 ] && echo -e "${RED}Failed    : $FAILED${RESET}"
echo -e "${BOLD}─────────────────────────────${RESET}"
