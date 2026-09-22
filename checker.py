import json, os, re, requests
from datetime import datetime, timezone

BETTER_URL="https://bookings.better.org.uk/location/islington-tennis-centre/highbury-tennis/{date}/by-time"

def check(alert):
    # Better renders availability client-side. This first free version intentionally
    # keeps the checker isolated so the rendered-page extraction can be tuned safely.
    url=BETTER_URL.format(date=alert["date"])
    r=requests.get(url,timeout=30,headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    text=r.text.lower()
    return {"url":url,"page_reachable":True,"contains_court_text":"tennis" in text}

if __name__=="__main__":
    path="alerts.json"
    if not os.path.exists(path):
        print("No alerts configured.")
        raise SystemExit
    with open(path) as f: alerts=json.load(f)
    for a in alerts:
        print(datetime.now(timezone.utc).isoformat(), check(a))
