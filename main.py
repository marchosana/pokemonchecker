import asyncio
import random
import datetime
import os
import requests
from playwright.async_api import async_playwright

# --- CONFIG ---
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

# Location settings (use ZIP 89139)
CHECK_ZIP = "89139"
# Approximate geolocation for Las Vegas (used for browser geolocation API)
GEO_LAT = 36.1699
GEO_LON = -115.1398

# Target API settings (captured values)
TARGET_API_KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
TARGET_STORE_ID = "2164"
USE_TARGET_API_FIRST = True

PRODUCTS = [
    {
        "name": "Pokemon ETB - Target",
        "url": "https://www.target.com/p/pok-233-mon-trading-card-game-mega-evolution-8212-phantasmal-flames-elite-trainer-box/-/A-94860231#lnk=sametab",
        "retailer": "target",
        "tcin": "94860231"
    },
    {
        "name": "Pokemon ETB - Ascended Heroes Booster Bundle",
        "url": "https://www.target.com/p/pok-233-mon-trading-card-game-mega-evolution-ascended-heroes-booster-bundle/-/A-95120834#lnk=sametab",
        "retailer": "target",
        "tcin": "95120834"
    },
    {
        "name": "Pokemon ETB - Pitch Black Elite Trainer Box",
        "url": "https://www.target.com/p/pok-233-mon-trading-card-game-mega-evolution-pitch-black-elite-trainer-box/-/A-1011483406#lnk=sametab",
        "retailer": "target",
        "tcin": "1011483406"
    },
    {
        "name": "Pokemon ETB - Prismatic Evolutions E.T.B.",
        "url": "https://www.target.com/p/pok-233-mon-trading-card-game-scarlet-38-violet-8212-prismatic-evolutions-elite-trainer-box/-/A-1011206804#lnk=sametab",
        "retailer": "target",
        "tcin": "1011206804"
    },
    {
        "name": "Pokemon ETB - 2025 ME 2.5 Elite Trainer Box",
        "url": "https://www.target.com/p/2025-pok-me-2-5-elite-trainer-box/-/A-95082118#lnk=sametab",
        "retailer": "target",
        "tcin": "95082118"
    },
]

# --- SCHEDULING ---
def get_poll_interval():
    now = datetime.datetime.now()
    hour = now.hour
    day = now.weekday()  # 0=Mon, 4=Fri

    # Peak windows: Tue/Wed/Thu mornings
    if day in [1, 2, 3] and 7 <= hour <= 11:
        return 45
    # Walmart Wednesday evening
    if day == 2 and 19 <= hour <= 22:
        return 45
    # Off peak
    return 300

# --- DISCORD ALERT ---
def send_alert(name, url):
    payload = {
        "content": f"🚨 **IN STOCK ALERT** 🚨\n**{name}**\n{url}\n@everyone"
    }
    requests.post(DISCORD_WEBHOOK, json=payload)
    print(f"[ALERT SENT] {name}")

# --- STOCK CHECKERS ---
async def check_target(page, url):
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_selector('[data-test="@web/AddToCart/FulfillmentSection"]', timeout=15000)
    section = await page.query_selector('[data-test="@web/AddToCart/FulfillmentSection"]')
    if not section:
        return False

    text = await section.inner_text()
    if text and any(keyword in text.lower() for keyword in ["out of stock", "sold out", "unavailable"]):
        return False

    button = await section.query_selector('button')
    if button:
        is_disabled = await button.get_attribute("disabled")
        aria_disabled = await button.get_attribute("aria-disabled")
        if is_disabled is not None or aria_disabled == "true":
            return False

    return True


