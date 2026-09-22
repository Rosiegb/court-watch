import json
import os
from playwright.sync_api import sync_playwright

BETTER_URL = "https://bookings.better.org.uk/location/islington-tennis-centre/highbury-tennis/{date}/by-time"


def inspect_page(alert):
    url = BETTER_URL.format(date=alert["date"])

    print(f"\nOpening: {url}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
        )

        page.goto(url, wait_until="networkidle", timeout=60000)

        # Give the booking application a little extra time to render.
        page.wait_for_timeout(5000)

        print("PAGE TITLE:")
        print(page.title())

        print("\nVISIBLE PAGE TEXT:")
        print(page.locator("body").inner_text())

        print("\nBUTTONS:")
        for button in page.locator("button").all():
            try:
                text = button.inner_text().strip()
                if text:
                    print(repr(text))
            except Exception:
                pass

        print("\nLINKS:")
        for link in page.locator("a").all():
            try:
                text = link.inner_text().strip()
                href = link.get_attribute("href")
                if text or href:
                    print(f"TEXT={text!r}  HREF={href!r}")
            except Exception:
                pass

        browser.close()


if __name__ == "__main__":
    if not os.path.exists("alerts.json"):
        print("No alerts.json found.")
        raise SystemExit

    with open("alerts.json") as f:
        alerts = json.load(f)

    if not alerts:
        print("No alerts configured.")
        raise SystemExit

    for alert in alerts:
        inspect_page(alert)
