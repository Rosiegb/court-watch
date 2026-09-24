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
                if (
                    typeof OneTrust !== "undefined" &&
                    typeof OneTrust.Close === "function"
                ) {
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
            force=True,
            timeout=2000
        )
    except Exception:
        pass


def extract_slots(page):
    """Find Better time slots that have a bookable link."""

    slots = []

    links = page.locator(
        'a[href*="/slot/"]'
    )

    for i in range(links.count()):

        try:

            href = links.nth(i).get_attribute(
                "href"
            )

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

            print(
                f"Could not read slot: {e}"
            )


    unique = {}

    for slot in slots:

        key = (
            slot["start"],
            slot["end"],
            slot["url"]
        )

        unique[key] = slot

    return list(unique.values())


def get_court_selector(page):

    selectors = [
        'input[role="combobox"][aria-label="Select location"]',
        '[role="combobox"][aria-label="Select location"]',
        'input[aria-label="Select location"]',
    ]

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            ).first

            if locator.count() > 0:
                return locator

        except Exception:
            pass

    return None


def open_court_selector(page):

    selector = get_court_selector(page)

    if selector is None:

        print(
            "Court selector not found."
        )

        return False


    # Try keyboard first.

    try:

        selector.evaluate(
            "el => el.focus()"
        )

        page.wait_for_timeout(150)

        page.keyboard.press(
            "ArrowDown"
        )

        page.wait_for_timeout(500)

        expanded = selector.get_attribute(
            "aria-expanded"
        )

        if expanded == "true":
            return True

    except Exception:
        pass


    # Try mouse events.

    try:

        selector.evaluate("""
            el => {

                const eventOptions = {
                    bubbles: true,
                    cancelable: true,
                    view: window
                };

                el.dispatchEvent(
                    new MouseEvent(
                        "mousedown",
                        eventOptions
                    )
                );

                el.dispatchEvent(
                    new MouseEvent(
                        "mouseup",
                        eventOptions
                    )
                );

                el.dispatchEvent(
                    new MouseEvent(
                        "click",
                        eventOptions
                    )
                );
            }
        """)

        page.wait_for_timeout(500)

        expanded = selector.get_attribute(
            "aria-expanded"
        )

        if expanded == "true":
            return True

    except Exception:
        pass


    # Try clicking the React control.

    try:

        selector.evaluate("""
            el => {

                let node = el;

                for (
                    let i = 0;
                    i < 8 && node;
                    i++
                ) {

                    if (
                        node.getAttribute &&
                        node.getAttribute("role") === "combobox"
                    ) {

                        node.click();

                        return;
                    }

                    node = node.parentElement;
                }
            }
        """)

        page.wait_for_timeout(500)

        expanded = selector.get_attribute(
            "aria-expanded"
        )

        if expanded == "true":
            return True

    except Exception:
        pass


    return False


def extract_available_courts(page):

    courts = []


    # Preferred method:
    # read React Select options.

    options = page.locator(
        '[role="option"]'
    )

    for i in range(options.count()):

        try:

            option = options.nth(i)

            if not option.is_visible():
                continue

            text = option.inner_text().strip()

            match = re.search(
                r"Highbury Fields Court\s+(\d+)",
                text,
                re.IGNORECASE
            )

            if not match:
                continue

            if re.search(
                r"^\s*FULL\s*-",
                text,
                re.IGNORECASE
            ):
                continue

            number = int(
                match.group(1)
            )

            if 1 <= number <= 11:

                courts.append(
                    f"Highbury Fields Court {number}"
                )

        except Exception:
            pass


    # Fallback:
    # inspect visible page text.

    if not courts:

        try:

            body_text = page.locator(
                "body"
            ).inner_text()

            lines = [
                line.strip()
                for line in body_text.splitlines()
                if line.strip()
            ]

            for line in lines:

                match = re.fullmatch(
                    r"Highbury Fields Court\s+(\d+)",
                    line,
                    re.IGNORECASE
                )

                if not match:
                    continue

                number = int(
                    match.group(1)
                )

                if 1 <= number <= 11:

                    courts.append(
                        f"Highbury Fields Court {number}"
                    )

        except Exception:
            pass


    return sorted(
        set(courts),
        key=lambda value: int(
            re.search(
                r"\d+",
                value
            ).group()
        )
    )


