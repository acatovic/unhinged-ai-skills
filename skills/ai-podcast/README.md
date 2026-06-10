# AI Podcast Skill

Creates a realistic AI-generated video podcast from a dialogue script, speaker
portraits, and optional reference audio. Missing scripts, speaker images, or
voices can be generated from the user's description when enough detail is
available.

Use `SKILL.md` as the primary workflow guide. In short:

1. Create or provide `script.txt` with `Speaker: dialogue` lines.
2. Provide speaker images, or generate them with `scripts/generate_image.py`.
3. Generate audio with `scripts/generate_audio.py`.
4. Generate lip-synced clips with `scripts/generate_video.py`.
5. Stitch the clips with `scripts/stitch.sh`.

Required credentials belong in `.env`: `PRUNA_API_KEY` for video generation and
Pruna `p-image` image generation.

To obtain Pruna API key please visit https://www.pruna.ai/.

## Example

In Codex you can just copy-paste this:

```text
Use $ai-podcast skill to create a short podcast episode where I (Armin) am the host and Jane Edna (it's a made-up name) is the guest. Jane is an actress and the podcast is called "Human Uncut" discussing the impact of generative AI on the film industry. The podcast should consist of 5 dialogue turns (so 10 audio/video clips in total), i.e. 001-armin, 002-jane, 003-armin, etc, starting with me introducing Jane and the topic. I have provided both my photo and my speech sample for cloning, but for Jane you are supposed to generate a nice clean image as per the skill - make her a brunette in her late 20s. You are also supposed to generate the script.
```
