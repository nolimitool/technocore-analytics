#!/usr/bin/env python3
"""
flop_tracker.py — Monitor Flop Labs announcements & $FLOP token news
========================================================================
Scans:
  - https://flop.finance
  - Flop Labs X/Twitter via Nitter/alternate frontends
  - Public FLOP news via RSS-like endpoints

Outputs:
  - /root/technocore/flop_tracker_log.jsonl  (append-only evidence log)
  - Optional signed post to #technocore if new material found

Requirements: Python stdlib only (no external deps).
"""

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

try:
    from urllib.request import Request, urlopen
except ImportError:
    print("Fatal: urllib not available")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

FLOP_FINANCE = "https://flop.finance"
FLOP_X_HANDLE = "flop_labs"
TRACKER_LOG = Path("/root/technocore/flop_tracker_log.jsonl")
TRACKER_STATE = Path("/root/technocore/.flop_tracker_state.json")
POST_ROOM = "technocore"

# Known alternate frontends for X/Twitter (public, no auth required)
X_FRONTENDS = [
    f"https://nitter.net/{FLOP_X_HANDLE}",
    f"https://nitter.privacydev.net/{FLOP_X_HANDLE}",
    f"https://nitter.poast.org/{FLOP_X_HANDLE}",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

def fetch(url: str, timeout: int = 20) -> str:
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")

def append_log(entry: dict):
    TRACKER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with TRACKER_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def load_state() -> dict:
    if TRACKER_STATE.exists():
        try:
            return json.loads(TRACKER_STATE.read_text())
        except Exception:
            pass
    return {"last_flop_finance_hash": "", "last_x_hashes": [], "seen_urls": []}

def save_state(state: dict):
    TRACKER_STATE.write_text(json.dumps(state, ensure_ascii=False))

# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

def extract_text_from_html(html: str) -> str:
    """Very basic HTML-to-text extraction (no external deps)."""
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&lt;", "<", html)
    html = re.sub(r"&gt;", ">", html)
    html = re.sub(r"&#\d+;", "", html)
    return re.sub(r"\s+", " ", html).strip()

def extract_flop_finance_news(html: str) -> list[dict]:
    """Pull visible text blocks that look like news/updates from flop.finance."""
    text = extract_text_from_html(html)
    # Split into sentences-ish and look for FLOP/token/airdrop keywords
    sentences = re.split(r'(?<=[.!?])\s+', text)
    hits = []
    keywords = ["token", "airdrop", "allocation", "supply", "miner", "validator", "agent",
                "flop", "tge", "presale", "launch", "claim", "draft", "tokenomics"]
    for s in sentences:
        lower = s.lower()
        if any(k in lower for k in keywords) and len(s) > 40:
            hits.append({"text": s, "source": "flop.finance"})
    return hits

def extract_x_posts(html: str) -> list[dict]:
    """Extract tweet-like blocks from Nitter HTML."""
    text = extract_text_from_html(html)
    # Nitter usually shows tweets as paragraphs; split on tweet boundaries
    # Look for tweet text patterns — date + content + hashtags
    tweets = []
    # Match blocks that contain tweet-like content
    blocks = re.split(r'\n{2,}', text)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        # Must mention FLOP or be from flop_labs context
        if "flop" in block.lower() or "labs" in block.lower():
            if len(block) > 20 and len(block) < 500:
                tweets.append({"text": block, "source": f"x/{FLOP_X_HANDLE}"})
    return tweets[:20]  # cap per fetch

# ---------------------------------------------------------------------------
# Core scan
# ---------------------------------------------------------------------------

def scan_flop_finance(state: dict) -> tuple[list[dict], bool]:
    """Scan flop.finance for new content. Returns (items, changed_flag)."""
    items = []
    try:
        html = fetch(FLOP_FINANCE)
        hits = extract_flop_finance_news(html)
        page_hash = sha256_hex(html)
        changed = page_hash != state.get("last_flop_finance_hash", "")
        for h in hits:
            h["url"] = FLOP_FINANCE
            h["ts"] = now_iso()
            items.append(h)
        state["last_flop_finance_hash"] = page_hash
        return items, changed
    except Exception as e:
        print(f"[tracker] flop.finance error: {e}")
        return [], False

def scan_x_frontends(state: dict) -> tuple[list[dict], bool]:
    """Scan X/Twitter via nitter frontends. Returns (items, changed_flag)."""
    items = []
    any_changed = False
    last_hashes = state.get("last_x_hashes", [])
    new_hashes = []
    for idx, url in enumerate(X_FRONTENDS):
        try:
            html = fetch(url, timeout=15)
            tweets = extract_x_posts(html)
            page_hash = sha256_hex(html)
            if idx < len(last_hashes):
                if page_hash != last_hashes[idx]:
                    any_changed = True
            else:
                any_changed = True
            new_hashes.append(page_hash)
            for t in tweets:
                t["url"] = url
                t["ts"] = now_iso()
                items.append(t)
        except Exception as e:
            print(f"[tracker] {url} error: {e}")
            new_hashes.append(last_hashes[idx] if idx < len(last_hashes) else "")
    state["last_x_hashes"] = new_hashes
    return items, any_changed

def dedupe(items: list[dict], seen_urls: list[str]) -> tuple[list[dict], list[str]]:
    """Dedupe by text hash + seen URLs."""
    seen_texts = set()
    unique = []
    for it in items:
        text_hash = sha256_hex(it.get("text", ""))
        url = it.get("url", "")
        key = f"{text_hash}:{url}"
        if key in seen_texts or url in seen_urls:
            continue
        seen_texts.add(key)
        seen_urls.append(url)
        unique.append(it)
    return unique, seen_urls

# ---------------------------------------------------------------------------
# Poster
# ---------------------------------------------------------------------------

def post_to_technocore(text: str) -> bool:
    """Post signed message via tc_post.py helper."""
    try:
        from subprocess import run, PIPE
        cmd = [
            "/root/technocore/.venv/bin/python",
            "/root/technocore/tc_post.py",
            "say", POST_ROOM, text
        ]
        env = os.environ.copy()
        passphrase = Path("/root/.technocore_passphrase").read_text().strip()
        result = run(cmd, input=(passphrase + "\n"),
                     capture_output=True, text=True, env=env, timeout=30)
        out = (result.stdout or "") + (result.stderr or "")
        print(f"[tracker] post stdout: {result.stdout[:200]}")
        print(f"[tracker] post stderr: {result.stderr[:200]}")
        if '"ok": true' in out or '"seq"' in out:
            print(f"[tracker] Posted to #{POST_ROOM}")
            return True
        else:
            print(f"[tracker] Post failed: {out[:200]}")
            return False
    except Exception as e:
        print(f"[tracker] Post error: {e}")
        return False

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"[tracker] FLOP tracker started {now_iso()}")
    state = load_state()
    seen_urls = state.get("seen_urls", [])

    # Scan sources
    f_items, f_changed = scan_flop_finance(state)
    x_items, x_changed = scan_x_frontends(state)

    all_items = f_items + x_items
    unique_items, seen_urls = dedupe(all_items, seen_urls)

    print(f"[tracker] flop.finance: {len(f_items)} hits, changed={f_changed}")
    print(f"[tracker] x frontends : {len(x_items)} hits, changed={x_changed}")
    print(f"[tracker] unique new  : {len(unique_items)}")

    if not unique_items:
        print("[tracker] No new content.")
        save_state(state)
        return

    # Log everything
    for it in unique_items:
        it["tracked_at"] = now_iso()
        append_log(it)

    # Post to Technocore if anything material is new
    material_keywords = ["airdrop", "tokenomics", "allocation", "claim", "tge", "launch",
                         "supply", "miner", "validator", "agent", "draft"]
    material = [it for it in unique_items if any(k in it.get("text", "").lower() for k in material_keywords)]

    if material:
        # Pick the most significant hit (longest text with most keywords)
        best = max(material, key=lambda x: sum(1 for k in material_keywords if k in x.get("text", "").lower()))
        snippet = best["text"][:200].strip()
        msg = f"FLOP tracker: new material detected — {snippet} [source: {best.get('source','?')}]"
        posted = post_to_technocore(msg)
        # Also log post result
        append_log({"type": "post", "room": POST_ROOM, "ok": posted, "ts": now_iso(), "msg": msg})
    else:
        print("[tracker] New content found, but not material enough to post.")

    save_state(state)
    print("[tracker] Done.")

if __name__ == "__main__":
    main()
