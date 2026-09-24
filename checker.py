import json
import os
import re
import requests
from pathlib import Path
from playwright.sync_api import sync_playwright

BETTER_URL = (
    "https://bookings.better.org.uk/"
    "location/islington-tennis-centre/highbury-tennis/{date}/by-time"
)

STATE_FILE = Path("slot_state.json")


def time_to_minutes(value):
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def close_cookie_banner(page):
    try:
        page.evaluate("""
            () => {
                if (typeof OneTrust !== 'undefined' &&
                    typeof OneTrust.Close === 'function') {
                    OneTrust.Close();
                }
            }
        """)
    except Exception:
        pass


def extract_slots(page):
    slots = []

    links = page.locator('a[href*="/slot/"]')

    for i in range(links.count()):
        link = links.nth(i)

        try:
            href = link.get_attribute("href")

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

            duration = (
                time_to_minutes(end)
                - time_to_minutes(start)
            )

            if duration <= 0:
                continue

            full_url = (
                "https://bookings.better.org.uk" + href
                if href.startswith("/")
                else href
            )

            slots.append({
                "start": start,
                "end": end,
                "duration_minutes": duration,
                "url": full_url,
            })

        except Exception as e:
            print(f"Could not read slot: {e}")

    return slots


def extract_courts_from_popup(page):

    courts = []

    # Look for the court-selection control.
    candidates = page.locator(
        "text=Select location:"
    )

    if candidates.count() == 0:
        return courts

    try:
        # Find the clickable control near "Select location".
        control = page.locator(
            '[role="combobox"]'
        ).first

        if control.count() > 0:
            control.click()
        else:
            # Fallback: click the visible selection text.
            page.locator(
                "text=FULL - Highbury Fields Court 1"
            ).first.click()

        page.wait_for_timeout(300)

    except Exception as e:
        print(f"Could not open court selector: {e}")
        return courts

    # Read all visible text containing "Highbury Fields Court".
    elements = page.locator(
        "text=/Highbury Fields Court/"
    )

    for i in range(elements.count()):

        try:
            text = elements.nth(i).inner_text().strip()

            match = re.search(
                r"Highbury Fields Court\s+(\d+)",
                text,
                re.IGNORECASE
            )

            if not match:
                continue

            # Ignore courts explicitly marked FULL.
            if text.upper().startswith("FULL -"):
                continue

            court = (
                f"Highbury Fields Court "
                f"{match.group(1)}"
            )

            courts.append(court)

        except Exception:
            pass

    page.keyboard.press("Escape")

    return sorted(set(courts))


def inspect_slot(page, slot):

    try:
        close_cookie_banner(page)

        href = slot["url"].replace(
            "https://bookings.better.org.uk",
            ""
        )

        link = page.locator(
            f'a[href="{href}"]'
        ).first

        link.scroll_into_view_if_needed()

        link.click(
            timeout=10000,
            force=True
        )

        page.wait_for_timeout(800)

        courts = extract_courts_from_popup(page)

        print(
            f"{slot['start']}-{slot['end']}: "
            f"{courts}"
        )

        page.keyboard.press("Escape")

        return courts

    except Exception as e:

        print(
            f"Could not inspect courts for "
            f"{slot['start']}-{slot['end']}: {e}"
        )

        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        return []


def check(alert):

    url = BETTER_URL.format(date=alert["date"])

    print("\nChecking Better:")
    print(url)

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            viewport={
                "width": 1440,
                "height": 1200
            },
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
        )

        page.goto(
            url,
            wait_until="networkidle",
            timeout=60000
        )

        page.wait_for_timeout(5000)

        close_cookie_banner(page)

        slots = extract_slots(page)

        requested_from = time_to_minutes(alert["from"])
        requested_until = time_to_minutes(alert["until"])
        requested_duration = alert["duration_minutes"]

        matching = []

        for slot in slots:

            if time_to_minutes(slot["start"]) < requested_from:
                continue

            if time_to_minutes(slot["end"]) > requested_until:
                continue

            if slot["duration_minutes"] != requested_duration:
                continue

            matching.append(slot)

        print(
            f"\nFound {len(matching)} "
            f"matching bookable time slots."
        )

        results = []

        for slot in matching:

            courts = inspect_slot(
                page,
                slot
            )

            for court in courts:

                results.append({
                    "start": slot["start"],
                    "end": slot["end"],
                    "duration_minutes": slot[
                        "duration_minutes"
                    ],
                    "court": court,
                    "url": slot["url"],
                })

        print("\nAVAILABLE COURTS:")
        print(json.dumps(results, indent=2))

        browser.close()

        return results


def load_state():

    if not STATE_FILE.exists():
        return {}

    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def send_notification(slot, alert):

    topic = os.environ.get("NTFY_TOPIC")

    if not topic:
        print("NTFY_TOPIC secret not found.")
        return

    message = (
        f"🎾 Highbury Tennis available\n"
        f"{slot['court']}\n"
        f"{alert['date']} · "
        f"{slot['start']}–{slot['end']}\n\n"
        f"Tap to book:\n"
        f"{slot['url']}"
    )

    requests.post(
        "https://ntfy.sh/" + topic,
        data=message.encode("utf-8"),
        headers={
            "Title": "Highbury Tennis available",
            "Priority": "high",
            "Tags": "tennis",
            "Click": slot["url"],
        },
        timeout=30,
    )


def main():

    if not os.path.exists("alerts.json"):
        return

    with open("alerts.json") as f:
        alerts = json.load(f)

    if not alerts:
        return

    state = load_state()

    for alert in alerts:

        available_slots = check(alert)

        current = {}

        for slot in available_slots:

            slot_id = (
                f"{alert['date']}-"
                f"{slot['court']}-"
                f"{slot['start']}-"
                f"{slot['end']}"
            )

            current[slot_id] = slot

        previous = state.get(
            alert["date"],
            {}
        )

        for slot_id, slot in current.items():

            if slot_id not in previous:
                print(
                    f"NEW AVAILABILITY: "
                    f"{slot['court']} "
                    f"{slot['start']}-{slot['end']}"
                )

                send_notification(slot, alert)

        state[alert["date"]] = {
            slot_id: True
            for slot_id in current
        }

    save_state(state)


if __name__ == "__main__":
    main()
