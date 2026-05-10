"""
Card Rush PSA scraper
Scrapes all pages of https://www.cardrush-op.jp/product-group/37/
Outputs: docs/cards.json
"""

import re
import json
import time
import os
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.cardrush-op.jp/product-group/37"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
}
DELAY = 1.5  # seconds between requests — be polite


def fetch_page(page: int) -> BeautifulSoup:
    url = BASE_URL if page == 1 else f"{BASE_URL}?page={page}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


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


def scrape_all() -> list[dict]:
    print("Fetching page 1 to get total count…")
    soup = fetch_page(1)

    # total item count
    count_el = soup.find(string=re.compile(r"\d+件"))
    total_items = 0
    if count_el:
        m = re.search(r"([\d,]+)件", count_el)
        if m:
            total_items = int(m.group(1).replace(",", ""))
    print(f"Total items: {total_items}")

    # 100 items per page is the max; default is 10 — use the ?display=100 param
    # Actually Card Rush shows 100 items when we add display=100
    # Re-fetch page 1 with display=100
    pages_to_fetch = max(1, -(-total_items // 100))  # ceiling division
    print(f"Will fetch {pages_to_fetch} pages (100 items/page)")

    all_cards = []
    seen_ids = set()

    for page in range(1, pages_to_fetch + 1):
        url = f"{BASE_URL}?page={page}&display=100"
        print(f"  Fetching page {page}/{pages_to_fetch}: {url}")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception as e:
            print(f"  ERROR on page {page}: {e}")
            break

        # find all product links on this page
        links = soup.select("a[href*='/product/']")
        found = 0
        for a in links:
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

            # price
            price_m = re.search(r"([\d,]+)円", raw_text)
            if not price_m:
                continue
            price = int(price_m.group(1).replace(",", ""))

            # stock
            stock_m = re.search(r"在庫数\s*(\d+)枚", raw_text)
            stock = int(stock_m.group(1)) if stock_m else 1

            seen_ids.add(product_id)
            found += 1
            all_cards.append({
                "id": product_id,
                "name": parse_name(raw_text),
                "name_ja": parse_name(raw_text),
                "grade": parse_grade(raw_text),
                "rarity": parse_rarity(raw_text),
                "set": parse_set(raw_text),
                "price_jpy": price,
                "stock": stock,
                "damaged": is_damaged(raw_text),
                "url": f"https://www.cardrush-op.jp/product/{product_id}",
            })

        print(f"  → {found} new cards parsed (total so far: {len(all_cards)})")

        if page < pages_to_fetch:
            time.sleep(DELAY)

    return all_cards


def main():
    cards = scrape_all()

    output = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(cards),
        "cards": cards,
    }

    os.makedirs("docs", exist_ok=True)
    with open("docs/cards.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nDone. {len(cards)} cards written to public/cards.json")


if __name__ == "__main__":
    main()
