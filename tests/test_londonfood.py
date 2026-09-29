import os
import tempfile
import unittest
from unittest import mock

from londonfood import cli, emails, osm
from londonfood.boroughs import BOROUGHS, short_name
from londonfood.categories import categorize


class TestCategories(unittest.TestCase):
    def test_cuisine_tags(self):
        self.assertEqual(categorize({"amenity": "restaurant", "cuisine": "sushi;japanese"}),
                         ["Asian", "Japanese", "Sushi"])
        self.assertIn("Mexican", categorize({"amenity": "fast_food", "cuisine": "tacos"}))
        self.assertIn("Tacos", categorize({"amenity": "fast_food", "cuisine": "tacos"}))

    def test_amenity_and_shop(self):
        self.assertEqual(categorize({"amenity": "pub", "name": "The Crown"}), ["Pub"])
        self.assertEqual(categorize({"shop": "bakery"}), ["Bakery"])
        self.assertIn("Food truck", categorize({"amenity": "fast_food", "street_vendor": "yes"}))
        self.assertIn("Halal", categorize({"amenity": "fast_food", "diet:halal": "yes"}))

    def test_name_fallback_and_default(self):
        self.assertIn("Pizza", categorize({"amenity": "restaurant", "name": "Pizzeria Uno"}))
        self.assertEqual(categorize({"amenity": "cafe", "name": "Joe's"}), ["Cafe"])


class TestBoroughs(unittest.TestCase):
    def test_all_33(self):
        self.assertEqual(len(BOROUGHS), 33)
        self.assertEqual(short_name("London Borough of Camden"), "Camden")
        self.assertEqual(short_name("City of London"), "City of London")
        self.assertEqual(short_name("City of Westminster"), "Westminster")


