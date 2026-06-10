---
name: ai-podcast
description: Creates a full AI-generated video podcast; if reference materials are provided, such as a script, reference images or portraits, and reference speech or audio, those are taken into account, otherwise they can be generated on the fly. Use this skill to generate dialogue audio and a properly lip-synced video podcast.
---

# AI Podcast

Use this skill to create a realistic video podcast from a dialogue script, speaker
images, and optional reference audio for voice cloning. If any inputs are missing,
generate them from the user's instructions or ask for clarification when speaker
identity, tone, or source material is ambiguous.

Use the bundled scripts for the deterministic parts of the pipeline:

- `scripts/generate_image.py` reads image guidelines from `SKILL.md` and
  generates portrait speaker reference images with Pruna `p-image`.
- `scripts/generate_audio.py` parses `script.txt`, optionally exports cloned voices,
  and generates per-turn WAV files.
- `scripts/resize.sh` resizes two speaker images to 810x1440 and appends
  `-810x1440` to each output filename.
- `scripts/generate_video.py` uploads speaker images and dialogue audio to Pruna,
  starts `p-video-avatar` predictions, polls them, and downloads MP4 clips.
- `scripts/stitch.sh` concatenates numbered MP4 clips into the final podcast video.

Resolve these bundled script paths relative to this skill directory. Run them
with the podcast project as the working directory so relative inputs and
`output/` paths resolve into the project. The examples below use `./scripts/...`
for this skill repository; when using the installed skill elsewhere, replace that
prefix with the resolved path to the bundled script. If the dialogue script is
not in the current working directory, pass it explicitly with `--script`.

## Inputs

The final input set should contain:

- `script.txt` with one dialogue turn per line, such as `Host: Welcome...`.
- One reference image per speaker, such as `host.png` and `guest.png`.
- Optional short reference audio per speaker, such as `host.wav` and `guest.wav`,
  only when voice cloning is desired.

Speaker names are slugified for generated file names. For example, `Main Host`
becomes `main-host`, producing files such as `001-main-host.wav`.

Expected output layout:

```text
output/
output/images/host.jpg
output/images/guest.jpg
output/safetensors/host.safetensors
output/safetensors/guest.safetensors
output/audio/001-host.wav
output/audio/002-guest.wav
output/video/001-host.mp4
output/video/002-guest.mp4
output/final/podcast-final.mp4
```

## Environment

Required host tools:

- Python 3.10+
- `uv` or `uvx` for Pocket TTS
- `ffmpeg` for final stitching
- ImageMagick `magick` for image resizing

Pruna credentials must be in `.env` in the project root:

```text
PRUNA_API_KEY=pru...
```

## Workflow

1. Confirm or create `script.txt`.
2. Confirm or create speaker reference images. If they are missing, generate
   them from the user's speaker descriptions with `scripts/generate_image.py`,
   or ask a concise clarification when identity, visual style, or required
   likeness is too ambiguous to invent.
3. Resize speaker images if they are not already 810x1440.
4. Generate dialogue audio with `scripts/generate_audio.py`.
5. Generate avatar video clips with `scripts/generate_video.py`.
6. Stitch numbered video clips with `scripts/stitch.sh`.

Before running a network or generation-heavy step, use `--dry-run` where available
to verify discovered speakers, images, voices, and output paths.

## Script Generation

If a script is provided, use it as-is unless the user asks for edits. If the script
is missing, create a short podcast script from the user's topic or instructions.
Keep it to about 4-5 exchanges unless the user requests a longer episode.

Use a line-oriented format:

```text
Host: Welcome to the podcast...
Guest: Thank you for having me...
Host: Let's start with...
```

When generating dialogue, use clear speaker names and prefer `...` for spoken
breaks instead of comma-heavy or punctuation-heavy phrasing. Save the final script
as `script.txt`.

## Image Generation

Use `scripts/generate_image.py` when one or more speaker reference images are
missing and the user has provided enough description to create them. The script
extracts `Image Prompt Guidelines` from `SKILL.md`, combines those guidelines
with the user description, and calls Pruna `p-image` to generate a portrait
image. It uses `aspect_ratio: "custom"` with explicit width and height. Default
output is `816x1440` JPG in `output/images/`, then `scripts/resize.sh` can
produce the exact 810x1440 video reference image.

### Image Prompt Guidelines

Generated speaker images should be portrait-format source references for a
realistic video podcast avatar. Use a 9:16 upper-body composition with the
speaker centered, a podcast microphone visible in front of the speaker, and a
plain clean background. Keep the face clear, the gaze natural, and the lighting
realistic. Avoid text, captions, logos, watermarks, extra people, and distracting
props. Honor the user's requested visual style; if no style is specified, use a
realistic editorial podcast portrait.

