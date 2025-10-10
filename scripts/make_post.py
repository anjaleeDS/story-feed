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
IMG_MODEL = os.environ.get("IMG_MODEL", "dall-e-2")
TOPIC = os.environ.get("POST_TOPIC", "a horror story in three sentences")
MIN_WORDS = int(os.environ.get("POST_MIN_WORDS", "10"))
MAX_WORDS = int(os.environ.get("POST_MAX_WORDS", "55"))
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
    
# ----------------------- OpenAI: Story JSON ----------------------------------

def get_story_and_prompt():
    system = "You are a concise literary editor and illustration prompt-writer. Return strictly valid JSON."
    user = (
        f"Write a {MIN_WORDS}-{MAX_WORDS} word story in clean HTML using only <h2>, <p>, <em>. "
        f"Topic: {TOPIC}. Tone: warm, scary, hypervisual. "
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
    
# --------------------- OpenAI: Image Generation (strict) ----------------------

def generate_image(prompt: str):
    """
    Generate an image with OpenAI Images API.
    Returns (image_bytes, ext) where ext is 'png'.
    """
    r = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={"model": IMG_MODEL, "prompt": prompt, "size": IMG_SIZE, "response_format": "b64_json"},
        timeout=180
    )
    if r.status_code != 200:
        raise RuntimeError(f"Images API error {r.status_code}: {r.text[:800]}")
    b64 = r.json()["data"][0]["b64_json"]
    return base64.b64decode(b64), "png"
    
# ---------------------------- RSS Update -------------------------------------
def append_rss_item(title: str, post_url: str, story_html: str, img_abs_url: str, img_mime: str):
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
    desc.text = f"<![CDATA[{story_html}<p><img src='{img_abs_url}' alt='illustration'/></p>]]>"

    # Helpful for some consumers: <enclosure>
    enc = ET.SubElement(item, "enclosure")
    enc.set("url", img_abs_url)
    enc.set("type", img_mime)

    new_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    FEED.write_bytes(new_xml)

# ------------------------------ Main -----------------------------------------

def main():
    ensure_feed()
    title, story_html, image_prompt = get_story_and_prompt()

    timestamp = utcnow().strftime("%Y%m%d-%H%M%S")
    slug = f"{slugify(title)}-{timestamp}"

    # Image
    img_bytes, img_ext = generate_image(image_prompt)
    print({"image_ext": img_ext})
    img_name = f"{timestamp}.{img_ext}"
    (IMGS / img_name).write_bytes(img_bytes)

    # Paths/URLs
    rel_img_url = f"/{IMGS.relative_to(DOCS)}/{img_name}"   # served by GH Pages
    base = PUBLIC_BASE_URL or "https://<your-user>.github.io/<your-repo>"
    img_abs_url = f"{base}/images/{img_name}"
    post_url = f"{base}/posts/{slug}.html"

    # MIME for enclosure
    img_mime = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "svg": "image/svg+xml",
    }.get(img_ext.lower(), "image/png")

    # Pretty HTML post with sticky top bar + Close button
    home_url = base
    pretty_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{
      --bg: #0e0f11;
      --fg: #f6f7fb;
      --muted: #a5adba;
      --card: #12151b;
      --accent: #7aa2ff;
      --ring: rgba(122,162,255,.35);
      --maxw: 900px;
      --radius: 16px;
      --shadow: 0 10px 30px rgba(0,0,0,.35);
    }}
    @media (prefers-color-scheme: light) {{
      :root {{
        --bg: #ffffff;
        --fg: #121317;
        --muted: #68707c;
        --card: #f7f8fb;
        --accent: #1a73e8;
        --ring: rgba(26,115,232,.25);
        --shadow: 0 12px 28px rgba(16,24,40,.12);
      }}
    }}
    html, body {{
      margin: 0; background: var(--bg); color: var(--fg);
      font: 16px/1.65 system-ui, -apple-system, Segoe UI, Roboto, Inter, Arial, sans-serif;
      text-rendering: optimizeLegibility; -webkit-font-smoothing: antialiased;
    }}
    .topbar {{
      position: sticky; top: 0; z-index: 3;
      backdrop-filter: blur(8px);
      background: color-mix(in oklab, var(--bg) 85%, transparent);
      border-bottom: 1px solid color-mix(in oklab, var(--fg) 15%, transparent);
    }}
    .topwrap {{
      max-width: var(--maxw); margin: 0 auto; padding: 10px 16px;
      display:flex; gap:12px; align-items:center; justify-content:space-between;
    }}
    .title {{
      font-size: clamp(1.1rem, 2.2vw, 1.5rem); font-weight: 640; margin: 0;
      letter-spacing: .2px;
    }}
    .close {{
      appearance: none; border: 1px solid color-mix(in oklab, var(--fg) 12%, transparent);
      background: color-mix(in oklab, var(--card) 70%, transparent);
      color: var(--fg); padding: 8px 12px; border-radius: 999px; cursor: pointer;
      font-weight: 600; transition: .15s ease; box-shadow: 0 0 0 0 var(--ring);
    }}
    .close:hover {{ transform: translateY(-1px); border-color: color-mix(in oklab, var(--fg) 22%, transparent); }}
    .close:focus-visible {{ outline: none; box-shadow: 0 0 0 6px var(--ring); }}
    .wrap {{ max-width: var(--maxw); margin: 24px auto 60px auto; padding: 0 16px; }}
    figure {{
      margin: 0 0 18px 0; display:block;
      background: color-mix(in oklab, var(--fg) 4%, transparent);
      border-radius: var(--radius); overflow: hidden; box-shadow: var(--shadow);
    }}
    img.post {{
      width: 100%; height: auto; display:block; object-fit: cover;
    }}
    article {{ font-size: 1.05rem; }}
    article h2 {{ font-size: 1.6rem; line-height:1.25; margin: 12px 0 8px 0; }}
    article p {{ margin: 0 0 12px 0; }}
    footer.note {{
      margin-top: 28px; color: var(--muted); font-size: .95rem; text-align:center;
    }}
    a.home {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
    a.home:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <div class="topbar">
    <div class="topwrap">
      <h1 class="title">{title}</h1>
      <button class="close" onclick="smartClose()" aria-label="Close this story">Close ✕</button>
    </div>
  </div>

  <main class="wrap">
    <figure>
      <img class="post" src="{rel_img_url}" alt="illustration for {title}">
    </figure>

    <article>
      {story_html}
    </article>

    <footer class="note">
      <a class="home" href="{home_url}">← Back to all stories</a>
    </footer>
  </main>

  <script>
    function smartClose() {{
      if (window.opener && !window.opener.closed) {{ window.close(); return; }}
      if (document.referrer && history.length > 1) {{ history.back(); return; }}
      window.location.href = "{home_url}";
    }}
    window.addEventListener('keydown', (e) => {{ if (e.key === 'Escape') smartClose(); }});
  </script>
</body>
</html>"""

    post_path = POSTS / f"{slug}.html"
    post_path.write_text(pretty_html, encoding="utf-8")

    # RSS
    append_rss_item(title, post_url, story_html, img_abs_url, img_mime)

    # Summary for GitHub Actions + console notice
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("## ✅ New post published\n")
            f.write(f"- **Title:** {title}\n")
            f.write(f"- **URL:** {post_url}\n")
            f.write(f"- **Image:** {img_abs_url}\n")
            f.write(f"- **Image file:** {img_name}\n\n")
    print(f"::notice title=New post::{post_url}")

if __name__ == "__main__":
    main()
