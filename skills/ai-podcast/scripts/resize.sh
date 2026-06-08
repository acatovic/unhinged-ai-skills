#!/usr/bin/env bash
set -euo pipefail

W=810
H=1440
BG="white"

if [[ "$#" -ne 2 ]]; then
  echo "Usage: $0 <image1> <image2>" >&2
  exit 1
fi

if ! command -v magick >/dev/null 2>&1; then
  echo "Error: ImageMagick is not installed or 'magick' is not in PATH." >&2
  echo "Install it with: brew install imagemagick" >&2
  exit 1
fi

resize_image() {
  local input="$1"

  if [[ ! -f "$input" ]]; then
    echo "Error: file not found: $input" >&2
    exit 1
  fi

  local dir
  local filename
  local base
  local ext
  local output

  dir="$(dirname "$input")"
  filename="$(basename "$input")"
  ext="${filename##*.}"
  base="${filename%.*}"

  output="${dir}/${base}-${W}x${H}.${ext}"

  magick "$input" \
    -resize "${W}x${H}" \
    -gravity center \
    -background "$BG" \
    -extent "${W}x${H}" \
    "$output"

  echo "Created: $output"
}

resize_image "$1"
resize_image "$2"