def check_target_api(tcin, zip_code=CHECK_ZIP):
    """Lightweight API check using Target's PDP/availability endpoint.

    Returns:
      - True if available
      - False if explicitly out of stock
      - None if inconclusive or request error
    """
    if not tcin:
        return None

    url = f"https://redsky.target.com/v2/pdp/tcin/{tcin}"
    params = {
        "key": TARGET_API_KEY,
        "scheduled_delivery_zip_code": zip_code,
        "store_id": TARGET_STORE_ID,
        "pricing_store_id": TARGET_STORE_ID,
        "latitude": GEO_LAT,
        "longitude": GEO_LON,
        "channel": "WEB",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json",
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[API ERROR] Target API request failed: {e}")
        return None

    # Heuristic checks on response JSON for availability
    # Look for common fields: available_to_promise_network, availability, onlineAvailability
    try:
        # available_to_promise_network is often a boolean at top-level product aggregation
        atp = None
        # try several common paths
        if isinstance(data, dict):
            # Search recursively for keys that suggest availability
            def find_keys(obj, key_names):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k in key_names:
                            return v
                        res = find_keys(v, key_names)
                        if res is not None:
                            return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = find_keys(item, key_names)
                        if res is not None:
                            return res
                return None

            atp = find_keys(data, ["available_to_promise_network", "available", "onlineAvailability", "availability"])

            if isinstance(atp, bool):
                return bool(atp)
            if isinstance(atp, dict):
                # some APIs return { "status": "IN_STOCK" } etc.
                for v in atp.values():
                    if isinstance(v, str) and v.lower() in ("in_stock", "available", "available_online"):
                        return True
                # fallback
            if isinstance(atp, str):
                if any(s in atp.lower() for s in ["in_stock", "available", "available_online"]):
                    return True

        # As a fallback, scan text for 'out of stock'
        text_blob = str(data).lower()
        if "out of stock" in text_blob or "sold out" in text_blob:
            return False

    except Exception as e:
        print(f"[API PARSE ERROR] {e}")

    return None

async def check_walmart(page, url):
    # Walmart checks are disabled in this trimmed config.
    return False


# --- MAIN LOOP ---
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            geolocation={"latitude": GEO_LAT, "longitude": GEO_LON},
            permissions=["geolocation"],
            locale="en-US",
        )

        # Inject ZIP into localStorage for sites that read it (Target may use a different key).
        await context.add_init_script(f"""() => {{
            try {{
                localStorage.setItem('guestPostalCode', '{CHECK_ZIP}');
                localStorage.setItem('postalCode', '{CHECK_ZIP}');
            }} catch(e) {{}}
        }}""")

        # Add cookies scoped to target.com so server-side code may pick up the postal code.
        try:
            await context.add_cookies([
                {"name": "guestPostalCode", "value": CHECK_ZIP, "domain": ".target.com", "path": "/"},
                {"name": "postalCode", "value": CHECK_ZIP, "domain": ".target.com", "path": "/"},
            ])
        except Exception:
            # Non-fatal if adding cookies fails in certain environments
            pass

        print("Monitor started...")

        while True:
            for product in PRODUCTS:
                try:
                    page = await context.new_page()
                    print(f"[CHECKING] {product['name']}")

                    in_stock = False
                    if product["retailer"] == "target":
                        # Try API first when available
                        api_result = None
                        if USE_TARGET_API_FIRST and product.get("tcin"):
                            api_result = check_target_api(product.get("tcin"), CHECK_ZIP)
                        if api_result is True:
                            in_stock = True
                        elif api_result is False:
                            in_stock = False
                        else:
                            # Fall back to the page-based check
                            in_stock = await check_target(page, product["url"])

                    await page.close()

                    if in_stock:
                        send_alert(product["name"], product["url"])

                except Exception as e:
                    print(f"[ERROR] {product['name']}: {e}")

                # Stagger requests between products
                await asyncio.sleep(random.uniform(3, 7))

            interval = get_poll_interval()
            jitter = random.uniform(-10, 10)
            sleep_time = interval + jitter
            print(f"[WAITING] {sleep_time:.0f}s until next check...")
            await asyncio.sleep(sleep_time)

if __name__ == "__main__":
    asyncio.run(main())