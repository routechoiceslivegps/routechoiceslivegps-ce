import base64
from io import BytesIO

import arrow
from django.core.cache import cache
from django.core.files import File
from rest_framework import status
from rest_framework.test import APIClient

from routechoices.api.tests import EssentialApiBase
from routechoices.core.models import Club, Competitor, Device, Event, EventSet, Map


class ClubViewsTestCase(EssentialApiBase):
    def setUp(self):
        super().setUp()
        self.club = Club.objects.create(name="Kemiön Kiilat", slug="kiilat")
        self.club.admins.set([self.user])

    def test_club_logo_load(self):
        cache.clear()
        icon_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAAXN"
            "SR0IArs4c6QAAAA1JREFUGFdjED765z8ABZcC1M3x7TQAAAAASUVORK5CYII="
        )
        base64.b64decode(
            "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEB"
            "QoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/wA"
            "ALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAA"
            "AAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
        )
        self.club.logo.save("logo.png", File(BytesIO(icon_bytes)))
        self.club.banner.save("banner.png", File(BytesIO(icon_bytes)))

        client = APIClient(HTTP_HOST="kiilat.routechoices.dev")

        url = self.reverse_and_check(
            "club_logo",
            "/logo",
            host="clubs",
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.headers.get("X-Cache-Hit"))
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.headers["X-Cache-Hit"], "1")

        url = self.reverse_and_check(
            "club_banner",
            "/banner",
            host="clubs",
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.headers.get("X-Cache-Hit"))
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.headers["X-Cache-Hit"], "1")

        url = self.reverse_and_check(
            "club_thumbnail",
            "/thumbnail",
            host="clubs",
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        url = self.reverse_and_check(
            "club_favicon",
            "/favicon.ico",
            host="clubs",
            extra_kwargs={"icon_name": "favicon.ico"},
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # test range response
        url = self.reverse_and_check(
            "club_banner",
            "/banner",
            host="clubs",
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )

        response = client.get(url, HTTP_RANGE="bytes=0-10")
        self.assertEqual(response.status_code, status.HTTP_206_PARTIAL_CONTENT)
        data = b""
        for d in iter(response.streaming_content):
            data += d
        self.assertEqual(len(data), 11)
        self.assertEqual(response.headers["Content-Length"], "11")
        self.assertEqual(response.headers["Content-Range"], "bytes 0-10/286")

        response = client.get(url, HTTP_RANGE="bytes=10-20")
        self.assertEqual(response.status_code, status.HTTP_206_PARTIAL_CONTENT)
        data2 = b""
        for d in iter(response.streaming_content):
            data2 += d
        self.assertEqual(len(data2), 11)
        self.assertNotEqual(data, data2)
        self.assertEqual(response.headers["Content-Length"], "11")
        self.assertEqual(response.headers["Content-Range"], "bytes 10-20/286")

    def test_club_commons_load(self):
        client = APIClient(HTTP_HOST="kiilat.routechoices.dev")
        s = EventSet.objects.create(
            club=self.club, name="Killa Cup", create_page=True, slug="kiila-cup"
        )
        Event.objects.create(
            name="Kiila Cup A",
            slug="kiila-cup-A",
            club=self.club,
            start_date=arrow.now().shift(days=-11).datetime,
            end_date=arrow.now().shift(days=-10).datetime,
            event_set=s,
        )
        Event.objects.create(
            name="Kiila Cup B",
            slug="kiila-cup-B",
            club=self.club,
            start_date=arrow.now().shift(hours=-1).datetime,
            end_date=arrow.now().shift(hours=1).datetime,
            event_set=s,
        )
        url = self.reverse_and_check(
            "club_sitemap.xml",
            "/sitemap.xml",
            host="clubs",
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        url = self.reverse_and_check(
            "club_feed",
            "/feed",
            host="clubs",
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        url = self.reverse_and_check(
            "manifest",
            "/manifest.json",
            host="clubs",
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        url = self.reverse_and_check(
            "robots.txt",
            "/robots.txt",
            host="clubs",
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_event_set_page_load(self):
        s = EventSet.objects.create(
            club=self.club,
            name="Killa Cup",
        )
        Event.objects.create(
            name="Kiila Cup A",
            slug="kiila-cup-A",
            club=self.club,
            start_date=arrow.now().shift(days=-11).datetime,
            end_date=arrow.now().shift(days=-10).datetime,
            event_set=s,
        )
        Event.objects.create(
            name="Kiila Cup B",
            slug="kiila-cup-B",
            club=self.club,
            start_date=arrow.now().shift(hours=-1).datetime,
            end_date=arrow.now().shift(hours=1).datetime,
            event_set=s,
        )
        client = APIClient(HTTP_HOST="kiilat.routechoices.dev")
        url = self.reverse_and_check(
            "event_view",
            "/kiila-cup/",
            host="clubs",
            extra_kwargs={"slug": "kiila-cup"},
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        s.create_page = True
        s.slug = "kiila-cup"
        s.save()

        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_event_map_load(self):
        client = APIClient(HTTP_HOST="kiilat.routechoices.dev")
        raster_map = Map.objects.create(
            club=self.club,
            name="Test map",
            calibration_string=(
                "61.45075,24.18994,61.44656,24.24721,"
                "61.42094,24.23851,61.42533,24.18156"
            ),
            width=1,
            height=1,
        )
        raster_map.data_uri = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAAXNSR0IArs4c6Q"
            "AAAA1JREFUGFdjED765z8ABZcC1M3x7TQAAAAASUVORK5CYII="
        )
        raster_map.save()
        event = Event.objects.create(
            name="Kiila Cup 1",
            slug="kiila-cup-1",
            club=self.club,
            start_date=arrow.now().shift(hours=-1).datetime,
            end_date=arrow.now().shift(hours=1).datetime,
        )
        url = self.reverse_and_check(
            "event_main_map_view",
            "/kiila-cup-1/map",
            host="clubs",
            host_kwargs={"club_slug": "kiilat"},
            extra_kwargs={"slug": "kiila-cup-1"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        response = self.client.get(response["Location"])
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        event.map = raster_map
        event.save()

        # event image
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        response = self.client.get(response["Location"])
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # event kmz
        url = self.reverse_and_check(
            "event_main_kmz_view",
            "/kiila-cup-1/kmz",
            host="clubs",
            host_kwargs={"club_slug": "kiilat"},
            extra_kwargs={"slug": "kiila-cup-1"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        response = self.client.get(response["Location"])
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # event zip
        url = self.reverse_and_check(
            "event_zip_view",
            "/kiila-cup-1/zip",
            host="clubs",
            host_kwargs={"club_slug": "kiilat"},
            extra_kwargs={"slug": "kiila-cup-1"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        response = self.client.get(response["Location"])
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # event thumbnail
        url = self.reverse_and_check(
            "event_map_thumbnail",
            "/kiila-cup-1/thumbnail",
            host="clubs",
            host_kwargs={"club_slug": "kiilat"},
            extra_kwargs={"slug": "kiila-cup-1"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_event_pages_load(self):
        client = APIClient(HTTP_HOST="kiilat.routechoices.dev")
        e = Event.objects.create(
            name="Kiila Cup 1",
            slug="kiila-cup-1",
            club=self.club,
            start_date=arrow.now().shift(hours=-1).datetime,
            end_date=arrow.now().shift(hours=1).datetime,
        )
        url = self.reverse_and_check(
            "club_view",
            "/",
            host="clubs",
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        url = self.reverse_and_check(
            "event_view",
            "/kiila-cup-1/",
            host="clubs",
            extra_kwargs={"slug": "kiila-cup-1"},
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        url = self.reverse_and_check(
            "event_export_view",
            "/kiila-cup-1/export",
            host="clubs",
            extra_kwargs={"slug": "kiila-cup-1"},
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Export event data")

        url = self.reverse_and_check(
            "event_contribute_view",
            "/kiila-cup-1/contribute",
            host="clubs",
            extra_kwargs={"slug": "kiila-cup-1"},
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        e.open_registration = True
        e.save()
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Enter yourself")
        self.assertNotContains(response, "Upload GPX")

        e.allow_route_upload = True
        e.save()
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Enter yourself")
        self.assertContains(response, "Upload GPX")

        e.open_registration = False
        e.save()
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotContains(response, "Enter yourself")
        self.assertContains(response, "Upload GPX")

        url = self.reverse_and_check(
            "event_view",
            "/kiila-cup-1/",
            host="clubs",
            extra_kwargs={"slug": "kiila-cup-1"},
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(f"{url}/does-not-exist")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("This page does not exist", response.content.decode())

        url = self.reverse_and_check(
            "event_startlist_view",
            "/kiila-cup-1/startlist",
            host="clubs",
            extra_kwargs={"slug": "kiila-cup-1"},
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Start List")

    def test_no_event_pages_load(self):
        client = APIClient(HTTP_HOST="kiilat.routechoices.dev")
        url = self.reverse_and_check(
            "event_view",
            "/kiila-cup-69/",
            host="clubs",
            extra_kwargs={"slug": "kiila-cup-69"},
            host_kwargs={"club_slug": "kiilat"},
            prefix="kiilat",
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("Event not found", response.content.decode())
        response = client.get(f"{url}export")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("Event not found", response.content.decode())
        response = client.get(f"{url}contribute")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("Event not found", response.content.decode())
        response = client.get(f"{url}does-not-exist")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("This page does not exist", response.content.decode())

    def test_custom_domain_load(self):
        client = APIClient(HTTP_HOST="gpstracking.kiilat.com")

        response = client.get("/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn(
            "This domain has not been associated with a club...",
            response.content.decode(),
        )

        self.club.domain = "gpstracking.kiilat.com"
        self.club.save()

        response = client.get("/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.club.acme_challenge = "hello.world"
        self.club.save()
        response = client.get("/.well-known/acme-challenge/hello")
        self.assertEqual(response.content, b"hello.world")

        self.club.domain = ""
        self.club.save()
        cache.clear()

        response = client.get("/")
        self.assertIn(
            "This domain has not been associated with a club...",
            response.content.decode(),
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_no_club_pages_load(self):
        client = APIClient(HTTP_HOST="haldensk.routechoices.dev")

        response = client.get("/kiila-cup-69/does-not-exist")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("This page does not exist", response.content.decode())

        response = client.get("/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("Club not found", response.content.decode())

    def test_future_event_pages_load(self):
        client = APIClient(HTTP_HOST="kiilat.routechoices.dev")

        Event.objects.create(
            name="Kiila Cup 2",
            slug="kiila-cup-2",
            club=self.club,
            start_date=arrow.now().shift(hours=1).datetime,
            end_date=arrow.now().shift(hours=2).datetime,
            open_registration=True,
            allow_route_upload=True,
        )

        response = client.get("/kiila-cup-2/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = client.get("/kiila-cup-2/export")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Export is not available yet")

        response = client.get("/kiila-cup-2/contribute")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Enter yourself")
        self.assertNotContains(response, "Upload GPX")

    def test_past_event_pages_load(self):
        client = APIClient(HTTP_HOST="kiilat.routechoices.dev")

        Event.objects.create(
            name="Kiila Cup 3",
            slug="kiila-cup-3",
            club=self.club,
            start_date=arrow.now().shift(hours=-2).datetime,
            end_date=arrow.now().shift(hours=-1).datetime,
            open_registration=True,
            allow_route_upload=True,
        )

        response = client.get("/kiila-cup-3/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = client.get("/kiila-cup-3/export")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Export event data")

        response = client.get("/kiila-cup-3/contribute")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Enter yourself")
        self.assertContains(response, "Upload GPX")

    def test_private_event_page_load(self):
        client = APIClient(HTTP_HOST="kiilat.routechoices.dev")

        Event.objects.create(
            name="Kiila Cup 4",
            slug="kiila-cup-4",
            club=self.club,
            start_date=arrow.now().shift(hours=-1).datetime,
            end_date=arrow.now().shift(hours=1).datetime,
            privacy="private",
            open_registration=True,
            allow_route_upload=True,
        )

        response = client.get("/kiila-cup-4/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        response = client.get("/kiila-cup-4/export")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        response = client.get("/kiila-cup-4/contribute")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        client.login(username="alice", password="pa$$word123")
        response = client.get("/kiila-cup-4/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = client.get("/kiila-cup-4/export")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_gpsseuranta_compat_pages_load(self):
        club = Club.objects.create(name="Test club", slug="myclub")
        event = Event.objects.create(
            club=club,
            slug="myevent",
            name="Test event",
            start_date=arrow.get().shift(hours=-2).datetime,
            end_date=arrow.get().shift(hours=-1).datetime,
        )
        device = Device.objects.create()
        device.add_locations(
            [
                (arrow.get().shift(seconds=-4070).timestamp(), 0, 0),
                (arrow.get().shift(seconds=-4069).timestamp(), 0.00001, 0.00001),
                (arrow.get().shift(seconds=-4068).timestamp(), 0.00101, 0.00101),
                (arrow.get().shift(seconds=-4067).timestamp(), 0.00121, 0.00039),
            ]
        )
        Competitor.objects.create(
            name="Alice A",
            short_name="A",
            event=event,
            device=device,
        )
        client = APIClient(HTTP_HOST="myclub.routechoices.dev")
        url = self.reverse_and_check(
            "event_gpsseuranta_data_view",
            "/myevent/data.lst",
            host="clubs",
            extra_kwargs={"extension": "lst", "slug": event.slug},
            host_kwargs={"club_slug": club.slug},
            prefix=club.slug,
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        url = self.reverse_and_check(
            "event_gpsseuranta_init_view",
            "/myevent/init.txt",
            host="clubs",
            extra_kwargs={"slug": event.slug},
            host_kwargs={"club_slug": club.slug},
            prefix=club.slug,
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        url = self.reverse_and_check(
            "gpsseuranta_time",
            "/gps/time.php",
            host="clubs",
            host_kwargs={"club_slug": club.slug},
            prefix=club.slug,
        )
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
