import json
import os
import re
import requests
from collections import defaultdict
from datetime import datetime
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright


BETTER_BASE = "https://bookings.better.org.uk/location/islington-tennis-centre/highbury-tennis"
BETTER_ORIGIN = "https://bookings.better.org.uk"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
EXCLUDED_COURTS = {9, 10, 11}


def time_to_minutes(value):
    h, m = map(int, value.split(":"))
    return h * 60 + m


def load_watches():
    with open("alerts.json", "r", encoding="utf-8") as f:
        watches = json.load(f)
    if not isinstance(watches, list):
        raise ValueError("alerts.json must contain a list of alerts")
    return watches


def load_state():
    try:
        with open("slot_state.json", "r", encoding="utf-8") as f:
            state = json.load(f)
            return state if isinstance(state, dict) else {}
    except FileNotFoundError:
        return {}


def save_state(state):
    with open("slot_state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def extract_slots(page):
    slots = []
    links = page.locator('a[href*="/slot/"]')

    for i in range(links.count()):
        link = links.nth(i)
        try:
            href = link.get_attribute("href") or ""
            text = link.inner_text().strip()
            match = re.search(r"/slot/(\d{2}:\d{2})-(\d{2}:\d{2})/", href)
            if not match:
                continue
            start, end = match.groups()
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
        except Exception as exc:
            print(f"Could not parse slot link: {exc}")

    unique = {(slot["start"], slot["end"], slot["href"]): slot for slot in slots}
    return list(unique.values())


def court_number(name):
    match = re.search(r"Court\s+(\d+)", name, re.I)
    return int(match.group(1)) if match else None


def find_courts(page):
    courts = []
    options = page.locator('[role="option"]')
    for i in range(options.count()):
        try:
            text = options.nth(i).inner_text().strip()
            match = re.search(r"Highbury Fields Court\s+(\d+)", text, re.I)
            if match:
                courts.append(f"Highbury Fields Court {match.group(1)}")
        except Exception:
            pass

    if not courts:
        try:
            body = page.locator("body").inner_text()
        except Exception:
            body = ""
        for number in range(1, 12):
            name = f"Highbury Fields Court {number}"
            if name.lower() in body.lower():
                courts.append(name)

    # Hard exclusion, regardless of what the website returns.
    courts = [name for name in set(courts) if court_number(name) not in EXCLUDED_COURTS]
    return sorted(courts, key=lambda name: court_number(name) or 999)


def check_slot(page, slot):
    url = urljoin(BETTER_ORIGIN, slot["href"])
    print(f"Checking slot: {url}")
    try:
        response = page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1200)
        if response is None or response.status >= 400:
            print(f"Slot page returned an invalid response: {getattr(response, 'status', None)}")
            return None
        body = page.locator("body").inner_text(timeout=10000)
        courts = find_courts(page)
        if courts:
            return courts

        # An empty court list is only considered a real zero-availability result
        # when the page looks like a loaded Better booking page. Otherwise treat
        # it as an unknown scrape result and preserve prior state for that slot.
        body_lower = body.lower()
        known_page = (
            "highbury" in body_lower
            or "court" in body_lower
            or "no availability" in body_lower
            or "no courts" in body_lower
        )
        if known_page:
            return []
        print("Could not verify the slot page; preserving prior state for this slot.")
        return None
    except Exception as exc:
        print(f"Slot check failed: {exc}")
        return None


def ordinal(day):
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def formatted_date(date_value):
    date = datetime.strptime(date_value, "%Y-%m-%d")
    return f"{date.strftime('%A')} {ordinal(date.day)} {date.strftime('%B')}"


def formatted_time(value):
    date = datetime.strptime(value, "%H:%M")
    return date.strftime("%I:%M%p").lstrip("0").lower().replace(":00", "")


