#!/usr/bin/env python3
"""
One-shot Target availability checker for GitHub Actions.
Uses Playwright to check product pages and alerts via Discord webhook.
"""
import asyncio
import os
from main import (
    PRODUCTS,
    CHECK_ZIP,
    GEO_LAT,
    GEO_LON,
)
from playwright.async_api import async_playwright
import requests


async def check_target(page, url):
    """Check Target product page for availability."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_selector(
            '[data-test="@web/AddToCart/FulfillmentSection"]',
            timeout=15000
        )
        section = await page.query_selector('[data-test="@web/AddToCart/FulfillmentSection"]')
        if not section:
            return False

        text = await section.inner_text()
        if text and any(
            keyword in text.lower()
            for keyword in ["out of stock", "sold out", "unavailable"]
        ):
            return False

        button = await section.query_selector("button")
        if button:
            is_disabled = await button.get_attribute("disabled")
            aria_disabled = await button.get_attribute("aria-disabled")
            if is_disabled is not None or aria_disabled == "true":
                return False
            # Button must say "Add to cart" to count as in stock
            btn_text = await button.inner_text()
            if "add to cart" not in btn_text.lower():
                return False
            return True  # enabled button that says "Add to cart" = in stock

        return False  # no button found = not available

    except Exception as e:
        print(f"[PAGE CHECK ERROR] {e}")
        return False


def send_alert(name, url):
    """Send Discord alert via webhook."""
    webhook = os.environ.get("DISCORD_WEBHOOK")
    if not webhook:
        print("[SKIP] No DISCORD_WEBHOOK env set; skipping alert")
        return
    payload = {"content": f"🚨 **IN STOCK ALERT** 🚨\n**{name}**\n{url}\n@everyone"}
    try:
        requests.post(webhook, json=payload, timeout=10)
        print(f"[ALERT SENT] {name}")
    except Exception as e:
        print(f"[ALERT ERROR] Failed to send alert: {e}")


async def main():
    """Check all Target products and alert if any are in stock."""
    print("[CHECK] Starting Target monitor check...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            geolocation={"latitude": GEO_LAT, "longitude": GEO_LON},
            permissions=["geolocation"],
            locale="en-US",
        )

        # Inject ZIP into localStorage — must be an IIFE to actually execute
        await context.add_init_script(f"""
            (() => {{
                try {{
                    localStorage.setItem('guestPostalCode', '{CHECK_ZIP}');
                    localStorage.setItem('postalCode', '{CHECK_ZIP}');
                }} catch(e) {{}}
            }})();
        """)

        # Add cookies for target.com
        try:
            await context.add_cookies([
                {{"name": "guestPostalCode", "value": CHECK_ZIP, "domain": ".target.com", "path": "/"}},
                {{"name": "postalCode", "value": CHECK_ZIP, "domain": ".target.com", "path": "/"}},
            ])
        except Exception:
            pass

        found_any = False
        for product in PRODUCTS:
            if product.get("retailer") != "target":
                continue

            name = product.get("name", "Unknown")
            url = product.get("url", "")

            print(f"[CHECKING] {name}")
            page = await context.new_page()
            try:
                in_stock = await check_target(page, url)
                if in_stock:
                    found_any = True
                    print(f"  => IN STOCK")
                    send_alert(name, url)
                else:
                    print(f"  => out of stock")
            finally:
                await page.close()

        await browser.close()

    if not found_any:
        print("[CHECK] No items in stock.")

    # Heartbeat notification (optional)
    heartbeat_enabled = os.environ.get("HEARTBEAT_ENABLED", "false").lower() in ("1", "true", "yes")
    if heartbeat_enabled:
        webhook = os.environ.get("DISCORD_WEBHOOK")
        hb_msg = f"[HEARTBEAT] check complete - items_found={found_any}"
        if webhook:
            try:
                requests.post(webhook, json={"content": hb_msg}, timeout=5)
                print("[HEARTBEAT SENT]")
            except Exception as e:
                print(f"[HEARTBEAT ERROR] {e}")
        else:
            print("[HEARTBEAT SKIP] DISCORD_WEBHOOK not set")

    print("[CHECK] Monitor check complete.")


if __name__ == "__main__":
    asyncio.run(main())