# Playbook: food-venue emails and websites for a city

This is the method used for London (Sept 2026) and Greater Bangkok. Follow it for every new city.
Each city is kept separate: its settings live in `cities/<slug>.json`, its results in `output/<slug>/`,
and its cache snapshot in `cache/<slug>/`.

## What the client wants (standing rules)

1. **Every business that sells food**: restaurants, pubs and bars serving food, cafés, takeaways,
   bakeries, delis, ice-cream shops and food trucks. The categories they care about are Deli, Chinese,
   Japanese, Asian, Sushi, Korean, Indian, Halal, Pizza, Italian, Tacos, Mexican, Spanish, Burger,
   American, Greek, Food truck, Pub, French, BBQ, Steak, Bakery, Breakfast, Donuts, Bagels,
   Ice cream and Seafood (plus Thai and Western, added for Bangkok).
2. **Two separate files**: an **emails** file and a **websites** file. A website is as good as an
   email. A venue with both appears in both files.
3. **Nothing guessed.** Every email is published by the venue (on the map or on its own site). Every
   website either comes from a listing or was verified to belong to the venue (see step 5 below).
4. **No duplicates** (one row per email / per website; chains get a `locations` count) and
   **no permanently closed venues**.
5. **Venue websites only.** No social media (Facebook, Instagram, X, TikTok, Linktree) and no
   delivery, booking or review platforms (Just Eat, Deliveroo, OpenTable, Tripadvisor, Google Maps).
6. **Cover every district** of the metro area. If the client sends a list of areas, cover all of them.
7. **Prioritise when asked.** For Bangkok, English-speaking or Americanized venues come first (see
   "English priority" below).
8. **Deliver clickable PDFs for iPhone**: `tools/make_pdfs.py <city>` makes phone-sized PDFs where
   tapping an email opens Mail and tapping a website opens Safari. Send both PDFs to the client.
9. **Take as long as needed**, but keep the client posted with totals after each batch.

The client decided against paying for a Google Places API key. Don't scrape Google Maps, Yelp,
OpenTable, search-engine result pages or social media: it breaks their terms and they block it.

## Sources, in order

1. **OpenStreetMap** (Overpass API; mirrors in `londonfood/osm.py`): every food venue inside each
   district boundary, with any website, email, phone and address on the map.
2. **Official food-business registers**, where one exists. The UK has the FSA hygiene-rating register
   (`londonfood/fsa.py`), which lists every registered food business but no contacts. Look for the
   local equivalent for each new country.
3. **Wikidata**: official websites of venues inside the city's bounding box, matched by name within
   200 m.
4. **Chain branches**: when at least 3 mapped branches with the same name agree on one domain, other
   branches with that name get it.
5. **Verified website discovery** (`londonfood/discover.py`): build candidate domains from the
   venue's English name (plus district, city word and aliases like `bkk`, using the city's TLDs).
   Check each exists via DNS-over-HTTPS, open it, and accept it only if the site shows the venue's
   **phone** or **postcode**. In countries with 5-digit postcodes, the venue's name must also appear.
   For Bangkok, a site showing the venue's **full name and the city** is also accepted, labelled
   `name + city`. London result: about 30% recall and 100% precision on a test against known sites.
6. **Venue websites crawled** (`londonfood/emails.py`): the homepage, contact/about/booking pages and
   the usual paths (`/contact`, `/about` ...). This collects `mailto:` links, plain and obfuscated
   addresses (`info [at] x [dot] com`, Cloudflare) and the site's language. It also detects closed
   venues ("permanently closed" in English or Thai), dead domains (DNS NXDOMAIN, 404/410) and
   parked domains. Only addresses on the venue's own domain or free-mail are kept; role addresses
   (`info@`, `bookings@`) are preferred, and privacy@ / jobs@ / investor@ are dropped.

## Merging with the partner pipeline (agreed rules)

The client runs a second pipeline ("Muse sweep": social media, Wongnai, food blogs, delivery
platforms) and merges both into one master list. Rules agreed with it:

- **Name matching** (`londonfood/names.py`): map curly quotes to straight quotes before ASCII folding.
  Names of 4 or more characters match when one contains the other ("Panadera" = "Panadera Bakery").
  A match always needs email, website, phone or location to agree too, never the name alone.
- **Email validation** (`londonfood/validate.py`, `tools/validate_emails.py <city>`): check syntax,
  then MX via DNS-over-HTTPS. Reject no-domain, null-MX and no-MX addresses, and list them in
  `<city>_rejected_emails.csv`. SMTP RCPT checks aren't possible from the sandbox, which only allows
  HTTPS out.
- **`english` yes/no** per row, plus the score and reasons.
- **Handoff file**: `output/<city>/<city>_venues_master.csv` lists every open venue, contact or not,
  already merged, for the partner pipeline to fill the gaps. Brief the client with totals.

## English priority (non-English-speaking cities)

`english_score` in `londonfood/city.py`: +3 if the website is in English, +2 if it has an English
version or is bilingual, +2 for Western/Americanized food (burger, pizza, steak, American, Italian,
Mexican, pub, breakfast, bagels ...), +1 for a Latin-script name. Priority is **High** at 4 or more,
**Medium** at 2 or 3, and **Low** otherwise. Files are sorted High first, with a `why` column.

## New city checklist

1. Find the metro-area boundary on OpenStreetMap: run `is_in(lat,lon);out tags;` on Overpass at the
   city centre and pick the region relation. The area id is 3600000000 + relation id.
2. Write `cities/<slug>.json` (copy `cities/bangkok.json`): `region_area_id`, `unit_admin_level` (the
   district level), `exclude_units` for neighbouring districts that touch the boundary,
   `expected_units` (the official count), `country_code`, `min_phone_digits`, `postcode_needs_name`,
   `tlds`, `city_word`, `city_aliases`, `city_evidence`, `bbox`, `local_script` (regex for the local
   alphabet, or omit it), and `english_priority`.
3. Test one central district: `python3 -m londonfood.city <slug> --units "<district>" --discover --discover-limit 300`.
   Check the files look right.
4. Full run in the background: `./city_all.sh <slug>`. It runs the map data and crawl, then discovery
   batches of 2000, commits and pushes after every batch, and makes the PDFs at the end. The cache
   is in `~/.foodcontacts-cache/<slug>`, and runs resume from the snapshot after a container restart.
5. While it runs: re-arm a Monitor on `/tmp/claude-0/<slug>_batch.log` for "unique websites",
   "Traceback" and "Error". Schedule an hourly `send_later` check-in that restarts the script if the
   container restarted. If "crawled N" stops moving for 30+ minutes, look for a hang (a regex was
   once the cause).
6. When it's done: check there are no social links in the websites file, send the two PDFs, and report
   the totals (venues, emails, websites, High-priority counts).

## London specifics

London predates the generic pipeline. It runs `python -m londonfood` (see `README.md`),
`run_all.sh` (borough by borough) and `discover_all.sh` (discovery batches). It uses the FSA
register, the client's neighbourhood list `data/london_areas.csv` (adds an `area` column) and
Hamsey Green (outside the boundary, searched by radius). Final result: 57,886 venues, 5,566 emails,
12,251 websites.
