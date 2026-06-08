#!/usr/bin/env python3
"""Generate per-turn podcast audio with Kyutai Pocket TTS.

The input script is line-oriented and follows the skill format:

    HOST: Welcome to the podcast ...
    GUEST: Thank you for having me ...

For each speaker, this script either:

1. clones from a reference audio file by exporting a safetensors voice state, or
2. falls back to a configured male/female default voice.

Outputs are written as:

    output/audio/001-host.wav
    output/audio/002-guest.wav
    output/safetensors/host.safetensors
"""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_FEMALE_VOICE = "hf://kyutai/tts-voices/vctk/p361_023_enhanced.wav"
DEFAULT_MALE_VOICE = "hf://kyutai/tts-voices/alba-mackenna/casual.wav"

AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".m4a", ".ogg")
DEFAULT_REFERENCE_DIRS = (
    ".",
    "input",
    "inputs",
    "project",
    "reference",
    "references",
    "audio",
)


@dataclass(frozen=True)
class Turn:
    number: int
    speaker: str
    speaker_key: str
    text: str


@dataclass(frozen=True)
class SpeakerPlan:
    speaker: str
    speaker_key: str
    gender: str
    voice: str
    reference_audio: str | None
    safetensors_path: Path | None

    @property
    def is_cloned(self) -> bool:
        return self.reference_audio is not None


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


def parse_gender_mapping(values: list[str]) -> dict[str, str]:
    mapping = parse_mapping(values, "--gender")
    for key, gender in mapping.items():
        normalized = gender.lower()
        if normalized not in {"male", "female"}:
            raise argparse.ArgumentTypeError(
                f"--gender for {key!r} must be 'male' or 'female', got {gender!r}"
            )
        mapping[key] = normalized
    return mapping


def parse_default_genders(value: str) -> list[str]:
    genders = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not genders:
        raise argparse.ArgumentTypeError("--default-genders cannot be empty")

    invalid = [gender for gender in genders if gender not in {"male", "female"}]
    if invalid:
        joined = ", ".join(invalid)
        raise argparse.ArgumentTypeError(
            f"--default-genders values must be male or female, got: {joined}"
        )
    return genders


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


def is_remote_path(value: str) -> bool:
    return value.startswith(("http://", "https://", "hf:"))


def resolve_local_path(value: str, base_dir: Path) -> str:
    if is_remote_path(value):
        return value

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return str(path)


def validate_reference_audio(value: str) -> str:
    if is_remote_path(value):
        return value

    path = Path(value)
    if not path.exists():
        raise FileNotFoundError(f"Reference audio not found: {path}")
    if not path.is_file():
        raise ValueError(f"Reference audio is not a file: {path}")
    return str(path)


