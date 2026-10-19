"""Topic research: finds the videos that are beating their own channel's normal numbers.

Uses the free YouTube Data API key (10,000 units a day; one full run uses roughly 400-600).
Output: data/research/latest.md (readable report), a CSV, and a topics prompt.
"""
import csv
import datetime as dt
import re
import statistics

import requests

from . import config, llm
from .notify import notify
from .util import DATA, PROMPTS, log, read_json, retry, write_json

API = "https://www.googleapis.com/youtube/v3/"
OUT = DATA / "research"


def _get(endpoint, **params):
    key = config.key("youtube_api_key")
    if not key:
        raise RuntimeError("youtube_api_key is empty in config.yaml (it's free - see README step 2)")
    params["key"] = key

    def call():
        r = requests.get(API + endpoint, params=params, timeout=30)
        if r.status_code == 403 and "quota" in r.text.lower():
            raise SystemExit("YouTube API daily quota used up - it resets at midnight Pacific time.")
        if r.status_code != 200:
            raise RuntimeError(f"YouTube API {endpoint} HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    return retry(call, tries=3, base_delay=5, what=f"YouTube {endpoint}")


def _duration_minutes(iso):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0.0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 1440 + h * 60 + mi + s / 60


def _parse_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def resolve_channel(ref):
    ref = ref.strip()
    params = {"part": "snippet,contentDetails,statistics"}
    if ref.startswith("UC") and len(ref) == 24:
        params["id"] = ref
    else:
        params["forHandle"] = ref if ref.startswith("@") else "@" + ref
    items = _get("channels", **params).get("items", [])
    if not items:
        return None
    c = items[0]
    return {"id": c["id"], "title": c["snippet"]["title"],
            "uploads": c["contentDetails"]["relatedPlaylists"]["uploads"],
            "subs": int(c["statistics"].get("subscriberCount", 0) or 0)}


def channel_uploads(uploads_playlist, pages=2):
    ids, token = [], None
    for _ in range(pages):
        params = {"part": "contentDetails", "playlistId": uploads_playlist, "maxResults": 50}
        if token:
            params["pageToken"] = token
        data = _get("playlistItems", **params)
        ids += [it["contentDetails"]["videoId"] for it in data.get("items", [])]
        token = data.get("nextPageToken")
        if not token:
            break
    return video_details(ids)


def video_details(ids):
    out = []
    for i in range(0, len(ids), 50):
        data = _get("videos", part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50]))
        for v in data.get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            thumbs = sn.get("thumbnails", {})
            out.append({
                "id": v["id"], "title": sn["title"], "channel": sn["channelTitle"], "channel_id": sn["channelId"],
                "published": sn["publishedAt"], "views": int(st.get("viewCount", 0) or 0),
                "minutes": round(_duration_minutes(v["contentDetails"].get("duration")), 1),
                "thumb": (thumbs.get("maxres") or thumbs.get("high") or thumbs.get("default") or {}).get("url", ""),
            })
    return out


def _score(videos, now):
    """Adds age, views/day and 'x times the channel's normal' to each video of one channel."""
    for v in videos:
        v["age_days"] = max(1, (now - _parse_time(v["published"])).days)
        v["views_per_day"] = round(v["views"] / v["age_days"], 1)
    mature = [v["views"] for v in videos if v["age_days"] >= 7] or [v["views"] for v in videos]
    median = statistics.median(mature) if mature else 0
    for v in videos:
        v["channel_median"] = int(median)
        v["outlier_x"] = round(v["views"] / median, 2) if median else 0.0
    return videos