def send_notification(date_value, available):
    if not NTFY_TOPIC:
        print("NTFY: FAILED — NTFY_TOPIC secret is empty.")
        return False
    if not available:
        return True

    # One concise notification per newly available time slot, regardless of how
    # many eligible courts have opened for that time.
    first = available[0]
    booking_url = first["url"]
    time_label = formatted_time(first["start"])
    date_label = formatted_date(date_value)
    message = f"{date_label} - {time_label}"

    payload = {
        "topic": NTFY_TOPIC,
        "title": "Court available 🎾",
        "message": message,
        "priority": 5,
        "tags": ["tennis"],
        "click": booking_url,
        "actions": [{"action": "view", "label": "Book here", "url": booking_url}],
    }

    print(f"NTFY: Sending notification for {message}...")
    try:
        response = requests.post("https://ntfy.sh/", json=payload, timeout=20)
        print(f"NTFY: HTTP {response.status_code}; response: {response.text[:300]}")
        if response.ok:
            print("NTFY: SUCCESS")
            return True
        print("NTFY: FAILED")
        return False
    except Exception as exc:
        print(f"NTFY: FAILED — {exc}")
        return False


def main():
    watches = load_watches()
    state = load_state()
    active_ids = {
        str(watch.get("id") or f"{watch.get('date')}_{watch.get('from')}_{watch.get('until')}_{watch.get('duration_minutes')}")
        for watch in watches
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for alert in watches:
            print("\n" + "=" * 60)
            print("WATCH:", alert)
            print("=" * 60)
            if not alert.get("enabled", True):
                print("Watch paused.")
                continue

            date_value = alert["date"]
            from_time = alert["from"]
            until_time = alert["until"]
            wanted_duration = int(alert["duration_minutes"])
            date_url = f"{BETTER_BASE}/{date_value}/by-time"
            print(f"Checking Better: {date_url}")

            try:
                response = page.goto(date_url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(1000)
                if response is None or response.status >= 400:
                    print(f"Date page failed ({getattr(response, 'status', None)}); skipping this alert without changing state.")
                    continue
                slots = extract_slots(page)
            except Exception as exc:
                print(f"Could not load date page; skipping without changing state: {exc}")
                continue

            from_minutes = time_to_minutes(from_time)
            until_minutes = time_to_minutes(until_time)
            matching = [slot for slot in slots if
                time_to_minutes(slot["start"]) >= from_minutes
                and time_to_minutes(slot["end"]) <= until_minutes
                and slot["duration_minutes"] == wanted_duration]
            print(f"Found {len(matching)} matching bookable time slots.")

            watch_id = str(alert.get("id") or f"{date_value}_{from_time}_{until_time}_{wanted_duration}")
            previous_keys = set(state.get(watch_id, []))
            current_keys = set()
            unknown_ranges = set()
            available_by_range = defaultdict(list)

            for slot in matching:
                courts = check_slot(page, slot)
                range_key = f"{slot['start']}|{slot['end']}"
                if courts is None:
                    unknown_ranges.add(range_key)
                    continue
                for court in courts:
                    number = court_number(court)
                    if number is None or number in EXCLUDED_COURTS:
                        continue
                    key = f"{slot['start']}|{slot['end']}|{court}"
                    current_keys.add(key)
                    available_by_range[range_key].append({
                        "start": slot["start"],
                        "end": slot["end"],
                        "court": court,
                        "url": urljoin(BETTER_ORIGIN, slot["href"]),
                        "key": key,
                    })

            # Preserve previous keys for slots that could not be checked, so a
            # temporary website failure cannot create false alerts or erase state.
            for key in previous_keys:
                parts = key.split("|", 2)
                if len(parts) == 3 and f"{parts[0]}|{parts[1]}" in unknown_ranges:
                    current_keys.add(key)

            newly_available = [key for key in current_keys if key not in previous_keys]
            newly_by_range = defaultdict(list)
            for range_key, items in available_by_range.items():
                for item in items:
                    if item["key"] in newly_available:
                        newly_by_range[range_key].append(item)

            failed_keys = set()
            for range_key, items in newly_by_range.items():
                print(f"New availability for {range_key}: {[item['court'] for item in items]}")
                if not send_notification(date_value, items):
                    failed_keys.update(item["key"] for item in items)

            # If delivery fails, do not mark those newly available courts as seen;
            # the next run can retry. Deleted alert IDs are pruned below.
            state[watch_id] = sorted(current_keys - failed_keys)

        browser.close()

    # Remove stale state for alerts that no longer exist. This also keeps the
    # state file small and prevents deleted alert state from being reused.
    state = {key: value for key, value in state.items() if key in active_ids}
    save_state(state)


if __name__ == "__main__":
    main()
