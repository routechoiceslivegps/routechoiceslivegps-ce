import random
import re
from io import BytesIO

import arrow
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django_hosts.resolvers import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from routechoices.core.models import (
    Club,
    Competitor,
    Device,
    Event,
    EventSet,
    ImeiDevice,
    Map,
)


class EssentialDashboardBase(APITestCase):
    def setUp(self):
        self.client = APIClient(HTTP_HOST="dashboard.routechoices.dev")
        self.club = Club.objects.create(name="My Club", slug="myclub")
        self.user = User.objects.create_user(
            "alice", f"alice{random.randrange(1000)}@example.com", "pa$$word123"
        )
        self.club.admins.set([self.user])
        self.client.force_login(self.user)
        self.client.get(f"/club/{self.club.aid}")

    def reverse_and_check(
        self,
        path,
        expected,
        extra_kwargs=None,
        host_kwargs=None,
    ):
        url = reverse(
            path, host="dashboard", kwargs=extra_kwargs, host_kwargs=host_kwargs
        )
        self.assertEqual(url, f"//dashboard.routechoices.dev{expected}")
        return url


class TestDashboard(EssentialDashboardBase):
    def test_edit_account(self):
        url = self.reverse_and_check("account_edit_view", "/account/")

        res = self.client.get(url)
        self.assertContains(res, "alice")

        res = self.client.post(
            url,
            {"username": "brice", "first_name": "Brice", "last_name": ""},
            follow=True,
        )
        self.assertContains(res, "brice")
        self.assertContains(res, "Brice")

    def test_delete_account(self):
        url = self.reverse_and_check("account_delete_view", "/account/delete")
        res = self.client.get(url)
        self.assertContains(res, "Delete your account?")
        res = self.client.get(f"{url}?confirmation_key=invalid")
        self.assertContains(
            res,
            "This account deletion confirmation link is either expired, invalid or not destinated for account",
        )

        res = self.client.post(url, {"confirmation_key": "invalid"}, follow=True)
        self.assertContains(
            res,
            "This account deletion confirmation link is either expired, invalid or not destinated for account",
        )

        EmailAddress.objects.create(
            user=self.user, email=self.user.email, primary=True, verified=True
        )
        res = self.client.post(url)
        self.assertContains(res, "Account deletion confirmation sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue("Sad to see you go" in mail.outbox[0].body)

        key = re.search(r"\?confirmation_key=([^\n]+)", mail.outbox[0].body).group(1)

        res = self.client.get(f"{url}?confirmation_key={key}")
        self.assertContains(res, "This is definitive and cannot be reversed.")
        res = self.client.post(url, {"confirmation_key": key}, follow=True)
        self.assertContains(res, "They Trust Us")
        self.assertEqual(User.objects.count(), 0)

    def test_change_club_slug(self):
        url = self.reverse_and_check(
            "dashboard_club:edit_view",
            "/clubs/myclub/",
            extra_kwargs={"club_slug": self.club.slug},
        )

        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        res = self.client.post(
            url,
            {"name": self.club.name, "admins": self.user.pk, "slug": "mynewclubslug"},
            follow=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotContains(res, "invalid-feedback")

        res = self.client.post(
            "/clubs/mynewclubslug/",
            {
                "name": self.club.name,
                "admins": self.user.pk,
                "slug": "mynewestclubslug",
            },
            follow=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(
            res, "Domain prefix can be changed only once every 72 hours."
        )

        res = self.client.post(
            "/clubs/mynewclubslug/",
            {"name": self.club.name, "admins": self.user.pk, "slug": "myclub"},
            follow=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotContains(res, "invalid-feedback")

        Club.objects.create(name="Other Club", slug="mynewclubslug")

        res = self.client.post(
            url,
            {"name": self.club.name, "admins": self.user.pk, "slug": "mynewclubslug"},
            follow=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "Domain prefix already registered.")

    def test_custom_domain(self):
        url = self.reverse_and_check(
            "dashboard_club:custom_domain_view",
            "/clubs/myclub/custom-domain",
            extra_kwargs={"club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        res = self.client.post(
            url,
            {"domain": "latlong.uk"},
            follow=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotContains(res, "invalid-feedback")
        self.club.refresh_from_db()
        self.assertEqual(self.club.domain, "latlong.uk")

        other_club = Club.objects.create(name="My other Club", slug="otherclub")
        other_club.admins.set([self.user])
        url = "/clubs/otherclub/custom-domain"
        res = self.client.post(
            url,
            {"domain": "latlong.uk"},
            follow=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "Domain 'latlong.uk' already used by another club.")
        other_club.refresh_from_db()
        self.assertEqual(other_club.domain, "")
        res = self.client.post(
            url,
            {"domain": "gps.kiilat.com"},
            follow=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(
            res, "DNS record for 'gps.kiilat.com' has not been set properly."
        )

        url = "/clubs/myclub/custom-domain"
        res = self.client.post(
            url,
            {"domain": ""},
            follow=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotContains(res, "invalid-feedback")
        self.club.refresh_from_db()
        self.assertEqual(self.club.domain, "")

    def test_change_club_logo(self):
        url = self.reverse_and_check(
            "dashboard_club:edit_view",
            "/clubs/myclub/",
            extra_kwargs={"club_slug": self.club.slug},
        )

        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        image = Image.new("RGB", (200, 300), (255, 0, 0))
        buffer = BytesIO()
        image.save(buffer, "PNG")
        logo = SimpleUploadedFile(
            "logo.png", buffer.getvalue(), content_type="image/png"
        )
        res = self.client.post(
            url,
            {
                "name": self.club.name,
                "admins": self.user.pk,
                "slug": self.club.slug,
                "logo": logo,
            },
            follow=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotContains(res, "invalid-feedback")

        image = Image.new("RGB", (300, 200), (255, 0, 0))
        buffer = BytesIO()
        image.save(buffer, "PNG")
        logo = SimpleUploadedFile(
            "logo.png", buffer.getvalue(), content_type="image/png"
        )
        res = self.client.post(
            url,
            {
                "name": self.club.name,
                "admins": self.user.pk,
                "slug": self.club.slug,
                "logo": logo,
            },
            follow=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotContains(res, "invalid-feedback")

        image = Image.new("RGB", (100, 100), (255, 0, 0))
        buffer = BytesIO()
        image.save(buffer, "PNG")
        logo = SimpleUploadedFile(
            "logo.png", buffer.getvalue(), content_type="image/png"
        )
        res = self.client.post(
            url,
            {
                "name": self.club.name,
                "admins": self.user.pk,
                "slug": self.club.slug,
                "logo": logo,
            },
            follow=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "The image is too small, minimum 128x128 pixels")

        self.club.refresh_from_db()
        url = self.club.logo.url
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_change_club_banner(self):
        url = self.reverse_and_check(
            "dashboard_club:edit_view",
            "/clubs/myclub/",
            extra_kwargs={"club_slug": self.club.slug},
        )

        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        image = Image.new("RGB", (700, 400), (255, 0, 0))
        buffer = BytesIO()
        image.save(buffer, "JPEG")
        banner = SimpleUploadedFile(
            "banner.jpg", buffer.getvalue(), content_type="image/jpeg"
        )
        res = self.client.post(
            url,
            {
                "name": self.club.name,
                "admins": self.user.pk,
                "slug": self.club.slug,
                "banner": banner,
            },
            follow=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotContains(res, "invalid-feedback")

        image = Image.new("RGB", (800, 400), (255, 0, 0))
        buffer = BytesIO()
        image.save(buffer, "JPEG")
        banner = SimpleUploadedFile(
            "banner.jpg", buffer.getvalue(), content_type="image/jpeg"
        )
        res = self.client.post(
            url,
            {
                "name": self.club.name,
                "admins": self.user.pk,
                "slug": self.club.slug,
                "banner": banner,
            },
            follow=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotContains(res, "invalid-feedback")

        image = Image.new("RGB", (100, 100), (255, 0, 0))
        buffer = BytesIO()
        image.save(buffer, "JPEG")
        banner = SimpleUploadedFile(
            "banner.jpg", buffer.getvalue(), content_type="image/jpeg"
        )
        res = self.client.post(
            url,
            {
                "name": self.club.name,
                "admins": self.user.pk,
                "slug": self.club.slug,
                "banner": banner,
            },
            follow=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "The image is too small, minimum 600x315 pixels")

        self.club.refresh_from_db()
        url = self.club.banner.url
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_device_lists(self):
        device = Device.objects.create()

        url = self.reverse_and_check(
            "dashboard_club:device:add_view",
            "/clubs/myclub/devices/new",
            extra_kwargs={"club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        res = self.client.post(url, {"device": device.aid, "nickname": "MyTrckr"})
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)

        url = self.reverse_and_check(
            "dashboard_club:device:list_view",
            "/clubs/myclub/devices/",
            extra_kwargs={"club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, device.aid)
        self.assertContains(res, "MyTrckr")

        url = self.reverse_and_check(
            "dashboard_club:device:list_download",
            "/clubs/myclub/devices/csv",
            extra_kwargs={"club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, f"MyTrckr;{device.aid};\r\n")

        ImeiDevice.objects.create(imei="012345678901237", device=device)

        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, f"MyTrckr;{device.aid};012345678901237\r\n")

    def test_delete_club(self):
        url = self.reverse_and_check(
            "dashboard_club:delete_view",
            "/clubs/myclub/delete",
            extra_kwargs={"club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        res = self.client.post(url, {"password": "not the password"})
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertTrue(Club.objects.filter(id=self.club.id).exists())
        res = self.client.post(url, {"password": "pa$$word123"})
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertFalse(Club.objects.filter(id=self.club.id).exists())

    def test_edit_map(self):
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

        url = self.reverse_and_check(
            "dashboard_club:map:edit_view",
            f"/clubs/myclub/maps/{raster_map.aid}/",
            extra_kwargs={"map_id": raster_map.aid, "club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        image = Image.new("RGB", (100, 100), (255, 0, 0))
        buffer = BytesIO()
        image.save(buffer, "JPEG")
        map_image = SimpleUploadedFile(
            "map.jpg", buffer.getvalue(), content_type="image/jpeg"
        )

        res = self.client.post(
            url,
            {
                "name": "My Test Map",
                "image": map_image,
                "calibration_string": "61.45075,24.18994,61.44656,24.24721,61.42094,24.23851,61.42533,24.18157",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)

        res = self.client.post(
            url,
            {
                "name": "My Test Map",
                "image": map_image,
                "calibration_string": "61.45075,24.18994,61.44656,24.24721,61.42094,24.23851,61.42533,24.18157,123",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "Invalid format")
        res = self.client.post(
            url,
            {
                "name": "My Test Map",
                "image": map_image,
                "calibration_string": "61.45075,24.18994,61.44656,24.24721,61.42094,24.23851,61.42533,xx.18157",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "Invalid format")

        raster_map.refresh_from_db()
        url = raster_map.image.url
        res = self.client.get(url)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        url = self.reverse_and_check(
            "dashboard_club:map:delete_view",
            f"/clubs/myclub/maps/{raster_map.aid}/delete",
            extra_kwargs={"map_id": raster_map.aid, "club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertFalse(Map.objects.filter(id=raster_map.id).exists())

    def test_edit_events(self):
        # Create event
        url = self.reverse_and_check(
            "dashboard_club:event:create_view",
            "/clubs/myclub/events/new",
            extra_kwargs={"club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        with open("cypress/fixtures/geojson/valid.geojson", "rb") as fp:
            geojson = SimpleUploadedFile(
                "route.geojson", fp.read(), content_type="application/json"
            )

        res = self.client.post(
            url,
            {
                "name": "My Event",
                "slug": "myevent",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "timezone": "UTC",
                "geojson_layer": geojson,
            },
        )
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertTrue(Event.objects.filter(slug="myevent").exists())
        event = Event.objects.get(slug="myevent")

        # List event
        url = self.reverse_and_check(
            "dashboard_club:event:list_view",
            "/clubs/myclub/events/",
            extra_kwargs={"club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "My Event")

        # Edit event
        url = self.reverse_and_check(
            "dashboard_club:event:edit_view",
            f"/clubs/myclub/events/{event.aid}/",
            extra_kwargs={"event_id": event.aid, "club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        res = self.client.post(
            url,
            {
                "name": "My Competition",
                "slug": "myevent",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "geo_json_layer": "",
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        res = self.client.get("/clubs/myclub/events/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotContains(res, "My Event")
        self.assertContains(res, "My Competition")

        # test validations errors
        # event outside free trial period
        self.club.creation_date = arrow.now().shift(days=-5).datetime
        self.club.save()
        res = self.client.post(
            url,
            {
                "name": "My Competition",
                "slug": "myevent",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2125-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(
            res,
            "You can not create events that extend beyond the expiration date of your free trial.",
        )

        # free trial expired
        self.club.creation_date = arrow.now().shift(years=-1).datetime
        self.club.save()
        res = self.client.post(
            url,
            {
                "name": "My Competition",
                "slug": "myevent",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(
            res,
            "Your 10 days free trial has now expired, you cannot create or edit events anymore.",
        )

        # subscription paused
        self.club.upgraded = True
        self.club.subscription_paused_at = arrow.now().shift(hours=-1).datetime
        self.club.save()

        res = self.client.post(
            url,
            {
                "name": "My Competition",
                "slug": "myevent",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "competitors-0-short_name": "A",
                "competitors-0-name": "Alice",
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(
            res,
            "Your subscription is currently paused, you cannot create or edit events.",
        )

        self.club.subscription_paused_at = None
        self.club.save()

        # messed up dates
        res = self.client.post(
            url,
            {
                "name": "My Competition",
                "slug": "myevent",
                "start_date": "2025-02-05T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "End Date must be after than the Start Date.")

        # name used other event in same event set
        es = EventSet.objects.create(
            name="Blob",
            club=self.club,
            create_page=True,
            slug="eventset-slug",
        )
        Event.objects.create(
            club=self.club,
            slug="myeventslug",
            name="My event in a set",
            start_date=arrow.get("2023-08-01T00:00:00Z").datetime,
            end_date=arrow.get("2023-08-01T23:59:59Z").datetime,
            event_set=es,
        )
        res = self.client.post(
            url,
            {
                "name": "My event in a set",
                "slug": "myevent",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "event_set": es.id,
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(
            res, "Name already used by another event in this event set."
        )

        # slug already exists in an event
        res = self.client.post(
            url,
            {
                "name": "My event in a set",
                "slug": "myeventslug",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "URL already used by another event.")

        # slug already exists in an event set
        res = self.client.post(
            url,
            {
                "name": "My event in a set",
                "slug": "eventset-slug",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "URL already used by an event set.")

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

        raster_map2 = Map.objects.create(
            club=self.club,
            name="Test map",
            calibration_string=(
                "61.45075,24.18994,61.44656,24.24721,"
                "61.42094,24.23851,61.42533,24.18156"
            ),
            width=1,
            height=1,
        )
        raster_map2.data_uri = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAAXNSR0IArs4c6Q"
            "AAAA1JREFUGFdjED765z8ABZcC1M3x7TQAAAAASUVORK5CYII="
        )
        raster_map2.save()

        # map appears twice
        res = self.client.post(
            url,
            {
                "name": "My event in a set",
                "slug": "myevent",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map": raster_map.id,
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "map_assignations-0-map": raster_map.id,
                "map_assignations-0-title": "Alt map",
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "Map assigned more than once in this event")

        # extra map appears twice
        res = self.client.post(
            url,
            {
                "name": "My event in a set",
                "slug": "myevent",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map": raster_map2.id,
                "map_assignations-TOTAL_FORMS": 2,
                "map_assignations-INITIAL_FORMS": 0,
                "map_assignations-0-map": raster_map.id,
                "map_assignations-0-title": "Alt map",
                "map_assignations-1-map": raster_map.id,
                "map_assignations-1-title": "Alt map 2",
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "Map assigned more than once in this event")

        # map title appears twice
        res = self.client.post(
            url,
            {
                "name": "My event in a set",
                "slug": "myevent",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map": raster_map.id,
                "map_title": "Main map",
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "map_assignations-0-map": raster_map.id,
                "map_assignations-0-title": "Main map",
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "Map title given more than once in this event")

        # extra map title appears twice
        res = self.client.post(
            url,
            {
                "name": "My event in a set",
                "slug": "myevent",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map": raster_map2.id,
                "map_title": "Main map",
                "map_assignations-TOTAL_FORMS": 2,
                "map_assignations-INITIAL_FORMS": 0,
                "map_assignations-0-map": raster_map.id,
                "map_assignations-0-title": "Extra map",
                "map_assignations-1-map": raster_map.id,
                "map_assignations-1-title": "Extra map",
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "Map title given more than once in this event")

        # extra map without main map
        res = self.client.post(
            url,
            {
                "name": "My event in a set",
                "slug": "myevent",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "map_assignations-0-map": raster_map.id,
                "map_assignations-0-title": "Main map",
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(
            res, "Extra maps can be set only if the main map field is set first"
        )

        # competitor start time bad
        res = self.client.post(
            url,
            {
                "name": "My Event",
                "slug": "myevent",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "competitors-0-start_time": "2024-01-20 00:00:00",
                "competitors-0-name": "Alice",
                "competitors-0-short_name": "A",
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(
            res, "Competitor start time should be during the event time"
        )

        with open("cypress/fixtures/geojson/invalid.geojson", "rb") as fp:
            geojson = SimpleUploadedFile(
                "route.geojson", fp.read(), content_type="application/json"
            )
        res = self.client.post(
            url,
            {
                "name": "My Event",
                "slug": "myevent",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "timezone": "UTC",
                "geojson_layer": geojson,
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "Invalid GeoJSON File")

        with open("cypress/fixtures/geojson/invalid.not-a-geojson", "rb") as fp:
            geojson = SimpleUploadedFile(
                "route.geojson", fp.read(), content_type="application/json"
            )
        res = self.client.post(
            url,
            {
                "name": "My Event",
                "slug": "myevent",
                "start_date": "2025-02-03T00:00:00",
                "end_date": "2025-02-04T00:00:00",
                "privacy": "public",
                "tail_length": 60,
                "send_interval": 5,
                "backdrop_map": "blank",
                "map_assignations-TOTAL_FORMS": 1,
                "map_assignations-INITIAL_FORMS": 0,
                "competitors-TOTAL_FORMS": 1,
                "competitors-INITIAL_FORMS": 0,
                "timezone": "UTC",
                "geojson_layer": geojson,
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "Invalid JSON File")

    def test_edit_event_sets(self):
        # Create event set
        url = self.reverse_and_check(
            "dashboard_club:event_set:create_view",
            "/clubs/myclub/event-sets/new",
            extra_kwargs={"club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        res = self.client.post(url, {"name": "Tough Competition"})
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)

        # List event set
        url = self.reverse_and_check(
            "dashboard_club:event_set:list_view",
            "/clubs/myclub/event-sets/",
            extra_kwargs={"club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "Tough Competition")

        # Edit event set
        es = EventSet.objects.first()
        url = self.reverse_and_check(
            "dashboard_club:event_set:edit_view",
            f"/clubs/myclub/event-sets/{es.aid}/",
            extra_kwargs={"event_set_id": es.aid, "club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        res = self.client.post(url, {"name": "Easy Competition"})
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        res = self.client.get("/clubs/myclub/event-sets/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotContains(res, "Tough Competition")
        self.assertContains(res, "Easy Competition")

        res = self.client.post(
            url,
            {"name": "Easy Competition", "create_page": "on", "slug": "myeventsetslug"},
        )
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        es.refresh_from_db()
        self.assertEqual(es.slug, "myeventsetslug")
        self.assertTrue(es.create_page)

        # test validation errors
        res = self.client.post(
            url, {"name": "Easy Competition", "create_page": "on", "slug": ""}
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "URL must be set when creating a page.")

        url = "/clubs/myclub/event-sets/new"
        res = self.client.post(
            url, {"name": "Easy Competition", "create_page": "on", "slug": "someslug"}
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "Name already used by another event set of this club.")

        res = self.client.post(
            url,
            {"name": "Easy Competition", "create_page": "on", "slug": "myeventsetslug"},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "URL already used by another event set.")

        Event.objects.create(
            club=self.club,
            slug="myeventslug",
            name="My Event",
            start_date=arrow.get("2023-08-01T00:00:00Z").datetime,
            end_date=arrow.get("2023-08-01T23:59:59Z").datetime,
        )
        res = self.client.post(
            url,
            {"name": "Easy Competition", "create_page": "on", "slug": "myeventslug"},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "URL already used by an event.")

        # Delete event set
        url = self.reverse_and_check(
            "dashboard_club:event_set:delete_view",
            f"/clubs/myclub/event-sets/{es.aid}/delete",
            extra_kwargs={"event_set_id": es.aid, "club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        res = self.client.get("/clubs/myclub/event-sets/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotContains(res, "Tough Competition")
        self.assertNotContains(res, "Easy Competition")
        self.assertFalse(EventSet.objects.filter(id=es.id).exists())

    def test_delete_event(self):
        event = Event.objects.create(
            club=self.club,
            slug="abc",
            name="WOC Long Distance",
            start_date=arrow.get("2023-08-01T00:00:00Z").datetime,
            end_date=arrow.get("2023-08-01T23:59:59Z").datetime,
        )
        url = self.reverse_and_check(
            "dashboard_club:event:delete_view",
            f"/clubs/myclub/events/{event.aid}/delete",
            extra_kwargs={"event_id": event.aid, "club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertFalse(Event.objects.filter(id=event.id).exists())

    def test_competitors_page(self):
        event = Event.objects.create(
            club=self.club,
            slug="abc",
            name="WOC Long Distance",
            start_date=arrow.get("2023-08-01T00:00:00Z").datetime,
            end_date=arrow.get("2023-08-01T23:59:59Z").datetime,
        )
        comps = []
        for i in range(120):
            c = Competitor(
                event=event,
                name="c {i}",
                short_name=f"c{i}",
            )
            comps.append(c)
        Competitor.objects.bulk_create(comps)
        url = self.reverse_and_check(
            "dashboard_club:event:competitors_view",
            f"/clubs/myclub/events/{event.aid}/competitors",
            extra_kwargs={"event_id": event.aid, "club_slug": self.club.slug},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)


class TestInviteFlow(APITestCase):
    def setUp(self):
        self.client = APIClient(HTTP_HOST="dashboard.routechoices.dev")
        self.club = Club.objects.create(name="My Club", slug="myclub")
        self.user = User.objects.create_user(
            "alice", f"alice{random.randrange(1000)}@example.com", "pa$$word123"
        )
        self.user2 = User.objects.create_user(
            "bob", f"bob{random.randrange(1000)}@example.com", "pa$$word123"
        )
        EmailAddress.objects.create(
            user=self.user, email=self.user.email, primary=True, verified=True
        )
        EmailAddress.objects.create(
            user=self.user2, email=self.user2.email, primary=True, verified=True
        )

        self.club.admins.set([self.user])

    def test_request_invite(self):
        self.client.force_login(self.user2)
        self.client.get("/request-invite")
        res = self.client.post("/request-invite", {"club": self.club.id})
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(
            f"Request for an invitation to manage club {self.club}"
            in mail.outbox[0].subject
        )
        self.assertTrue(
            mail.outbox[0].body.startswith(
                f"Hello,\n\nA user ({self.user2.email}) has requested an invite to manage the club"
            )
        )

        self.client.force_login(self.user)
        self.client.get("/request-invite")
        res = self.client.post("/request-invite", {"club": self.club.id})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, "invalid-feedback")
        self.assertContains(res, "You are already an admin of this club.")

    def test_send_invite_new_user(self):
        new_user_email = "new@example.com"
        new_user_password = "myPa$$word123"
        # Send invite
        self.client.force_login(self.user)
        self.client.get("/clubs/myclub/send-invite")
        res = self.client.post("/clubs/myclub/send-invite", {"email": new_user_email})
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(
            f"Invitation to manage club {self.club} on " in mail.outbox[0].subject
        )
        # Accept invite
        accept_link = re.findall(r"http\:\/\/[^ ]+", mail.outbox[0].body)[0]

        self.client.logout()
        res = self.client.get(accept_link)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(
            f"You have been invited to manage club {self.club}, please confirm to continue"
            in res.content.decode("utf-8")
        )
        res = self.client.post(accept_link)
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertRedirects(res, "/signup", target_status_code=status.HTTP_200_OK)
        self.assertFalse(self.club.admins.filter(email=new_user_email).exists())

        res = self.client.post(
            "/signup",
            data={
                "username": "newuser",
                "email": new_user_email,
                "password1": new_user_password,
                "password2": new_user_password,
            },
        )
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertRedirects(
            res,
            "//dashboard.routechoices.dev",
            target_status_code=status.HTTP_302_FOUND,
        )

        self.assertTrue(self.club.admins.filter(email=new_user_email).exists())

    def test_send_invite_existing_user(self):
        # Send invite
        self.client.force_login(self.user)
        self.client.get("/clubs/myclub/send-invite")
        res = self.client.post("/clubs/myclub/send-invite", {"email": self.user2.email})
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(
            f"Invitation to manage club {self.club} on " in mail.outbox[0].subject
        )

        # Accept invite
        accept_link = re.findall(r"http\:\/\/[^ ]+", mail.outbox[0].body)[0]

        # Logged out user tries to accept
        self.client.logout()
        res = self.client.get(accept_link)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(
            f"You have been invited to manage club {self.club}, please confirm to continue"
            in res.content.decode("utf-8")
        )
        res = self.client.post(accept_link)
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertRedirects(res, "https://dashboard.routechoices.dev/login")
        self.assertFalse(self.club.admins.filter(id=self.user2.id).exists())

        # Wrong user try to accept
        self.client.force_login(self.user)
        res = self.client.get(accept_link)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(
            "Invite is targeted to a different email address than yours"
            in res.content.decode("utf-8")
        )
        res = self.client.post(accept_link)
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertRedirects(res, "/logout")

        # Logged in target user tries to accept
        self.client.force_login(self.user2)
        res = self.client.get(accept_link)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        res = self.client.post(accept_link)
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertRedirects(res, "/clubs/")
        self.assertTrue(self.club.admins.filter(id=self.user2.id).exists())

        # Logged in target user retries to accept later
        res = self.client.get(accept_link)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(
            f"You were invited to manage club {self.club}, however you already accepted the invitation."
            in res.content.decode("utf-8")
        )
        res = self.client.post(accept_link)
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertRedirects(
            res,
            "https://dashboard.routechoices.dev/login",
            target_status_code=status.HTTP_302_FOUND,
        )
