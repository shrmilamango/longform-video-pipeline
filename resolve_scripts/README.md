# Resolve scripts (examples)

These Lua scripts run inside **DaVinci Resolve** (Workspace › Scripts).

- `SleepEditor_Build.lua`: imports the episode's FCPXML timeline, applies colour correction (CDL) to every clip, and can start the render.
- `SleepEditor_Render.lua`: renders the currently open timeline to a 1440p MP4.

**You don't copy these by hand.** `sleep_editor.py` generates fresh ones for every episode and installs them into
`%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility`.

Why generated? Lua inside the free Resolve has no file access (`io` and `os` are unavailable), so it can't read a
settings file. Instead, Python writes the episode's paths, size and per-clip colour values straight into the script.

The files here are real examples with paths made generic and the colour table shortened.