### Script Usage

Preview the prompt before spending generation time:

```bash
./scripts/generate_image.py \
  --speaker host \
  --dry-run \
  "warm, thoughtful host in their 30s, short dark hair, navy sweater"
```

Generate one image per speaker:

```bash
./scripts/generate_image.py \
  --speaker host \
  "warm, thoughtful host in their 30s, short dark hair, navy sweater"

./scripts/generate_image.py \
  --speaker guest \
  "curious technical guest in their 40s, glasses, charcoal jacket"
```

Useful options:

- `--output PATH` writes to an explicit image path.
- `--description-file PATH` reads a longer character description from a file.
- `--width PIXELS` and `--height PIXELS` set Pruna custom dimensions. Each must
  be 256-1440 pixels and a multiple of 16. Defaults are 816x1440.
- `--overwrite` replaces an existing output file.

## Reference Images

If speaker images are provided, use them. If one or more are missing, create
plain-background upper-body podcast portraits based on the user's speaker
descriptions, with a podcast microphone visible in front of each speaker. When a
missing speaker image would require inventing important identity, visual style,
or likeness details, ask the user for clarification before generating. Otherwise
choose simple podcast-portrait defaults and proceed.

The video target is 9:16, and the recommended video reference image size is
810x1440. Pruna `p-image` custom dimensions must be multiples of 16, so
`scripts/generate_image.py` defaults to 816x1440 and generated images should be
resized before video generation when exact 810x1440 inputs are needed. Check
image dimensions with:

```bash
magick identify <IMAGE>
```

Resize two speaker images with:

```bash
./scripts/resize.sh ./output/images/host.jpg ./output/images/guest.jpg
```

The resized files are written next to the originals as `host-810x1440.jpg` and
`guest-810x1440.jpg`. Use the resized images for video generation.

## Audio Generation

Use `scripts/generate_audio.py` for both cloned and default voices. It reads
`script.txt`, creates `output/audio/NNN-speaker.wav`, and writes cloned voice
states to `output/safetensors/`.

For auto-discovered reference audio, place files such as `host.wav` and
`guest.wav` near the script or in `input/`, `inputs/`, `project/`, `reference/`,
`references/`, or `audio/`, then run:

```bash
./scripts/generate_audio.py --script script.txt
```

For explicit voice-cloning references:

```bash
./scripts/generate_audio.py \
  --script script.txt \
  --reference-audio host=./project/host.wav \
  --reference-audio guest=./project/guest.wav
```

If no reference audio is available, the script uses default Pocket TTS voices.
Optionally pin default speaker genders:

```bash
./scripts/generate_audio.py \
  --script script.txt \
  --gender host=male \
  --gender guest=female
```

Useful options:

- `--voice speaker=VOICE` uses an explicit Pocket TTS voice or `hf://` URL.
- `--reference-dir DIR` adds another directory for auto-discovery.
- `--overwrite` regenerates existing WAV and safetensors files.
- `--dry-run` prints the Pocket TTS commands without running them.

If Pocket TTS needs extra dependencies for non-WAV inputs, pass a custom command:

```bash
./scripts/generate_audio.py --pocket-tts-command "uvx --with soundfile pocket-tts"
```

## Video Generation

Use `scripts/generate_video.py` instead of manual Pruna API calls. It uploads each
speaker image once per run, uploads each turn audio file, starts the Pruna
`p-video-avatar` prediction, polls until completion, and downloads each MP4 into
`output/video/`.

The script auto-discovers speaker images by slugified speaker name. It prefers
`speaker-810x1440` files and searches near the script plus `input/`, `inputs/`,
`project/`, `reference/`, `references/`, `image/`, `images/`, and `output/images/`.

Dry-run the plan first:

```bash
./scripts/generate_video.py --script script.txt --dry-run
```

Run with auto-discovered images:

```bash
./scripts/generate_video.py --script script.txt
```

Run with explicit images:

```bash
./scripts/generate_video.py \
  --script script.txt \
  --image host=./project/host-810x1440.png \
  --image guest=./project/guest-810x1440.png
```

Useful options:

- `--image-dir DIR` adds another image search directory.
- `--poll-interval SECONDS` changes Pruna status polling frequency.
- `--poll-timeout SECONDS` changes the maximum wait per video prediction.
- `--overwrite` regenerates existing MP4 clips.

## Final Video

Create the final directory and stitch numbered clips in lexical order:

```bash
mkdir -p output/final
./scripts/stitch.sh output/video output/final/podcast-final.mp4
```

`scripts/stitch.sh` expects files named like `001-host.mp4`, `002-guest.mp4`, and
so on. If the final video fails to stitch, check that all expected clips exist in
`output/video/` and that `ffmpeg` is installed.