def inspect_slot(page, slot):

    try:

        close_cookie_banner(page)

        relative_href = slot["url"].replace(
            "https://bookings.better.org.uk",
            ""
        )

        link = page.locator(
            f'a[href="{relative_href}"]'
        ).first

        if link.count() == 0:

            print(
                "Book link not found: "
                f"{slot['start']}-{slot['end']}"
            )

            return []


        link.click(
            force=True,
            timeout=10000
        )

        page.wait_for_timeout(1000)

        close_cookie_banner(page)


        selector = get_court_selector(
            page
        )

        if selector is None:

            print(
                "Court selector missing: "
                f"{slot['start']}-{slot['end']}"
            )

            return []


        opened = open_court_selector(
            page
        )

        if not opened:

            print(
                "Could not open court selector: "
                f"{slot['start']}-{slot['end']}"
            )

            return []


        courts = extract_available_courts(
            page
        )


        print(
            f"{slot['start']}-{slot['end']}: "
            f"{courts}"
        )


        page.keyboard.press(
            "Escape"
        )

        page.wait_for_timeout(200)

        return courts


    except Exception as e:

        print(
            "Could not inspect "
            f"{slot['start']}-{slot['end']}: {e}"
        )

        try:
            page.keyboard.press(
                "Escape"
            )
        except Exception:
            pass

        return []


def check(alert):

    url = BETTER_URL.format(
        date=alert["date"]
    )

    print(
        "\nChecking Better:"
    )

    print(url)


    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            viewport={
                "width": 1440,
                "height": 1600
            },

            user_agent=(
                "Mozilla/5.0 "
                "(Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
        )


        try:

            page.goto(
                url,
                wait_until="networkidle",
                timeout=60000
            )

            page.wait_for_timeout(
                5000
            )

            close_cookie_banner(
                page
            )


            slots = extract_slots(
                page
            )


            requested_from = time_to_minutes(
                alert["from"]
            )

            requested_until = time_to_minutes(
                alert["until"]
            )

            requested_duration = int(
                alert["duration_minutes"]
            )


            matching = []


            for slot in slots:

                start = time_to_minutes(
                    slot["start"]
                )

                end = time_to_minutes(
                    slot["end"]
                )


                if start < requested_from:
                    continue

                if end > requested_until:
                    continue

                if (
                    slot["duration_minutes"]
                    != requested_duration
                ):
                    continue


                matching.append(
                    slot
                )


            print(
                "\nFound "
                f"{len(matching)} "
                "matching bookable time slots."
            )


            results = []


            for slot in matching:

                courts = inspect_slot(
                    page,
                    slot
                )


                for court in courts:

                    results.append({

                        "start":
                            slot["start"],

                        "end":
                            slot["end"],

                        "duration_minutes":
                            slot["duration_minutes"],

                        "court":
                            court,

                        "url":
                            slot["url"],

                    })


            print(
                "\nAVAILABLE COURTS:"
            )

            print(
                json.dumps(
                    results,
                    indent=2
                )
            )


            return results


        finally:

            browser.close()


def load_state():

    if not STATE_FILE.exists():
        return {}


    try:

        with open(
            STATE_FILE
        ) as f:

            return json.load(f)


    except Exception:

        return {}


def save_state(state):

    with open(
        STATE_FILE,
        "w"
    ) as f:

        json.dump(
            state,
            f,
            indent=2
        )