class TestEmails(unittest.TestCase):
    def test_extract(self):
        page = ('<a href="mailto:Hello@TheCrown.co.uk?subject=hi">email</a> '
                'img@2x.png info@example.com bookings [at] thecrown.co.uk '
                '<span data-cfemail="422b2c242d023637302f2d6c212d6c3729">x</span>')
        found = emails.extract_emails(page)
        self.assertEqual(found[0], "hello@thecrown.co.uk")
        self.assertIn("info@turmo.co.uk", found)
        self.assertNotIn("info@example.com", found)

    def test_obfuscated(self):
        self.assertEqual(emails.extract_emails("write to info [at] venue [dot] co [dot] uk"), ["info@venue.co.uk"])
        self.assertEqual(emails.extract_emails("hello at thepub dot com"), ["hello@thepub.com"])
        self.assertEqual(emails.extract_emails("open at noon. dot matrix"), [])

    def test_fallback_contact_path(self):
        pages = {"https://v.com": ("https://v.com/", "<div id=app></div>"),
                 "https://v.com/robots.txt": ("", ""),
                 "https://v.com/contact": ("", "Email: hello@v.com")}
        def get(url, **kw):
            if url in pages:
                return pages[url]
            raise OSError("404")
        with mock.patch("londonfood.http.get", side_effect=get):
            self.assertEqual(emails.find_email("v.com")[0], "hello@v.com")

    def test_extract_is_fast_on_hostile_pages(self):
        import time
        page = "a" + " " * 50000 + "b" + "Q" * 200000 + "data:image/png;base64," + "A" * 300000 + " hi@v.com"
        t = time.time()
        self.assertEqual(emails.extract_emails(page), ["hi@v.com"])
        self.assertLess(time.time() - t, 2)

    def test_cfemail(self):
        self.assertEqual(emails.decode_cfemail("422b2c242d023637302f2d6c212d6c3729"), "info@turmo.co.uk")

    def test_best_email_prefers_own_domain(self):
        self.assertEqual(emails.best_email(["chef@gmail.com", "info@venue.co.uk"], "https://www.venue.co.uk/"),
                         "info@venue.co.uk")
        # other companies' domains (parent group, agency) are ignored; free-mail is fine
        self.assertEqual(emails.best_email(["hello@amalfi.co.uk"], "https://www.caferouge.com/"), "")
        self.assertEqual(emails.best_email(["hello@amalfi.co.uk", "joescafe@gmail.com"], "https://joes.cafe"),
                         "joescafe@gmail.com")
        # role addresses beat personal ones
        self.assertEqual(emails.best_email(["jane.doe@venue.com", "press@venue.com", "bookings@venue.com"],
                                           "https://venue.com"), "bookings@venue.com")
        self.assertEqual(emails.best_email(["investor@pe.com", "media@pe.com", "stpauls@pe.com"], "https://pe.com"),
                         "stpauls@pe.com")
        self.assertEqual(emails.extract_emails("\\u003einfo@venue.com"), ["info@venue.com"])

    def test_contact_links_same_site_only(self):
        page = ('<a href="/contact-us">Contact</a><a href="https://facebook.com/contact">fb</a>'
                '<a href="/menu">Menu</a><a href="about.html">Our story</a>')
        self.assertEqual(emails.contact_links(page, "https://venue.com/"),
                         ["https://venue.com/contact-us", "https://venue.com/about.html"])

    def test_not_venue_site(self):
        for url in ("https://www.facebook.com/pub", "instagram.com/cafe", "https://x.com/pub", "linktr.ee/bar",
                    "https://www.just-eat.co.uk/restaurants-x", "https://deliveroo.co.uk/menu/x",
                    "https://maps.app.goo.gl/abc", "https://www.opentable.co.uk/r/x"):
            self.assertTrue(emails.not_venue_site(url), url)
        for url in ("https://www.foodbox.com", "https://sites.google.com/view/mycafe", "https://pablospizza.co.uk",
                    "https://www.jdwetherspoon.com/pubs/all-pubs/england/london/the-crosse-keys"):
            self.assertFalse(emails.not_venue_site(url), url)
        self.assertTrue(emails.is_aggregator("https://www.jdwetherspoon.com/pubs/x"))

    def test_aggregators_skipped(self):
        self.assertEqual(emails.find_email("https://www.facebook.com/somepub"), ("", [], "skipped", "https://www.facebook.com/somepub"))

    def test_find_email_follows_contact_page(self):
        pages = {"https://venue.com": ("https://venue.com/", '<a href="/contact">Contact</a>'),
                 "https://venue.com/robots.txt": ("", ""),
                 "https://venue.com/contact": ("", "Write to info@venue.com")}
        with mock.patch("londonfood.http.get", side_effect=lambda url, **kw: pages[url]):
            self.assertEqual(emails.find_email("venue.com")[0], "info@venue.com")

    def test_closed_and_parked_sites(self):
        self.assertEqual(emails.site_status("<p>Sadly we have now closed for good. Thanks!</p>"), "closed")
        self.assertEqual(emails.site_status("<p>This domain is for sale</p>"), "parked")
        self.assertEqual(emails.site_status("<p>We are closed on Mondays. Open Tue-Sun.</p>"), "ok")
        self.assertEqual(emails.site_status("<script>var s='permanently closed'</script><p>Welcome</p>"), "ok")

    def test_parked_lander_redirect(self):
        page = '<html><head><script>window.onload=function(){window.location.href="/lander"}</script></head></html>'
        with mock.patch("londonfood.http.get", side_effect=lambda url, **kw: (
                ("", "") if url.endswith("robots.txt") else ("https://theivy.com/", page))):
            self.assertEqual(emails.find_email("theivy.com")[2], "parked")

    def test_meta_refresh_followed(self):
        pages = {"https://v.com": ("https://v.com/", '<meta http-equiv="refresh" content="0; url=/home">'),
                 "https://v.com/robots.txt": ("", ""),
                 "https://v.com/home": ("https://v.com/home", "mail us: hi@v.com")}
        with mock.patch("londonfood.http.get", side_effect=lambda url, **kw: pages[url]):
            self.assertEqual(emails.find_email("v.com")[0], "hi@v.com")

    def test_dead_site(self):
        import socket
        import urllib.error
        self.assertEqual(emails.fetch_status(urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))), "dead")
        self.assertEqual(emails.fetch_status(urllib.error.HTTPError("u", 404, "nf", {}, None)), "dead")
        self.assertEqual(emails.fetch_status(urllib.error.HTTPError("u", 403, "forbidden", {}, None)), "unknown")
        self.assertEqual(emails.fetch_status(TimeoutError()), "unknown")