def reference_search_dirs(
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
    for directory in DEFAULT_REFERENCE_DIRS:
        add(Path(directory))
    for directory in requested_dirs:
        add(Path(directory))

    return [directory for directory in dirs if directory.exists() and directory.is_dir()]


def find_reference_audio(
    speaker: str, speaker_key: str, search_dirs: list[Path]
) -> str | None:
    stems = {
        speaker_key,
        speaker_key.replace("-", "_"),
        speaker.strip(),
        speaker.strip().lower(),
    }

    for directory in search_dirs:
        for stem in stems:
            if not stem or "/" in stem or "\\" in stem:
                continue
            for suffix in AUDIO_SUFFIXES:
                candidate = directory / f"{stem}{suffix}"
                if candidate.exists() and candidate.is_file():
                    return str(candidate)
    return None


def build_speaker_plans(
    speakers: list[tuple[str, str]],
    args: argparse.Namespace,
    script_path: Path,
) -> dict[str, SpeakerPlan]:
    cwd = Path.cwd()
    reference_map = {
        key: resolve_local_path(value, cwd)
        for key, value in parse_mapping(args.reference_audio, "--reference-audio").items()
    }
    voice_map = parse_mapping(args.voice, "--voice")
    gender_map = parse_gender_mapping(args.gender)
    default_genders = parse_default_genders(args.default_genders)
    search_dirs = reference_search_dirs(cwd, script_path, args.reference_dir)

    plans: dict[str, SpeakerPlan] = {}
    for index, (speaker_key, speaker) in enumerate(speakers):
        gender = gender_map.get(speaker_key, default_genders[index % len(default_genders)])

        if speaker_key in voice_map:
            voice = voice_map[speaker_key]
            plans[speaker_key] = SpeakerPlan(
                speaker=speaker,
                speaker_key=speaker_key,
                gender=gender,
                voice=voice,
                reference_audio=None,
                safetensors_path=None,
            )
            continue

        reference_audio = reference_map.get(speaker_key)
        if reference_audio is None and not args.no_auto_discover_references:
            reference_audio = find_reference_audio(speaker, speaker_key, search_dirs)

        if reference_audio is not None:
            reference_audio = validate_reference_audio(reference_audio)
            safetensors_path = args.safetensors_dir / f"{speaker_key}.safetensors"
            plans[speaker_key] = SpeakerPlan(
                speaker=speaker,
                speaker_key=speaker_key,
                gender=gender,
                voice=str(safetensors_path),
                reference_audio=reference_audio,
                safetensors_path=safetensors_path,
            )
            continue

        voice = args.female_voice if gender == "female" else args.male_voice
        plans[speaker_key] = SpeakerPlan(
            speaker=speaker,
            speaker_key=speaker_key,
            gender=gender,
            voice=voice,
            reference_audio=None,
            safetensors_path=None,
        )

    return plans


def pocket_tts_base_command(args: argparse.Namespace) -> list[str]:
    command = shlex.split(args.pocket_tts_command)
    if not command:
        raise ValueError("--pocket-tts-command cannot be empty")
    return command


def add_model_options(command: list[str], args: argparse.Namespace) -> list[str]:
    if args.config:
        command.extend(["--config", args.config])
    else:
        command.extend(["--language", args.language])
    return command


def add_generation_options(command: list[str], args: argparse.Namespace) -> list[str]:
    command.extend(["--lsd-decode-steps", str(args.lsd_decode_steps)])
    command.extend(["--temperature", str(args.temperature)])

    if args.noise_clamp is not None:
        command.extend(["--noise-clamp", str(args.noise_clamp)])
    if args.eos_threshold is not None:
        command.extend(["--eos-threshold", str(args.eos_threshold)])
    if args.frames_after_eos is not None:
        command.extend(["--frames-after-eos", str(args.frames_after_eos)])
    if args.device:
        command.extend(["--device", args.device])
    if args.quantize:
        command.append("--quantize")
    if args.quiet:
        command.append("--quiet")

    return command


def run_command(command: list[str], dry_run: bool) -> None:
    print("+", shlex.join(command))
    if dry_run:
        return
    subprocess.run(command, check=True)


def ensure_command_available(command: list[str], dry_run: bool) -> None:
    if dry_run:
        return

    executable = command[0]
    if shutil.which(executable) is None:
        raise FileNotFoundError(
            f"Required command not found in PATH: {executable}. "
            "Install uv and use the default 'uvx pocket-tts', or pass "
            "--pocket-tts-command pocket-tts if pocket-tts is already installed."
        )


def export_cloned_voices(
    plans: dict[str, SpeakerPlan], base_command: list[str], args: argparse.Namespace
) -> None:
    cloned_plans = [plan for plan in plans.values() if plan.is_cloned]
    if not cloned_plans:
        return

    args.safetensors_dir.mkdir(parents=True, exist_ok=True)
    for plan in cloned_plans:
        assert plan.reference_audio is not None
        assert plan.safetensors_path is not None

        if plan.safetensors_path.exists() and not args.overwrite:
            print(f"Skipping existing voice clone: {plan.safetensors_path}")
            continue

        command = [*base_command, "export-voice", plan.reference_audio, str(plan.safetensors_path)]
        add_model_options(command, args)
        if args.quiet:
            command.append("--quiet")
        run_command(command, args.dry_run)


def generate_turn_audio(
    turns: list[Turn],
    plans: dict[str, SpeakerPlan],
    base_command: list[str],
    args: argparse.Namespace,
) -> None:
    args.audio_dir.mkdir(parents=True, exist_ok=True)
    for turn in turns:
        plan = plans[turn.speaker_key]
        output_path = args.audio_dir / f"{turn.number:03d}-{turn.speaker_key}.wav"

        if output_path.exists() and not args.overwrite:
            print(f"Skipping existing turn: {output_path}")
            continue

        command = [
            *base_command,
            "generate",
            "--text",
            turn.text,
            "--voice",
            plan.voice,
            "--output-path",
            str(output_path),
        ]
        add_model_options(command, args)
        add_generation_options(command, args)
        run_command(command, args.dry_run)


def print_plan(turns: list[Turn], plans: dict[str, SpeakerPlan], args: argparse.Namespace) -> None:
    print(f"Script turns: {len(turns)}")
    print("Speakers:")
    for plan in plans.values():
        if plan.is_cloned:
            detail = f"clone from {plan.reference_audio} -> {plan.safetensors_path}"
        else:
            detail = f"default {plan.gender} voice {plan.voice}"
        print(f"  - {plan.speaker} ({plan.speaker_key}): {detail}")
    print(f"Audio output: {args.audio_dir}")
    if any(plan.is_cloned for plan in plans.values()):
        print(f"Voice clone output: {args.safetensors_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate per-turn podcast speech WAV files with Pocket TTS."
    )
    parser.add_argument(
        "--script",
        type=Path,
        default=Path("script.txt"),
        help="Dialogue script with lines like 'Host: text'. Default: script.txt",
    )
    parser.add_argument(
        "--audio-dir",
        type=Path,
        default=Path("output/audio"),
        help="Directory for generated turn WAV files. Default: output/audio",
    )
    parser.add_argument(
        "--safetensors-dir",
        type=Path,
        default=Path("output/safetensors"),
        help="Directory for exported cloned voice states. Default: output/safetensors",
    )
    parser.add_argument(
        "--reference-audio",
        action="append",
        default=[],
        metavar="SPEAKER=PATH",
        help=(
            "Reference audio to clone for a speaker. Can be repeated. "
            "Example: --reference-audio host=host.wav"
        ),
    )
    parser.add_argument(
        "--reference-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="Extra directory to search for auto-discovered speaker audio files.",
    )
    parser.add_argument(
        "--no-auto-discover-references",
        action="store_true",
        help="Do not auto-detect files like host.wav or guest.wav.",
    )
    parser.add_argument(
        "--voice",
        action="append",
        default=[],
        metavar="SPEAKER=VOICE",
        help=(
            "Explicit Pocket TTS voice for a speaker. Overrides reference audio. "
            "VOICE can be a local file, safetensors file, built-in name, or hf:// URL."
        ),
    )
    parser.add_argument(
        "--gender",
        action="append",
        default=[],
        metavar="SPEAKER=male|female",
        help="Gender used when choosing a default voice for a non-cloned speaker.",
    )
    parser.add_argument(
        "--default-genders",
        default="male,female",
        help=(
            "Comma-separated gender cycle for speakers without --gender. "
            "Default: male,female"
        ),
    )
    parser.add_argument(
        "--female-voice",
        default=DEFAULT_FEMALE_VOICE,
        help=f"Default voice for female speakers. Default: {DEFAULT_FEMALE_VOICE}",
    )
    parser.add_argument(
        "--male-voice",
        default=DEFAULT_MALE_VOICE,
        help=f"Default voice for male speakers. Default: {DEFAULT_MALE_VOICE}",
    )
    parser.add_argument(
        "--pocket-tts-command",
        default="uvx pocket-tts",
        help=(
            "Command prefix used to invoke Pocket TTS. "
            "Default: 'uvx pocket-tts'. Use 'pocket-tts' for a manual install."
        ),
    )
    parser.add_argument(
        "--language",
        default="english",
        help="Pocket TTS language model. Ignored when --config is set. Default: english",
    )
    parser.add_argument(
        "--config",
        help="Local Pocket TTS config YAML. Incompatible with --language in Pocket TTS.",
    )
    parser.add_argument(
        "--lsd-decode-steps",
        type=int,
        default=5,
        help="Pocket TTS generation steps. Default: 5",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.8,
        help="Pocket TTS generation temperature. Default: 0.8",
    )
    parser.add_argument("--noise-clamp", type=float, help="Pocket TTS noise clamp.")
    parser.add_argument("--eos-threshold", type=float, help="Pocket TTS EOS threshold.")
    parser.add_argument(
        "--frames-after-eos",
        type=int,
        help="Pocket TTS frames to generate after EOS. Each frame is 80ms.",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        help="Generation device passed to Pocket TTS. Default: cpu",
    )
    parser.add_argument(
        "--quantize",
        action="store_true",
        help="Use Pocket TTS int8 quantization.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Pass --quiet to Pocket TTS commands.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate existing safetensors and WAV files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the commands without running Pocket TTS.",
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

        args.audio_dir = args.audio_dir.expanduser()
        args.safetensors_dir = args.safetensors_dir.expanduser()

        turns = parse_script(script_path)
        speakers = ordered_speakers(turns)
        plans = build_speaker_plans(speakers, args, script_path)
        base_command = pocket_tts_base_command(args)

        print_plan(turns, plans, args)
        ensure_command_available(base_command, args.dry_run)
        export_cloned_voices(plans, base_command, args)
        generate_turn_audio(turns, plans, base_command, args)
    except (OSError, ValueError, argparse.ArgumentTypeError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
