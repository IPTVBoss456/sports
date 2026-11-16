
#!/usr/bin/env python3
"""
LiveZoneTV Sports Auto Sync
- Fetches sports events.
- Fetches each event's streamsUrl.
- Converts events and streams into LiveZoneTV format.
- Keeps existing output safe if the source API fails.
"""

import json
import os
import tempfile
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

EVENTS_API = "https://vortextv.modsdone.com/cricfy.php/events"

OUTPUT_JSON = Path("LiveZoneTV_Sports.json")

USER_AGENT = "Mozilla/5.0 (compatible; LiveZoneTV-Sync/1.0)"
TIMEOUT = 25
MAX_WORKERS = 8


def fetch_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        raw = response.read().decode("utf-8-sig")

    return json.loads(raw)


def first_value(data, *keys, default=""):
    if not isinstance(data, dict):
        return default

    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value

    return default


def get_events(source):
    if isinstance(source, list):
        return source

    if isinstance(source, dict):
        for key in ("data", "events", "results"):
            value = source.get(key)

            if isinstance(value, list):
                return value

            if isinstance(value, dict):
                for nested in ("events", "items", "results"):
                    if isinstance(value.get(nested), list):
                        return value[nested]

    raise ValueError("Could not find events array in API response.")


def get_stream_items(source):
    """
    Supports common stream response structures.
    Exact source keys will be finalized after checking
    one real streamsUrl response.
    """
    if isinstance(source, list):
        return source

    if isinstance(source, dict):
        for key in ("data", "streams", "formats", "results", "sources"):
            value = source.get(key)

            if isinstance(value, list):
                return value

            if isinstance(value, dict):
                for nested in ("streams", "formats", "items", "sources"):
                    if isinstance(value.get(nested), list):
                        return value[nested]

        # A single stream object
        if any(k in source for k in (
            "streamUrl", "stream_url", "url", "m3u8"
        )):
            return [source]

    return []


def convert_stream(item, event):
    if not isinstance(item, dict):
        return None

    url = first_value(
        item, "streamUrl", "stream_url", "url", "m3u8", "link",
        "webLink"
    )

    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None

    headers = item.get("headers", "")

    if isinstance(headers, dict):
        headers = "|".join(
            f"{key}={value}"
            for key, value in headers.items()
            if value
        )

    backup = first_value(
        item, "backupUrls", "backup_urls", default=[]
    )

    if isinstance(backup, str):
        backup = [backup] if backup else []

    if not isinstance(backup, list):
        backup = []

    return {
        "channelName": str(first_value(
            item, "channelName", "channel_name", "name", "title",
            default=first_value(event, "title", "eventName", "name")
        )),
        "streamUrl": url,
        "logoUrl": first_value(
            item, "logoUrl", "logo", "image", "thumbnail"
        ) or None,
        "drmKey": first_value(
            item, "drmKey", "drm_key", "licenseKey"
        ) or None,
        "headers": str(headers),
        "quality": str(first_value(
            item, "quality", "label", "resolution"
        )),
        "backupUrls": backup,
    }


def fetch_event_streams(event):
    streams_endpoint = first_value(
        event, "streamsUrl", "streams_url"
    )

    if not isinstance(streams_endpoint, str):
        return []

    if not streams_endpoint.startswith(("http://", "https://")):
        return []

    try:
        response = fetch_json(streams_endpoint)
        items = get_stream_items(response)

        converted = []

        for item in items:
            stream = convert_stream(item, event)

            if stream:
                converted.append(stream)

        return converted

    except Exception as exc:
        print(f"Stream fetch failed: {streams_endpoint}: {exc}")
        return []


def convert_event(event, streams):
    if not isinstance(event, dict):
        return None

    event_id = first_value(event, "id", "eventId")
    event_name = first_value(
        event, "title", "eventName", "name"
    )

    if not event_name:
        return None

    info = event.get("eventInfo", {})

    if not isinstance(info, dict):
        info = {}

    return {
        "id": str(event_id or event_name),
        "category": first_value(
            event, "category", "sport", default="Sports"
        ),
        "eventName": str(event_name),
        "status": first_value(
            event, "status", "matchStatus", default="UPCOMING"
        ),
        "eventInfo": info,
        "streams": streams,
    }


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=path.name,
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)

        os.replace(temp_name, path)

    except Exception:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


def main():
    print("Fetching LiveZoneTV Sports Events...")

    source = fetch_json(EVENTS_API)
    source_events = get_events(source)

    if not source_events:
        raise RuntimeError("No events found. Existing JSON preserved.")

    print(f"Events found: {len(source_events)}")

    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_event_streams, event): index
            for index, event in enumerate(source_events)
        }

        for future in as_completed(futures):
            index = futures[future]

            try:
                results[index] = future.result()
            except Exception as exc:
                print(f"Event {index} failed: {exc}")
                results[index] = []

    converted_events = []

    for index, event in enumerate(source_events):
        streams = results.get(index, [])
        converted = convert_event(event, streams)

        if converted:
            converted_events.append(converted)

    if not converted_events:
        raise RuntimeError(
            "No valid events converted. Existing JSON preserved."
        )

    output = {
        "events": converted_events
    }

    json_text = json.dumps(
        output,
        ensure_ascii=False,
        indent=2
    ) + "\n"

    atomic_write(OUTPUT_JSON, json_text)

    total_streams = sum(
        len(event["streams"]) for event in converted_events
    )

    print("--------------------------------")
    print(f"Total Events: {len(converted_events)}")
    print(f"Total Streams: {total_streams}")
    print(f"Saved: {OUTPUT_JSON}")
    print("Sports Sync Completed!")


if __name__ == "__main__":
    main()
