import json
import os
import re
from datetime import datetime
from playwright.sync_api import sync_playwright

BETTER_URL = (
    "https://bookings.better.org.uk/"
    "location/islington-tennis-centre/highbury-tennis/{date}/by-time"
)


def time_to_minutes(value):
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def extract_slots(page):
    slots = []

    # Better exposes bookable sessions as links containing /slot/
    links = page.locator('a[href*="/slot/"]')

    for i in range(links.count()):
        link = links.nth(i)

        try:
            href = link.get_attribute("href")
            text = link.inner_text().strip()

            if not href:
                continue

            match = re.search(
                r"/slot/(\d{1,2}:\d{2})-(\d{1,2}:\d{2})/",
                href
            )

            if not match:
                continue

            start = match.group(1)
            end = match.group(2)

            start_minutes = time_to_minutes(start)
            end_minutes = time_to_minutes(end)

            duration = end_minutes - start_minutes

            if duration <= 0:
                continue

            if href.startswith("/"):
                full_url = "https://bookings.better.org.uk" + href
            else:
                full_url = href

            slots.append({
                "start": start,
                "end": end,
                "duration_minutes": duration,
                "text": text,
                "url": full_url,
            })

        except Exception as e:
            print(f"Could not read link: {e}")

    return slots


def check(alert):
    url = BETTER_URL.format(date=alert["date"])

    print(f"\nChecking Better:")
    print(url)

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

        page.goto(
            url,
            wait_until="networkidle",
            timeout=60000
        )

        page.wait_for_timeout(5000)

        slots = extract_slots(page)

        requested_from = time_to_minutes(alert["from"])
        requested_until = time_to_minutes(alert["until"])
        requested_duration = alert["duration_minutes"]

        matching = []

        for slot in slots:
            slot_start = time_to_minutes(slot["start"])
            slot_end = time_to_minutes(slot["end"])

            if slot_start < requested_from:
                continue

            if slot_end > requested_until:
                continue

            if slot["duration_minutes"] != requested_duration:
                continue

            matching.append(slot)

        print("\nBOOKABLE SLOTS FOUND:")
        print(json.dumps(slots, indent=2))

        print("\nMATCHING SLOTS:")
        print(json.dumps(matching, indent=2))

        browser.close()

        return matching


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
        check(alert)
