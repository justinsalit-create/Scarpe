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

    def test_dead_site(self):
        import socket
        import urllib.error
        self.assertEqual(emails.fetch_status(urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))), "dead")
        self.assertEqual(emails.fetch_status(urllib.error.HTTPError("u", 404, "nf", {}, None)), "dead")
        self.assertEqual(emails.fetch_status(urllib.error.HTTPError("u", 403, "forbidden", {}, None)), "unknown")
        self.assertEqual(emails.fetch_status(TimeoutError()), "unknown")


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
            {"type": "node", "id": 6, "lat": 51.8, "lon": -0.4,
             "tags": {"amenity": "cafe", "name": "Gone Cafe", "disused": "yes", "website": "gone.cafe"}},
            {"type": "way", "id": 2, "center": {"lat": 51.5, "lon": -0.1},
             "tags": {"amenity": "restaurant", "cuisine": "pizza", "name": "Pizza Place", "website": "pizza.place"}},
            {"type": "node", "id": 3, "tags": {"amenity": "restaurant", "name": "No Contact"}},
        ]
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(osm, "fetch_borough", return_value=elements), \
                mock.patch.object(emails, "find_email", side_effect=lambda u: {
                    "pizza.place": ("ciao@pizza.place", ["ciao@pizza.place"], "ok", "https://pizza.place/"),
                    "oldbistro.com": ("", [], "closed", "https://oldbistro.com/")}[cli.CrawlCache.key(u)]):
            cli.main(["--boroughs", "Camden", "--out", d, "--cache", d])
            with open(os.path.join(d, "london_food_contacts.csv")) as f:
                text = f.read()
            self.assertIn("hi@crown.pub,openstreetmap", text)
            self.assertIn("ciao@pizza.place,venue website,https://pizza.place,2,", text)
            self.assertNotIn("No Contact", text)
            self.assertNotIn("Old Bistro", text)
            self.assertNotIn("Gone Cafe", text)
            self.assertNotIn("Kentish Town", text)
            self.assertEqual(text.count("The Crown"), 1)
            self.assertTrue(os.path.exists(os.path.join(d, "by_borough", "Camden.csv")))


if __name__ == "__main__":
    unittest.main()
