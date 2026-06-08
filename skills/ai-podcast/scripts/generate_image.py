#!/usr/bin/env python3
"""Generate a portrait speaker image for the AI podcast workflow."""

from __future__ import annotations

import argparse
import base64
import os
import re
import sys
from pathlib import Path


DEFAULT_MODEL = "gpt-image-2"
DEFAULT_QUALITY = "medium"
DEFAULT_SIZE = "1024x1536"
DEFAULT_OUTPUT_DIR = Path("output/images")
DEFAULT_GUIDELINE_SECTIONS = ("Image Prompt Guidelines",)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "speaker"


def validate_portrait_size(value: str) -> str:
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", value)
    if not match:
        raise argparse.ArgumentTypeError("size must use WIDTHxHEIGHT, such as 1024x1536")

    width = int(match.group(1))
    height = int(match.group(2))
    pixels = width * height

    if width >= height:
        raise argparse.ArgumentTypeError("size must be portrait, with width less than height")
    if width % 16 != 0 or height % 16 != 0:
        raise argparse.ArgumentTypeError("gpt-image-2 size edges must be multiples of 16")
    if max(width, height) >= 3840:
        raise argparse.ArgumentTypeError("gpt-image-2 maximum edge length must be less than 3840")
    if height / width > 3:
        raise argparse.ArgumentTypeError("gpt-image-2 aspect ratio must not exceed 3:1")
    if pixels < 655_360 or pixels > 8_294_400:
        raise argparse.ArgumentTypeError(
            "gpt-image-2 total pixels must be between 655360 and 8294400"
        )

    return value


