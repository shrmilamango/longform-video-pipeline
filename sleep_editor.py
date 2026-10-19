#!/usr/bin/env python3
"""
Sleep on Galaxy - Auto Editor
=============================
Turns  script + long TTS voiceover + background music
into   a ready DaVinci Resolve timeline (1440p, dips to black, mixed audio).

Usage (Windows, from the SleepEditor folder):

  1) Plan scenes (makes episodes/<name>/scenes.csv you can edit):
     python sleep_editor.py ep01 --script input\\script.txt --voice input\\voice.mp3 --plan-only

  2) Build everything (download clips, mix audio, write timeline):
     python sleep_editor.py ep01 --script input\\script.txt --voice input\\voice.mp3 --music input\\music.mp3

  3) In DaVinci Resolve:  Workspace > Scripts > SleepEditor_Build

Re-running step 2 keeps your edited scenes.csv. Add bad clip names to
blocklist.txt and re-run step 2 to swap them out.
"""

import argparse
import csv
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from fractions import Fraction
from pathlib import Path
import xml.etree.ElementTree as ET

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
LIB = BASE / "library"
CLIPS_DIR = LIB / "clips"
CACHE_DIR = LIB / "search_cache"
INDEX_FILE = LIB / "index.json"
BLOCKLIST_FILE = BASE / "blocklist.txt"
EPISODES = BASE / "episodes"
JOB_FILE = Path.home() / "SleepEditor_job.json"   # read by the Resolve scripts

DEFAULTS = {
    "pexels_api_key": "",
    "pixabay_api_key": "",
    "local_clips_folder": "",
    "width": 2560,                  # 1440p output (use 3840 x 2160 for 4K)
    "height": 1440,
    "fps": 30,
    # --- footage sources (in this order of preference when mixing)
    "sources": ["nasa_svs", "pixabay", "nasa_images"],
    "nasa_max_mb": 800,             # skip NASA files bigger than this
    # --- quality filter: every clip is checked frame by frame before it's used
    "look_check": True,
    "max_brightness": 0.28,         # deep-space look (reference video averages ~0.10)
    "max_brightness_bright_topics": 0.45,   # Sun / planets / Earth may be brighter
    # --- shots: cut only at pauses in the voice
    "shot_min_seconds": 8,
    "shot_max_seconds": 25,
    "pause_min_seconds": 0.35,      # silence this long counts as a pause
    "pause_noise_db": -35,
    # --- transitions: "black" = dip to black, "dissolve" = cross dissolve
    "transition_style": "black",
    "fade_seconds": 1.0,            # each half of the dip (out, then in)
    "transition_seconds": 2,        # used for "dissolve"
    "end_fade_seconds": 3,          # final fade to black
    "topic_share": 0.6,             # share of shots that follow the narration topic (rest: calm deep space)
    "scene_seconds": 45,
    "min_clip_width": 1920,
    "prefer_clip_width": 2560,      # download the smallest file that is at least this wide
    "max_new_downloads": 250,
    "reuse_gap_minutes": 20,
    # --- color correction (applied in Resolve on each clip)
    "color_correction": True,
    "color_look": "cool",           # "cool", "neutral" or "warm"
    # --- voice
    "voice_style": "deep",          # "deep" or "none"
    "voice_pitch": 0.94,            # 1.0 = unchanged, lower = deeper
    "voice_reverb": True,
    # --- music + mix
    "music_level": 0.30,            # music level in pauses (it ducks under the voice)
    "music_duck": True,
    "music_crossfade_seconds": 5,   # smooth loop when the music is shorter than the video
    "intro_seconds": 0,             # seconds of music before the voice starts (0 = start right away)
    "music_fade_seconds": 6,
    "tail_seconds": 10,
    "target_lufs": -18,
    "auto_render": False,
    "render_codec": "H264",
    "video_bitrate_kbps": 20000,    # 1440p: 16-24 Mbps is YouTube's sweet spot
    # calm deep-space footage used between topic shots and whenever a topic has nothing good
    "generic_queries": [
        "nebula", "space nebula", "deep space", "galaxy", "starfield", "cosmos",
        "stars space", "universe", "milky way galaxy", "interstellar", "cosmic dust", "outer space",
    ],
}

# keyword in narration  ->  stock-footage search (only things that LOOK like deep space)
KEYWORDS = [
    (r"black holes?|event horizon|accretion", "black hole"),
    (r"neutron stars?|pulsars?|magnetars?", "neutron star"),
    (r"supernova(e|s)?|exploding star|stellar explosion", "supernova"),
    (r"nebula(e|s)?|stellar nursery|pillars of creation", "nebula"),
    (r"milky way", "milky way galaxy"),
    (r"andromeda", "andromeda galaxy"),
    (r"galax(y|ies)|spiral arms?", "galaxy"),
    (r"big bang|early universe|cosmic microwave", "big bang"),
    (r"dark matter|dark energy|voids?\b|cosmic web", "deep space"),
    (r"quasars?", "quasar"),
    (r"exoplanets?|alien worlds?|habitable zone", "exoplanet"),
    (r"\bsun\b|solar flares?|solar wind|corona|sunspots?", "sun"),
    (r"eclipse", "solar eclipse"),
    (r"\bmoon\b|lunar", "moon"),
    (r"\bearth\b|our planet|blue marble", "earth from space"),
    (r"\bmars\b|red planet", "mars planet"),
    (r"jupiter|great red spot", "jupiter"),
    (r"saturn|rings of", "saturn"),
    (r"venus", "venus planet"),
    (r"mercury", "mercury planet"),
    (r"uranus|neptune|ice giants?", "neptune"),
    (r"pluto|kuiper|dwarf planets?", "pluto"),
    (r"comets?", "comet"),
    (r"asteroids?", "asteroid"),
    (r"solar system|planets? orbit", "solar system"),
    (r"star clusters?|globular", "star cluster"),
    (r"\bstars?\b|starlight", "stars space"),
    (r"universe|cosmos|cosmic", "universe"),
]

# searches whose clips may be brighter than the deep-space limit
BRIGHT_TOPICS = ("sun", "solar", "earth", "moon", "mars", "jupiter", "saturn", "venus",
                 "mercury", "neptune", "pluto", "planet", "comet", "asteroid", "eclipse")

# ---- words that disqualify a clip (matched as whole words, case-insensitive)
BAN_COMMON = [
    "person", "people", "man", "men", "woman", "women", "girl", "boy", "child", "children", "kid",
    "face", "hand", "hands", "crowd", "family", "couple", "astronaut", "astronauts",
    "telescope", "telescopes", "observatory", "binoculars", "stargazing", "stargazer",
    "city", "building", "house", "street", "road", "car", "tree", "trees", "forest", "mountain",
    "mountains", "beach", "ocean", "sea", "lake", "river", "waterfall", "desert", "grass",
    "flower", "flowers", "animal", "bird", "dog", "cat", "sunset", "sunrise", "daylight", "daytime",
    "green screen", "greenscreen", "chroma", "chroma key", "chromakey", "text", "logo", "intro",
    "title", "countdown", "hud", "interface", "ui", "screen", "monitor", "computer", "laptop",
    "phone", "office", "cartoon", "anime", "emoji", "christmas", "birthday", "wedding",
    "launch", "rocket launch", "launch pad", "tent", "camping", "campfire", "silhouette",
    "airplane", "drone", "subscribe", "lower third", "transition",
]
BAN_PIXABAY = BAN_COMMON + [
    "game", "games", "gaming", "gameplay", "video game", "minecraft", "simulation", "simulator",
    "unity", "unreal", "spaceship", "spacecraft", "space ship", "ufo", "alien", "aliens",
    "sci-fi", "scifi", "science fiction", "futuristic", "cockpit", "robot", "laser", "battle",
    "war", "weapon", "hologram", "wireframe", "grid", "neon", "vj", "dj", "visualizer",
    "equalizer", "plexus", "polygon", "geometric", "shapes", "hexagon", "triangle", "cube",
    "fractal", "kaleidoscope", "mandala", "lowpoly", "low poly", "pixel", "8-bit", "retro",
]
BAN_NASA = BAN_COMMON + [
    "briefing", "conference", "interview", "live", "webinar", "podcast", "panel", "this week",
    "what's up", "whats up", "skywatching", "ask nasa", "asknasa", "career", "profile",
    "tool time", "training", "ceremony", "award", "student", "students", "team", "engineer",
    "engineers", "crew", "spacewalk", "launches", "rocket", "sls", "artemis", "landing", "rover",
    "testing", "assembly", "behind the scenes", "b-roll", "broll", "explained", "explains",
    "we asked", "expert", "science live", "sciencecasts", "gravity assist", "space to ground",
    "hangout", "twitch", "stream", "demo", "trailer", "highlights", "official", "news", "update",
    "recap", "sonification", "sounds", "listening", "music", "event", "reveal", "watch party",
    "space station", "iss", "lecture", "talk", "q&a", "faq", "infographic", "chart", "map",
    "how to", "spot", "kids", "colorbar", "labels", "labeled",
]
# a NASA Image Library video must look like pure visuals, not a produced show
NASA_IMAGES_REQUIRE = ["fly-through", "flythrough", "fly through", "visualization", "animation",
                       "animated", "simulation", "zoom", "moment of zen", "space zen", "3d", "pan",
                       "tour", "journey", "orbit around"]