class TestDiscover(unittest.TestCase):
    def test_candidates(self):
        from londonfood import discover
        c = discover.candidates("Pablo's Pizza", "Barking", ["Pizza"])
        self.assertEqual(c[:2], ["pablospizza.co.uk", "pablospizza.com"])
        self.assertIn("pablos-pizza.com", c)
        self.assertIn("pablospizzabarking.co.uk", c)
        self.assertEqual(discover.candidates("Cafe", ""), [])
        self.assertFalse(discover.usable("Mr A Smith"))
        self.assertFalse(discover.usable("Cafe"))
        self.assertTrue(discover.usable("Bambinos"))

    def test_evidence_requires_postcode_or_phone(self):
        from londonfood import discover
        v = {"postcode": "E1 6AN", "phone": "+44 20 7123 4567"}
        self.assertEqual(discover.evidence("Find us at 12 Brick Lane, London E1 6AN", v), "postcode")
        self.assertEqual(discover.evidence("Call 020 7123 4567", v), "phone")
        self.assertEqual(discover.evidence("Pablo's Pizza, Manchester M1 1AA", v), "")

    def test_verify_rejects_unrelated_site(self):
        from londonfood import discover
        v = {"name": "Pablo's Pizza", "postcode": "IG11 8DP", "phone": ""}
        with mock.patch("londonfood.http.get", return_value=("https://pablospizza.co.uk/", "Pablo's Pizza, Leeds LS1 4AP")):
            self.assertIsNone(discover.verify("pablospizza.co.uk", v))
        with mock.patch("londonfood.http.get", return_value=("https://pablospizza.co.uk/", "Pablo's, 3 East St, IG11 8DP")):
            self.assertEqual(discover.verify("pablospizza.co.uk", v), ("https://pablospizza.co.uk/", "postcode"))


class TestExtraSources(unittest.TestCase):
    def test_brand_sites(self):
        from londonfood import extra
        mk = lambda n, w="": {"name": n, "website": w, "website_type": "own site" if w else ""}
        venues = ([mk("Greggs", "https://www.greggs.co.uk/shop/%d" % i) for i in range(4)] + [mk("Greggs")]
                  + [mk("The Red Lion", "https://redlion%d.co.uk" % i) for i in range(4)] + [mk("The Red Lion")])
        self.assertEqual(extra.add_brand_sites(venues), 1)
        self.assertEqual(venues[4]["website"], "https://greggs.co.uk/")
        self.assertEqual(venues[-1]["website"], "")  # different pubs, different sites: no match

    def test_wikidata_match(self):
        from londonfood import extra
        items = [{"itemLabel": {"value": "The River Café"}, "site": {"value": "https://www.rivercafe.co.uk/"},
                  "coord": {"value": "Point(-0.2234 51.4846)"}}]
        venues = [{"name": "The River Cafe", "website": "", "lat": 51.4847, "lon": -0.2235},
                  {"name": "The River Cafe", "website": "", "lat": 51.60, "lon": -0.10}]
        with mock.patch.object(extra, "fetch_wikidata", return_value=items):
            self.assertEqual(extra.add_wikidata(venues, "/x"), 1)
        self.assertEqual(venues[0]["website"], "https://www.rivercafe.co.uk/")


class TestSocial(unittest.TestCase):
    def test_canonical_links(self):
        from londonfood import social
        self.assertEqual(social.canonical_instagram("@burger.dads"), "https://www.instagram.com/burger.dads/")
        self.assertEqual(social.canonical_instagram("https://instagram.com/burgerdads/?hl=en"),
                         "https://www.instagram.com/burgerdads/")
        self.assertEqual(social.canonical_instagram("https://www.instagram.com/p/Cxyz/"), "")
        self.assertEqual(social.canonical_facebook("https://m.facebook.com/burgerdadsbkk/"),
                         "https://www.facebook.com/burgerdadsbkk")
        self.assertEqual(social.canonical_facebook("https://facebook.com/profile.php?id=123&ref=x"),
                         "https://www.facebook.com/profile.php?id=123")
        self.assertEqual(social.canonical_facebook("https://www.facebook.com/sharer.php?u=x"), "")

    def test_collect_prefers_instagram_and_uses_social_website(self):
        from londonfood import social
        v = {"tags": {}, "facebook": "", "instagram": "", "social_website": "https://www.facebook.com/somepub"}
        social.collect(v)
        self.assertEqual(v["social_url"], "https://www.facebook.com/somepub")
        v = {"tags": {"contact:instagram": "somepub"}, "facebook": "somepub", "instagram": ""}
        social.collect(v)
        self.assertEqual(v["social_url"], "https://www.instagram.com/somepub/")


