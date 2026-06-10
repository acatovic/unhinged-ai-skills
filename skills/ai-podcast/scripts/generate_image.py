#!/usr/bin/env python3
"""Generate a portrait speaker image for the AI podcast workflow with Pruna."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_API_BASE_URL = "https://api.pruna.ai/v1"
DEFAULT_MODEL = "p-image"
DEFAULT_WIDTH = 816
DEFAULT_HEIGHT = 1440
DEFAULT_OUTPUT_DIR = Path("output/images")
DEFAULT_GUIDELINE_SECTIONS = ("Image Prompt Guidelines",)
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "speaker"


def validate_dimension(value: str) -> int:
    try:
        dimension = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("dimension must be an integer") from error

    if dimension < 256 or dimension > 1440:
        raise argparse.ArgumentTypeError(
            "Pruna p-image custom dimensions must be between 256 and 1440"
        )
    if dimension % 16 != 0:
        raise argparse.ArgumentTypeError(
            "Pruna p-image custom dimensions must be multiples of 16"
        )
    return dimension


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


def load_api_key(env_path: Path) -> str:
    load_env_file(env_path)
    api_key = os.environ.get("PRUNA_API_KEY")
    if not api_key:
        raise ValueError(
            "PRUNA_API_KEY is not set. Add it to .env or the environment."
        )
    return api_key


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
    return args.output_dir / f"{stem}.jpg"


def build_api_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def absolute_url(value: str, base_url: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme in {"http", "https"}:
        return value

    parsed_base = urllib.parse.urlparse(base_url)
    if value.startswith("/"):
        origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
        return urllib.parse.urljoin(origin, value)
    return urllib.parse.urljoin(f"{base_url.rstrip('/')}/", value)


def request_json(
    url: str,
    *,
    api_key: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    request_headers = {"apikey": api_key}
    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(
        url,
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} for {url}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Request failed for {url}: {error.reason}") from error

    if not payload:
        return {}

    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as error:
        text = payload.decode("utf-8", errors="replace")
        raise RuntimeError(f"Expected JSON from {url}, got: {text[:500]}") from error


def create_prediction(
    prompt: str, *, api_key: str, args: argparse.Namespace
) -> dict[str, Any]:
    payload = json.dumps(
        {
            "input": {
                "prompt": prompt,
                "aspect_ratio": "custom",
                "width": args.width,
                "height": args.height,
            }
        }
    ).encode("utf-8")
    url = build_api_url(args.api_base_url, "/predictions")
    return request_json(
        url,
        api_key=api_key,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Model": args.model,
        },
        body=payload,
        timeout=args.request_timeout,
    )


def iter_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        for item in value.values():
            strings.extend(iter_strings(item))
    elif isinstance(value, list):
        for item in value:
            strings.extend(iter_strings(item))
    return strings


def normalize_image_url(value: str, api_base_url: str) -> str | None:
    url = absolute_url(value, api_base_url)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None

    path = parsed.path.lower()
    if path.endswith(IMAGE_SUFFIXES):
        return url
    if "/delivery/" in path:
        normalized_path = f"{parsed.path.rstrip('/')}/output.jpg"
        return urllib.parse.urlunparse(parsed._replace(path=normalized_path))
    return None


def find_image_url(status_response: dict[str, Any], api_base_url: str) -> str | None:
    preferred_keys = ("output", "generation_url", "image", "url")
    for key in preferred_keys:
        if key not in status_response:
            continue
        for value in iter_strings(status_response[key]):
            image_url = normalize_image_url(value, api_base_url)
            if image_url is not None:
                return image_url

    for value in iter_strings(status_response):
        image_url = normalize_image_url(value, api_base_url)
        if image_url is not None:
            return image_url
    return None


def poll_prediction(
    get_url: str, *, api_key: str, args: argparse.Namespace
) -> dict[str, Any]:
    deadline = time.monotonic() + args.poll_timeout
    last_status = "unknown"

    while True:
        response = request_json(
            absolute_url(get_url, args.api_base_url),
            api_key=api_key,
            timeout=args.request_timeout,
        )
        status = str(response.get("status", "unknown")).lower()
        last_status = status
        print(f"Prediction status: {status}")

        if status == "succeeded":
            if find_image_url(response, args.api_base_url) is None:
                raise RuntimeError(
                    "Prediction succeeded but no image URL was found in response: "
                    f"{response}"
                )
            return response

        if status in {"failed", "canceled", "cancelled", "error"}:
            raise RuntimeError(f"Prediction ended with status {status}: {response}")

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out after {args.poll_timeout}s waiting for prediction. "
                f"Last status: {last_status}"
            )

        time.sleep(args.poll_interval)


def download_file(
    url: str, output_path: Path, *, api_key: str, args: argparse.Namespace
) -> None:
    print(f"Downloading: {output_path}")
    request = urllib.request.Request(url, headers={"apikey": api_key}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=args.download_timeout) as response:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} while downloading {url}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Download failed for {url}: {error.reason}") from error


def parse_args() -> tuple[argparse.Namespace, argparse.ArgumentParser]:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a portrait podcast character image with Pruna p-image. "
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
        help="Output image path. Defaults to output/images/<speaker>.jpg.",
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
        help="Optional .env file containing PRUNA_API_KEY.",
    )
    parser.add_argument(
        "--api-base-url",
        default=DEFAULT_API_BASE_URL,
        help=f"Pruna API base URL. Default: {DEFAULT_API_BASE_URL}",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Pruna model header value. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--width",
        type=validate_dimension,
        default=DEFAULT_WIDTH,
        help=f"Custom image width in pixels. Default: {DEFAULT_WIDTH}",
    )
    parser.add_argument(
        "--height",
        type=validate_dimension,
        default=DEFAULT_HEIGHT,
        help=f"Custom image height in pixels. Default: {DEFAULT_HEIGHT}",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Seconds between prediction status polls. Default: 5",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=600,
        help="Maximum seconds to wait for the image prediction. Default: 600",
    )
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=120,
        help="Timeout in seconds for API requests. Default: 120",
    )
    parser.add_argument(
        "--download-timeout",
        type=int,
        default=600,
        help="Timeout in seconds for image downloads. Default: 600",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output file.")
    parser.add_argument("--dry-run", action="store_true", help="Print the prompt without generating.")

    args = parser.parse_args()
    if args.width >= args.height:
        parser.error("width must be less than height for portrait speaker images")
    return args, parser


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
        prompt = build_prompt(description, guidelines, args.speaker)

        if args.dry_run:
            print(f"Model: {args.model}")
            print("Aspect ratio: custom")
            print(f"Width: {args.width}")
            print(f"Height: {args.height}")
            print(f"Output: {output_path}")
            print()
            print(prompt)
            return 0

        api_key = load_api_key(args.env_file)
        print(f"Generating image with {args.model} at {args.width}x{args.height}")
        response = create_prediction(prompt, api_key=api_key, args=args)
        image_url = find_image_url(response, args.api_base_url)

        if image_url is None:
            get_url = response.get("get_url")
            if not isinstance(get_url, str) or not get_url:
                raise RuntimeError(
                    f"Prediction response did not include get_url: {response}"
                )
            status_response = poll_prediction(get_url, api_key=api_key, args=args)
            image_url = find_image_url(status_response, args.api_base_url)

        if image_url is None:
            raise RuntimeError(f"Could not locate image URL: {response}")

        download_file(image_url, output_path, api_key=api_key, args=args)
        print(f"Created: {output_path}")
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