SVS_TYPES_OK = ("visualization", "animation")
SVS_FILE_BAN = ["label", "text", "caption", "title", "slate", "credit", "narr", "print", "prores",
                "hevc", "h265", "vertical", "square", "1x1", "9x16", "4x5", "social", "instagram",
                "preview", "thm", "searchweb", "colorbar", "legend", "date", "annotated"]
SVS_FILE_PREFER = ["nolabel", "no_label", "nolabels", "no_labels", "clean", "notext", "no_text"]


def banned(text, words):
    """True if any banned word/phrase appears in text as a whole word."""
    t = " " + re.sub(r"[^a-z0-9&'\-]+", " ", (text or "").lower()) + " "
    return next((w for w in words if f" {w} " in t), None)


# ----------------------------------------------------------------- helpers
def log(msg):
    print(msg, flush=True)


def die(msg):
    print(f"\nERROR: {msg}", flush=True)
    sys.exit(1)


def load_config():
    cfg = dict(DEFAULTS)
    path = BASE / "config.json"
    if path.exists():
        # Windows paths like D:\Clips break JSON, so turn every \ into /
        raw = path.read_text(encoding="utf-8-sig").replace("\\\\", "/").replace("\\", "/")
        try:
            cfg.update(json.loads(raw))
        except json.JSONDecodeError as e:
            die(f"config.json has a typo on line {e.lineno} (near column {e.colno}).\n"
                "Check for a missing comma or quote. Every line except the last setting needs a comma.")
    else:
        log("No config.json found - using defaults (copy config.example.json to config.json).")
    for k in ("pexels_api_key", "pixabay_api_key"):   # placeholder text = no key
        v = str(cfg.get(k) or "").strip()
        cfg[k] = "" if (not v or v.upper().startswith("PASTE")) else v
    return cfg


def need(tool):
    if not shutil.which(tool):
        die(f"'{tool}' not found. Install FFmpeg and make sure it's on PATH (see README).")


def ffprobe(path):
    """Return dict(duration, width, height, fps_num, fps_den) for a media file."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,r_frame_rate,sample_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        return None
    data = json.loads(out.stdout or "{}")
    info = {"duration": float(data.get("format", {}).get("duration", 0) or 0),
            "width": 0, "height": 0, "fps_num": 0, "fps_den": 1, "sample_rate": 0}
    for s in data.get("streams", []):
        if s.get("codec_type") == "audio" and not info["sample_rate"]:
            info["sample_rate"] = int(s.get("sample_rate") or 0)
        if s.get("codec_type") == "video" and not info["width"]:
            info["width"], info["height"] = s.get("width", 0), s.get("height", 0)
            try:
                f = Fraction(s.get("r_frame_rate", "0/1"))
                info["fps_num"], info["fps_den"] = f.numerator, f.denominator
            except (ValueError, ZeroDivisionError):
                pass
    return info


STANDARD_FPS = (23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60)


def is_standard_fps(num, den):
    fps = num / (den or 1)
    return any(abs(fps - s) < 0.01 for s in STANDARD_FPS)


def has_timecode(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format_tags=timecode:stream_tags=timecode", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return any(":" in line or ";" in line for line in out.splitlines())


def strip_timecode(src, dest):
    """Copy the video without its hidden start timecode (Resolve can't match clips that have one)."""
    tmp = Path(dest).with_suffix(".tc.mp4")
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0",
                        "-c", "copy", "-map_metadata", "-1", "-map_metadata:s:v", "-1",
                        "-write_tmcd", "0", str(tmp)])
    if r.returncode == 0 and tmp.exists():
        tmp.replace(dest)
        return True
    tmp.unlink(missing_ok=True)
    return False


RESOLVE_OK_PIX = ("yuv420p", "yuvj420p")   # 8-bit 4:2:0 - what the free Resolve reads in H.264


def pix_fmt(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=pix_fmt", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return out.strip().split(",")[0]


def to_8bit(src, dest):
    """Re-encode a 10-bit (or other unusual) clip as normal 8-bit H.264 that free Resolve can import."""
    tmp = Path(dest).with_suffix(".8bit.mp4")
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
                        "-map_metadata", "-1", "-write_tmcd", "0", str(tmp)])
    if r.returncode == 0 and tmp.exists():
        tmp.replace(dest)
        return True
    tmp.unlink(missing_ok=True)
    return False


def hms(seconds):
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60]


# ----------------------------------------------------------- 0. voice pauses
def detect_pauses(voice, voice_dur, cfg, cache_dir):
    """Find silences in the voiceover. Returns [(start, end), ...] in voice seconds."""
    st = Path(voice).stat()
    sig = f"{st.st_size}_{int(st.st_mtime)}_{cfg['pause_min_seconds']}_{cfg['pause_noise_db']}"
    cache = Path(cache_dir) / "pauses.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("sig") == sig:
            return [tuple(p) for p in data["pauses"]]
    log("  finding pauses in the voiceover ...")
    out = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", str(voice), "-vn",
         "-af", f"silencedetect=noise={cfg['pause_noise_db']}dB:d={cfg['pause_min_seconds']}",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    pauses, start = [], None
    for line in out.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            pauses.append((round(start, 3), round(float(m.group(1)), 3)))
            start = None
    if start is not None:
        pauses.append((round(start, 3), round(voice_dur, 3)))
    cache.write_text(json.dumps({"sig": sig, "pauses": pauses}), encoding="utf-8")
    return pauses


def speech_clock(pauses, voice_dur):
    """Return f(fraction_of_words) -> voice seconds, skipping silent parts.
    TTS speaks at a steady rate, so word position maps to *speaking* time."""
    speech, t = [], 0.0
    for a, b in pauses:
        if a > t:
            speech.append((t, a))
        t = max(t, b)
    if t < voice_dur:
        speech.append((t, voice_dur))
    total = sum(b - a for a, b in speech) or voice_dur
    if not speech:
        speech = [(0.0, voice_dur)]

    def at(frac):
        want, acc = max(0.0, min(1.0, frac)) * total, 0.0
        for a, b in speech:
            if acc + (b - a) >= want:
                return a + (want - acc)
            acc += b - a
        return voice_dur
    return at


# ----------------------------------------------------------- 1. plan scenes
def plan_scenes(script_path, voice_dur, cfg, seed, clock=None):
    clock = clock or (lambda f: f * voice_dur)
    text = Path(script_path).read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"\[[^\]]*\]", " ", text)          # drop [pause] style cues
    text = re.sub(r"^#+\s*", "", text, flags=re.M)   # drop markdown heading marks
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n\s*\n", text) if s.strip()]
    if not sentences:
        die("Script file is empty.")
    total_words = sum(len(s.split()) for s in sentences)

    def when(words):
        return clock(words / total_words)

    rng = random.Random(seed)
    generic = list(cfg["generic_queries"])
    rng.shuffle(generic)
    scenes, buf, start_w, words_seen, g = [], [], 0, 0, 0
    for i, sent in enumerate(sentences):
        buf.append(sent)
        words_seen += len(sent.split())
        if when(words_seen) - when(start_w) >= cfg["scene_seconds"] or i == len(sentences) - 1:
            chunk = re.sub(r"\s+", " ", " ".join(buf))
            lows = [b.lower() for b in buf]
            best, best_n = None, 0
            for pattern, query in KEYWORDS:   # topic mentioned in the most sentences wins
                n = sum(1 for b in lows if re.search(pattern, b))
                if n > best_n:
                    best, best_n = query, n
            if not best:
                best = generic[g % len(generic)]
                g += 1
            scenes.append({"start": round(when(start_w) if start_w else 0.0, 2),
                           "end": round(voice_dur if i == len(sentences) - 1 else when(words_seen), 2),
                           "query": best,
                           "text": chunk[:140]})
            buf, start_w = [], words_seen
    return scenes


def write_scenes(path, scenes):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["scene", "start", "end", "query", "text"])
        for i, s in enumerate(scenes, 1):
            w.writerow([i, hms(s["start"]), hms(s["end"]), s["query"], s["text"]])


