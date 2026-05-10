"""
Card Rush PSA scraper — incremental image fetching
─────────────────────────────────────────────────
Phase 1 : scrape all listing pages (17 requests/day, always runs)
Phase 2 : fetch product-page images ONLY for cards that are new
          (not in the previous cards.json) or that have no image yet.
          Cards already in the cache keep their image — no re-request.

This keeps daily image requests to ~0 on stable days and only
spikes when Card Rush adds new PSA listings.

Outputs: docs/cards.json
"""

import re
import json
import time
import os
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

BASE_URL      = "https://www.cardrush-op.jp/product-group/37"
PRODUCT_URL   = "https://www.cardrush-op.jp/product/{}"
CARDS_JSON    = "docs/cards.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
}
LIST_DELAY    = 1.5   # seconds between listing-page requests
PRODUCT_DELAY = 1.2   # seconds between product-page requests (be polite)
MAX_NEW_IMAGES_PER_RUN = 200  # safety cap — never fetch more than this per day


# ── parsers ───────────────────────────────────────────────────────────────────

def parse_grade(raw: str) -> str:
    m = re.search(r"〔(?:※状態難/)?(\w+\d+)鑑定済〕", raw)
    return m.group(1) if m else "PSA"

def parse_name(raw: str) -> str:
    m = re.search(r"〕(.+?)【", raw)
    return m.group(1).strip() if m else raw

def parse_rarity(raw: str) -> str:
    m = re.search(r"【(.+?)】", raw)
    return m.group(1) if m else ""

def parse_set(raw: str) -> str:
    m = re.search(r"\{(.+?)\}", raw)
    return m.group(1) if m else ""

def is_damaged(raw: str) -> bool:
    return "状態難" in raw


# ── image cache: load previous cards.json ────────────────────────────────────

