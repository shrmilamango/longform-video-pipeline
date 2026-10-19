"""Small shared helpers: paths, logging, running ffmpeg, JSON files."""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PROMPTS = ROOT / "prompts"
LOGS = ROOT / "logs"

for _d in (DATA, LOGS):
    _d.mkdir(parents=True, exist_ok=True)


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def log(msg, slug=None):
    line = f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]" + (f" [{slug}]" if slug else "") + f" {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:  # Windows consoles with old code pages
        print(line.encode("ascii", "replace").decode(), flush=True)
    with open(LOGS / "sog.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def slugify(text, maxlen=60):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:maxlen].strip("-") or "video"


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def ffmpeg_bin():
    return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_bin():
    return shutil.which("ffprobe") or "ffprobe"


def run(cmd, check=True, quiet=True):
    """Run a command list. Raises RuntimeError with the tail of stderr on failure."""
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(map(str, cmd[:6]))} ...\n{tail}")
    if not quiet:
        print(proc.stdout)
    return proc


def ffmpeg(args, check=True):
    return run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y"] + [str(a) for a in args], check=check)


def media_duration(path):
    proc = run([ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def video_size(path):
    proc = run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                "stream=width,height", "-of", "csv=p=0:s=x", str(path)])
    try:
        w, h = proc.stdout.strip().split("x")[:2]
        return int(w), int(h)
    except ValueError:
        return 0, 0


def fmt_ts(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def retry(fn, tries=4, base_delay=5, what="request"):
    """Call fn() with exponential backoff. Re-raises the last error."""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < tries - 1:
                wait = base_delay * (2 ** i)
                log(f"{what} failed ({e.__class__.__name__}: {str(e)[:160]}); retrying in {wait}s")
                time.sleep(wait)
    raise last


def die(msg):
    log("ERROR: " + msg)
    sys.exit(1)
