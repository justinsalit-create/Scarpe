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

    def test_contact_links_same_site_only(self):
        page = ('<a href="/contact-us">Contact</a><a href="https://facebook.com/contact">fb</a>'
                '<a href="/menu">Menu</a><a href="about.html">Our story</a>')
        self.assertEqual(emails.contact_links(page, "https://venue.com/"),
                         ["https://venue.com/contact-us", "https://venue.com/about.html"])

    def test_aggregators_skipped(self):
        self.assertEqual(emails.find_email("https://www.facebook.com/somepub"), ("", []))

    def test_find_email_follows_contact_page(self):
        pages = {"https://venue.com": ("https://venue.com/", '<a href="/contact">Contact</a>'),
                 "https://venue.com/robots.txt": ("", ""),
                 "https://venue.com/contact": ("", "Write to info@venue.com")}
        with mock.patch("londonfood.http.get", side_effect=lambda url, **kw: pages[url]):
            self.assertEqual(emails.find_email("venue.com")[0], "info@venue.com")


class TestEndToEnd(unittest.TestCase):
    def test_pipeline_without_network(self):
        elements = [
            {"type": "node", "id": 1, "lat": 51.5, "lon": -0.1,
             "tags": {"amenity": "pub", "name": "The Crown", "email": "hi@crown.pub"}},
            {"type": "way", "id": 2, "center": {"lat": 51.5, "lon": -0.1},
             "tags": {"amenity": "restaurant", "cuisine": "pizza", "name": "Pizza Place", "website": "pizza.place"}},
            {"type": "node", "id": 3, "tags": {"amenity": "restaurant", "name": "No Contact"}},
        ]
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(osm, "fetch_borough", return_value=elements), \
                mock.patch.object(emails, "find_email", return_value=("ciao@pizza.place", [])):
            cli.main(["--boroughs", "Camden", "--out", d, "--cache", d])
            with open(os.path.join(d, "london_food_contacts.csv")) as f:
                text = f.read()
            self.assertIn("hi@crown.pub,openstreetmap", text)
            self.assertIn("ciao@pizza.place,venue website", text)
            self.assertNotIn("No Contact", text)
            self.assertTrue(os.path.exists(os.path.join(d, "by_borough", "Camden.csv")))


if __name__ == "__main__":
    unittest.main()
