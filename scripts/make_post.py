import os, re, json, base64, requests, datetime
from pathlib import Path
import xml.etree.ElementTree as ET

# ---------- Config via env ----------
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
MODEL     = os.environ.get("MODEL", "o4-mini")
IMG_MODEL = os.environ.get("IMG_MODEL", "gpt-image-1")
TOPIC     = os.environ.get("POST_TOPIC", "an uplifting micro-story about clarity on a foggy ocean run")
MIN_WORDS = int(os.environ.get("POST_MIN_WORDS", "450"))
MAX_WORDS = int(os.environ.get("POST_MAX_WORDS", "650"))
IMG_SIZE  = os.environ.get("IMG_SIZE", "1024x1024")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://anjaleeDS.github.io/story-feed")

# ---------- Paths ----------
ROOT  = Path(__file__).resolve().parents[1]
DOCS  = ROOT / "docs"
POSTS = DOCS / "posts"
IMGS  = DOCS / "images"
FEED  = DOCS / "feed.xml"
POSTS.mkdir(parents=True, exist_ok=True)
IMGS.mkdir(parents=True, exist_ok=True)

def utcnow():
    return datetime.datetime.utcnow()

def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\- ]", "", s).strip().lower()
    s = re.sub(r"\s+", "-", s)
    return s[:60] or "story"

def ensure_feed():
    """Create a minimal RSS feed if docs/feed.xml is missing."""
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
    """Try Responses API first (json_object), then fall back to Chat Completions."""
    system = "You are a concise literary editor and illustration prompt-writer. Return clean JSON only."
    user = (
        f"Write a {MIN_WORDS}-{MAX_WORDS} word story in clean HTML using only <h2>, <p>, <em>. "
        f"Topic: {TOPIC}. Tone: warm, grounded, visual. "
        "Also produce a single-sentence illustration prompt (no camera brands; include subject, mood, composition, light), "
        "and a short human title. "
        'Return STRICT JSON with keys: {"title": "...", "story_html": "...", "image_prompt": "..."} — nothing else.'
    )
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

    # ---- Responses API
    try:
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers=headers,
            json={
                "model": MODEL,
                "input": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        if r.status_code == 200:
            data = r.json()
            obj = data
            # Some providers nest under 'output' or under content[0].text
            if "output" in data and isinstance(data["output"], dict):
                obj = data["output"]
            if not {"title","story_html","image_prompt"} <= set(obj.keys()):
                try:
                    txt = (data.get("content") or [{}])[0].get("text", "")
                    obj = json.loads(txt)
                except Exception:
                    pass
            title = obj["title"]; story_html = obj["story_html"]; image_prompt = obj["image_prompt"]
            return title, story_html, image_prompt
        else:
            print("::warning::Responses API body:", r.text[:1200])
    except Exception as e:
        print(f"::warning::Responses API call failed: {e}")

    # ---- Chat Completions fallback
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user + " Return ONLY the JSON."},
                ],
                "temperature": 0.8,
            },
            timeout=120,
        )
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"].strip()
        obj = json.loads(txt)
        return obj["title"], obj["story_html"], obj["image_prompt"]
    except Exception as e:
        try:
            print("::error::Chat body:", r.text[:1500])
        except Exception:
            pass
        raise RuntimeError(f"Failed to get story JSON: {e}")

def generate_image_bytes(prompt: str) -> bytes:
    r = requests.post(
        "https://api.openai.com/v1/images",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={"model": IMG_MODEL, "prompt": prompt, "size": IMG_SIZE},
        timeout=180,
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

    # Update lastBuildDate
    lbd = chan.find("lastBuildDate") or ET.SubElement(chan, "lastBuildDate")
    lbd.text = utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")

    # Append item
    item = ET.SubElement(chan, "item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "link").text = post_url
    ET.SubElement(item, "guid").text = post_url
    ET.SubElement(item, "pubDate").text = utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    desc = ET.SubElement(item, "description")
    desc.text = f"<![CDATA[{story_html}<p><img src='{img_abs_url}' alt='illustration'/></p>]]>"

    new_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    FEED.write_bytes(new_xml)

def main():
    ensure_feed()

    title, story_html, image_prompt = get_story_and_prompt()
    ts   = utcnow().strftime("%Y%m%d-%H%M%S")
    slug = f"{slugify(title)}-{ts}"
    img_name = f"{ts}.png"

    # 1) Save image
    img_bytes = generate_image_bytes(image_prompt)
    (IMGS / img_name).write_bytes(img_bytes)

    # 2) Write post HTML
    rel_img = f"/{IMGS.relative_to(DOCS)}/{img_name}"          # /images/<file>
    post_html = f"<h2>{title}</h2>\n{story_html}\n<p><img src='{rel_img}' alt='illustration'/></p>\n"
    post_path = POSTS / f"{slug}.html"
    post_path.write_text(post_html, encoding="utf-8")

    # 3) Update feed
    post_url = f"{PUBLIC_BASE_URL}/posts/{slug}.html"
    img_abs  = f"{PUBLIC_BASE_URL}{rel_img}"
    append_rss_item(title, post_url, story_html, img_abs)

    # 4) Surface outputs
    print({"slug": slug, "post_url": post_url})
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("## ✅ New post published\n")
            f.write(f"- **URL:** {post_url}\n\n")
    print(f"::notice title=New post::{post_url}")

if __name__ == "__main__":
    main()