def read_scenes(path):
    def secs(v):
        parts = [float(p) for p in str(v).split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    scenes = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            scenes.append({"start": secs(row["start"]), "end": secs(row["end"]),
                           "query": (row.get("query") or "").strip()})
    return scenes


# ------------------------------------------------------ 2b. clip quality check
LOOK_W, LOOK_H = 128, 72   # sampled without averaging so single stars survive


def grab_frame(path, t):
    """One small RGB frame as bytes, or None."""
    out = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(path),
         "-frames:v", "1", "-vf", f"scale={LOOK_W}:{LOOK_H}:flags=neighbor,format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True)
    data = out.stdout
    return data if len(data) == LOOK_W * LOOK_H * 3 else None


def analyse_look(path, in_s, out_s, samples=6):
    """Measure what a clip looks like: brightness, dark background, green screen, blue sky."""
    frames = []
    span = max(0.5, out_s - in_s)
    for i in range(samples):
        f = grab_frame(path, in_s + span * (i + 0.5) / samples)
        if not f:
            continue
        n = LOOK_W * LOOK_H
        lum = []
        green = bright = 0
        top_l = top_b = top_r = 0.0
        for p in range(n):
            r, g, b = f[3 * p], f[3 * p + 1], f[3 * p + 2]
            y = (0.299 * r + 0.587 * g + 0.114 * b) / 255
            lum.append(y)
            if g > 90 and g > 1.35 * r and g > 1.35 * b:
                green += 1
            if y > 0.75:
                bright += 1
            if p < n // 3:                       # top third of the picture
                top_l += y
                top_b += b
                top_r += r
        mean = sum(lum) / n
        std = (sum((v - mean) ** 2 for v in lum) / n) ** 0.5
        t3 = n // 3
        frames.append({"y": mean, "std": std, "dark": sum(1 for v in lum if v < 0.12) / n,
                       "green": green / n, "bright": bright / n,
                       "sky": (top_l / t3 > 0.45 and top_b / t3 > top_r / t3 + 15)})
    if not frames:
        return None
    ys = sorted(fr["y"] for fr in frames)
    return {"y": round(ys[len(ys) // 2], 4),
            "dark": round(sum(fr["dark"] for fr in frames) / len(frames), 3),
            "green": round(max(fr["green"] for fr in frames), 3),
            "bright": round(max(fr["bright"] for fr in frames), 3),
            "flat": sum(1 for fr in frames if fr["std"] < 0.012),
            "sky": sum(1 for fr in frames if fr["sky"]),
            "n": len(frames)}


def look_problem(look, cfg, bright_ok=False):
    """Why a clip doesn't fit the deep-space look (None = it fits)."""
    if not look:
        return "unreadable"
    if look["sky"] * 2 >= look["n"]:
        return "daytime sky"
    if look["green"] > 0.15:
        return "green screen"
    if look["flat"] * 2 >= look["n"]:
        return "blank / title card"
    limit = cfg["max_brightness_bright_topics"] if bright_ok else cfg["max_brightness"]
    if look["y"] > limit:
        return f"too bright ({look['y']:.2f})"
    if not bright_ok and look["dark"] < 0.25:
        return "no dark space background"
    return None


def is_bright_topic(query):
    q = (query or "").lower()
    return any(w in q for w in BRIGHT_TOPICS)


# ------------------------------------------------------ 2. footage library
class Library:
    def __init__(self, cfg, allow_download=True, refresh=False):
        self.cfg = cfg
        self.allow_download = allow_download
        self.refresh = refresh
        CLIPS_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.index = json.loads(INDEX_FILE.read_text(encoding="utf-8")) if INDEX_FILE.exists() else {}
        self.blocked = set()
        if BLOCKLIST_FILE.exists():
            for line in BLOCKLIST_FILE.read_text(encoding="utf-8").splitlines():
                line = line.split("#")[0].strip()
                if line:
                    self.blocked.add(line)
        self.failed = set()
        self.new_downloads = 0
        self.fail_streak = 0
        self.rejected_now = {}
        self._cands = {}
        self._http = None
        self._local = self._scan_local()

    # ---- searching
    def http(self):
        if self._http is None:
            try:
                import requests
            except ImportError:
                die("Python package 'requests' missing. Run:  pip install requests")
            self._http = requests.Session()
            self._http.headers["User-Agent"] = "SleepEditor/2 (YouTube documentary editor)"
        return self._http

    def sources(self):
        srcs = list(self.cfg.get("sources") or ["pixabay"])
        if self.cfg["pexels_api_key"] and "pexels" not in srcs:
            srcs.append("pexels")
        if not self.cfg["pixabay_api_key"] and "pixabay" in srcs:
            srcs.remove("pixabay")
        return srcs

    def candidates(self, query):
        if query in self._cands:
            return self._cands[query]
        search = {"pixabay": self._search_pixabay, "pexels": self._search_pexels,
                  "nasa_svs": self._search_svs, "nasa_images": self._search_nasa_images}
        groups = [[c for c in self._local if self._local_match(c, query)]]
        for src in self.sources():
            if src in search:
                groups.append(self._cached_search(src, query, search[src]))
        min_len = self.cfg["shot_min_seconds"] + 2 * self.handle_seconds() + 1
        groups = [[c for c in grp if c["key"] not in self.blocked
                   and not self.index.get(c["key"], {}).get("rejected")
                   and c["duration"] >= min_len] for grp in groups]
        mixed = []          # interleave sources so one site doesn't dominate
        while any(groups):
            for grp in groups:
                if grp:
                    mixed.append(grp.pop(0))
        self._cands[query] = mixed
        return mixed

    def handle_seconds(self):
        longest = max(transition_frames(self.cfg)[0], end_fade_frames(self.cfg))
        return longest / self.cfg["fps"] / 2 + 0.5

    def _cached_search(self, source, query, fn):
        path = CACHE_DIR / f"{source}_v2_{slug(query)}.json"
        if path.exists() and not self.refresh:
            return json.loads(path.read_text(encoding="utf-8"))
        if not self.allow_download:
            return []
        try:
            results = fn(query)
        except Exception as e:  # network / quota problems shouldn't kill the run
            log(f"  ! {source} search failed for '{query}': {e}")
            return []
        path.write_text(json.dumps(results, indent=1), encoding="utf-8")
        log(f"  searched {source}: '{query}' -> {len(results)} usable clips")
        time.sleep(0.4)
        return results

    def _pick_file(self, files):
        """files: list of (width, height, url). Choose best for our target."""
        ok = [f for f in files if f[2] and f[0] >= self.cfg["min_clip_width"] and f[0] >= f[1]]
        if not ok:
            return None
        pref = self.cfg["prefer_clip_width"]
        big = [f for f in ok if f[0] >= pref]
        return min(big) if big else max(ok)

    def _search_pexels(self, query):
        r = self.http().get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": self.cfg["pexels_api_key"]},
            params={"query": query, "per_page": 80, "orientation": "landscape"}, timeout=30)
        r.raise_for_status()
        out = []
        for v in r.json().get("videos", []):
            if banned(v.get("url", "").replace("-", " "), BAN_PIXABAY):
                continue
            files = [(f.get("width") or 0, f.get("height") or 0, f.get("link"))
                     for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"]
            best = self._pick_file(files)
            if best:
                out.append({"key": f"pexels_{v['id']}", "source": "pexels",
                            "duration": v.get("duration", 0), "width": best[0],
                            "url": best[2], "page": v.get("url", ""),
                            "author": (v.get("user") or {}).get("name", "")})
        return out

    def _search_pixabay(self, query):
        r = self.http().get(
            "https://pixabay.com/api/videos/",
            params={"key": self.cfg["pixabay_api_key"], "q": query[:100], "order": "popular",
                    "per_page": 200, "safesearch": "true"},
            timeout=30)
        r.raise_for_status()
        out = []
        for v in r.json().get("hits", []):
            if banned(v.get("tags", ""), BAN_PIXABAY):
                continue                       # games, people, telescopes, landscapes...
            files = [(f.get("width") or 0, f.get("height") or 0, f.get("url"))
                     for f in (v.get("videos") or {}).values()]
            best = self._pick_file(files)
            if best:
                out.append({"key": f"pixabay_{v['id']}", "source": "pixabay",
                            "duration": v.get("duration", 0), "width": best[0],
                            "url": best[2], "page": v.get("pageURL", ""),
                            "author": v.get("user", ""), "tags": v.get("tags", "")})
        return out

    @staticmethod
    def _relevant(query, text):
        words = [w for w in re.findall(r"[a-z]+", query.lower()) if len(w) > 3 and w != "space"]
        t = (text or "").lower()
        return not words or any(w in t for w in words)

    def _search_svs(self, query):
        """NASA Scientific Visualization Studio: high-resolution visualizations and animations."""
        r = self.http().get("https://svs.gsfc.nasa.gov/api/search/",
                            params={"search": query, "limit": 100}, timeout=40)
        r.raise_for_status()
        out = []
        for it in r.json().get("results", []):
            kind = str(it.get("result_type", "")).lower()
            title, desc = it.get("title", ""), it.get("description", "")
            if not any(k in kind for k in SVS_TYPES_OK):
                continue                       # skip narrated "Produced Video" shows
            if banned(title, BAN_NASA) or not self._relevant(query, title + " " + desc[:400]):
                continue
            out.append({"key": f"svs_{it['id']}", "source": "nasa_svs", "duration": 120,
                        "width": 0, "url": f"svs:{it['id']}", "title": title,
                        "page": it.get("url") or f"https://svs.gsfc.nasa.gov/{it['id']}/",
                        "author": "NASA's Scientific Visualization Studio"})
        return out

    def _search_nasa_images(self, query):
        """NASA Image and Video Library (images.nasa.gov) - only fly-throughs/animations."""
        r = self.http().get("https://images-api.nasa.gov/search",
                            params={"q": query, "media_type": "video", "page_size": 100}, timeout=40)
        r.raise_for_status()
        out = []
        for it in r.json().get("collection", {}).get("items", []):
            d = (it.get("data") or [{}])[0]
            nid, title = d.get("nasa_id", ""), d.get("title", "")
            kw = d.get("keywords") or []
            kw = " ".join(kw) if isinstance(kw, list) else str(kw)
            kw = kw.replace(",", " ")
            text = f"{title} {d.get('description', '')} {kw}"
            if not nid or re.match(r"^(KSC|JSC|MSFC|GRC|AFRC)-", title) or banned(f"{title} {kw}", BAN_NASA):
                continue
            if not any(w in text.lower() for w in NASA_IMAGES_REQUIRE):
                continue                       # must be pure visuals (fly-through, animation, zoom...)
            if not self._relevant(query, text):
                continue
            key = "nasa_" + slug(nid)[:40] + "_" + hashlib.md5(nid.encode()).hexdigest()[:6]
            out.append({"key": key, "source": "nasa_images", "duration": 120, "width": 0,
                        "url": f"nasaimg:{nid}", "title": title,
                        "page": "https://images.nasa.gov/details/" + urllib.parse.quote(nid),
                        "author": "NASA"})
        return out

    # ---- turning a NASA search result into a real video file URL
    def _size_ok(self, url):
        try:
            r = self.http().head(url, allow_redirects=True, timeout=30)
            size = int(r.headers.get("Content-Length") or 0)
            return r.status_code < 400 and (not size or size <= self.cfg["nasa_max_mb"] * 1024 * 1024)
        except Exception:
            return True                      # unknown size: try anyway

    def _resolve_url(self, c):
        url = c.get("url", "")
        if url.startswith("svs:"):
            r = self.http().get(f"https://svs.gsfc.nasa.gov/api/{url[4:]}", timeout=40)
            r.raise_for_status()
            page = r.json()
            movies = []
            insts = [page.get("main_video") or {}]
            for grp in page.get("media_groups") or []:
                for item in grp.get("items") or []:
                    insts.append((item or {}).get("instance") or {})
            for ins in insts:
                name = str(ins.get("filename") or ins.get("url") or "").lower()
                w, h = ins.get("width") or 0, ins.get("height") or 0
                plain = name
                for p in SVS_FILE_PREFER:         # "nolabels" must not count as "labels"
                    plain = plain.replace(p, "")
                if (ins.get("media_type") != "Movie" or not name.endswith(".mp4")
                        or w < self.cfg["min_clip_width"] or w > 4096 or w < 1.5 * h
                        or any(b in plain for b in SVS_FILE_BAN)):
                    continue
                out_w = self.cfg["width"]     # closest file that is at least the output size
                score = (any(p in name for p in SVS_FILE_PREFER), w >= out_w, -abs(w - out_w))
                movies.append((score, ins["url"], w))
            for _, u, w in sorted(movies, reverse=True):
                if self._size_ok(u):
                    c["width"] = w
                    return u
            return None
        if url.startswith("nasaimg:"):
            nid = url[8:]
            r = self.http().get("https://images-api.nasa.gov/asset/" + urllib.parse.quote(nid), timeout=40)
            r.raise_for_status()
            hrefs = [i.get("href", "") for i in r.json().get("collection", {}).get("items", [])]
            for tag in ("~orig.mp4", "~large.mp4", "~medium.mp4"):
                for h in hrefs:
                    if h.endswith(tag):
                        u = h.replace("http://", "https://")
                        if self._size_ok(u):
                            return u
            return None
        return url

    # ---- your own footage folder (optional)
    def _scan_local(self):
        folder = self.cfg.get("local_clips_folder")
        if not folder:
            return []
        folder = Path(folder)
        if not folder.exists():
            log(f"  ! local_clips_folder not found: {folder}")
            return []
        out = []
        for p in sorted(folder.rglob("*")):
            if p.suffix.lower() not in (".mp4", ".mov", ".mkv", ".m4v"):
                continue
            key = "local_" + hashlib.md5(str(p).encode()).hexdigest()[:10]
            if key not in self.index:
                info = ffprobe(p)
                if not info or not info["width"]:
                    continue
                self.index[key] = {"path": str(p), "source": "local", "author": "own footage",
                                   "page": "", **info}
            meta = self.index[key]
            if meta.get("rejected"):
                continue
            out.append({"key": key, "source": "local", "duration": meta["duration"],
                        "name": p.stem.lower(), "url": "", "page": "", "author": "own footage"})
        log(f"  local footage: {len(out)} clips")
        return out

    @staticmethod
    def _local_match(c, query):
        return any(w in c["name"] for w in query.lower().split() if len(w) > 2)

    # ---- quality check
    def usable_range(self, key):
        """(in, out) seconds worth using - NASA videos lose their logo intro/outro."""
        m = self.index[key]
        d = m["duration"]
        if m.get("source") == "nasa_images":
            cut = max(3.0, d * 0.12)
        elif m.get("source") == "nasa_svs":
            cut = max(1.0, d * 0.04)
        else:
            cut = 0.0
        if d - 2 * cut < 5:
            cut = 0.0
        return cut, d - cut

    def look_ok(self, key, query=""):
        if not self.cfg.get("look_check", True):
            return True
        m = self.index.get(key) or {}
        if m.get("rejected"):
            return False
        if "look" not in m:
            a, b = self.usable_range(key)
            m["look"] = analyse_look(m["path"], a, b)
            self._looked = getattr(self, "_looked", 0) + 1
            if self._looked % 25 == 0:
                log(f"     checked the look of {self._looked} clips ...")
                self.save()
        lenient = look_problem(m["look"], self.cfg, bright_ok=True)
        if lenient:                       # doesn't fit at all: never use it
            self.reject(key, lenient)
            return False
        return look_problem(m["look"], self.cfg, bright_ok=is_bright_topic(query)) is None

    def reject(self, key, reason):
        m = self.index.setdefault(key, {})
        m["rejected"] = reason
        self.rejected_now[reason] = self.rejected_now.get(reason, 0) + 1
        p = m.get("path")
        if p and m.get("source") != "local" and Path(p).exists():
            try:
                Path(p).unlink()          # free the disk space
            except OSError:
                pass
        m.pop("path", None)

    # ---- downloading
    def is_ready(self, key):
        m = self.index.get(key)
        return bool(m and m.get("path") and not m.get("rejected") and Path(m["path"]).exists())

    def ensure(self, c, query=""):
        """Make sure clip c is on disk and fits the look. Returns True if usable."""
        if self.is_ready(c["key"]):
            return self.look_ok(c["key"], query)
        if (not self.allow_download or c["key"] in self.failed or not c.get("url")
                or self.index.get(c["key"], {}).get("rejected")
                or self.new_downloads >= self.cfg["max_new_downloads"]):
            return False
        try:
            url = self._resolve_url(c)
        except Exception as e:
            log(f"  ! couldn't open {c['key']}: {e}")
            self.failed.add(c["key"])
            return False
        if not url:
            self.reject(c["key"], "no suitable video file")
            return False
        dest = CLIPS_DIR / f"{c['key']}.mp4"
        part = dest.with_suffix(".part")
        size = f"{c['width']}px, " if c.get("width") else ""
        dur = f"{c['duration']}s" if c["source"] not in ("nasa_svs", "nasa_images") else "NASA"
        log(f"  downloading {c['key']} ({size}{dur}) ...")
        waits = [5, 15, 30]          # give a shaky connection time to come back
        for attempt in range(len(waits) + 1):
            try:
                with self.http().get(url, stream=True, timeout=60) as r:
                    if r.status_code >= 400:
                        log(f"    skipped (server said {r.status_code})")
                        break
                    with open(part, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                part.replace(dest)
                self.fail_streak = 0
                break
            except Exception as e:
                if attempt == len(waits):
                    self.fail_streak += 1
                    break
                reason = "no internet" if ("NameResolution" in str(e) or "getaddrinfo" in str(e)) \
                    else "connection dropped"
                log(f"    {reason} - trying again in {waits[attempt]} s ...")
                time.sleep(waits[attempt])
        if not dest.exists():
            part.unlink(missing_ok=True)
            self.failed.add(c["key"])
            if self.fail_streak >= 3:
                self.save()
                die("The internet connection seems to be down.\n"
                    "Everything downloaded so far is saved. When your internet is back,\n"
                    "run 2_make_video.bat again with the SAME episode name - it continues from here.")
            return False
        self.new_downloads += 1
        info = ffprobe(dest)
        meta = {"path": str(dest), "source": c["source"], "author": c.get("author", ""),
                "page": c.get("page", ""), "title": c.get("title", ""), **(info or {})}
        self.index[c["key"]] = meta
        if not info or not info["width"]:
            self.reject(c["key"], "broken file")
            return False
        if not is_standard_fps(info["fps_num"], info["fps_den"]):
            self.reject(c["key"], "odd frame rate")
            return False
        if info["width"] < self.cfg["min_clip_width"]:
            self.reject(c["key"], "low resolution")
            return False
        if pix_fmt(dest) not in RESOLVE_OK_PIX:
            self.reject(c["key"], "10-bit video (free Resolve can't import it)")
            return False
        if has_timecode(dest):
            strip_timecode(dest, dest)
        meta["clean"] = True
        ok = self.look_ok(c["key"], query)
        self.save()
        return ok

    def save(self):
        INDEX_FILE.write_text(json.dumps(self.index, indent=1), encoding="utf-8")


# ------------------------------------------------------ 3. choose shots
def transition_frames(cfg):
    """(transition length, black gap length) in frames."""
    F = cfg["fps"]
    if cfg.get("transition_style", "black") == "black":
        d = max(2, round(cfg["fade_seconds"] * F))
        return d, d
    return max(2, round(cfg["transition_seconds"] * F)), 0


def end_fade_frames(cfg):
    return max(0, round(cfg["end_fade_seconds"] * cfg["fps"]))


def build_shots(scenes, pauses, total_sec, intro_sec, lib, cfg, seed):
    """Plan shots so every change of clip falls inside a pause in the voice,
    5-15 s apart. pauses = [(start, end)] in timeline seconds."""
    F = cfg["fps"]
    D, G = transition_frames(cfg)
    g1 = G // 2
    g2 = G - g1
    H = round(lib.handle_seconds() * F)
    E = end_fade_frames(cfg)
    min_space = round(cfg["shot_min_seconds"] * F)
    max_space = round(cfg["shot_max_seconds"] * F)
    min_v = max(min_space - G, D + 2)           # shortest visible part of a shot
    reuse_f = round(cfg["reuse_gap_minutes"] * 60 * F)
    total_f = round(total_sec * F)
    content_end = total_f - E                     # last picture frame before the final black

    rng = random.Random(seed)
    generic = list(cfg["generic_queries"])
    rng.shuffle(generic)
    last_used, uses = {}, {}
    shots, g = [], 0

    cut_points = [(round((a + b) / 2 * F), b - a) for a, b in pauses]
    scene_marks = [round((s["start"] + intro_sec) * F) for s in scenes[1:]]

    def scene_query(frame):
        sec = frame / F - intro_sec
        for s in scenes:
            if s["start"] <= sec < s["end"]:
                return s["query"]
        if scenes and sec >= scenes[-1]["start"]:
            return scenes[-1]["query"]
        return scenes[0]["query"] if scenes else ""

    def usable_frames(key):
        m = lib.index[key]
        if not m.get("path") or not is_standard_fps(m.get("fps_num", 0), m.get("fps_den", 1)):
            return -1          # rejected, or odd frame rate Resolve can't import reliably
        a, b = lib.usable_range(key)
        return int((b - a - 0.1) * F) - 2 * H

    def reuse_ok(key, frame):
        # long clips (NASA) can come back sooner - a different part of them is shown
        gap = reuse_f // 4 if usable_frames(key) > 3 * max_space else reuse_f
        return frame - last_used[key] >= gap

    def choose(query, frame):
        pools = ([query] if query else []) + generic[g:] + generic[:g]
        # for each search (scene's own first, then generic fallbacks):
        #   1) a clip not used yet in this video that's already on disk
        #   2) a clip not used yet that we download now
        # then 3) a clip used long ago, 4) anything on disk
        for q in pools:
            fresh = [c for c in lib.candidates(q) if c["key"] not in last_used]
            for c in fresh:
                if (lib.is_ready(c["key"]) and usable_frames(c["key"]) >= min_v
                        and lib.look_ok(c["key"], q)):
                    return c["key"], q
            for c in fresh:
                if lib.ensure(c, q) and usable_frames(c["key"]) >= min_v:
                    return c["key"], q
        for q in pools:
            old = [c for c in lib.candidates(q)
                   if c["key"] in last_used and reuse_ok(c["key"], frame)
                   and lib.is_ready(c["key"]) and usable_frames(c["key"]) >= min_v]
            if old:
                return min(old, key=lambda c: last_used[c["key"]])["key"], q
        ready = [k for k in last_used if lib.is_ready(k) and usable_frames(k) >= min_v]
        if ready:
            return min(ready, key=lambda k: last_used[k]), "(reuse)"
        return None, None

    def pick_cut(prev_m, start, avail):
        """Choose the moment of the next change (middle of the black)."""
        lo = max(prev_m + min_space, start + min_v + g1)
        hi = min(prev_m + max_space, start + avail + g1, content_end - g2 - min_v)
        if hi < lo:
            return None
        t_lo = cfg["shot_min_seconds"] + 2
        t_hi = max(t_lo, cfg["shot_max_seconds"] - 3)
        target = prev_m + rng.uniform(t_lo, t_hi) * F
        best, best_score = None, None
        for p, strength in cut_points:
            if p < lo:
                continue
            if p > hi:
                break
            score = abs(p - target) / F - 2.5 * min(strength, 1.5)
            if any(abs(p - s) <= 1.5 * F for s in scene_marks):
                score -= 3       # a new topic starts here: perfect place to change
            if best_score is None or score < best_score:
                best, best_score = p, score
        return best if best is not None else int(min(max(target, lo), hi))

    start, prev_m = 0, 0
    while start < content_end:
        query = scene_query(start)
        if query not in generic and rng.random() > cfg.get("topic_share", 0.6):
            query = generic[(len(shots) + g) % len(generic)]
        key, used_q = choose(query, start)
        if key is None:
            die("No usable clips found. Check your API keys / internet, or lower min_clip_width.")
        avail = usable_frames(key)
        remaining = content_end - start
        m = None
        if remaining > min(avail, max_space):
            m = pick_cut(prev_m, start, avail)
        if m is None:   # last shot (or nothing fits): run to the end, as far as the clip allows
            end = start + min(remaining, avail)
        else:
            end = m - g1
        length = end - start
        n = uses.get(key, 0)                      # reused clip -> start at a different spot
        spare = max(0, avail - length)
        src_in = round(lib.usable_range(key)[0] * F) + H + (0 if n == 0 else (n * 3 * F * 7) % (spare + 1))
        shots.append({"key": key, "query": used_q, "start": start, "length": length, "src_in": src_in})
        last_used[key] = start
        uses[key] = n + 1
        if used_q != query:
            g += 1
        if end >= content_end:
            break
        prev_m = end + g1
        start = end + G
        if len(shots) % 50 == 0:
            log(f"  planned {len(shots)} shots ({hms(start / F)} / {hms(total_f / F)})")
    return shots, total_f


# ------------------------------------------------------ 4. color correction
LOOKS = {"cool": (0.97, 1.0, 1.04), "neutral": (1.0, 1.0, 1.0), "warm": (1.04, 1.0, 0.95)}


def clip_stats(path, duration):
    """Average brightness (0-1) and saturation of a clip, from 3 sample frames."""
    ys, ss = [], []
    for frac in (0.25, 0.5, 0.75):
        out = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-v", "info", "-ss", f"{duration * frac:.2f}", "-i", str(path),
             "-frames:v", "1", "-an", "-vf", "scale=256:-2,format=yuv420p,signalstats,metadata=print",
             "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        y = re.search(r"signalstats\.YAVG=([\d.]+)", out.stderr)
        s = re.search(r"signalstats\.SATAVG=([\d.]+)", out.stderr)
        if y and s:
            ys.append(float(y.group(1)))
            ss.append(float(s.group(1)))
    if not ys:
        return None
    y = (sum(ys) / len(ys) - 16) / 219
    return {"y": round(max(0.0, min(1.0, y)), 4), "sat": round(sum(ss) / len(ss), 2)}


def make_cdl(stats, cfg):
    """Balance the clip (too dark / too bright / too colourful), then add one calm look."""
    slope, sat = 1.0, 0.92
    if stats:
        y = max(stats["y"], 0.01)
        # bring every clip close to the reference look (average brightness ~0.10-0.18)
        if y < 0.06:
            slope = min(1.25, (0.06 / y) ** 0.5)       # lift very dark clips a little
        elif y > 0.18:
            slope = max(0.65, (0.18 / y) ** 0.6)       # calm down brighter clips
        if stats["sat"] > 45:
            sat *= max(0.75, 40 / stats["sat"])        # tame oversaturated clips
    tint = LOOKS.get(cfg.get("color_look", "cool"), LOOKS["cool"])
    s = " ".join(f"{slope * t:.3f}" for t in tint)
    return [s, "-0.008 -0.008 -0.008", "1.060 1.060 1.060", f"{sat:.3f}"]


def build_color(shots, lib, cfg):
    cdl = {}
    keys = list(dict.fromkeys(s["key"] for s in shots))
    todo = [k for k in keys if "color" not in lib.index[k]]
    if todo:
        log(f"  analysing colour of {len(todo)} clips ...")
    for i, k in enumerate(todo, 1):
        m = lib.index[k]
        m["color"] = clip_stats(m["path"], m["duration"])
        if i % 25 == 0:
            log(f"    {i}/{len(todo)}")
            lib.save()
    lib.save()
    for k in keys:
        m = lib.index[k]
        c = make_cdl(m.get("color"), cfg)
        p = Path(m["path"])
        for name in (k, p.name, p.stem):
            cdl[name] = c
    return cdl


# ------------------------------------------------------ 5. audio mix
_FILTERS = None


def has_filter(name):
    global _FILTERS
    if _FILTERS is None:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        _FILTERS = out.stdout
    return re.search(rf"\s{re.escape(name)}\s", _FILTERS) is not None


def voice_chain(cfg):
    """Deep, warm, soft 'sleep narrator' voice. Keeps the exact same length."""
    if cfg.get("voice_style", "deep") != "deep":
        return ""
    parts = []
    k = float(cfg.get("voice_pitch", 1.0))
    if abs(k - 1.0) > 0.001:
        if has_filter("rubberband"):
            parts.append(f"rubberband=pitch={k}")
        else:
            parts.append(f"asetrate={48000 * k:.0f},aresample=48000,atempo={1 / k:.5f}")
    parts += [
        "highpass=f=70",                                   # remove rumble
        "bass=g=3:f=110:w=0.6",                            # warmth / depth
        "equalizer=f=3200:t=q:w=1.4:g=-3",                 # soften harsh presence
        "treble=g=-3:f=7500",                              # soft highs, less hiss and 's' sounds
        "acompressor=threshold=-22dB:ratio=2.5:attack=20:release=300:makeup=2",  # steady level
    ]
    if cfg.get("voice_reverb", True):
        parts.append("aecho=0.9:0.9:45|83:0.10|0.06")    # small soft room
    return "," + ",".join(parts)


def normalize_tracks(tracks, workdir):
    """Bring every music track to the same loudness once (cached), so no song is suddenly louder."""
    workdir.mkdir(parents=True, exist_ok=True)
    out = []
    for t in tracks:
        t = Path(t)
        info = ffprobe(t)
        if not info or info["duration"] < 10:
            log(f"  ! skipping music file (can't read it or shorter than 10 s): {t.name}")
            continue
        st = t.stat()
        dest = workdir / f"{slug(t.stem)}_{hashlib.md5(f'{t}{st.st_size}{int(st.st_mtime)}'.encode()).hexdigest()[:8]}.wav"
        if not dest.exists():
            log(f"  preparing music: {t.name}")
            r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(t), "-vn", "-af",
                                "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                                "loudnorm=I=-24:TP=-3:LRA=11,aresample=48000",
                                "-c:a", "pcm_s16le", str(dest)])
            if r.returncode != 0 or not dest.exists():
                log(f"  ! couldn't read music file: {t.name}")
                continue
        out.append((dest, (ffprobe(dest) or info)["duration"], t))
    return out


def music_playlist(tracks, total, cfg, seed):
    """Shuffle the (normalized) tracks and repeat the list until it covers the whole video.
    tracks: [(path, length, original)]. Never the same track twice in a row."""
    lens = list(tracks)
    if not lens:
        return []
    rng = random.Random(seed)
    xf = float(cfg["music_crossfade_seconds"])
    order, covered = [], 0.0
    while covered < total and len(order) < 400:
        batch = lens[:]
        rng.shuffle(batch)
        if order and len(batch) > 1 and batch[0][0] == order[-1][0]:
            batch.append(batch.pop(0))         # no track twice in a row
        for item in batch:
            order.append(item)
            covered += item[1] - (min(xf, item[1] / 3) if len(order) > 1 else 0)
            if covered >= total:
                break
    if covered < total:
        log(f"  ! your music only fills {hms(covered)} of {hms(total)} - add more or longer tracks")
    return order


def mix_audio(voice, tracks, out, cfg, seed="mix"):
    need("ffmpeg")
    vinfo = ffprobe(voice)
    if not vinfo or not vinfo["duration"]:
        die(f"Can't read the voiceover: {voice}\nMake sure it's a normal mp3/wav/m4a audio file.")
    intro = float(cfg["intro_seconds"])
    total = intro + vinfo["duration"] + cfg["tail_seconds"]
    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    prepared = normalize_tracks(tracks, Path(out).parent / "music_prepared") if tracks else []
    playlist = music_playlist(prepared, total, cfg, seed) if prepared else []
    duck = bool(playlist) and cfg.get("music_duck", True)
    graph = [f"[0:a]{fmt}{voice_chain(cfg)},adelay={int(intro * 1000)}:all=1,"
             f"apad=whole_dur={total:.3f}" + (",asplit=2[v][vsc]" if duck else "[v]")]
    cmd = ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-v", "error", "-stats", "-i", str(voice)]
    if playlist:
        xf_max = float(cfg["music_crossfade_seconds"])
        for path, _, _ in playlist:
            cmd += ["-i", str(path)]
        for i in range(len(playlist)):
            graph.append(f"[{i + 1}:a]{fmt}[m{i}]")
        last, prev_len = "m0", playlist[0][1]
        for i in range(1, len(playlist)):     # smooth crossfade from one track to the next
            xf = min(xf_max, prev_len / 3, playlist[i][1] / 3)
            graph.append(f"[{last}][m{i}]acrossfade=d={xf:.2f}:c1=qsin:c2=qsin[x{i}]")
            last, prev_len = f"x{i}", playlist[i][1]
        fade = float(cfg["music_fade_seconds"])
        graph.append(
            f"[{last}]apad=whole_dur={total:.3f},atrim=0:{total:.3f},asetpts=N/SR/TB,"
            "highpass=f=35,equalizer=f=1800:t=q:w=1.0:g=-4,"     # leave room for the voice
            f"volume={cfg['music_level']},"
            f"afade=t=in:st=0:d={min(fade, intro + 2):.2f},"
            f"afade=t=out:st={max(0, total - fade):.3f}:d={fade:.2f}[mus]")
        if duck:   # music gently dips while the voice speaks, rises in the pauses
            graph.append("[mus][vsc]sidechaincompress=threshold=0.025:ratio=3.5:"
                         "attack=250:release=1800:knee=4[mus2]")
            mus = "mus2"
        else:
            mus = "mus"
        graph.append(f"[v][{mus}]amix=inputs=2:duration=first:normalize=0[mix]")
        names = list(dict.fromkeys(orig.stem for _, _, orig in playlist))
        log(f"  music: {len(names)} track(s), {len(playlist)} plays in a shuffled, crossfaded mix")
    else:
        graph.append("[v]anull[mix]")
        log("  no music found - the video will have voice only")
    graph.append(f"[mix]loudnorm=I={cfg['target_lufs']}:TP=-2:LRA=15,aresample=48000,"
                 "alimiter=limit=0.89:level=0[out]")
    cmd += ["-filter_complex", ";".join(graph), "-map", "[out]", "-t", f"{total:.3f}",
            "-ar", "48000", "-c:a", "pcm_s16le", str(out)]
    log(f"  mixing voice + music (deep voice, ducking, loudness) - about {hms(total)} of audio ...")
    log("  (don't type or press keys in this window while it works)")
    if subprocess.run(cmd, stdin=subprocess.DEVNULL).returncode != 0:
        die("FFmpeg audio mix failed (see message above).")
    print()
    got = (ffprobe(out) or {}).get("duration", 0)
    if got < total - 1:
        die(f"The mixed audio stopped early ({hms(got)} instead of {hms(total)}).\n"
            "Please run 2_make_video.bat again and don't press keys in the window while it mixes.")
    return got, [orig for _, _, orig in playlist]


# ------------------------------------------------------ 6. FCPXML timeline
STANDARD_RATES = [(24000, 1001), (24, 1), (25, 1), (30000, 1001), (30, 1),
                  (48, 1), (50, 1), (60000, 1001), (60, 1)]


def standard_rate(num, den):
    """Round odd/variable frame rates (31.25, 30.0003 ...) to the nearest standard one.
    Resolve refuses to import a timeline that contains unusual rates."""
    fps = Fraction(num, den or 1)
    return min(STANDARD_RATES, key=lambda s: abs(Fraction(s[0], s[1]) - fps))


def make_resolve_safe(shots, lib):
    """Remove hidden start timecodes from the clips this video uses (checked once per clip)."""
    fixed = 0
    # 10-bit clips break the whole timeline import in free Resolve: convert them to 8-bit once
    for k in dict.fromkeys(s["key"] for s in shots):
        m = lib.index[k]
        if m.get("pix_ok"):
            continue
        p = Path(m["path"])
        if pix_fmt(p) not in RESOLVE_OK_PIX:
            dest = CLIPS_DIR / f"{k}.mp4" if m.get("source") == "local" else p
            log(f"     converting 10-bit clip {k} to 8-bit (free Resolve can't import 10-bit) ...")
            if to_8bit(p, dest):
                m["path"] = str(dest)
                fixed += 1
        m["pix_ok"] = True
    keys = [k for k in dict.fromkeys(s["key"] for s in shots) if not lib.index[k].get("clean")]
    for k in keys:
        m = lib.index[k]
        p = Path(m["path"])
        if has_timecode(p):
            if m.get("source") == "local":     # never change your own files: use a cleaned copy
                dest = CLIPS_DIR / f"{k}.mp4"
                if strip_timecode(p, dest):
                    m["path"] = str(dest)
                    fixed += 1
            elif strip_timecode(p, p):
                fixed += 1
        m["clean"] = True
    lib.save()
    if fixed:
        log(f"     fixed {fixed} clips so Resolve can import them (timecode / 10-bit)")


def write_fcpxml(path, name, shots, total_f, audio_path, audio_sec, lib, cfg):
    F, W, Hh = cfg["fps"], cfg["width"], cfg["height"]
    D, G = transition_frames(cfg)

    def tm(frames):
        return f"{int(frames)}/{F}s" if frames else "0s"

    root = ET.Element("fcpxml", version="1.9")
    res = ET.SubElement(root, "resources")
    fmt_name = {"name": f"FFVideoFormat{W}x{Hh}p{F}"} if (W, Hh) in ((1920, 1080), (3840, 2160)) else {}
    ET.SubElement(res, "format", id="r1", **fmt_name,
                  frameDuration=f"1/{F}s", width=str(W), height=str(Hh))
    ET.SubElement(res, "effect", id="r2", name="Cross Dissolve",
                  uid="FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265")

    next_id = [3]
    formats, assets = {}, {}

    def new_id():
        i = f"r{next_id[0]}"
        next_id[0] += 1
        return i

    for s in shots:
        key = s["key"]
        if key in assets:
            continue
        m = lib.index[key]
        fkey = (m["width"], m["height"], m["fps_num"], m["fps_den"])
        if fkey not in formats and m["fps_num"]:
            fid = new_id()
            formats[fkey] = fid
            n, d = standard_rate(m["fps_num"], m["fps_den"])
            ET.SubElement(res, "format", id=fid, width=str(m["width"]), height=str(m["height"]),
                          frameDuration=f"{d}/{n}s")
        aid = new_id()
        assets[key] = aid
        attrs = {"id": aid, "name": key, "start": "0s",
                 "duration": tm(int(m["duration"] * F)),
                 "hasVideo": "1", "hasAudio": "0", "videoSources": "1"}
        if fkey in formats:
            attrs["format"] = formats[fkey]
        a = ET.SubElement(res, "asset", **attrs)
        ET.SubElement(a, "media-rep", kind="original-media", src=Path(m["path"]).resolve().as_uri())

    aud_id = new_id()
    aud = ET.SubElement(res, "asset", id=aud_id, name="Voice + Music", start="0s",
                        duration=tm(int(audio_sec * F)), hasVideo="0", hasAudio="1",
                        audioSources="1", audioChannels="2", audioRate="48000")
    ET.SubElement(aud, "media-rep", kind="original-media", src=Path(audio_path).resolve().as_uri())

    lib_el = ET.SubElement(root, "library")
    ev = ET.SubElement(lib_el, "event", name="Sleep on Galaxy")
    proj = ET.SubElement(ev, "project", name=name)
    seq = ET.SubElement(proj, "sequence", format="r1", duration=tm(total_f), tcStart="0s",
                        tcFormat="NDF", audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(seq, "spine")

    def transition(at, dur):
        tr = ET.SubElement(spine, "transition", name="Cross Dissolve",
                           offset=tm(at - dur // 2), duration=tm(dur))
        ET.SubElement(tr, "filter-video", ref="r2", name="Cross Dissolve")

    def black(at, dur):
        ET.SubElement(spine, "gap", name="Black", offset=tm(at), start="0s", duration=tm(dur))

    for i, s in enumerate(shots):
        if i > 0:
            prev_end = shots[i - 1]["start"] + shots[i - 1]["length"]
            if s["start"] > prev_end:            # dip to black: fade out, black, fade in
                transition(prev_end, D)
                black(prev_end, s["start"] - prev_end)
            transition(s["start"], D)
        clip = ET.SubElement(spine, "asset-clip", ref=assets[s["key"]], name=s["key"],
                             offset=tm(s["start"]), start=tm(s["src_in"]),
                             duration=tm(s["length"]))
        if i == 0:   # voice+music track rides under the whole timeline
            ET.SubElement(clip, "asset-clip", ref=aud_id, lane="-1", name="Voice + Music",
                          offset=tm(s["src_in"]), start="0s",
                          duration=tm(min(total_f, int(audio_sec * F))), audioRole="dialogue")
    end = shots[-1]["start"] + shots[-1]["length"]
    if total_f > end:                            # slow fade to black at the very end
        transition(end, min(total_f - end, end_fade_frames(cfg)) // 2 * 2)
        black(end, total_f - end)

    ET.indent(root)
    xml = ET.tostring(root, encoding="unicode")
    Path(path).write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + xml + "\n",
                          encoding="utf-8")


# ------------------------------------------------------ 7. Resolve scripts
# Resolve's built-in Lua can't read files (no io/os), so each run writes the
# Resolve scripts with this episode's details baked in.
LUA_JOB = r"""-- Made by sleep_editor.py for episode: @NAME@
local JOB = {
    name = [==[@NAME@]==],
    fcpxml = [==[@FCPXML@]==],
    render_dir = [==[@RENDER_DIR@]==],
    width = @WIDTH@, height = @HEIGHT@, fps = @FPS@,
    codec = [==[@CODEC@]==],
    video_bitrate_kbps = @BITRATE@,
    auto_render = @AUTO_RENDER@,
}

local rv = resolve or (Resolve and Resolve()) or bmd.scriptapp("Resolve")

local function start_render(project, name)
    rv:OpenPage("deliver")
    project:SetCurrentRenderMode(1)
    if not project:SetCurrentRenderFormatAndCodec("mp4", JOB.codec) then
        print("Couldn't set MP4/H.264 - check the Deliver page settings.")
    end
    local settings = {
        SelectAllFrames = true,
        TargetDir = JOB.render_dir,
        CustomName = name,
        FormatWidth = JOB.width,
        FormatHeight = JOB.height,
        ExportVideo = true,
        ExportAudio = true,
    }
    if JOB.video_bitrate_kbps > 0 then settings.VideoQuality = JOB.video_bitrate_kbps end
    project:SetRenderSettings(settings)
    local id = project:AddRenderJob()
    if not id then
        print("Couldn't add the render job.")
        return
    end
    project:StartRendering(id)
    print("Rendering '" .. name .. "' at " .. JOB.width .. "x" .. JOB.height .. " -> " .. JOB.render_dir)
end
"""

LUA_BUILD = LUA_JOB + r"""
-- colour correction per clip: { slope, offset, power, saturation }
local CDL = {
@CDL@
}

local function find_cdl(item)
    local names = { item:GetName() or "" }
    local mpi = item:GetMediaPoolItem()
    if mpi then names[#names + 1] = mpi:GetName() or "" end
    for _, n in ipairs(names) do
        local c = CDL[n] or CDL[(n:gsub("%.%w+$", ""))]
        if c then return c end
    end
    return nil
end

local function apply_color(tl)
    local found, done = 0, 0
    rv:OpenPage("color")
    for _, item in pairs(tl:GetItemListInTrack("video", 1) or {}) do
        local c = find_cdl(item)
        if c then
            found = found + 1
            local ok = item:SetCDL({ NodeIndex = "1", Slope = c[1], Offset = c[2], Power = c[3], Saturation = c[4] })
            if ok then done = done + 1 end
        end
    end
    rv:OpenPage("edit")
    if found == 0 then return end
    print("Colour correction applied to " .. done .. " of " .. found .. " clips.")
    if done == 0 then
        print("(Resolve didn't accept the colour settings - you can grade on the Color page instead.)")
    end
end

local function build()
print("SleepEditor: building '" .. JOB.name .. "' ... (big timelines take 1-2 minutes)")
print("  timeline file: " .. JOB.fcpxml)
local pm = rv:GetProjectManager()
local project = pm:CreateProject(JOB.name)
local n = 2
while not project and n < 50 do
    project = pm:CreateProject(JOB.name .. "_" .. n)
    n = n + 1
end
if not project then
    print("Couldn't create a new project.")
else
    project:SetSetting("timelineResolutionWidth", tostring(JOB.width))
    project:SetSetting("timelineResolutionHeight", tostring(JOB.height))
    project:SetSetting("timelineFrameRate", tostring(JOB.fps))
    project:SetSetting("timelinePlaybackFrameRate", tostring(JOB.fps))
    local mp = project:GetMediaPool()
    local tl = mp:ImportTimelineFromFile(JOB.fcpxml, { timelineName = JOB.name, importSourceClips = true })
    if not tl then
        print("Timeline import failed. Try File > Import > Timeline and pick:")
        print("  " .. JOB.fcpxml)
    else
        project:SetCurrentTimeline(tl)
        local count = 0
        for _ in pairs(tl:GetItemListInTrack("video", 1) or {}) do count = count + 1 end
        print("Timeline '" .. JOB.name .. "' ready: " .. count .. " clips.")
        apply_color(tl)
        rv:OpenPage("edit")
        pm:SaveProject()
        if JOB.auto_render then
            start_render(project, JOB.name)
        else
            print("Check the timeline, then run Workspace > Scripts > SleepEditor_Render.")
        end
    end
end
end

local ok, err = pcall(build)
if not ok then
    print("SleepEditor ERROR: " .. tostring(err))
    print("Send a screenshot of this Console to Claude.")
end
"""

LUA_RENDER = LUA_JOB + r"""
local project = rv:GetProjectManager():GetCurrentProject()
local tl = project and project:GetCurrentTimeline()
if not tl then
    print("Open the project and timeline you want to render first.")
else
    start_render(project, tl:GetName())
end
"""


def lua_str(s):
    return "[==[" + str(s) + "]==]"


def resolve_utility_folder():
    import os
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Fusion" / "Scripts" / "Utility"
    return BASE / "resolve_scripts"


def install_resolve_scripts(job, cdl=None):
    cdl = cdl or {}
    rows = ",\n".join(f"    [ {lua_str(k)} ] = {{ {', '.join(lua_str(x) for x in v)} }}"
                      for k, v in sorted(cdl.items()))
    subs = {"@NAME@": job["name"], "@FCPXML@": job["fcpxml"], "@RENDER_DIR@": job["render_dir"],
            "@WIDTH@": str(int(job["width"])), "@HEIGHT@": str(int(job["height"])),
            "@FPS@": str(int(job["fps"])), "@CODEC@": job["codec"],
            "@BITRATE@": str(int(job.get("video_bitrate_kbps") or 0)),
            "@AUTO_RENDER@": "true" if job["auto_render"] else "false",
            "@CDL@": rows}

    def fill(tpl):
        for k, v in subs.items():
            tpl = tpl.replace(k, v)
        return tpl

    folder = resolve_utility_folder()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SleepEditor_Build.lua").write_text(fill(LUA_BUILD), encoding="utf-8")
        (folder / "SleepEditor_Render.lua").write_text(fill(LUA_RENDER), encoding="utf-8")
        for old in ("SleepEditor_Build.py", "SleepEditor_Render.py"):
            (folder / old).unlink(missing_ok=True)
        log(f"     Resolve scripts updated in {folder}")
    except OSError as e:
        log(f"  ! couldn't write Resolve scripts to {folder}: {e}")


def make_job(name, cfg):
    ep = EPISODES / name
    render_dir = ep / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    return {"name": name, "fcpxml": str((ep / f"{name}.fcpxml").resolve()),
            "render_dir": str(render_dir.resolve()),
            "width": cfg["width"], "height": cfg["height"], "fps": cfg["fps"],
            "codec": cfg["render_codec"], "video_bitrate_kbps": cfg["video_bitrate_kbps"],
            "auto_render": cfg["auto_render"]}


def load_cdl(name):
    p = EPISODES / name / "color.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# ------------------------------------------------------ input folders
INPUT = BASE / "input"
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma")
FOLDER_HELP = {
    "script": "Put ONE narration script here (.txt). Any file name is fine.",
    "voice": "Put ONE voiceover audio file here (mp3, wav, m4a...). Any file name is fine.",
    "music": "Put ALL your background music here (as many tracks as you like, any names).\n"
             "They are shuffled and crossfaded into one smooth soundtrack for the whole video.",
}


def make_input_folders():
    for name, text in FOLDER_HELP.items():
        d = INPUT / name
        d.mkdir(parents=True, exist_ok=True)
        note = d / "_PUT_FILES_HERE.txt"
        if not note.exists():
            note.write_text(text + "\n", encoding="utf-8")


def files_in(folder, exts):
    if not folder.exists():
        return []
    return sorted((p for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in exts and not p.name.startswith(("_", "."))),
                  key=lambda p: p.name.lower())


def pick_one(kind, folder, exts, old_names):
    """The single script/voice file: from its folder (any name), or the old fixed names."""
    found = files_in(folder, exts)
    if not found:
        found = [INPUT / n for n in old_names if (INPUT / n).exists()]
    if not found:
        return None
    if len(found) > 1:
        newest = max(found, key=lambda p: p.stat().st_mtime)
        log(f"  ! {len(found)} files in input\\{kind} - using the newest one: {newest.name}")
        log(f"    (keep only one {kind} file there to avoid mix-ups)")
        return newest
    return found[0]


def find_inputs(args):
    make_input_folders()
    script = Path(args.script) if args.script else pick_one(
        "script", INPUT / "script", (".txt", ".md"), ["script.txt"])
    voice = Path(args.voice) if args.voice else pick_one(
        "voice", INPUT / "voice", AUDIO_EXT, ["voice.wav", "voice.mp3"])
    if args.music:
        music = [Path(m) for m in args.music]
    else:
        music = files_in(INPUT / "music", AUDIO_EXT) or [
            INPUT / n for n in ("music.mp3", "music.wav") if (INPUT / n).exists()]
    if not voice or not voice.exists():
        die("No voiceover found.\nPut your voiceover (mp3/wav/m4a, any name) in the folder:  input\\voice")
    return script, voice, music


# ------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Sleep on Galaxy auto editor")
    ap.add_argument("name", help="episode name, e.g. ep01_black_holes")
    ap.add_argument("--script", help="narration script (default: the file in input/script)")
    ap.add_argument("--voice", help="voiceover (default: the file in input/voice)")
    ap.add_argument("--music", nargs="*", help="music tracks (default: everything in input/music)")
    ap.add_argument("--plan-only", action="store_true", help="only make scenes.csv, then stop")
    ap.add_argument("--replan", action="store_true", help="rebuild scenes.csv from the script")
    ap.add_argument("--no-download", action="store_true", help="use only clips already downloaded")
    ap.add_argument("--refresh-search", action="store_true", help="ignore cached search results")
    ap.add_argument("--resolve-only", action="store_true",
                    help="only refresh the Resolve scripts for an episode that's already built")
    args = ap.parse_args()

    cfg = load_config()
    if args.resolve_only:
        if not (EPISODES / args.name / f"{args.name}.fcpxml").exists():
            die(f"No timeline found for '{args.name}'. Run 2_make_video.bat first.")
        install_resolve_scripts(make_job(args.name, cfg), load_cdl(args.name))
        log("\nDone! Restart DaVinci Resolve, then run:  Workspace > Scripts > SleepEditor_Build\n")
        return
    need("ffmpeg")
    need("ffprobe")
    script, voice, music = find_inputs(args)
    ep = EPISODES / args.name
    ep.mkdir(parents=True, exist_ok=True)
    scenes_csv = ep / "scenes.csv"
    log(f"\n  script: {script.name if script else '(none)'}")
    log(f"  voice:  {voice.name}")
    log(f"  music:  {len(music)} track(s)" + (": " + ", ".join(m.name for m in music[:6]) +
                                              (" ..." if len(music) > 6 else "") if music else ""))

    vinfo = ffprobe(voice)
    voice_dur = vinfo["duration"] if vinfo else 0
    if not voice_dur:
        die("Couldn't read the voiceover length.")
    log(f"\n== {args.name}  |  voiceover {hms(voice_dur)}")
    pauses = detect_pauses(voice, voice_dur, cfg, ep)
    log(f"     {len(pauses)} pauses found in the voice")

    # 1. scenes
    if args.replan or not scenes_csv.exists():
        if not script or not script.exists():
            die("No script found.\nPut your narration script (.txt, any name) in the folder:  input\\script")
        scenes = plan_scenes(script, voice_dur, cfg, args.name, speech_clock(pauses, voice_dur))
        write_scenes(scenes_csv, scenes)
        log(f"1/5  planned {len(scenes)} scenes -> {scenes_csv}")
    else:
        log(f"1/5  using your existing {scenes_csv.name}")
    if args.plan_only:
        log("\nEdit the 'query' column in scenes.csv if you like, then run 2_make_video.bat.")
        return
    scenes = read_scenes(scenes_csv)

    if not (cfg["pexels_api_key"] or cfg["pixabay_api_key"] or cfg["local_clips_folder"]):
        die("Add a Pexels and/or Pixabay API key (or local_clips_folder) to config.json.")

    # 2. audio
    mix_path = ep / "voice_music_mix.wav"
    audio_sec, played = mix_audio(voice, music, mix_path, cfg, seed=args.name)
    with open(ep / "inputs_used.txt", "w", encoding="utf-8") as f:   # a record of what went in
        f.write(f"Episode: {args.name}\nMade: {time.strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"Script: {script}\nVoice:  {voice}\nMusic tracks:\n")
        for m in music:
            f.write(f"  {m}\n")
    log(f"2/5  audio ready ({hms(audio_sec)}) -> {mix_path.name}")

    # 3. footage
    log("3/5  choosing and downloading footage ...")
    intro = float(cfg["intro_seconds"])
    tl_pauses = [(a + intro, b + intro) for a, b in pauses]
    lib = Library(cfg, allow_download=not args.no_download, refresh=args.refresh_search)
    shots, total_f = build_shots(scenes, tl_pauses, audio_sec, intro, lib, cfg, args.name)
    lib.save()
    F = cfg["fps"]
    with open(ep / "shots.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["shot", "timeline", "seconds", "clip", "search"])
        for i, s in enumerate(shots, 1):
            w.writerow([i, hms(s["start"] / F), round(s["length"] / F, 1), s["key"], s["query"]])
    used = list(dict.fromkeys(s["key"] for s in shots))
    with open(ep / "credits.txt", "w", encoding="utf-8") as f:
        f.write("Footage credits\n")
        for k in used:
            m = lib.index[k]
            if m["source"] != "local":
                site = {"nasa_svs": "NASA SVS", "nasa_images": "NASA", "pixabay": "Pixabay",
                        "pexels": "Pexels"}.get(m["source"], m["source"])
                what = "Visualization" if m["source"] == "nasa_svs" else "Video"
                f.write(f"{what} by {m.get('author') or 'unknown'} ({site}): {m.get('page', '')}\n")
        if played:
            f.write("\nMusic (YouTube Audio Library - check each track's attribution rules)\n")
            for name in dict.fromkeys(p.stem for p in played):
                f.write(f"  {name}\n")
    log(f"     {len(shots)} shots, {len(used)} different clips, {lib.new_downloads} new downloads")
    by_src = {}
    for k in used:
        by_src[lib.index[k]["source"]] = by_src.get(lib.index[k]["source"], 0) + 1
    log("     footage: " + ", ".join(f"{n} {s}" for s, n in sorted(by_src.items())))
    if lib.rejected_now:
        log("     clips thrown out by the quality check: " +
            ", ".join(f"{n} {r}" for r, n in sorted(lib.rejected_now.items(), key=lambda x: -x[1])))

    make_resolve_safe(shots, lib)

    # 4. colour
    cdl = {}
    if cfg.get("color_correction", True):
        log("4/5  colour correction ...")
        cdl = build_color(shots, lib, cfg)
    (ep / "color.json").write_text(json.dumps(cdl, indent=0), encoding="utf-8")

    # 5. timeline
    fcpxml = ep / f"{args.name}.fcpxml"
    write_fcpxml(fcpxml, args.name, shots, total_f, mix_path, audio_sec, lib, cfg)
    job = make_job(args.name, cfg)
    JOB_FILE.write_text(json.dumps(job, indent=1), encoding="utf-8")
    log(f"5/5  timeline written -> {fcpxml}")
    install_resolve_scripts(job, cdl)
    log("\nDone! Restart DaVinci Resolve, open any project, then run:  Workspace > Scripts > SleepEditor_Build\n")


if __name__ == "__main__":
    main()
