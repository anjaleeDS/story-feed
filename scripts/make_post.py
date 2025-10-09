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
            # JSON mode for Responses API
            "text": {"format": {"type": "json_object"}}
        },
        timeout=120
    )
    r.raise_for_status()
    data = r.json()

    # Helper to pull fields if dict has them
    def try_extract(obj):
        if isinstance(obj, dict) and {"title","story_html","image_prompt"} <= set(obj.keys()):
            return obj["title"] or "Automated Story", obj["story_html"], obj["image_prompt"]
        return None

    # 1) Top-level object
    got = try_extract(data)
    if got: return got

    # 2) Nested under output (dict)
    out = data.get("output")
    got = try_extract(out)
    if got: return got

    # 3) Nested under output (list) -> find message -> content[].text (dict or JSON string)
    if isinstance(out, list):
        for item in out:
            if item.get("type") == "message":
                for seg in item.get("content", []):
                    t = seg.get("text")
                    # dict already
                    got = try_extract(t)
                    if got: return got
                    # string -> JSON
                    if isinstance(t, str):
                        try:
                            parsed = json.loads(t)
                            got = try_extract(parsed)
                            if got: return got
                        except Exception:
                            pass

    # 4) Some providers put content at top level
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

    # 5) Nothing matched -> show a compact summary for debugging
    raise RuntimeError(f"Missing keys in JSON response (keys: {list(data.keys())} ...)")
