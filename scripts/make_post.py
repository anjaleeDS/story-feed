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

def generate_image(prompt: str):
    """
    Generate an image with OpenAI Images API.
    Returns (image_bytes, ext) where ext is 'png'/'jpg'/'webp'/'svg'.
    Prefer PNG; retry/parse URL or SVG if b64_json is not supported.
    """
    import mimetypes

    url = "https://api.openai.com/v1/images/generations"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    base_payload = {
        "model": IMG_MODEL,   # e.g., "gpt-image-1"
        "prompt": prompt,
        "size": IMG_SIZE,     # e.g., "1024x1024"
    }

    def parse_items(data):
        items = (data or {}).get("data") or []
        if not items:
            raise RuntimeError(f"Unexpected image API response (no data): {str(data)[:600]}")
        item = items[0]

        # 1) b64 -> PNG
        b64 = item.get("b64_json")
        if b64:
            return base64.b64decode(b64), "png"

        # 2) URL -> download and infer extension
        url_field = item.get("url")
        if url_field:
            resp = requests.get(url_field, timeout=180)
            resp.raise_for_status()
            ct = (resp.headers.get("Content-Type") or "").lower()
            if "png" in ct:
                ext = "png"
            elif "jpeg" in ct or "jpg" in ct:
                ext = "jpg"
            elif "webp" in ct:
                ext = "webp"
            elif "svg" in ct:
                ext = "svg"
            else:
                # Try to guess from URL as a fallback
                guess = (mimetypes.guess_extension(ct) or "").lstrip(".")
                ext = guess if guess else "png"
            return resp.content, ext

        # 3) Direct SVG string
        if "svg" in item and item["svg"]:
            return item["svg"].encode("utf-8"), "svg"

        raise RuntimeError(f"No b64_json/url/svg in image response: {str(data)[:600]}")

    try:
        # Attempt 1: ask for PNG bytes explicitly
        payload = dict(base_payload, response_format="b64_json")
        r = requests.post(url, headers=headers, json=payload, timeout=180)
        if r.status_code in (401, 403):
            raise PermissionError(f"Images forbidden {r.status_code}: {r.text[:600]}")
        if r.status_code == 400:
            # Some orgs/models reject response_format -> warn & retry without it
            try:
                print("::warning::images b64_json attempt 400:", r.json())
            except Exception:
                print("::warning::images b64_json attempt 400:", r.text[:600])
            r2 = requests.post(url, headers=headers, json=base_payload, timeout=180)
            if r2.status_code in (401, 403):
                raise PermissionError(f"Images forbidden {r2.status_code}: {r2.text[:600]}")
            r2.raise_for_status()
            return parse_items(r2.json())

        r.raise_for_status()
        return parse_items(r.json())

    except PermissionError:
        # Only for auth/permission blocks do we use the local SVG so the pipeline still publishes
        safe = (prompt or "illustration").strip().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        snippet = (safe[:160] + "…") if len(safe) > 160 else safe
        svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#e8eef9"/><stop offset="100%" stop-color="#d4ece1"/></linearGradient></defs>
  <rect width="100%" height="100%" fill="url(#g)"/>
  <g transform="translate(60,140)">
    <text x="0" y="0" font-family="Helvetica, Arial, sans-serif" font-size="48" font-weight="700" fill="#222">Auto Illustration</text>
    <foreignObject x="0" y="40" width="904" height="820">
      <div xmlns="http://www.w3.org/1999/xhtml" style="font-family: Helvetica, Arial, sans-serif; font-size: 28px; line-height: 1.35; color:#333;">{snippet}</div>
    </foreignObject>
  </g>
</svg>"""
        return svg.encode("utf-8"), "svg"

    except Exception as e:
        # Surface payload snippet for fast diagnosis next time
        try:
            print("::error::Images API unexpected error:", str(e)[:300])
        finally:
            raise

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

    # --- Generate filenames and slugs ---
    timestamp = utcnow().strftime("%Y%m%d-%H%M%S")
    slug = f"{slugify(title) or 'story'}-{timestamp}"

    # --- Generate and save image ---
    img_bytes, img_ext = generate_image(image_prompt)
    img_name = f"{timestamp}.{img_ext}"
    (IMGS / img_name).write_bytes(img_bytes)

    # --- Prepare image paths ---
    # For HTML: relative path (GitHub Pages serves posts under /story-feed/posts/)
    html_img_src = f"../images/{img_name}"

    # For RSS: absolute URL
    img_abs_url = f"{PUBLIC_BASE_URL}/images/{img_name}"

    # --- Build post HTML ---
    post_html = (
        f"<h2>{title}</h2>\n"
        f"{story_html}\n"
        f"<p><img src='{html_img_src}' alt='illustration'/></p>\n"
    )
    post_path = POSTS / f"{slug}.html"
    post_path.write_text(post_html, encoding="utf-8")

    # --- Build URLs for RSS and logging ---
    post_url = f"{PUBLIC_BASE_URL}/posts/{slug}.html"

    # --- Append to RSS feed ---
    append_rss_item(title, post_url, story_html, img_abs_url)

    # --- Print JSON log (useful for debugging in Actions) ---
    print({"slug": slug, "post_url": post_url})

    # --- GitHub Actions summary + notice ---
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("## ✅ New post published\n")
            f.write(f"- **Title:** {title}\n")
            f.write(f"- **URL:** {post_url}\n\n")

    print(f"::notice title=New post::{post_url}")
if __name__ == "__main__":
    main()