def load_image_cache() -> dict[str, str]:
    """Return {product_id: image_url} from the last saved cards.json."""
    if not os.path.exists(CARDS_JSON):
        print("  No existing cards.json — starting fresh (all images will be fetched).")
        return {}
    try:
        with open(CARDS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        cache = {c["id"]: c.get("image", "") for c in data.get("cards", [])}
        print(f"  Loaded image cache: {len(cache)} entries ({sum(1 for v in cache.values() if v)} with images)")
        return cache
    except Exception as e:
        print(f"  Could not load cache: {e}")
        return {}


# ── phase 1: scrape listing pages ────────────────────────────────────────────

def scrape_listings() -> list[dict]:
    print("\n=== Phase 1: scraping listing pages ===")

    resp = requests.get(f"{BASE_URL}?page=1&display=100", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    count_el = soup.find(string=re.compile(r"\d+件"))
    total_items = 1625  # fallback
    if count_el:
        m = re.search(r"([\d,]+)件", count_el)
        if m:
            total_items = int(m.group(1).replace(",", ""))
    pages_to_fetch = max(1, -(-total_items // 100))
    print(f"Total items: {total_items} → {pages_to_fetch} pages")

    all_cards = []
    seen_ids  = set()

    for page in range(1, pages_to_fetch + 1):
        url = f"{BASE_URL}?page={page}&display=100"
        print(f"  Page {page}/{pages_to_fetch} …", end=" ", flush=True)
        try:
            if page > 1:
                time.sleep(LIST_DELAY)
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception as e:
            print(f"ERROR: {e}")
            break

        found = 0
        for a in soup.select("a[href*='/product/']"):
            href = a.get("href", "")
            id_m = re.search(r"/product/(\d+)", href)
            if not id_m:
                continue
            product_id = id_m.group(1)
            if product_id in seen_ids:
                continue

            raw_text = a.get_text(" ", strip=True)
            if "鑑定済" not in raw_text:
                continue

            price_m = re.search(r"([\d,]+)円", raw_text)
            if not price_m:
                continue
            price = int(price_m.group(1).replace(",", ""))

            stock_m = re.search(r"在庫数\s*(\d+)枚", raw_text)
            stock   = int(stock_m.group(1)) if stock_m else 1

            seen_ids.add(product_id)
            found += 1
            all_cards.append({
                "id":        product_id,
                "name":      parse_name(raw_text),
                "grade":     parse_grade(raw_text),
                "rarity":    parse_rarity(raw_text),
                "set":       parse_set(raw_text),
                "price_jpy": price,
                "stock":     stock,
                "damaged":   is_damaged(raw_text),
                "url":       PRODUCT_URL.format(product_id),
                "image":     "",   # filled in phase 2
            })

        print(f"{found} cards (total: {len(all_cards)})")

    return all_cards


# ── phase 2: incremental image fetch ─────────────────────────────────────────

def fetch_image(product_id: str) -> str:
    """Fetch the main image URL from a single product page."""
    url = PRODUCT_URL.format(product_id)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # og:image is the most reliable signal
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]

        # fallback: known Card Rush image selectors
        for selector in ["img.goods_img", ".product_img img",
                         ".item_img img", "#goods_img img", ".itemphoto img"]:
            el = soup.select_one(selector)
            if el and el.get("src"):
                src = el["src"]
                if src.startswith("//"):   src = "https:" + src
                elif src.startswith("/"): src = "https://www.cardrush-op.jp" + src
                return src

        # last resort: any img whose URL contains /image/ or /img/
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if "/image/" in src or "/img/" in src:
                if src.startswith("//"):   src = "https:" + src
                elif src.startswith("/"): src = "https://www.cardrush-op.jp" + src
                if src.startswith("http"):
                    return src

    except Exception as e:
        print(f"    ✗ {product_id}: {e}")

    return ""


def enrich_incrementally(cards: list[dict], cache: dict[str, str]) -> list[dict]:
    """
    For each card:
      - If the ID exists in cache AND has a non-empty image → reuse it (0 requests)
      - Otherwise → fetch the product page (new card or previously failed)
    Caps at MAX_NEW_IMAGES_PER_RUN fetches per run.
    """
    print("\n=== Phase 2: incremental image fetch ===")

    # Identify which cards need a fetch
    needs_fetch = [c for c in cards if not cache.get(c["id"])]
    has_cache   = [c for c in cards if cache.get(c["id"])]

    print(f"  Reusing cached images : {len(has_cache)}")
    print(f"  Need image fetch      : {len(needs_fetch)}")

    # Apply safety cap
    if len(needs_fetch) > MAX_NEW_IMAGES_PER_RUN:
        print(f"  ⚠ Capping at {MAX_NEW_IMAGES_PER_RUN} fetches this run "
              f"(remaining {len(needs_fetch) - MAX_NEW_IMAGES_PER_RUN} will be picked up tomorrow)")
        needs_fetch = needs_fetch[:MAX_NEW_IMAGES_PER_RUN]

    est_mins = len(needs_fetch) * PRODUCT_DELAY / 60
    print(f"  Estimated fetch time  : ~{est_mins:.1f} min\n")

    # Apply cache to all cards first
    for card in cards:
        card["image"] = cache.get(card["id"], "")

    # Fetch only the ones that need it
    fetched = 0
    for i, card in enumerate(needs_fetch, 1):
        print(f"  [{i}/{len(needs_fetch)}] {card['id']}  {card['name'][:40]} …", end=" ", flush=True)
        img = fetch_image(card["id"])
        card["image"] = img
        print("✓" if img else "—")
        fetched += 1
        time.sleep(PRODUCT_DELAY)

    # Summary
    total_with_images = sum(1 for c in cards if c["image"])
    print(f"\n  Fetched this run : {fetched}")
    print(f"  Total with images: {total_with_images}/{len(cards)}")

    return cards


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== Card Rush PSA Scraper (incremental) ===")

    # Load previous image cache before scraping
    print("\nLoading image cache …")
    cache = load_image_cache()

    # Phase 1: always scrape all listing pages for fresh prices/stock
    cards = scrape_listings()

    # Phase 2: only fetch images for new / missing cards
    cards = enrich_incrementally(cards, cache)

    # Write output
    output = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total":      len(cards),
        "cards":      cards,
    }

    os.makedirs("docs", exist_ok=True)
    with open(CARDS_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Done. {len(cards)} cards written to {CARDS_JSON}")


if __name__ == "__main__":
    main()
