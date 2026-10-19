"""Optional automatic writing with Google's free Gemini API key.

In "manual" mode nothing here is used: the system writes a prompt file and you paste it
into your Claude Project instead.
"""
import requests

from . import config
from .util import retry

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def available():
    return bool(config.key("gemini_api_key"))


def generate(prompt, max_tokens=8192, temperature=0.8):
    key = config.key("gemini_api_key")
    if not key:
        raise RuntimeError("gemini_api_key is empty in config.yaml")
    model = config.load()["ai"]["gemini_text_model"]

    def call():
        r = requests.post(API.format(model=model), headers={"x-goog-api-key": key}, timeout=300, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        })
        if r.status_code != 200:
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()

    return retry(call, tries=4, base_delay=20, what="Gemini text")
