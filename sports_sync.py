
#!/usr/bin/env python3

import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

EVENTS_API = "https://vortextv.modsdone.com/cricfy.php/events"
OUTPUT_FILE = "LiveZoneTV_Sports.json"

TIMEOUT = 25
MAX_WORKERS = 8

HEADERS = {
    "User-Agent": "LiveZoneTV-Sports-Sync/1.0",
    "Accept": "application/json"
}


def fetch_json(url):
    request = urllib.request.Request(url, headers=HEADERS)

    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def valid_url(url):
    if not isinstance(url, str):
        return False

    try:
        parsed = urlsplit(url.strip())
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def is_published(value):
    if value is None:
        return True

    if isinstance(value, bool):
        return value

    return str(value).strip().lower() not in (
        "false", "0", "no", "off", "unpublished"
    )


def parse_stream_link(raw_link):
    """
    Source format:
    https://example.com/master.m3u8|User-Agent=Mozilla&Referer=https://example.com

    LiveZoneTV format:
    streamUrl: https://example.com/master.m3u8
    headers: User-Agent=Mozilla|Referer=https://example.com
    """

    if not isinstance(raw_link, str):
        return None, ""

    parts = raw_link.strip().split("|", 1)
    stream_url = parts[0].strip()

    if not valid_url(stream_url):
        return None, ""

    parsed_headers = []

    if len(parts) > 1:
        raw_headers = parts[1].strip()

        for item in re.split(r"[&|]", raw_headers):
            item = item.strip()

            if "=" not in item:
                continue

            key, value = item.split("=", 1)
            key = key.strip()
            value = value.strip()

            if key and value:
                parsed_headers.append(f"{key}={value}")

    return stream_url, "|".join(parsed_headers)


def fetch_event_streams(event):
    event_id = str(event.get("id", "")).strip()
    streams_url = event.get("streamsUrl")

    if not valid_url(streams_url):
        return event_id, []

    response = fetch_json(streams_url)

    if not isinstance(response, dict) or response.get("status") != "ok":
        raise ValueError(f"Invalid stream response for event {event_id}")

    data = response.get("data", {})
    source_streams = data.get("streamUrls", [])

    if not isinstance(source_streams, list):
        raise ValueError(f"Invalid stream list for event {event_id}")

    streams = []

    for source in source_streams:
        if not isinstance(source, dict):
            continue

        # type 7 entries are DRM/CENC streams.
        # Do not publish or extract their api key material.
        if str(source.get("type", "")).strip() == "7":
            continue

        stream_url, headers = parse_stream_link(source.get("link", ""))

        if not stream_url:
            continue

        streams.append({
            "channelName": str(
                source.get("title") or "Live Stream"
            ).strip(),
            "streamUrl": stream_url,
            "quality": "HD",
            "headers": headers,
            "backupUrls": []
        })

    return event_id, streams


def main():
    print("Fetching events...")

    response = fetch_json(EVENTS_API)

    if not isinstance(response, dict) or response.get("status") != "ok":
        raise ValueError("Events API returned an invalid response")

    source_events = response.get("data")

    if not isinstance(source_events, list):
        raise ValueError("Events API data is not a list")

    published_events = []

    for source in source_events:
        if not isinstance(source, dict):
            continue

        if not is_published(source.get("publish")):
            continue

        event_id = str(source.get("id", "")).strip()
        title = str(source.get("title", "")).strip()

        if not event_id or not title:
            continue

        published_events.append(source)

    print(f"Found {len(published_events)} published events.")

    stream_results = {}

    # Fetch each event's stream endpoint automatically.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_event_streams, event): event
            for event in published_events
            if valid_url(event.get("streamsUrl"))
        }

        for future in as_completed(futures):
            event = futures[future]

            try:
                event_id, streams = future.result()
                stream_results[event_id] = streams
                print(f"Fetched {event_id}: {len(streams)} playable HLS streams")

            except Exception as error:
                # Abort instead of publishing incomplete data.
                raise RuntimeError(
                    f"Stream fetch failed for event "
                    f"{event.get('id')}: {error}"
                ) from error

    final_events = []

    for source in published_events:
        event_id = str(source["id"]).strip()

        final_events.append({
            "id": event_id,
            "category": source.get("category") or "Other",
            "eventName": source.get("title") or "Live Event",
            "status": source.get("status") or "UPCOMING",
            "eventInfo": source.get("eventInfo") or {},
            "streams": stream_results.get(event_id, [])
        })

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "total": len(final_events),
        "events": final_events
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)

    print("--------------------------------")
    print("LiveZoneTV Sports Sync Complete")
    print(f"Events: {len(final_events)}")
    print(f"Output: {OUTPUT_FILE}")
    print("--------------------------------")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"SYNC FAILED: {error}", file=sys.stderr)
        sys.exit(1)