class TestMergeRules(unittest.TestCase):
    def test_fold_and_names(self):
        from londonfood import names
        self.assertEqual(names.fold("Hon\u2019s BBQ"), names.fold("Hon's BBQ"))
        self.assertTrue(names.names_match("Panadera Bakery", "Panadera"))
        self.assertTrue(names.names_match("Bombolone", "Bombolone Doughnuts"))
        self.assertFalse(names.names_match("Pad", "Pad Thai House"))  # under 4 chars: no substring match

    def test_never_name_only(self):
        from londonfood import names
        a = {"name": "Panadera", "lat": 13.7, "lon": 100.5}
        far = {"name": "Panadera Bakery", "lat": 13.9, "lon": 100.6}
        close = {"name": "Panadera Bakery", "lat": 13.7002, "lon": 100.5001}
        self.assertFalse(names.same_venue(a, far))
        self.assertTrue(names.same_venue(a, close))
        self.assertTrue(names.same_venue({"name": "X", "phone": "+66 2 123 4567"},
                                         {"name": "Y", "phone": "02-123-4567"}, "66"))
        merged = names.dedupe_venues([dict(a, email=""), dict(close, email="hi@panadera.com")])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["email"], "hi@panadera.com")

    def test_mx_validation(self):
        from londonfood import validate
        m = validate.MXChecker()
        m.data.update({"good.com": "ok", "gone.com": "no_domain", "nomx.com": "no_mx", "null.com": "null_mx"})
        self.assertEqual(m.check("info@good.com"), "valid")
        self.assertEqual(m.check("info@gone.com"), "no_domain")
        self.assertEqual(m.check("info@nomx.com"), "no_mx")
        self.assertEqual(m.check("info@null.com"), "null_mx")
        self.assertEqual(m.check("bad@@good.com"), "bad_syntax")


class TestClosedOSM(unittest.TestCase):
    def test_is_closed(self):
        self.assertTrue(osm.is_closed({"name": "The Bell (closed)"}))
        self.assertTrue(osm.is_closed({"name": "X", "disused": "yes"}))
        self.assertTrue(osm.is_closed({"name": "X", "end_date": "2020-01-01"}))
        self.assertTrue(osm.is_closed({"name": "X", "note": "Permanently closed 2024"}))
        self.assertFalse(osm.is_closed({"name": "X", "note": "closed on Sundays", "opening_hours": "Mo-Sa 09:00-17:00"}))


