#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
make_post.py
Generates a short story + image, writes an HTML post under docs/posts/,
saves the image under docs/images/, and appends an item to docs/feed.xml.

Configuration (via env vars):
  OPENAI_API_KEY
  MODEL              (default: "gpt-5")
  IMG_MODEL          (default: "dall-e-2")
  POST_TOPIC         (default: "a horror story in three sentences")
  POST_MIN_WORDS     (default: 10)
  POST_MAX_WORDS     (default: 55)
  IMG_SIZE           (default: "1024x1024")
  PUBLIC_BASE_URL    (default: "https://anjaleeds.github.io/story-feed")
"""

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
MODEL = os.environ.get("MODEL", "gpt-5")
IMG_MODEL = os.environ.get("IMG_MODEL", "dall-e-2")
TOPIC = os.environ.get("POST_TOPIC", "a horror story in three sentences")
MIN_WORDS = int(os.environ.get("POST_MIN_WORDS", "10"))
MAX_WORDS = int(os.environ.get("POST_MAX_WORDS", "55"))
IMG_SIZE = os.environ.get("IMG_SIZE", "1024x1024")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://anjaleeds.github.io/story-feed")

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

def utcnow_randomized_today():
    """
    Return today's date in UTC with a random time between RANDOM_HOUR_START and RANDOM_HOUR_END inclusive.
    """
    now = utcnow()
    start = max(0, min(23, RANDOM_HOUR_START))
    end   = max(start, min(23, RANDOM_HOUR_END))
    hour = random.randint(start, end)
    minute = random.randint(0, 59)
    second = random.randint(0, 59)
    return now.replace(hour=hour, minute=minute, second=second, microsecond=0)


def slugify(s: str) -> str:
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
    """
    Robustly fetch {title, story_html, image_prompt} via Responses API,
    handling multiple response shapes. Falls back to Chat Completions.
    """
    system = "You are a concise literary editor and illustration prompt-writer. Return strictly valid JSON."
    user = (
        f"Write a {MIN_WORDS}-{MAX_WORDS} word story in clean HTML using only <h2>, <p>, <em>. "
        f"Topic: {TOPIC}. Tone: vivid, eerie, cinematic. "
        "Also produce a one-sentence illustration prompt (no camera brands; include subject, mood, composition, light) "
        "and a short natural-language title. Return only a JSON object with keys: title, story_html, image_prompt."
    )

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

    def try_parse_obj(obj):
        """Return (title, story_html, image_prompt) or None."""
        if not isinstance(obj, dict):
            return None
        if {"title", "story_html", "image_prompt"} <= set(obj.keys()):
            title = (obj.get("title") or "Automated Story").strip()
            story_html = obj.get("story_html") or ""
            image_prompt = obj.get("image_prompt") or ""
            if story_html and image_prompt:
                return title, story_html, image_prompt
        return None

    def extract_from_responses_json(data):
        """
        Handle known shapes:
          - data.content[0].text -> JSON string
          - data.output[...].content[0].text -> JSON string
          - top-level keys (rare)
        Return tuple or None.
        """
        # 1) Top-level object (rare)
        got = try_parse_obj(data)
        if got: return got

        # 2) content list -> first segment text
        content = data.get("content")
        if isinstance(content, list) and content:
            txt = (content[0].get("text") or "").strip()
            if txt:
                # might be a JSON string, or in fringe cases already a dict
                if isinstance(txt, str):
                    try:
                        obj = json.loads(txt)
                        got = try_parse_obj(obj)
                        if got: return got
                    except Exception:
                        pass
                got = try_parse_obj(txt)
                if got: return got

        # 3) output list -> find a message -> its content[0].text
        out = data.get("output")
        if isinstance(out, list):
            for item in out:
                if item.get("type") == "message":
                    segs = item.get("content") or []
                    if segs:
                        txt = (segs[0].get("text") or "").strip()
                        if txt:
                            try:
                                obj = json.loads(txt)
                                got = try_parse_obj(obj)
                                if got: return got
                            except Exception:
                                pass
                            got = try_parse_obj(txt)
                            if got: return got

        # 4) output dict variant
        if isinstance(out, dict):
            got = try_parse_obj(out)
            if got: return got
            segs = out.get("content") or []
            if isinstance(segs, list) and segs:
                txt = (segs[0].get("text") or "").strip()
                if txt:
                    try:
                        obj = json.loads(txt)
                        got = try_parse_obj(obj)
                        if got: return got
                    except Exception:
                        pass

        return None

    # ---- Try Responses API ----
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
            },
            timeout=120,
        )
        # If the API returns an error body, surface it (helps debugging)
        if r.status_code != 200:
            raise RuntimeError(f"Responses API {r.status_code}: {r.text[:800]}")
        data = r.json()

        got = extract_from_responses_json(data)
        if got:
            return got

        # If we get here, we received a shape we didn't parse; show a snippet for visibility
        snippet = json.dumps(data)[:800]
        print("::warning::Unparsed Responses API shape:", snippet)

    except Exception as e:
        # Log, then fall back
        print("::warning::Responses API failed:", str(e)[:800])

    # ---- Fallback: Chat Completions ----
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user + " Return only JSON."},
                ],
            },
            timeout=120,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Chat API {r.status_code}: {r.text[:800]}")
        txt = r.json()["choices"][0]["message"]["content"].strip()
        obj = json.loads(txt)
        got = try_parse_obj(obj)
        if got:
            return got
        raise RuntimeError("Chat fallback returned invalid JSON object.")
    except Exception as e:
        # Bubble up a concise error; GH Actions will show this nicely
        raise RuntimeError(f"Failed to get story JSON after fallback: {e}")

# --------------------- OpenAI: Image Generation (robust) ----------------------

def generate_image(prompt: str):
    """
    Generate an image via Images API with retries.
    - Adds response_format=b64_json for DALL·E models.
    - Retries on 5xx/timeouts.
    - Falls back to an SVG poster if it still fails.
    Returns (bytes, ext) where ext is 'png'|'jpg'|'webp'|'svg'.
    """
    import time

    def make_svg(text: str) -> bytes:
        safe = (text or "illustration").strip().replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        snippet = (safe[:180] + "…") if len(safe) > 180 else safe
        svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0f172a"/>
      <stop offset="100%" stop-color="#1e293b"/>
    </linearGradient>
  </defs>
  <rect width="100%" height="100%" fill="url(#g)"/>
  <g transform="translate(56,80)">
    <text x="0" y="0" font-family="Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial" font-size="42" font-weight="700" fill="#e5e7eb">
      Auto Illustration
    </text>
    <foreignObject x="0" y="28" width="1168" height="600">
      <div xmlns="http://www.w3.org/1999/xhtml"
           style="font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial; font-size: 24px; line-height: 1.45; color:#cbd5e1;">
        {snippet}
      </div>
    </foreignObject>
  </g>
</svg>"""
        return svg.encode("utf-8")

    url = "https://api.openai.com/v1/images/generations"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

    payload = {"model": IMG_MODEL, "prompt": prompt, "size": IMG_SIZE}
    # DALL·E models behave better with explicit b64_json
    if IMG_MODEL.lower().startswith("dall-e"):
        payload["response_format"] = "b64_json"

    # Simple retry loop for transient 5xx/timeouts
    for attempt in range(3):
        try:
            print(f"Calling Images API ({IMG_MODEL}) attempt {attempt+1}/3 …")
            r = requests.post(url, headers=headers, json=payload, timeout=90)
            if r.status_code in (401, 403):
                print(f"::warning::Images API {r.status_code}: {r.text[:600]}")
                return make_svg(prompt), "svg"  # graceful publish
            if 500 <= r.status_code < 600:
                # transient backend error — retry
                print(f"::warning::Images API {r.status_code}: {r.text[:600]}")
                time.sleep(2 * (attempt + 1))
                continue

            r.raise_for_status()
            j = r.json()
            d = (j.get("data") or [{}])[0]

            if d.get("b64_json"):
                return base64.b64decode(d["b64_json"]), "png"

            if d.get("url"):
                img = requests.get(d["url"], timeout=90)
                img.raise_for_status()
                ct = (img.headers.get("Content-Type") or "").lower()
                if "png" in ct:   ext = "png"
                elif "jpeg" in ct or "jpg" in ct: ext = "jpg"
                elif "webp" in ct: ext = "webp"
                elif "svg" in ct:  ext = "svg"
                else:              ext = "png"
                return img.content, ext

            print("::warning::No b64_json/url in image response; using SVG fallback.")
            return make_svg(prompt), "svg"

        except requests.exceptions.Timeout:
            print("::warning::Images API timeout; retrying…")
            time.sleep(2 * (attempt + 1))
        except requests.exceptions.RequestException as e:
            # Non-HTTP errors (network hiccup, etc.) — retry once or twice then fallback
            print(f"::warning::Images API request error: {str(e)[:600]}")
            time.sleep(2 * (attempt + 1))

    # After retries, fallback
    print("::warning::Images API failed after retries; using SVG fallback.")
    return make_svg(prompt), "svg"

