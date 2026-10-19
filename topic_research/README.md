# Topic research

Finds which space sleep-documentary topics are **winning right now**, so every video starts from data, not guesswork.

## How it works

1. Reads the latest long videos (45+ min) from the competitor channels in `config.yaml`, plus a few discovery searches, using the free **YouTube Data API**.
2. Scores every video by **"x normal"**: its views compared with that channel's usual video. 3x+ means the topic or title pulled far more people than usual.
3. Writes a report with three tables: recent winners, all-time biggest in the niche, and your own channel.
4. Builds a ready-made prompt from the report. Paste it into your AI assistant (or let Gemini answer automatically) to get numbered topic ideas.

## Setup (one time)

1. Double-click `setup_windows.bat` (installs `requests` and `PyYAML`, creates `config.yaml`).
2. Get a free YouTube API key: console.cloud.google.com → new project → APIs & Services → Library →
   **YouTube Data API v3** → Enable → Credentials → Create credentials → **API key**.
3. Paste it into `config.yaml` as `youtube_api_key`. Optionally add your `channel_id`.

## Use

```
py sog.py research     # or double-click 0_topic_research.bat in the main folder
py sog.py ideas        # show the numbered ideas
```

Output in `data/research/`:

| File | What it is |
|---|---|
| `latest.md` | readable report with the winner tables |
| `research-<date>.csv` | all scored videos, for Excel |
| `topics_prompt.md` | prompt to paste into your AI assistant |
| `ideas.md` | save the AI's numbered answer here (written automatically in Gemini mode) |

One run uses about 400–600 of the 10,000 free daily API units.

## Files

| File | Job |
|---|---|
| `sog.py` | command line (`research`, `ideas`, `telegram-id`) |
| `sog/research.py` | API calls, scoring, report, prompt |
| `sog/config.py` | loads `config.yaml` (falls back to `config.example.yaml`) |
| `sog/llm.py` | optional Gemini call |
| `sog/notify.py` | optional Telegram phone alert when research is done |
| `sog/util.py` | paths, logging, retries, JSON helpers |
| `prompts/topics_prompt.md` | template for the ideas prompt |