class TestEndToEnd(unittest.TestCase):
    def test_pipeline_without_network(self):
        elements = [
            {"type": "node", "id": 1, "lat": 51.5, "lon": -0.1,
             "tags": {"amenity": "pub", "name": "The Crown", "email": "hi@crown.pub"}},
            # same pub mapped again as a building outline -> duplicate
            {"type": "way", "id": 11, "center": {"lat": 51.50001, "lon": -0.10001},
             "tags": {"amenity": "pub", "name": "The Crown", "addr:postcode": "NW1 1AA"}},
            # chain branch sharing the pizza place's website -> merged, locations=2
            {"type": "node", "id": 4, "lat": 51.6, "lon": -0.2,
             "tags": {"amenity": "restaurant", "name": "Pizza Place Kentish Town", "website": "https://www.pizza.place/"}},
            {"type": "node", "id": 5, "lat": 51.7, "lon": -0.3,
             "tags": {"amenity": "restaurant", "name": "Old Bistro", "website": "oldbistro.com"}},
            {"type": "node", "id": 7, "lat": 51.9, "lon": -0.5,
             "tags": {"amenity": "fast_food", "name": "Taco Truck", "contact:facebook": "tacotruckldn"}},
            {"type": "node", "id": 6, "lat": 51.8, "lon": -0.4,
             "tags": {"amenity": "cafe", "name": "Gone Cafe", "disused": "yes", "website": "gone.cafe"}},
            {"type": "way", "id": 2, "center": {"lat": 51.5, "lon": -0.1},
             "tags": {"amenity": "restaurant", "cuisine": "pizza", "name": "Pizza Place", "website": "pizza.place"}},
            {"type": "node", "id": 3, "tags": {"amenity": "restaurant", "name": "No Contact"}},
        ]
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(osm, "fetch_borough", return_value=elements), \
                mock.patch("londonfood.areas.fetch_places", return_value=[]), \
                mock.patch("londonfood.extra.fetch_wikidata", return_value=[]), \
                mock.patch("londonfood.validate.MXChecker.check", return_value="valid"), \
                mock.patch.object(emails, "find_email", side_effect=lambda u: {
                    "pizza.place": ("ciao@pizza.place", ["ciao@pizza.place"], "ok", "https://pizza.place/"),
                    "oldbistro.com": ("", [], "closed", "https://oldbistro.com/")}[cli.CrawlCache.key(u)]):
            cli.main(["--boroughs", "Camden", "--out", d, "--cache", d, "--no-fsa"])
            with open(os.path.join(d, "london_food_emails.csv")) as f:
                em = f.read()
            with open(os.path.join(d, "london_food_websites.csv")) as f:
                web = f.read()
            self.assertTrue(em.startswith("email,name,"))
            self.assertTrue(web.startswith("website,website_type,name,"))
            self.assertIn("hi@crown.pub,The Crown", em)
            # pizza place: email found on its site, 2 branches share it; also listed in websites file
            self.assertIn("ciao@pizza.place,Pizza Place,Camden,,Pizza,https://pizza.place,,venue website,openstreetmap,2,", em)
            self.assertIn("https://pizza.place,own site,Pizza Place,Camden,,Pizza,ciao@pizza.place,2,", web)
            self.assertNotIn("facebook", web)  # social pages are not venue websites
            with open(os.path.join(d, "london_food_social.csv")) as f:
                soc = f.read()
            self.assertIn("https://www.facebook.com/tacotruckldn,Taco Truck", soc)  # no email/site -> social file
            for gone in ("No Contact", "Old Bistro", "Gone Cafe", "Kentish Town"):
                self.assertNotIn(gone, em + web)
            self.assertEqual(em.count("The Crown"), 1)
            self.assertTrue(os.path.exists(os.path.join(d, "by_borough", "Camden_emails.csv")))
            self.assertTrue(os.path.exists(os.path.join(d, "by_borough", "Camden_websites.csv")))

    def test_fsa_merge(self):
        from londonfood import fsa
        osm_el = [{"type": "node", "id": 1, "lat": 51.5, "lon": -0.1,
                   "tags": {"amenity": "restaurant", "name": "The Golden Wok", "addr:postcode": "E1 6AN"}}]
        ests = [{"FHRSID": 1, "BusinessName": "Golden Wok Ltd", "BusinessTypeID": 1, "PostCode": "E1 6AN"},
                {"FHRSID": 2, "BusinessName": "Bob's Burger Van", "BusinessTypeID": 7846, "PostCode": "E2 7AA",
                 "geocode": {"latitude": "51.52", "longitude": "-0.07"}},
                {"FHRSID": 3, "BusinessName": "Corner Newsagent", "BusinessTypeID": 4613, "PostCode": "E1 1AA"},
                {"FHRSID": 4, "BusinessName": "Brick Lane Beigel Bake", "BusinessTypeID": 4613, "PostCode": "E1 6SB"}]
        with mock.patch.object(osm, "fetch_borough", return_value=osm_el), \
                mock.patch.object(fsa, "fetch_borough", return_value=ests):
            venues = cli.collect(["London Borough of Tower Hamlets"], "/nonexistent", set())
        names = {v["name"]: v for v in venues}
        self.assertEqual(set(names), {"The Golden Wok", "Bob's Burger Van", "Brick Lane Beigel Bake"})
        self.assertIn("Food truck", names["Bob's Burger Van"]["categories"])
        self.assertIn("Bagels", names["Brick Lane Beigel Bake"]["categories"])

    def test_area_assignment(self):
        from londonfood import areas
        area_list = [a for a in areas.load_areas() if a["borough"] == "Camden"]
        places = [{"lat": 51.5390, "lon": -0.1426, "tags": {"name": "Camden Town"}},
                  {"lat": 51.5560, "lon": -0.1780, "tags": {"name": "Hampstead"}}]
        venues = [{"borough": "Camden", "lat": 51.5395, "lon": -0.1430, "postcode": "NW1 7JR"},
                  {"borough": "Camden", "lat": 51.5555, "lon": -0.1775, "postcode": "NW3 1QE"},
                  {"borough": "Camden", "lat": None, "lon": None, "postcode": "NW5 2AA"}]
        areas.assign(venues, areas.locate(area_list, places, venues))
        self.assertEqual([v["area"] for v in venues], ["Camden Town", "Hampstead", "Gospel Oak / Kentish Town"])
        self.assertEqual(areas.clean_name("Burroughs, The"), ["The Burroughs"])
        self.assertEqual(areas.districts("E1W 1AA"), {"E1W", "E1"})

    def test_osm_email_list_split(self):
        v = {"email": "info@a.com; Bookings@A.com", "website": "a.com"}
        cli.split_osm_email(v)
        self.assertEqual((v["email"], v["other_emails"], v["website"]),
                         ("info@a.com", ["bookings@a.com"], "https://a.com"))

if __name__ == "__main__":
    unittest.main()
