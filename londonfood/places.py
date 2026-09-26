"""Optional: fill in missing websites via the official Google Places API (needs an API key)."""

import json

from . import http

ENDPOINT = "https://places.googleapis.com/v1/places:searchText"


def lookup_website(name, address, api_key, lat=None, lon=None):
    """Return (website, businessStatus) e.g. ("https://...", "OPERATIONAL" / "CLOSED_PERMANENTLY")."""
    body = {"textQuery": f"{name} {address} London", "maxResultCount": 1}
    if lat and lon:
        body["locationBias"] = {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": 150.0}}
    try:
        _, text = http.get(ENDPOINT, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.displayName,places.websiteUri,places.businessStatus",
        })
    except Exception:
        return "", ""
    places = json.loads(text).get("places", [])
    if not places:
        return "", ""
    return places[0].get("websiteUri", ""), places[0].get("businessStatus", "")
