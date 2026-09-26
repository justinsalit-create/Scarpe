"""Optional: fill in missing websites via the official Google Places API (needs an API key)."""

import json

from . import http

ENDPOINT = "https://places.googleapis.com/v1/places:searchText"


def lookup_website(name, address, api_key, lat=None, lon=None):
    body = {"textQuery": f"{name} {address} London", "maxResultCount": 1}
    if lat and lon:
        body["locationBias"] = {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": 150.0}}
    try:
        _, text = http.get(ENDPOINT, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.displayName,places.websiteUri",
        })
    except Exception:
        return ""
    places = json.loads(text).get("places", [])
    return places[0].get("websiteUri", "") if places else ""