def run_research():
    if not config.key("youtube_api_key"):
        raise RuntimeError("youtube_api_key is empty in config.yaml (it's free - see README step 2)")
    cfg = config.load()["research"]
    now = dt.datetime.now(dt.timezone.utc)
    OUT.mkdir(parents=True, exist_ok=True)
    pool, seen_channels, warnings = [], set(), []

    def add_channel(ref):
        ch = resolve_channel(ref)
        if not ch:
            warnings.append(f"Channel not found: {ref}")
            return
        if ch["id"] in seen_channels:
            return
        seen_channels.add(ch["id"])
        vids = _score(channel_uploads(ch["uploads"]), now)
        for v in vids:
            v["subs"] = ch["subs"]
        pool.extend(vids)
        log(f"research: {ch['title']} - {len(vids)} videos")

    for ref in cfg["competitors"]:
        try:
            add_channel(ref)
        except RuntimeError as e:
            warnings.append(f"{ref}: {e}")

    # Discovery: popular long sleep-space videos from channels we don't track yet.
    after = (now - dt.timedelta(days=int(cfg["lookback_days"]))).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_channels = []
    for q in cfg.get("discover_queries", []):
        try:
            data = _get("search", part="snippet", q=q, type="video", videoDuration="long", order="viewCount",
                        publishedAfter=after, maxResults=25, relevanceLanguage="en", regionCode="US")
        except RuntimeError as e:
            warnings.append(f"search '{q}': {e}")
            continue
        for it in data.get("items", []):
            cid = it["snippet"]["channelId"]
            if cid not in seen_channels and cid not in new_channels:
                new_channels.append(cid)
    for cid in new_channels[:15]:
        try:
            add_channel(cid)
        except RuntimeError as e:
            warnings.append(f"{cid}: {e}")

    # Our own channel: what we've already covered, and what worked.
    own_id = config.load()["channel"]["channel_id"]
    own = []
    try:
        ch = resolve_channel(own_id)
        if ch:
            own = _score(channel_uploads(ch["uploads"]), now)
    except RuntimeError as e:
        warnings.append(f"own channel: {e}")

    if not pool:
        raise RuntimeError("research found no videos: " + " | ".join(warnings[:3]))
    recent = [v for v in pool if v["minutes"] >= cfg["min_minutes"] and v["age_days"] <= cfg["lookback_days"]
              and v["channel_id"] != own_id]
    recent.sort(key=lambda v: (v["outlier_x"], v["views_per_day"]), reverse=True)
    top = recent[: int(cfg["top_n"])]
    evergreen = sorted([v for v in pool if v["minutes"] >= cfg["min_minutes"] and v["channel_id"] != own_id],
                       key=lambda v: v["views"], reverse=True)[:15]

    stamp = now.strftime("%Y-%m-%d")
    with open(OUT / f"research-{stamp}.csv", "w", newline="", encoding="utf-8") as f:
        cols = ["outlier_x", "views", "views_per_day", "age_days", "minutes", "channel", "subs", "title", "id", "thumb"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(recent)

    report = _report(top, evergreen, own, warnings, stamp)
    (OUT / "latest.md").write_text(report, encoding="utf-8")
    write_json(OUT / "last_run.json", {"date": stamp, "top": top, "own": own})
    _topics_prompt(report)
    log(f"research done: {len(recent)} recent long videos scored, report in data/research/latest.md")
    if warnings:
        log("research warnings: " + " | ".join(warnings))
    return report


def _row(v):
    return (f"| {v['outlier_x']}x | {v['views']:,} | {v['views_per_day']:,} | {v['age_days']}d | "
            f"{v['channel']} | [{v['title']}](https://youtu.be/{v['id']}) |")


def _report(top, evergreen, own, warnings, stamp):
    lines = [f"# Topic research - {stamp}", "",
             "**x normal** = views compared with that channel's usual video. 3x+ means the topic or packaging "
             "pulled far more people than usual.", "",
             "## Recent winners (sleep-space, long-form)", "",
             "| x normal | views | views/day | age | channel | title |", "|---|---|---|---|---|---|"]
    lines += [_row(v) for v in top]
    lines += ["", "## All-time biggest in the niche", "", "| x normal | views | views/day | age | channel | title |",
              "|---|---|---|---|---|---|"]
    lines += [_row(v) for v in evergreen]
    if own:
        best = sorted(own, key=lambda v: v["views"], reverse=True)
        lines += ["", "## Our channel (best first)", "", "| x normal | views | views/day | age | channel | title |",
                  "|---|---|---|---|---|---|"]
        lines += [_row(v) for v in best[:40]]
    if warnings:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in warnings]
    return "\n".join(lines) + "\n"


def _topics_prompt(report):
    tpl = (PROMPTS / "topics_prompt.md").read_text(encoding="utf-8")
    prompt = tpl.replace("{{REPORT}}", report)
    (OUT / "topics_prompt.md").write_text(prompt, encoding="utf-8")
    if config.load()["ai"]["topics_mode"] == "gemini" and llm.available():
        ideas = llm.generate(prompt, temperature=0.9)
        (OUT / "ideas.md").write_text(ideas + "\n", encoding="utf-8")
        notify("New topic ideas are ready. Run: python sog.py ideas")
    else:
        notify("Research report ready. Paste data/research/topics_prompt.md into your Claude Project, "
               "save the answer as data/research/ideas.md, then run: python sog.py ideas")


def research_due():
    last = read_json(OUT / "last_run.json")
    if not last:
        return True
    every = int(config.load()["research"].get("run_every_days", 2))
    last_date = dt.date.fromisoformat(last["date"])
    return (dt.date.today() - last_date).days >= every


def list_ideas():
    path = OUT / "ideas.md"
    if not path.exists():
        return []
    ideas = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(\d+)[.)]\s+(.*)", line)
        if m:
            text = m.group(2).strip()
            title = re.split(r"\s+[—–-]\s+|\s+\|\s+", text, maxsplit=1)[0].strip().strip("*").strip('"')
            ideas.append({"n": int(m.group(1)), "title": title, "line": text})
    return ideas
