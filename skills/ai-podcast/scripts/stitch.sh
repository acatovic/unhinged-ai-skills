#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <input-directory> <output-video.mp4>" >&2
  exit 1
}

[[ $# -eq 2 ]] || usage

input_dir="${1%/}"
output_file="$2"

command -v ffmpeg >/dev/null 2>&1 || {
  echo "Error: ffmpeg not found in PATH" >&2
  exit 1
}

[[ -d "$input_dir" ]] || {
  echo "Error: input directory does not exist: $input_dir" >&2
  exit 1
}

concat_list="$(mktemp "${TMPDIR:-/tmp}/ffmpeg-concat.XXXXXX.txt")"
trap 'rm -f "$concat_list"' EXIT

count=0

while IFS= read -r file; do
  # Convert to absolute path
  dir="$(cd "$(dirname "$file")" && pwd -P)"
  base="$(basename "$file")"
  abs_path="$dir/$base"

  # Escape single quotes for FFmpeg concat file format
  escaped_path="$(printf "%s" "$abs_path" | sed "s/'/'\\\\''/g")"

  printf "file '%s'\n" "$escaped_path" >> "$concat_list"
  count=$((count + 1))
done < <(
  find "$input_dir" -maxdepth 1 -type f -name '[0-9][0-9][0-9]-*.mp4' | LC_ALL=C sort
)

if [[ "$count" -eq 0 ]]; then
  echo "Error: no matching files found in $input_dir" >&2
  echo "Expected names like: 001-something.mp4, 002-something.mp4, ..." >&2
  exit 1
fi

echo "Found $count videos:"
cat "$concat_list"
echo

ffmpeg \
  -f concat \
  -safe 0 \
  -i "$concat_list" \
  -c copy \
  "$output_file"

echo
echo "Created: $output_file"
