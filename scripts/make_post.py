import os, base64, requests, datetime, re
from pathlib import Path
import xml.etree.ElementTree as ET

# --- Config knobs via env (safe defaults) ---
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
MODEL = os.environ.get("MODEL", "gpt-5")
IMG_MODEL = os.environ.get("IMG_MODEL", "gpt-image-1")
TOPIC = os.environ.get("POST_TOPIC", "an uplifting micro-story about clarity on a foggy ocean run")
MIN_WORDS = int(os.environ.get("POST_MIN_WORDS", "450"))
MAX_WORDS = int(os.environ.get("POST_MAX_WORDS", "650"))
IMG_SIZE = os.environ.get("IMG_SIZE", "1024x1024")

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
    s = re.sub(r"[^a-zA-Z0-9\- ]", "", s).strip().lower()
    s = re.sub(r"\s+", "-", s)
    return s[:60] if s else "post"

def get_story_and_prompt():
    # Ask the model for structured JSON: {story_html, image_prompt, title}
    prompt = (
        f"Write a {MIN_WORDS}-{MAX_WORDS} word story in clean HTML using only <h2>, <p>, <em>. "
        f"Topic: {TOPIC}. The tone should be warm, grounded, and visual. "
        "Also produce a single-sentence illustration prompt (no camera brands; include subject, mood, composition, light). "
        "Return strict JSON with keys: story_html, image_prompt, title."
    )
    resp = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": MODEL,
            "input": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}
        },
        timeout=120
    )
    resp.raise_for_status()
    data = resp.json()
    # The Responses API returns the JSON object directly in .json() when response_format is json_object
    # Try the common fields first; fall back if nested:
    story_html = data.get("story_html")
    image_prompt = data.get("image_prompt")
    title = data.get("title", "Automated Story")

    # Fallbacks if provider nests under 'output' or 'content'
    if not story_html:
        out = data.get("output") or {}
        story_html = out.get("story_html")
        image_prompt = out.get("image_prompt", image_prompt)
        title = out.get("title", title)

    if not story_html or not image_prompt:
        raise RuntimeError("Model did not return story_html / image_prompt")

    return title, story_html, image_prompt

def generate_image_b64(prompt: str) -> bytes:
    r = requests.post(
        "https://api.openai.com/v1/images",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={"model": IMG_MODEL, "prompt": prompt, "size": IMG_SIZE},
        timeout=180
    )
    r.raise_for_status()
    j = r.json()
    b64 = j["data"][0]["b64_json"]
    return base64.b64decode(b64)

def ensure_feed():
    if FEED.exists():
        return
    FEED.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        '<title>Your Automated Stories</title>'
        '<link>https://example.com/</link>'
        '<description>Auto-generated stories</description>'
        f'<lastBuildDate>{utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")}</lastBuildDate>'
        '</channel></rss>',
        encoding="utf-8"
    )

def append_rss_item(title: str, post_url: str, story_html: str, img_rel_url: str):
    xml = FEED.read_text(encoding="utf-8")
    root = ET.fromstring(xml)
    chan = root.find("channel")
    if chan is None:
        raise RuntimeError("Invalid RSS feed: missing <channel>")

    # Update lastBuildDate
    lbd = chan.find("lastBuildDate")
    if lbd is None:
        lbd = ET.SubElement(chan, "lastBuildDate")
    lbd.text = utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")

    # Create item
    item = ET.SubElement(chan, "item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "link").text = post_url
    ET.SubElement(item, "guid").text = post_url
    ET.SubElement(item, "pubDate").text = utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    desc = ET.SubElement(item, "description")
    desc.text = f"<![CDATA[{story_html}<p><img src='{img_rel_url}' alt='illustration'/></p>]]>"

    # Write back
    new_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    FEED.write_bytes(new_xml)

def main():
    ensure_feed()
    title, story_html, image_prompt = get_story_and_prompt()

    timestamp = utcnow().strftime("%Y%m%d-%H%M%S")
    slug = f"{slugify(title) or 'story'}-{timestamp}"
    img_name = f"{timestamp}.png"

    # 1) Save image
    img_bytes = generate_image_b64(image_prompt)
    (IMGS / img_name).write_bytes(img_bytes)

    # 2) Create post HTML
    rel_img_url = f"/{IMGS.relative_to(DOCS)}/{img_name}"    # /images/<file>
    post_html = (
        f"<h2>{title}</h2>\n"
        f"{story_html}\n"
        f"<p><img src='{rel_img_url}' alt='illustration'/></p>\n"
    )
    post_path = POSTS / f"{slug}.html"
    post_path.write_text(post_html, encoding="utf-8")

    # 3) Append RSS item
    # GitHub Pages final URL = https://<user>.github.io/<repo>/posts/<slug>.html
    base = os.environ.get("PUBLIC_BASE_URL", "https://<your-user>.github.io/<your-repo>")
    post_url = f"{base}/posts/{slug}.html"
    rel_img_for_feed = f"{base}{rel_img_url}"
    append_rss_item(title, post_url, story_html, rel_img_for_feed)

    print({"slug": slug, "post_url": post_url})

if __name__ == "__main__":
    main()

# If running in GitHub Actions, write a step summary
summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
if summary_path:
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(f"## ✅ New post published\n\n")
        f.write(f"- **URL:** {post_url}\n")

# GitHub Actions log annotation
print(f"::notice title=New post::{post_url}")
