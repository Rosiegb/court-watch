import json
import os
import re
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright


BETTER_BASE = "https://bookings.better.org.uk/location/islington-tennis-centre/highbury-tennis"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()


def parse_time(value):
    return datetime.strptime(value, "%H:%M").time()


def time_to_minutes(value):
    h, m = map(int, value.split(":"))
    return h * 60 + m


def load_watches():
    with open("alerts.json", "r") as f:
        return json.load(f)


def load_state():
    try:
        with open("slot_state.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(state):
    with open("slot_state.json", "w") as f:
        json.dump(state, f, indent=2)


def extract_slots(page):
    slots = []

    links = page.locator('a[href*="/slot/"]')

    for i in range(links.count()):
        link = links.nth(i)

        try:
            href = link.get_attribute("href") or ""
            text = link.inner_text().strip()

            match = re.search(
                r"/slot/(\d{2}:\d{2})-(\d{2}:\d{2})/",
                href
            )

            if not match:
                continue

            start = match.group(1)
            end = match.group(2)

            duration = time_to_minutes(end) - time_to_minutes(start)

            if duration <= 0:
                continue

            slots.append({
                "start": start,
                "end": end,
                "duration_minutes": duration,
                "href": href,
                "text": text,
            })

        except Exception:
            continue

    # Remove duplicates
    unique = {}

    for slot in slots:
        key = (
            slot["start"],
            slot["end"],
            slot["href"],
        )
        unique[key] = slot

    return list(unique.values())


def find_courts(page):
    courts = []

    # First try the actual court selector options.
    options = page.locator('[role="option"]')

    for i in range(options.count()):
        try:
            text = options.nth(i).inner_text().strip()

            match = re.search(
                r"Highbury Fields Court\s+(\d+)",
                text,
                re.I,
            )

            if match:
                court = f"Highbury Fields Court {match.group(1)}"

                if court not in courts:
                    courts.append(court)

        except Exception:
            pass

    # Fallback: inspect visible page text.
    if not courts:
        body = page.locator("body").inner_text()

        for number in range(1, 12):
            name = f"Highbury Fields Court {number}"

            if name.lower() in body.lower():
                courts.append(name)

    return sorted(
        courts,
        key=lambda x: int(re.search(r"\d+", x).group())
    )


def check_slot(page, slot):
    href = slot["href"]

    if href.startswith("/"):
        url = "https://bookings.better.org.uk" + href
    else:
        url = href

    print(f"Checking slot: {url}")

    page.goto(
        url,
        wait_until="networkidle",
        timeout=60000,
    )

    page.wait_for_timeout(1500)

    courts = find_courts(page)

    return courts


def send_notification(alert, available):
    if not NTFY_TOPIC:
        print("NTFY: FAILED — NTFY_TOPIC secret is empty.")
        return False

    date = alert["date"]

    lines = [
        f"🎾 Court available",
        "",
        f"{date} · {available[0]['start']}–{available[0]['end']}",
        "",
        "Courts:",
    ]

    for item in available:
        lines.append(f"• {item['court']}")

    lines.extend([
        "",
        "Tap this notification to book:",
        available[0]["url"],
    ])

    message = "\n".join(lines)

    payload = {
        "topic": NTFY_TOPIC,
        "title": "🎾 Court available",
        "message": message,
        "priority": 5,
        "tags": ["tennis"],
        "click": available[0]["url"],
    }

    print("NTFY: Sending notification...")

    try:
        response = requests.post(
            "https://ntfy.sh/",
            json=payload,
            timeout=20,
        )

        print(f"NTFY: HTTP {response.status_code}")
        print(f"NTFY: Response: {response.text[:500]}")

        if response.ok:
            print("NTFY: SUCCESS")
            return True

        print("NTFY: FAILED")
        return False

    except Exception as e:
        print(f"NTFY: FAILED — {e}")
        return False


def main():
    watches = load_watches()
    state = load_state()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page()

        for alert in watches:

            print()
            print("=" * 60)
            print("WATCH:", alert)
            print("=" * 60)

            if not alert.get("enabled", True):
                print("Watch paused.")
                continue

            date = alert["date"]
            from_time = alert["from"]
            until_time = alert["until"]
            wanted_duration = int(
                alert["duration_minutes"]
            )

            url = (
                f"{BETTER_BASE}/{date}/by-time"
            )

            print()
            print("Checking Better:")
            print(url)

            page.goto(
                url,
                wait_until="networkidle",
                timeout=60000,
            )

            page.wait_for_timeout(1500)

            slots = extract_slots(page)

            matching = []

            from_minutes = time_to_minutes(from_time)
            until_minutes = time_to_minutes(until_time)

            for slot in slots:

                start_minutes = time_to_minutes(
                    slot["start"]
                )

                end_minutes = time_to_minutes(
                    slot["end"]
                )

                if (
                    start_minutes >= from_minutes
                    and end_minutes <= until_minutes
                    and slot["duration_minutes"]
                    == wanted_duration
                ):
                    matching.append(slot)

            print(
                f"Found {len(matching)} matching "
                f"bookable time slots."
            )

            available = []

            for slot in matching:

                courts = check_slot(
                    page,
                    slot,
                )

                print(
                    f"{slot['start']}-{slot['end']}: "
                    f"{courts}"
                )

                for court in courts:

                    available.append({
                        "start": slot["start"],
                        "end": slot["end"],
                        "court": court,
                        "url": (
                            "https://bookings.better.org.uk"
                            + slot["href"]
                            if slot["href"].startswith("/")
                            else slot["href"]
                        ),
                    })

            print()
            print("AVAILABLE COURTS:")
            print(json.dumps(
                available,
                indent=2
            ))

            watch_id = alert.get(
                "id",
                f"{date}_{from_time}_{until_time}_{wanted_duration}"
            )

            previous = state.get(
                watch_id,
                []
            )

            previous_keys = set(previous)

            current_keys = set(
                f"{x['start']}|{x['end']}|{x['court']}"
                for x in available
            )

            newly_available = [
                x for x in available
                if (
                    f"{x['start']}|{x['end']}|{x['court']}"
                    not in previous_keys
                )
            ]

            print()
            print(
                f"NEWLY AVAILABLE: "
                f"{len(newly_available)}"
            )

            if newly_available:

                notification_sent = send_notification(
                    alert,
                    newly_available,
                )

                if notification_sent:
                    print(
                        "Notification accepted by ntfy."
                    )
                else:
                    print(
                        "Notification failed — "
                        "will retry next run."
                    )

            # Always keep current availability as state.
            # This means a cancellation/reopening becomes
            # a new alert.
            state[watch_id] = sorted(
                current_keys
            )

        browser.close()

    save_state(state)


if __name__ == "__main__":
    main()