def read_description(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    parts: list[str] = []

    if args.description_file is not None:
        if not args.description_file.exists():
            parser.error(f"description file not found: {args.description_file}")
        parts.append(args.description_file.read_text(encoding="utf-8").strip())

    if args.description_words:
        parts.append(" ".join(args.description_words).strip())

    description = "\n\n".join(part for part in parts if part)
    if not description:
        parser.error("provide a character description or --description-file")
    return description


def parse_env_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export ") :].strip()
    if "=" not in line:
        return None

    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip().strip("'\"")
    if not key:
        return None
    return key, value


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            parsed = parse_env_line(raw_line)
            if parsed is None:
                continue
            key, value = parsed
            os.environ.setdefault(key, value)


def normalize_heading(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower()


def extract_markdown_section(markdown: str, heading: str) -> str | None:
    target = normalize_heading(heading)
    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    capture = False
    start_level = 0
    lines: list[str] = []

    for line in markdown.splitlines():
        match = heading_re.match(line)
        if match:
            level = len(match.group(1))
            title = normalize_heading(match.group(2))

            if capture and level <= start_level:
                break
            if not capture and title == target:
                capture = True
                start_level = level

        if capture:
            lines.append(line)

    section = "\n".join(lines).strip()
    return section or None


def load_guidelines(skill_file: Path, section_names: list[str]) -> str:
    if not skill_file.exists():
        raise FileNotFoundError(f"SKILL.md not found: {skill_file}")

    markdown = skill_file.read_text(encoding="utf-8")
    sections: list[str] = []

    for section_name in section_names:
        section = extract_markdown_section(markdown, section_name)
        if section is not None:
            sections.append(section)

    if sections:
        return "\n\n".join(sections)

    return (
        "Generate a portrait-format upper-body podcast speaker image on a plain "
        "background, with a podcast microphone visible in front of the speaker."
    )


def build_prompt(description: str, guidelines: str, speaker: str | None) -> str:
    speaker_line = f"Speaker name: {speaker}\n" if speaker else ""
    return f"""Create a portrait-format podcast character reference image.

Purpose: source image for a realistic AI video podcast avatar.
{speaker_line}
User character description:
{description}

Guidelines extracted from SKILL.md:
{guidelines}

Output requirements:
- Portrait 9:16 composition, upper-body framing, speaker centered.
- Podcast microphone visible in front of the speaker.
- Plain, clean background suitable for resizing to 810x1440.
- Clear face, natural gaze, realistic lighting, and enough shoulder/torso room for video-avatar cropping.
- No text, captions, logos, watermarks, extra people, or distracting props.
- Honor the user's requested visual style; if no style is specified, use a realistic editorial podcast portrait.
"""


def default_output_path(args: argparse.Namespace) -> Path:
    stem = slugify(args.speaker or "podcast-character")
    return args.output_dir / f"{stem}.png"


def save_image_result(result: object, output_path: Path) -> None:
    data = getattr(result, "data", None)
    if not data:
        raise RuntimeError("OpenAI image response did not include image data")

    first_image = data[0]
    image_base64 = getattr(first_image, "b64_json", None)
    if not image_base64:
        raise RuntimeError("OpenAI image response did not include b64_json data")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(base64.b64decode(image_base64))


def parse_args() -> tuple[argparse.Namespace, argparse.ArgumentParser]:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a portrait podcast character image with OpenAI gpt-image-2. "
            "The prompt includes image guidelines extracted from SKILL.md."
        )
    )
    parser.add_argument(
        "description_words",
        nargs="*",
        help="Character description. Unquoted words are joined into one description.",
    )
    parser.add_argument(
        "--description-file",
        type=Path,
        help="Read the character description from a text file.",
    )
    parser.add_argument("--speaker", help="Speaker name used in the prompt and default filename.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output PNG path. Defaults to output/images/<speaker>.png.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Default output directory when --output is omitted.",
    )
    parser.add_argument(
        "--skill-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "SKILL.md",
        help="SKILL.md to extract image-generation guidelines from.",
    )
    parser.add_argument(
        "--guideline-section",
        action="append",
        help=(
            "SKILL.md section heading to include in the prompt. "
            "Defaults to Image Prompt Guidelines."
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Optional .env file containing OPENAI_API_KEY.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI image model.")
    parser.add_argument(
        "--quality",
        choices=("low", "medium", "high"),
        default=DEFAULT_QUALITY,
        help="OpenAI image generation quality.",
    )
    parser.add_argument(
        "--size",
        type=validate_portrait_size,
        default=DEFAULT_SIZE,
        help="Portrait output size. Default: 1024x1536.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output file.")
    parser.add_argument("--dry-run", action="store_true", help="Print the prompt without generating.")
    return parser.parse_args(), parser


def main() -> int:
    args, parser = parse_args()
    description = read_description(args, parser)
    section_names = args.guideline_section or list(DEFAULT_GUIDELINE_SECTIONS)
    output_path = args.output or default_output_path(args)

    if output_path.exists() and not args.overwrite and not args.dry_run:
        print(f"Error: output already exists: {output_path}", file=sys.stderr)
        print("Pass --overwrite to replace it.", file=sys.stderr)
        return 1

    try:
        guidelines = load_guidelines(args.skill_file, section_names)
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    prompt = build_prompt(description, guidelines, args.speaker)

    if args.dry_run:
        print(f"Model: {args.model}")
        print(f"Size: {args.size}")
        print(f"Quality: {args.quality}")
        print(f"Output: {output_path}")
        print()
        print(prompt)
        return 0

    load_env_file(args.env_file)
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "Error: OPENAI_API_KEY is not set. Add it to .env or the environment.",
            file=sys.stderr,
        )
        return 1

    try:
        from openai import OpenAI
    except ImportError:
        print(
            "Error: the openai Python package is not installed. "
            "Install it with: python3 -m pip install openai",
            file=sys.stderr,
        )
        return 1

    client = OpenAI()
    result = client.images.generate(
        model=args.model,
        prompt=prompt,
        size=args.size,
        quality=args.quality,
    )
    save_image_result(result, output_path)
    print(f"Created: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
