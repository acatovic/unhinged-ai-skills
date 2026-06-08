# Human Uncut AI Podcast

This project contains a short AI-generated video podcast episode created with the
`ai-podcast` skill.

Armin is the host of **Human Uncut**, joined by the fictional guest Jane Edna, a
brunette actress in her late 20s. The episode discusses the impact of generative
AI on the film industry, especially consent, actor likeness rights, independent
production, provenance, and the continued role of human creative agency.

## Outputs

- Final stitched episode: `output/final/podcast-final.mp4`
- Dialogue script: `script.txt`
- Generated Jane portrait: `output/images/jane-edna.png`
- Resized speaker portraits:
  - `armin-810x1440.png`
  - `output/images/jane-edna-810x1440.png`
- Per-turn audio clips: `output/audio/`
- Per-turn lip-synced video clips: `output/video/`

## Original Prompt

```text
Use $ai-podcast skill to create a short podcast episode where I (Armin) am the host and Jane Edna (it's a made-up name) is the guest. Jane is an actress and the podcast is called "Human Uncut" discussing the impact of generative AI on the film industry. The podcast should consist of 5 dialogue turns (so 10 audio/video clips in total), i.e. 001-armin, 002-jane, 003-armin, etc, starting with me introducing Jane and the topic. I have provided both my photo and my speech sample for cloning, but for Jane you are supposed to generate a nice clean image as per the skill - make her a brunette in her late 20s. You are also supposed to generate the script.
```
