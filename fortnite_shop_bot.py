import asyncio
import re
import os
import httpx
from playwright.async_api import async_playwright

# ─────────────────────────────────────────────
#  CONFIG — set as env vars or replace defaults
# ─────────────────────────────────────────────
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1486151006596235437/gGua0H4wcqjxW6VhEgdDVqo9rZndtlXXxLwoFztW52WKORTPLuiXn222xQuwOfZkKFn3")
SHOP_URL    = "http://51.68.190.101:3551/api/launcher/shop"
INTERVAL    = 5 * 60  # seconds
# ─────────────────────────────────────────────

ID_PATTERNS = {
    "Outfit":     re.compile(r"\bCID_[A-Za-z0-9_]+",        re.IGNORECASE),
    "Emote":      re.compile(r"\bEID_[A-Za-z0-9_]+",        re.IGNORECASE),
    "Back Bling": re.compile(r"\bBID_[A-Za-z0-9_]+",        re.IGNORECASE),
    "Pickaxe":    re.compile(r"\bPickaxe_ID_[A-Za-z0-9_]+", re.IGNORECASE),
    "Glider":     re.compile(r"\bGlider_ID_[A-Za-z0-9_]+",  re.IGNORECASE),
}

TYPE_EMOJI = {
    "Outfit":     "👤",
    "Emote":      "💃",
    "Back Bling": "🎒",
    "Pickaxe":    "⛏️",
    "Glider":     "🪂",
}

TYPE_COLOR = {
    "Outfit":     0x9B59B6,
    "Emote":      0xF1C40F,
    "Back Bling": 0x2ECC71,
    "Pickaxe":    0xE74C3C,
    "Glider":     0x3498DB,
}


async def fetch_page_html(url: str) -> str:
    """Use Playwright headless browser to fully render the JS-heavy page."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30_000)
        html = await page.content()
        await browser.close()
    return html


def extract_ids(html: str) -> dict[str, set[str]]:
    """Extract and deduplicate all cosmetic IDs from rendered HTML."""
    found = {}
    for type_name, pattern in ID_PATTERNS.items():
        matches = {m.upper() for m in pattern.findall(html)}
        if matches:
            found[type_name] = matches
    return found


async def lookup_cosmetic(client: httpx.AsyncClient, cosmetic_id: str) -> dict | None:
    """Fetch cosmetic name + image from fortnite-api.com (no key needed)."""
    try:
        r = await client.get(
            f"https://fortnite-api.com/v2/cosmetics/br/{cosmetic_id}",
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("data")
    except httpx.RequestError:
        pass
    return None


def build_embeds(type_name: str, cosmetics: list[dict]) -> list[dict]:
    """Build Discord embed objects for a cosmetic type — one embed per item."""
    color = TYPE_COLOR.get(type_name, 0x95A5A6)
    emoji = TYPE_EMOJI.get(type_name, "🔹")
    embeds = []

    for item in cosmetics:
        name     = item.get("name", "Unknown")
        cid      = item.get("id", "")
        images   = item.get("images", {})
        icon_url = images.get("icon") or images.get("smallIcon") or images.get("featured")
        rarity   = item.get("rarity", {}).get("displayValue", "")
        desc     = item.get("description", "")

        embed = {
            "title":       f"{emoji} {name}",
            "description": f"`{cid}`\n{desc}" if desc else f"`{cid}`",
            "color":       color,
        }
        if rarity:
            embed["footer"] = {"text": rarity}
        if icon_url:
            embed["thumbnail"] = {"url": icon_url}

        embeds.append(embed)

    return embeds


async def send_to_discord(all_embeds: list[dict], header: str):
    """Send embeds to Discord in batches of 10 (API limit per message)."""
    async with httpx.AsyncClient() as client:
        for i in range(0, len(all_embeds), 10):
            batch = all_embeds[i:i + 10]
            payload: dict = {"embeds": batch}
            if i == 0:
                payload["content"] = header
            r = await client.post(WEBHOOK_URL, json=payload, timeout=15)
            r.raise_for_status()
            await asyncio.sleep(0.5)


async def run():
    print(f"Bot started — checking {SHOP_URL} every {INTERVAL // 60} min")

    async with httpx.AsyncClient() as api_client:
        while True:
            try:
                print(f"\nFetching shop page (rendering JS)...")
                html  = await fetch_page_html(SHOP_URL)
                found = extract_ids(html)

                if not found:
                    print("  No cosmetic IDs found.")
                    await asyncio.sleep(INTERVAL)
                    continue

                total = sum(len(v) for v in found.values())
                print(f"  Found {total} IDs — resolving names & images...")

                all_embeds: list[dict] = []

                for type_name, id_set in found.items():
                    resolved = []
                    for cid in sorted(id_set):
                        data = await lookup_cosmetic(api_client, cid)
                        if data:
                            print(f"    {cid} -> {data.get('name')}")
                            resolved.append(data)
                        else:
                            print(f"    {cid} -> not found")
                            resolved.append({"id": cid, "name": "Unknown"})
                        await asyncio.sleep(0.2)

                    all_embeds.extend(build_embeds(type_name, resolved))

                header = "## Fortnite Item Shop — Current Listings"
                await send_to_discord(all_embeds, header)
                print(f"  Sent {len(all_embeds)} embeds to Discord.")

            except Exception as e:
                print(f"  Error: {e}")

            await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(run())
