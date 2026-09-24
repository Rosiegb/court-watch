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
    """Dismiss OneTrust cookie banner if it is present."""

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

    try:
        page.locator(
            "#onetrust-banner-sdk button"
        ).filter(
            has_text=re.compile(
                "accept|allow|close",
                re.IGNORECASE
            )
        ).first.click(
            timeout=3000
        )
    except Exception:
        pass

    page.wait_for_timeout(500)


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


def extract_available_courts(page):

    courts = []

    selects = page.locator("select")

    for i in range(selects.count()):

        select = selects.nth(i)

        try:
            options = select.locator("option")

            for j in range(options.count()):

                text = options.nth(j).inner_text().strip()

                if "Highbury Fields Court" not in text:
                    continue

                if text.upper().startswith("FULL -"):
                    continue

                match = re.search(
                    r"Highbury Fields Court\s+(\d+)",
                    text,
                    re.IGNORECASE
                )

                if match:
                    courts.append(
                        f"Highbury Fields Court {match.group(1)}"
                    )

        except Exception as e:
            print(
                f"Could not inspect court selector: {e}"
            )

    return sorted(set(courts))


def inspect_slot(page, slot):

    try:

        close_cookie_banner(page)

        link = page.locator(
            f'a[href="{slot["url"].replace("https://bookings.better.org.uk", "")}"]'
        ).first

        link.scroll_into_view_if_needed()

        link.click(
            timeout=10000,
            force=True
        )

        page.wait_for_timeout(1000)

        courts = extract_available_courts(page)

        print(
            f"{slot['start']}-{slot['end']}: "
            f"{courts}"
        )

        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

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

            slot_start = time_to_minutes(slot["start"])
            slot_end = time_to_minutes(slot["end"])

            if slot_start < requested_from:
                continue

            if slot_end > requested_until:
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
        print(
            json.dumps(
                results,
                indent=2
            )
        )

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
        json.dump(
            state,
            f,
            indent=2
        )


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

    response = requests.post(
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

    print(
        "Notification response:",
        response.status_code,
        response.text
    )


def main():

    if not os.path.exists("alerts.json"):
        print("No alerts configured.")
        return

    with open("alerts.json") as f:
        alerts = json.load(f)

    if not alerts:
        print("No alerts configured.")
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

                send_notification(
                    slot,
                    alert
                )

        state[alert["date"]] = {
            slot_id: True
            for slot_id in current
        }

    save_state(state)


if __name__ == "__main__":
    main()