# ---------------------------- RSS Update -------------------------------------

def append_rss_item(title: str, post_url: str, story_html: str, img_abs_url: str, img_mime: str):
    xml = FEED.read_text(encoding="utf-8")
    root = ET.fromstring(xml)
    chan = root.find("channel")
    if chan is None:
        raise RuntimeError("Invalid RSS feed: missing <channel>")

    lbd = chan.find("lastBuildDate")
    if lbd is None:
        lbd = ET.SubElement(chan, "lastBuildDate")
    lbd.text = utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")

    item = ET.SubElement(chan, "item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "link").text = post_url
    ET.SubElement(item, "guid").text = post_url
    ET.SubElement(item, "pubDate").text = utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    desc = ET.SubElement(item, "description")
    desc.text = f"<![CDATA[{story_html}<p><img src='{img_abs_url}' alt='illustration'/></p>]]>"

    enc = ET.SubElement(item, "enclosure")
    enc.set("url", img_abs_url)
    enc.set("type", img_mime)
  
    # Trim to most recent N posts
    MAX_POSTS = int(os.environ.get("MAX_FEED_POSTS", "99"))  # 👈 configurable limit
    items = chan.findall("item")
    if len(items) > MAX_POSTS:
        for old_item in items[:-MAX_POSTS]:
            chan.remove(old_item)

    # Save feed
    new_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    FEED.write_bytes(new_xml)

    new_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    FEED.write_bytes(new_xml)

# ------------------------------ Main -----------------------------------------

def main():
    ensure_feed()
    title, story_html, image_prompt = get_story_and_prompt()

    # Randomized UTC time for the day (between RANDOM_HOUR_START and RANDOM_HOUR_END)
    pub_dt = utcnow_randomized_today()
    timestamp = pub_dt.strftime("%Y%m%d-%H%M%S")
    slug = f"{slugify(title)}-{timestamp}"

    # --- Generate image ---
    img_bytes, img_ext = generate_image(image_prompt)
    print({"image_ext": img_ext})
    img_name = f"{timestamp}.{img_ext}"
    (IMGS / img_name).write_bytes(img_bytes)

    # --- Build URLs ---
    rel_img_url = f"/{IMGS.relative_to(DOCS)}/{img_name}"   # served by GH Pages
    base = PUBLIC_BASE_URL
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

    # --- Create HTML post ---
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
      <button class="close" onclick="smartBack()" aria-label="Back to feed">← Back</button>
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
    function smartBack() {{
      if (history.length > 1) {{
        history.back();  // go back to previous page
      }} else {{
        window.location.href = "{home_url}";  // fallback if no history
      }}
    }}
    window.addEventListener('keydown', (e) => {{
      if (e.key === 'Escape') smartBack();
    }});
  </script>

</body>
</html>"""

    # Write post HTML
    post_path = POSTS / f"{slug}.html"
    post_path.write_text(pretty_html, encoding="utf-8")

    # Append to RSS with random publish time
    append_rss_item(title, post_url, story_html, img_abs_url, img_mime)

    # Optional trimming of RSS feed
    if MAX_FEED_POSTS > 0:
        trim_feed(FEED, MAX_FEED_POSTS)

    # GitHub Actions summary + console notice
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
