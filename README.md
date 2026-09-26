# London food venue contacts

Builds a spreadsheet (CSV) of every food-selling venue in Greater London, covering restaurants, pubs,
takeaways, cafés, bakeries, food trucks and ice-cream shops. For each venue it gives an **email
address**, or the **website** when no email can be found.

It covers all 33 London boroughs: the City of London, Westminster, Camden, Hackney, Tower Hamlets,
Southwark, Lambeth, Islington, Kensington & Chelsea, Hammersmith & Fulham, Wandsworth, Greenwich,
Lewisham, Newham, Barking & Dagenham, Redbridge, Havering, Waltham Forest, Haringey, Enfield, Barnet,
Harrow, Brent, Ealing, Hillingdon, Hounslow, Richmond, Kingston, Merton, Sutton, Croydon, Bromley
and Bexley.

## Categories

Deli, Chinese, Japanese, Asian, Sushi, Korean, Indian, Halal, Pizza, Italian, Tacos, Mexican,
Spanish, Burger, American, Greek, Food truck, Pub, French, BBQ, Steak, Bakery, Breakfast, Donuts,
Bagels, Ice cream, Seafood. Venues that don't fit any of these are still kept and labelled Cafe,
Takeaway, Bar or Restaurant. A venue can have more than one category, for example
`Asian; Japanese; Sushi`.

## How it works

1. **OpenStreetMap (Overpass API):** pulls every restaurant, pub, fast-food place, café, bakery, deli,
   ice-cream shop and street vendor inside each borough boundary. This includes any email address,
   website, phone number and address recorded for it. OSM data is open (ODbL), so it can be reused
   legally.
2. **Venue websites:** for venues without an email, it visits their own website. It reads the
   homepage and up to 4 contact, about or booking pages, and collects `mailto:` links, plain-text
   addresses and Cloudflare-obfuscated addresses. It respects `robots.txt`. Where possible it picks
   an address on the venue's own domain, preferring `info@`, `hello@`, `bookings@` and similar.
   Links to Facebook, Instagram, Deliveroo, OpenTable and other aggregators are kept as the website
   but not crawled.
3. **No closed venues:** it drops a venue when OSM marks it disused, abandoned or closed, gives it an
   end date in the past, or has "closed" in its name. It also drops a venue when its website no
   longer exists (the domain is gone, or the site returns 404/410), is a parked or for-sale domain,
   or says the venue is "permanently closed", "closed for good" or "ceased trading". Every venue
   with a website is checked this way, including ones whose email came from OSM.
4. **No duplicates:** a venue mapped twice (for example once as a point and once as a building) is
   only kept once. Each email or website appears only once in its file. For chains, the
   `locations` column shows how many branches share it.
5. **Google Maps (optional):** if you give it a Google Places API key, it looks up a website for
   venues that don't have one, using the official API. It also drops any venue Google lists as
   permanently closed.

Yelp, OpenTable, blogs and chambers of commerce are not scraped directly. Their terms forbid it,
they block bots, and Yelp's official API doesn't return emails or websites anyway. OSM plus each
venue's own website covers the same businesses and gives you the contact details directly.

## Run it

Needs Python 3.9+ and no extra packages.

```bash
python -m londonfood                                    # everything, all 33 boroughs
python -m londonfood --boroughs Camden Hackney          # just some boroughs
python -m londonfood --categories Pizza Sushi Pub       # just some categories
GOOGLE_MAPS_API_KEY=... python -m londonfood            # also fill missing websites from Google
python -m londonfood --no-crawl                         # fast: OSM data only, no website visits
```

Output: two separate files, both de-duplicated.

- `output/london_food_emails.csv`: one row per unique email address. Columns: `email, name,
  borough, categories, website, other_emails, email_source, locations, phone, address, postcode,
  osm_url`.
- `output/london_food_websites.csv`: one row per unique venue website. Columns: `website, name,
  borough, categories, email, locations, phone, address, postcode, facebook, instagram, osm_url`.
- `output/by_borough/<Borough>_emails.csv` and `<Borough>_websites.csv`: the same data split by
  borough.

A venue with both an email and a website appears in both files. Nothing is guessed. Every email
was published by the venue, either on OpenStreetMap or on its own website. Addresses on unrelated
domains (web agencies, landlords, parent companies) and non-contact addresses (privacy@, jobs@,
investor@) are dropped. Every website comes from the venue's OpenStreetMap listing, or from Google
Places if you use a key.

A full run crawls tens of thousands of websites and takes a few hours. Results are cached in
`.cache/` (or `~/.londonfood-cache` for `discover_all.sh`, snapshotted to `cache/`), so if a run is interrupted, running it again picks up where it stopped.

## Tests

```bash
python -m unittest -v
```

## Using the list for email marketing

UK PECR rules let you email limited companies without their prior consent. Many independent venues
are sole traders or partnerships, though, and those need consent first. Always include an easy
unsubscribe option.