def send_notification(
    new_slots,
    alert
):
    """
    Send ONE notification containing all
    newly available courts/times for this watch.
    """

    topic = os.environ.get(
        "NTFY_TOPIC"
    )


    if not topic:

        print(
            "ERROR: NTFY_TOPIC secret not found."
        )

        return False


    # Group courts that share the same time.

    groups = {}

    for slot in new_slots:

        key = (
            slot["start"],
            slot["end"]
        )

        groups.setdefault(
            key,
            []
        ).append(
            slot["court"]
        )


    lines = []


    for (
        start,
        end
    ), courts in sorted(
        groups.items()
    ):

        court_numbers = []

        for court in courts:

            match = re.search(
                r"(\d+)$",
                court
            )

            if match:
                court_numbers.append(
                    int(match.group(1))
                )


        court_numbers.sort()


        if len(court_numbers) == 1:

            court_text = (
                f"Court {court_numbers[0]}"
            )

        elif len(court_numbers) == 2:

            court_text = (
                f"Courts {court_numbers[0]} "
                f"& {court_numbers[1]}"
            )

        else:

            court_text = (
                "Courts "
                + ", ".join(
                    str(n)
                    for n in court_numbers[:-1]
                )
                + " & "
                + str(court_numbers[-1])
            )


        lines.append(
            f"{court_text} · "
            f"{start}–{end}"
        )


    message = (
        "🎾 Highbury Tennis available\n\n"
        + "\n".join(lines)
        + "\n\n"
        "Tap to book:\n"
        + new_slots[0]["url"]
    )


    try:

        response = requests.post(

            "https://ntfy.sh/" + topic,

            data=message.encode(
                "utf-8"
            ),

            headers={

                "Title":
                    "🎾 Highbury Tennis available",

                "Priority":
                    "high",

                "Tags":
                    "tennis",

                "Click":
                    new_slots[0]["url"],

            },

            timeout=30,

        )


        print(
            "ntfy response:",
            response.status_code
        )


        if response.ok:

            print(
                "🎾 Notification accepted by ntfy."
            )

            return True


        print(
            "ERROR: ntfy rejected notification:"
        )

        print(
            response.text
        )

        return False


    except Exception as e:

        print(
            "ERROR sending ntfy notification:",
            e
        )

        return False


def main():

    if not os.path.exists(
        "alerts.json"
    ):

        print(
            "No alerts configured."
        )

        return


    with open(
        "alerts.json"
    ) as f:

        alerts = json.load(f)


    if not alerts:

        print(
            "No alerts configured."
        )

        return


    state = load_state()


    for alert in alerts:

        # Respect Pause from the iPhone app.

        if alert.get(
            "enabled",
            True
        ) is False:

            print(
                "Skipping paused watch:",
                alert
            )

            continue


        print(
            "\n================================"
        )

        print(
            "WATCH:",
            alert
        )

        print(
            "================================"
        )


        available = check(
            alert
        )


        current = {}


        for slot in available:

            slot_id = (
                f"{alert['date']}-"
                f"{slot['court']}-"
                f"{slot['start']}-"
                f"{slot['end']}"
            )

            current[slot_id] = slot


        # Each watch gets its own state.

        watch_key = (
            f"{alert['date']}|"
            f"{alert['from']}|"
            f"{alert['until']}|"
            f"{alert['duration_minutes']}"
        )


        previous = state.get(
            watch_key,
            {}
        )


        # Find only genuinely NEW availability.

        new_slots = [
            slot
            for slot_id, slot in current.items()
            if slot_id not in previous
        ]


        if new_slots:

            print(
                "\nNEW AVAILABILITY:"
            )

            for slot in new_slots:

                print(
                    f"{slot['court']} "
                    f"{slot['start']}-"
                    f"{slot['end']}"
                )


            # ONE notification for all new courts.

            notification_sent = (
                send_notification(
                    new_slots,
                    alert
                )
            )

        else:

            notification_sent = True


        /*
        Keep courts that were already known
        to be available.

        New courts are only added to state
        if the notification was successfully
        accepted by ntfy.
        */

        new_state = {}


        for slot_id in current:

            if slot_id in previous:

                new_state[slot_id] = True


        if notification_sent:

            for slot in new_slots:

                slot_id = (
                    f"{alert['date']}-"
                    f"{slot['court']}-"
                    f"{slot['start']}-"
                    f"{slot['end']}"
                )

                new_state[slot_id] = True


        else:

            print(
                "Notification failed."
            )

            print(
                "New availability will be "
                "retried on the next check."
            )


        state[watch_key] = new_state


    save_state(
        state
    )


if __name__ == "__main__":

    main()
