
#!/usr/bin/env python3

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

EVENTS_API = "https://vortextv.modsdone.com/cricfy.php/events"

OUTPUT_FILE = "LiveZoneTV_Sports.json"
STREAMS_FILE = "sports_streams.json"


def fetch_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "LiveZoneTV-Sports-Sync/1.0",
            "Accept": "application/json"
        }
    )

    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def load_stream_overrides():
    if not os.path.exists(STREAMS_FILE):
        return {}

    with open(STREAMS_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    return data if isinstance(data, dict) else {}


def is_published(value):
    if value is None:
        return True

    if value is False or value == 0:
        return False

    if isinstance(value, str):
        return value.strip().lower() not in {
            "false", "0", "no", "unpublished", "off"
        }

    return True


def valid_stream(stream):
    if not isinstance(stream, dict):
        return False

    url = str(stream.get("streamUrl", "")).strip()

    return url.startswith(("https://", "http://"))


def clean_streams(streams):
    result = []

    if not isinstance(streams, list):
        return result

    for stream in streams:
        if not valid_stream(stream):
            continue

        item = {
            "channelName": str(
                stream.get("channelName", "Live Stream")
            ),
            "streamUrl": stream["streamUrl"].strip(),
            "quality": str(stream.get("quality", "HD")),
            "headers": str(stream.get("headers", "")),
            "backupUrls": [
                url for url in stream.get("backupUrls", [])
                if isinstance(url, str)
                and url.startswith(("https://", "http://"))
            ]
        }

        logo = stream.get("logoUrl")
        if isinstance(logo, str) and logo.startswith(("https://", "http://")):
            item["logoUrl"] = logo

        # DRM keys are intentionally never copied to public JSON.
        result.append(item)

    return result


def main():
    print("Fetching LiveZoneTV sports events...")

    response = fetch_json(EVENTS_API)

    if not isinstance(response, dict):
        raise ValueError("Invalid API response")

    if response.get("status") != "ok":
        raise ValueError("Sports API did not return status=ok")

    source_events = response.get("data")

    if not isinstance(source_events, list):
        raise ValueError("API data is not a list")

    stream_overrides = load_stream_overrides()
    events = []

    for source in source_events:
        if not isinstance(source, dict):
            continue

        if not is_published(source.get("publish")):
            continue

        event_id = str(source.get("id", "")).strip()
        title = str(source.get("title", "")).strip()

        if not event_id or not title:
            continue

        streams = clean_streams(
            stream_overrides.get(event_id, [])
        )

        event = {
            "id": event_id,
            "category": source.get("category", "Other"),
            "eventName": title,
            "status": source.get("status", "UPCOMING"),
            "eventInfo": source.get("eventInfo", {}),
            "streams": streams
        }

        events.append(event)

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "total": len(events),
        "events": events
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(
            output,
            file,
            ensure_ascii=False,
            indent=2
        )

    print(f"Successfully synced {len(events)} events.")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"SYNC ERROR: {error}", file=sys.stderr)
        sys.exit(1)
