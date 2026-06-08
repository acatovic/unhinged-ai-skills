#!/usr/bin/env python3
"""Generate per-turn avatar videos with the Pruna AI API.

This script follows the ai-podcast skill layout:

    script.txt
    output/audio/001-host.wav
    output/audio/002-guest.wav
    output/video/001-host.mp4

For each turn, it uploads the speaker image and turn audio, starts a
p-video-avatar prediction, polls until completion, then downloads the MP4.
Speaker images are uploaded once per run and reused across that speaker's turns.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_API_BASE_URL = "https://api.pruna.ai/v1"
DEFAULT_MODEL = "p-video-avatar"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
DEFAULT_IMAGE_DIRS = (
    ".",
    "input",
    "inputs",
    "project",
    "reference",
    "references",
    "image",
    "images",
    "output/images",
)


@dataclass(frozen=True)
class Turn:
    number: int
    speaker: str
    speaker_key: str
    text: str


@dataclass(frozen=True)
class VideoTurn:
    turn: Turn
    image_path: Path
    audio_path: Path
    output_path: Path


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "speaker"


def parse_key_value(raw: str, option_name: str) -> tuple[str, str]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(
            f"{option_name} must use SPEAKER=value format, got: {raw!r}"
        )

    key, value = raw.split("=", 1)
    key = slugify(key)
    value = value.strip()
    if not value:
        raise argparse.ArgumentTypeError(f"{option_name} value cannot be empty")
    return key, value


def parse_mapping(values: list[str], option_name: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw in values:
        key, value = parse_key_value(raw, option_name)
        mapping[key] = value
    return mapping


def parse_script(script_path: Path) -> list[Turn]:
    if not script_path.exists():
        raise FileNotFoundError(f"Script file not found: {script_path}")

    turns: list[Turn] = []
    with script_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                raise ValueError(
                    f"{script_path}:{line_number}: expected 'Speaker: text'"
                )

            speaker, text = line.split(":", 1)
            speaker = speaker.strip()
            text = text.strip()
            if not speaker:
                raise ValueError(f"{script_path}:{line_number}: missing speaker name")
            if not text:
                raise ValueError(f"{script_path}:{line_number}: missing turn text")

            turns.append(
                Turn(
                    number=len(turns) + 1,
                    speaker=speaker,
                    speaker_key=slugify(speaker),
                    text=text,
                )
            )

    if not turns:
        raise ValueError(f"No dialogue turns found in {script_path}")
    return turns


def ordered_speakers(turns: list[Turn]) -> list[tuple[str, str]]:
    speakers: dict[str, str] = {}
    for turn in turns:
        speakers.setdefault(turn.speaker_key, turn.speaker)
    return [(key, speaker) for key, speaker in speakers.items()]


def resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


def image_search_dirs(
    cwd: Path, script_path: Path, requested_dirs: list[str]
) -> list[Path]:
    dirs: list[Path] = []

    def add(path: Path) -> None:
        path = path.expanduser()
        if not path.is_absolute():
            path = cwd / path
        path = path.resolve()
        if path not in dirs:
            dirs.append(path)

    add(script_path.parent)
    for directory in DEFAULT_IMAGE_DIRS:
        add(Path(directory))
    for directory in requested_dirs:
        add(Path(directory))

    return [directory for directory in dirs if directory.exists() and directory.is_dir()]


def find_speaker_image(
    speaker: str, speaker_key: str, search_dirs: list[Path]
) -> Path | None:
    stems = [
        f"{speaker_key}-810x1440",
        f"{speaker_key.replace('-', '_')}-810x1440",
        speaker_key,
        speaker_key.replace("-", "_"),
        speaker.strip(),
        speaker.strip().lower(),
    ]

    unique_stems: list[str] = []
    for stem in stems:
        if not stem or "/" in stem or "\\" in stem:
            continue
        if stem not in unique_stems:
            unique_stems.append(stem)

    for directory in search_dirs:
        for stem in unique_stems:
            for suffix in IMAGE_SUFFIXES:
                candidate = directory / f"{stem}{suffix}"
                if candidate.exists() and candidate.is_file():
                    return candidate
    return None


def build_image_map(
    turns: list[Turn], args: argparse.Namespace, script_path: Path
) -> dict[str, Path]:
    cwd = Path.cwd()
    explicit_images = {
        key: resolve_path(value, cwd)
        for key, value in parse_mapping(args.image, "--image").items()
    }
    search_dirs = image_search_dirs(cwd, script_path, args.image_dir)

    images: dict[str, Path] = {}
    missing: list[str] = []

    for speaker_key, speaker in ordered_speakers(turns):
        image_path = explicit_images.get(speaker_key)
        if image_path is None:
            image_path = find_speaker_image(speaker, speaker_key, search_dirs)

        if image_path is None:
            missing.append(f"{speaker} ({speaker_key})")
            continue
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found for {speaker}: {image_path}")
        if not image_path.is_file():
            raise ValueError(f"Image is not a file for {speaker}: {image_path}")

        images[speaker_key] = image_path

    if missing:
        joined = ", ".join(missing)
        raise FileNotFoundError(
            "Could not find reference image for: "
            f"{joined}. Pass explicit paths with --image speaker=path."
        )

    return images


def build_video_turns(
    turns: list[Turn], image_map: dict[str, Path], args: argparse.Namespace
) -> list[VideoTurn]:
    video_turns: list[VideoTurn] = []
    for turn in turns:
        audio_path = args.audio_dir / f"{turn.number:03d}-{turn.speaker_key}.wav"
        if not audio_path.exists():
            raise FileNotFoundError(f"Missing audio for turn {turn.number}: {audio_path}")
        if not audio_path.is_file():
            raise ValueError(f"Audio path is not a file: {audio_path}")

        video_turns.append(
            VideoTurn(
                turn=turn,
                image_path=image_map[turn.speaker_key],
                audio_path=audio_path,
                output_path=args.video_dir / f"{turn.number:03d}-{turn.speaker_key}.mp4",
            )
        )
    return video_turns


def read_env_file(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        raise FileNotFoundError(f"Environment file not found: {env_path}")

    values: dict[str, str] = {}
    with env_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                raise ValueError(f"{env_path}:{line_number}: expected KEY=value")

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                raise ValueError(f"{env_path}:{line_number}: missing environment key")
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value.startswith(("'", '"'))
            ):
                value = value[1:-1]
            values[key] = value
    return values


def load_api_key(env_path: Path) -> str:
    env_values = read_env_file(env_path)
    api_key = env_values.get("PRUNA_API_KEY") or os.environ.get("PRUNA_API_KEY")
    if not api_key:
        raise ValueError(f"PRUNA_API_KEY is missing from {env_path}")
    return api_key


def build_api_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


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


def multipart_body(field_name: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----codex-pruna-{int(time.time() * 1000)}"
    filename = file_path.name
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    chunks = [
        f"--{boundary}\r\n".encode("utf-8"),
        (
            f'Content-Disposition: form-data; name="{field_name}"; '
            f'filename="{filename}"\r\n'
        ).encode("utf-8"),
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
        file_path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ]
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def upload_file(file_path: Path, *, api_key: str, args: argparse.Namespace) -> str:
    body, content_type = multipart_body("content", file_path)
    url = build_api_url(args.api_base_url, "/files")
    print(f"Uploading: {file_path}")
    response = request_json(
        url,
        api_key=api_key,
        method="POST",
        headers={"Content-Type": content_type},
        body=body,
        timeout=args.request_timeout,
    )

    file_url = response.get("urls", {}).get("get")
    if not isinstance(file_url, str) or not file_url:
        raise RuntimeError(f"Upload response did not include urls.get: {response}")
    return file_url


def create_prediction(
    *,
    image_url: str,
    audio_url: str,
    api_key: str,
    args: argparse.Namespace,
) -> str:
    payload = json.dumps({"input": {"image": image_url, "audio": audio_url}}).encode(
        "utf-8"
    )
    url = build_api_url(args.api_base_url, "/predictions")
    response = request_json(
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

    get_url = response.get("get_url")
    if not isinstance(get_url, str) or not get_url:
        raise RuntimeError(f"Prediction response did not include get_url: {response}")
    return get_url


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


def normalize_video_url(value: str) -> str | None:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return None
    path = parsed.path.lower()
    if path.endswith(".mp4"):
        return value
    if "/delivery/" in path:
        normalized_path = f"{parsed.path.rstrip('/')}/output.mp4"
        return urllib.parse.urlunparse(parsed._replace(path=normalized_path))
    return None


def find_video_url(status_response: dict[str, Any]) -> str | None:
    preferred_keys = ("output", "generation_url", "video", "url")
    for key in preferred_keys:
        if key not in status_response:
            continue
        for value in iter_strings(status_response[key]):
            video_url = normalize_video_url(value)
            if video_url is not None:
                return video_url

    for value in iter_strings(status_response):
        video_url = normalize_video_url(value)
        if video_url is not None:
            return video_url
    return None


def poll_prediction(
    get_url: str, *, api_key: str, args: argparse.Namespace
) -> dict[str, Any]:
    deadline = time.monotonic() + args.poll_timeout
    last_status = "unknown"

    while True:
        response = request_json(
            get_url,
            api_key=api_key,
            timeout=args.request_timeout,
        )
        status = str(response.get("status", "unknown")).lower()
        last_status = status
        print(f"Prediction status: {status}")

        if status == "succeeded":
            if find_video_url(response) is None:
                raise RuntimeError(
                    "Prediction succeeded but no MP4 URL was found in response: "
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


def print_plan(video_turns: list[VideoTurn]) -> None:
    print(f"Video turns: {len(video_turns)}")
    speakers: dict[str, Path] = {}
    for video_turn in video_turns:
        speakers.setdefault(video_turn.turn.speaker_key, video_turn.image_path)

    print("Speaker images:")
    for speaker_key, image_path in speakers.items():
        print(f"  - {speaker_key}: {image_path}")
    print("Outputs:")
    for video_turn in video_turns:
        print(
            "  - "
            f"{video_turn.audio_path} + {video_turn.image_path} -> "
            f"{video_turn.output_path}"
        )


def generate_videos(video_turns: list[VideoTurn], api_key: str, args: argparse.Namespace) -> None:
    args.video_dir.mkdir(parents=True, exist_ok=True)
    image_uploads: dict[Path, str] = {}

    for video_turn in video_turns:
        if video_turn.output_path.exists() and not args.overwrite:
            print(f"Skipping existing video: {video_turn.output_path}")
            continue

        print(
            f"Generating turn {video_turn.turn.number:03d} "
            f"({video_turn.turn.speaker_key})"
        )

        image_url = image_uploads.get(video_turn.image_path)
        if image_url is None:
            image_url = upload_file(video_turn.image_path, api_key=api_key, args=args)
            image_uploads[video_turn.image_path] = image_url

        audio_url = upload_file(video_turn.audio_path, api_key=api_key, args=args)
        get_url = create_prediction(
            image_url=image_url,
            audio_url=audio_url,
            api_key=api_key,
            args=args,
        )
        status_response = poll_prediction(get_url, api_key=api_key, args=args)
        video_url = find_video_url(status_response)
        if video_url is None:
            raise RuntimeError(f"Could not locate video URL: {status_response}")

        download_file(video_url, video_turn.output_path, api_key=api_key, args=args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate per-turn Pruna avatar MP4 files from podcast audio."
    )
    parser.add_argument(
        "--script",
        type=Path,
        default=Path("script.txt"),
        help="Dialogue script with lines like 'Host: text'. Default: script.txt",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Environment file containing PRUNA_API_KEY. Default: .env",
    )
    parser.add_argument(
        "--audio-dir",
        type=Path,
        default=Path("output/audio"),
        help="Directory containing generated turn WAV files. Default: output/audio",
    )
    parser.add_argument(
        "--video-dir",
        type=Path,
        default=Path("output/video"),
        help="Directory for generated MP4 files. Default: output/video",
    )
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="SPEAKER=PATH",
        help=(
            "Reference image for a speaker. Can be repeated. "
            "Example: --image host=host-810x1440.png"
        ),
    )
    parser.add_argument(
        "--image-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="Extra directory to search for auto-discovered speaker image files.",
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
        "--poll-interval",
        type=float,
        default=10.0,
        help="Seconds between prediction status polls. Default: 10",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=3600,
        help="Maximum seconds to wait for each prediction. Default: 3600",
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
        help="Timeout in seconds for MP4 downloads. Default: 600",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate videos even when output MP4 files already exist.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without uploading files or calling Pruna.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        script_path = args.script.expanduser()
        if not script_path.is_absolute():
            script_path = Path.cwd() / script_path
        script_path = script_path.resolve()

        args.env_file = args.env_file.expanduser()
        if not args.env_file.is_absolute():
            args.env_file = Path.cwd() / args.env_file
        args.env_file = args.env_file.resolve()

        args.audio_dir = args.audio_dir.expanduser()
        args.video_dir = args.video_dir.expanduser()

        turns = parse_script(script_path)
        image_map = build_image_map(turns, args, script_path)
        video_turns = build_video_turns(turns, image_map, args)
        print_plan(video_turns)

        if args.dry_run:
            return 0

        api_key = load_api_key(args.env_file)
        generate_videos(video_turns, api_key, args)
    except (
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
        argparse.ArgumentTypeError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
