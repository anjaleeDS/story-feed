import os
import re
import json
import base64
import requests
import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

# --- Configuration via environment variables ---
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
MODEL = os.environ.get("MODEL", "o4-mini")
IMG_MODEL = os.environ.get("IMG_MODEL", "gpt-image-1")
TOPIC = os.environ.get("POST_TOPIC", "an uplifting micro-story about clarity on a foggy ocean run")
MIN_WORDS = int(os.environ.get("POST_MIN_WORDS", "450"))
MAX_WORDS = int(os.environ.get("POST_MAX_WORDS", "650"))
IMG_SIZE = os.environ.get("IMG_SIZE", "1024x1024")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://anjaleeDS.github.io/story-feed")

# --- Paths (repo-relative) ---
ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
POSTS = DOCS / "posts"
IMGS  = DOCS / "images"
FEED  = DOCS / "feed.xml"

POSTS.mkdir(parents=True, exist_ok=True)
IMGS.mkdir(parents=True, exist_ok=True)

def utcnow():
    return datetime.datetime.utcnow()

def slugify(s: str) -> str:
    # Keep only letters, numbers, space and hyphen; then collapse spaces to hyphens.
    s = re.sub(r"[^A-Za-z0-9 -]", "", s).strip().lower()
    s = re.sub(r"\s+", "-", s)
    return s[:60] or "story"

def ensure_feed():
    if FEED.exists():
        return
    FEED.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        '<title>Your Automated Stories</title>'
        f'<link>{PUBLIC_BASE_URL}/</link>'
        '<description>Auto-generated stories</description>'
        f'<lastBuildDate>{utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")}</lastBuildDate>'
        '</channel></rss>',
        encoding="utf-8"
    )

def get_story_and_prompt():
    system = "You are a concise literary editor and illustration prompt-writer. Return strictly valid JSON."
    user = (
        f"Write a {MIN_WORDS}-{MAX_WORDS} word story in clean HTML using only <h2>, <p>, <em>. "
        f"Topic: {TOPIC}. Tone: warm, grounded, visual. "
        "Also produce a single-sentence illustration prompt (no camera brands; include subject, mood, composition, light) "
        "and a short natural language title. "
        "Return a JSON object with keys: title, story_html, image_prompt — nothing else."
    )

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers=headers,
        json={
            "model": MODEL,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Proper JSON mode for Responses API
            "text": {"format": {"type": "json_object"}}
        },
        timeout=120
    )
    r.raise_for_status()
    data = r.json()

    def try_extract(obj):
        if isinstance(obj, dict) and {"title","story_html","image_prompt"} <= set(obj.keys()):
            return obj.get("title") or "Automated Story", obj["story_html"], obj["image_prompt"]
        return None

    # 1) Top-level
    got = try_extract(data)
    if got: return got

    # 2) Nested dict under "output"
    out = data.get("output")
    got = try_extract(out)
    if got: return got

    # 3) "output" list → find message → content[].text (dict or JSON string)
    if isinstance(out, list):
        for item in out:
            if item.get("type") == "message":
                for seg in item.get("content", []):
                    t = seg.get("text")
                    got = try_extract(t)
                    if got: return got
                    if isinstance(t, str):
                        try:
                            parsed = json.loads(t)
                            got = try_extract(parsed)
                            if got: return got
                        except Exception:
                            pass

    # 4) Top-level "content"
    content = data.get("content")
    if isinstance(content, list):
        for seg in content:
            t = seg.get("text")
            got = try_extract(t)
            if got: return got
            if isinstance(t, str):
                try:
                    parsed = json.loads(t)
                    got = try_extract(parsed)
                    if got: return got
                except Exception:
                    pass

    raise RuntimeError(f"Missing keys in JSON response: {json.dumps(data)[:800]}")

def generate_image_b64(prompt: str) -> bytes:
    r = requests.post(
        "https://api.openai.com/v1/images",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={"model": IMG_MODEL, "prompt": prompt, "size": IMG_SIZE},
        timeout=180
    )
    r.raise_for_status()
    b64 = r.json()["data"][0]["b64_json"]
    return base64.b64decode(b64)

def append_rss_item(title: str, post_url: str, story_html: str, img_abs_url: str):
    xml = FEED.read_text(encoding="utf-8")
    root = ET.fromstring(xml)
    chan = root.find("channel")
    if chan is None:
        raise RuntimeError("Invalid RSS feed: missing <channel>")

    lbd = chan.find("lastBuildDate") or ET.SubElement(chan, "lastBuildDate")
    lbd.text = utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")

    item = ET.SubElement(chan, "item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "link").text = post_url
    ET.SubElement(item, "guid").text = post_url
    ET.SubElement(item, "pubDate").text = utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    desc = ET.SubElement(item, "description")
    desc.text = f"<![CDATA[{story_html}<p><img src='{img_abs_url}' alt='illustration'/></p>]]>"
    FEED.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))

def main():
    ensure_feed()
    title, story_html, image_prompt = get_story_and_prompt()

    timestamp = utcnow().strftime("%Y%m%d-%H%M%S")
    slug = f"{slugify(title) or 'story'}-{timestamp}"
    img_name = f"{timestamp}.png"

    # Save image
    img_bytes = generate_image_b64(image_prompt)
    (IMGS / img_name).write_bytes(img_bytes)

    # Write post HTML
    rel_img_url = f"/{IMGS.relative_to(DOCS)}/{img_name}"
    post_html = f"<h2>{title}</h2>\n{story_html}\n<p><img src='{rel_img_url}' alt='illustration'/></p>\n"
    post_path = POSTS / f"{slug}.html"
    post_path.write_text(post_html, encoding="utf-8")

    # Update RSS
    post_url = f"{PUBLIC_BASE_URL}/posts/{slug}.html"
    img_abs_url = f"{PUBLIC_BASE_URL}{rel_img_url}"
    append_rss_item(title, post_url, story_html, img_abs_url)

    print({"slug": slug, "post_url": post_url})

    # GitHub Actions summary + notice
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("## ✅ New post published\n")
            f.write(f"- **URL:** {post_url}\n\n")
    print(f"::notice title=New post::{post_url}")

if __name__ == "__main__":
    main()
