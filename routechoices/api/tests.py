import json
import random
import socket
import threading
import time
from datetime import UTC, datetime

import arrow
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django_hosts.resolvers import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from routechoices.core.models import (
    PRIVACY_PRIVATE,
    Club,
    Competitor,
    Device,
    Event,
    Map,
)
from routechoices.lib.helpers import epoch_to_datetime


class EssentialApiBase(APITestCase):
    def setUp(self):
        self.client = APIClient(HTTP_HOST="api.routechoices.com")
        self.user = User.objects.create_user(
            "alice", f"alice{random.randrange(1000)}@example.com", "pa$$word123"
        )

    def reverse_and_check(
        self,
        path,
        expected,
        host="api",
        extra_kwargs=None,
        host_kwargs=None,
        prefix=None,
    ):
        url = reverse(path, host=host, kwargs=extra_kwargs, host_kwargs=host_kwargs)
        self.assertEqual(url, f"//{prefix or host}.routechoices.com{expected}")
        return url

    def get_device_id(self):
        d = Device.objects.create()
        return d.aid


class EssentialApiTestCase1(EssentialApiBase):
    def test_api_root(self):
        self.assertFalse(settings.DEBUG)
        url = self.reverse_and_check("api_doc", "/")
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_get_time(self):
        url = self.reverse_and_check("time_api", "/time")
        t1 = time.time()
        res = self.client.get(url)
        t2 = time.time()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(t1 < res.data.get("time") < t2)

    def test_id_is_registered(self):
        did = self.get_device_id()
        url = self.reverse_and_check(
            "device_info_api", f"/device/{did}/", extra_kwargs={"device_id": did}
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        url = self.reverse_and_check(
            "device_registrations_api",
            f"/device/{did}/registrations",
            extra_kwargs={"device_id": did},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_create_device_id(self):
        url = self.reverse_and_check("device_api", "/device/")
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_login(self.user)
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        validated_apps_user = User.objects.create_user(
            "system",
            f"apps{random.randrange(1000)}@example.com",
            "pa$$word123",
            is_superuser=True,
        )
        self.client.force_login(validated_apps_user)
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        dev_id = res.data.get("device_id")
        self.assertTrue(dev_id.isdigit())
        self.assertEqual(len(dev_id), 8)
        self.assertTrue(dev_id != self.get_device_id())

    @override_settings(SHORTCUT_URL="https://rtchcs.dev/")
    def test_list_events(self):
        club = Club.objects.create(name="Test club", slug="club")
        alt_club = Club.objects.create(
            name="Alternative club", slug="alt-club", domain="example.com"
        )
        e1 = Event.objects.create(
            club=club,
            name="Test event A",
            slug="abc",
            open_registration=True,
            start_date=arrow.get().shift(hours=-2).datetime,
            end_date=arrow.get().shift(hours=-1).datetime,
        )
        Event.objects.create(
            club=club,
            name="Test event B",
            slug="def",
            open_registration=True,
            start_date=arrow.get().shift(hours=-2).datetime,
            end_date=arrow.get().shift(hours=-1).datetime,
        )
        e3 = Event.objects.create(
            club=alt_club,
            name="Test event C",
            slug="ghi",
            open_registration=True,
            start_date=arrow.get().shift(hours=-2).datetime,
            end_date=arrow.get().shift(hours=-1).datetime,
        )

        e1b = Event.get_by_url("https://club.routechoices.dev/abc")
        self.assertEqual(e1, e1b)
        e3b = Event.get_by_url("https://example.com/ghi")
        self.assertEqual(e3, e3b)
        e3c = Event.get_by_url("https://alt-club.routechoices.dev/ghi")
        self.assertEqual(e3, e3c)
        e3d = Event.get_by_url("https://rtchcs.dev/alt-club/ghi")
        self.assertEqual(e3, e3d)
        e = Event.get_by_url("https://www.rtchcs.dev/alt-club/ghi")
        self.assertIsNone(e)
        e = Event.get_by_url("https://alt-club-routechoices.dev/ghi")
        self.assertIsNone(e)
        e = Event.get_by_url("https://alt-club.routechoices.dev/abc")
        self.assertIsNone(e)
        e = Event.get_by_url("https://google.com/abc")
        self.assertIsNone(e)

        url = self.reverse_and_check("event_list", "/events/")
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        res_json = json.loads(res.content)
        self.assertEqual(len(res_json), 3)
        res = self.client.get(f"{url}?club=club")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        res_json = json.loads(res.content)
        self.assertEqual(len(res_json), 2)
        res = self.client.get(f"{url}?event=abc")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        res_json = json.loads(res.content)
        self.assertEqual(len(res_json), 1)
        res = self.client.get(f"{url}?event=https://club.routechoices.dev/def")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        res_json = json.loads(res.content)
        self.assertEqual(len(res_json), 1)
        res = self.client.get(f"{url}?event=https://example.com/ghi")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        res_json = json.loads(res.content)
        self.assertEqual(len(res_json), 1)

    def test_add_locations(self):
        device = Device()
        self.assertEqual(device._location_count, 0)
        device.add_locations([(0, 0.000014, 0.1)], save=False)
        self.assertEqual(device.locations_encoded, "~n|sfjAA_pR")
        self.assertEqual(device._location_count, 1)
        device.add_locations([(0, 0.1, 0.1)], save=False)
        self.assertEqual(device.locations_encoded, "~n|sfjAA_pR")
        self.assertEqual(device._location_count, 1)
        device.add_locations([(-1, 0.1, 0.1)], save=False)
        self.assertEqual(device.locations_encoded, "`o|sfjA_pR_pR@|oR?")
        self.assertEqual(device._location_count, 2)
        device.add_locations([(2, 0.000016, 0.1), (3, 0.000024, 0.1)], save=False)
        self.assertEqual(device.locations_encoded, "`o|sfjA_pR_pR@|oR?AA?@??")
        self.assertEqual(device._location_count, 4)
        device.add_locations([(1, 0.000009, 0.1), (3, 0.1, 0.1)], save=False)
        self.assertEqual(device.locations_encoded, "`o|sfjA_pR_pR@|oR?@??@A?@??")
        self.assertEqual(device._location_count, 5)
        device.save()
        self.assertEqual(device.location_count, 5)
        self.assertEqual(device.last_location, (3, 0.00002, 0.1))
        self.assertEqual(device.last_location, device.locations[-1])
        device.add_locations([(4, 0.1, 0.000005)], save=False)
        device.add_locations([(5, 0.1, -0.000005)], save=False)
        device.add_locations([(6, 0.1, -0.00001)])
        device.add_locations([(7, -0.000015, 0.00001)], save=False)
        device.add_locations([(7, 0.00002, 0.00002)], save=False)
        self.assertEqual(device.last_location, device.locations[-1])
        self.assertEqual(device.last_location, (7, -0.00002, 0.00001))
        last_loc = device.last_location
        device.update_cached_data()
        self.assertEqual(last_loc, device.last_location)

    def test_clean_device(self):
        club = Club.objects.create(name="Test club", slug="club")
        device = Device()
        device.add_locations([(0, 0.1, 0.1), (5, 0.1, 0.1), (10, 0.1, 0.1)])

        device.remove_unused_location(until=epoch_to_datetime(13))
        self.assertEqual(device.location_count, 0)

        device.refresh_from_db()
        device.remove_unused_location(until=epoch_to_datetime(10))
        self.assertEqual(device.location_count, 1)

        device.refresh_from_db()
        device.remove_unused_location(until=epoch_to_datetime(6))
        self.assertEqual(device.location_count, 1)

        device.refresh_from_db()
        device.remove_unused_location(until=epoch_to_datetime(0))
        self.assertEqual(device.location_count, 3)

        event1 = Event.objects.create(
            club=club,
            name="test1",
            slug="abc",
            start_date=datetime.utcfromtimestamp(-1).replace(tzinfo=UTC),
            end_date=epoch_to_datetime(1),
        )
        Competitor.objects.create(
            name="A",
            short_name="A",
            device=device,
            event=event1,
        )

        device.refresh_from_db()
        device.remove_unused_location(until=epoch_to_datetime(10))
        self.assertEqual(device.location_count, 2)


class ImeiApiTestCase(EssentialApiBase):
    def setUp(self):
        super().setUp()
        self.url = self.reverse_and_check("device_api", "/device/")

    def test_get_imei_invalid(self):
        res = self.client.post(self.url, {"imei": "abcd"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_get_imei_valid(self):
        res = self.client.post(self.url, {"imei": "123456789123458"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        dev_id = res.data.get("device_id")
        self.assertTrue(dev_id.isdigit())
        self.assertEqual(len(dev_id), 8)
        self.assertNotEqual(dev_id, self.get_device_id())

        # request with same imei
        res = self.client.post(self.url, {"imei": "123456789123458"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(dev_id, res.data.get("device_id"))

        # test device with alpha character get new id with only digits
        device = Device.objects.get(aid=dev_id)
        device.aid = "1234abcd"
        device.save()
        res = self.client.post(self.url, {"imei": "123456789123458"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        new_dev_id = res.data.get("device_id")
        self.assertNotEqual(new_dev_id, "1234abcd")
        self.assertTrue(dev_id.isdigit())
        self.assertEqual(len(new_dev_id), 8)
        self.assertNotEqual(new_dev_id, self.get_device_id())

        # test device with alpha character dont get new id if assigned
        # a competitor in future
        club = Club.objects.create(name="Test club", slug="club")
        event = Event.objects.create(
            club=club,
            name="Test event",
            open_registration=True,
            start_date=arrow.get().shift(minutes=-1).datetime,
            end_date=arrow.get().shift(hours=1).datetime,
        )
        Competitor.objects.create(
            name="Alice", short_name="A", event=event, device=device
        )
        device.aid = "1234abcd"
        device.save()
        res = self.client.post(self.url, {"imei": "123456789123458"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        new_dev_id = res.data.get("device_id")
        self.assertEqual(new_dev_id, "1234abcd")

        # test device with alpha character get new id if assigned a competitor in past
        event.end_date = arrow.get().shift(seconds=-1).datetime
        event.save()
        res = self.client.post(self.url, {"imei": "123456789123458"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        new_dev_id = res.data.get("device_id")
        self.assertNotEqual(new_dev_id, "1234abcd")
        self.assertTrue(dev_id.isdigit())
        self.assertEqual(len(new_dev_id), 8)


class EventSetCreationApiTestCase(EssentialApiBase):
    def setUp(self):
        super().setUp()
        self.url = self.reverse_and_check("event_set", "/event-set")
        self.club = Club.objects.create(name="Test club", slug="club")
        self.club.admins.set([self.user])
        self.client.force_login(self.user)

    def test_ok(self):
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "name": "My event set",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_not_ok(self):
        res = self.client.post(
            self.url,
            {
                "club_slug": "not-a-club",
                "name": "My event set",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        res = self.client.post(
            self.url,
            {
                "name": "My event set",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class EventCreationApiTestCase(EssentialApiBase):
    def setUp(self):
        super().setUp()
        self.url = self.reverse_and_check("event_list", "/events/")
        self.club = Club.objects.create(name="Test club", slug="club")
        self.club.admins.set([self.user])
        self.client.force_login(self.user)

    def test_ok(self):
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "end_date": arrow.now().shift(minutes=60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_duplicate_slug(self):
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "name": "My event",
                "slug": "hello-event",
                "end_date": arrow.now().shift(minutes=60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "slug": "hello-event",
                "end_date": arrow.now().shift(minutes=60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_case(self):
        # invalid date
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "end_date": arrow.now().shift(minutes=-60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # invalid club
        res = self.client.post(
            self.url,
            {
                "end_date": arrow.now().shift(minutes=-60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        res = self.client.post(
            self.url,
            {
                "club_slug": "not-a-club",
                "end_date": arrow.now().shift(minutes=60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # invalid slug
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "slug": "-- not a nice slug :)",
                "end_date": arrow.now().shift(minutes=60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # invalid start date
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "start_date": "-- not a start date",
                "end_date": arrow.now().shift(minutes=60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "start_date": arrow.now().shift(minutes=70),
                "end_date": arrow.now().shift(minutes=60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "start_date": arrow.now().shift(minutes=50),
                "end_date": arrow.now().shift(minutes=60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # invalid end date
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "end_date": "-- not a end date",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        # invalid background map
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "backdrop": "openstreetmap",
                "end_date": arrow.now().shift(minutes=60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "backdrop": "osm",
                "end_date": arrow.now().shift(minutes=60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # invalid privacy
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "privacy": "secret",
                "end_date": arrow.now().shift(minutes=60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "privacy": "potato",
                "end_date": arrow.now().shift(minutes=60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # open registration
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "open_registration": True,
                "end_date": arrow.now().shift(minutes=60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # open upload
        res = self.client.post(
            self.url,
            {
                "club_slug": "club",
                "allow_route_upload": True,
                "end_date": arrow.now().shift(minutes=60),
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)


class EventApiTestCase(EssentialApiBase):
    def test_live_event_detail(self):
        club = Club.objects.create(name="Test club", slug="club")
        event = Event.objects.create(
            club=club,
            name="Test event",
            open_registration=True,
            start_date=arrow.get().shift(minutes=-1).datetime,
            end_date=arrow.get().shift(hours=1).datetime,
        )
        url = self.reverse_and_check(
            "event_detail", f"/events/{event.aid}/", "api", {"event_id": event.aid}
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["event"]["name"], "Test event")

    def test_cache_invalidation(self):
        cache.clear()
        club = Club.objects.create(name="Test club", slug="club")
        event_a = Event.objects.create(
            club=club,
            name="Test event A",
            open_registration=True,
            start_date=arrow.get().shift(hours=-2).datetime,
            end_date=arrow.get().shift(hours=-1).datetime,
        )
        event_b = Event.objects.create(
            club=club,
            name="Test event B",
            open_registration=True,
            start_date=arrow.get().shift(hours=-2).datetime,
            end_date=arrow.get().shift(hours=-1).datetime,
        )
        event_c = Event.objects.create(
            club=club,
            name="Test event C",
            open_registration=True,
            start_date=arrow.get().shift(hours=-2).datetime,
            end_date=arrow.get().shift(hours=-1).datetime,
        )
        event_d = Event.objects.create(
            club=club,
            name="Test event D",
            open_registration=True,
            start_date=arrow.get().shift(minutes=-50).datetime,
            end_date=arrow.get().shift(minutes=-20).datetime,
        )
        url_data_a = self.reverse_and_check(
            "event_data",
            f"/events/{event_a.aid}/data",
            "api",
            {"event_id": event_a.aid},
        )
        url_data_b = self.reverse_and_check(
            "event_data",
            f"/events/{event_b.aid}/data",
            "api",
            {"event_id": event_b.aid},
        )
        url_data_c = self.reverse_and_check(
            "event_data",
            f"/events/{event_c.aid}/data",
            "api",
            {"event_id": event_c.aid},
        )
        url_data_d = self.reverse_and_check(
            "event_data",
            f"/events/{event_d.aid}/data",
            "api",
            {"event_id": event_d.aid},
        )
        device = Device.objects.create()
        device_b = Device.objects.create()
        competitor_a = Competitor.objects.create(
            name="Alice A",
            short_name="A",
            event=event_a,
            device=device,
            start_time=arrow.get().shift(minutes=-70).datetime,
        )
        Competitor.objects.create(
            name="Alice B",
            short_name="A",
            event=event_b,
            device=device,
            start_time=arrow.get().shift(minutes=-75).datetime,
        )
        Competitor.objects.create(
            name="Alice C",
            short_name="A",
            event=event_c,
            device=device_b,
            start_time=arrow.get().shift(minutes=-73).datetime,
        )
        Competitor.objects.create(
            name="Alice D",
            short_name="A",
            event=event_d,
            device=device_b,
            start_time=arrow.get().shift(minutes=-23).datetime,
        )
        # fetch so cache exist
        self.client.get(url_data_a)
        self.client.get(url_data_b)
        self.client.get(url_data_c)
        self.client.get(url_data_d)
        # Assert cache exists
        res = self.client.get(url_data_a)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.headers.get("X-Cache-Hit"), "1")
        # Upload data in event B timespan should invalidate cache for only event B
        device.add_location(arrow.get().shift(minutes=-72).timestamp(), 0.2, 0.1)
        res = self.client.get(url_data_a)
        self.assertEqual(res.headers.get("X-Cache-Hit"), "1")
        res = self.client.get(url_data_b)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        # Updating competitor A name should invalidate event A
        competitor_a.name = "Bob"
        competitor_a.save()
        res = self.client.get(url_data_a)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        # Updating competitor A start time forward should invalidate event A and B
        competitor_a.start_time = arrow.get().shift(minutes=-71).datetime
        competitor_a.save()
        res = self.client.get(url_data_a)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        res = self.client.get(url_data_b)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        # Updating competitor A start time backward should only invalidate event A
        competitor_a.start_time = arrow.get().shift(minutes=-76).datetime
        competitor_a.save()
        res = self.client.get(url_data_a)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        res = self.client.get(url_data_b)
        self.assertEqual(res.headers.get("X-Cache-Hit"), "1")
        # Updating competitor A device should invalidate event A but not event B, C or D
        competitor_a.device = device_b
        competitor_a.start_time = arrow.get().shift(minutes=-70).datetime
        competitor_a.save()
        res = self.client.get(url_data_a)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        res = self.client.get(url_data_b)
        self.assertEqual(res.headers.get("X-Cache-Hit"), "1")
        res = self.client.get(url_data_c)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        res = self.client.get(url_data_d)
        self.assertEqual(res.headers.get("X-Cache-Hit"), "1")
        # Reset data
        competitor_a.device = device
        competitor_a.start_time = arrow.get().shift(minutes=-70).datetime
        competitor_a.save()
        self.client.get(url_data_a)
        self.client.get(url_data_b)
        self.client.get(url_data_c)
        self.client.get(url_data_d)
        # Changing competitor A device should invalidate event A, B and C
        competitor_a.device = device_b
        competitor_a.save()
        res = self.client.get(url_data_a)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        res = self.client.get(url_data_b)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        res = self.client.get(url_data_c)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        res = self.client.get(url_data_d)
        self.assertEqual(res.headers.get("X-Cache-Hit"), "1")

        # Creating a competitor on event B should update event A, B and C not D
        bob = Competitor.objects.create(
            name="Alice E",
            short_name="A",
            event=event_b,
            device=device_b,
            start_time=arrow.get().shift(minutes=-63).datetime,
        )
        res = self.client.get(url_data_a)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        res = self.client.get(url_data_b)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        res = self.client.get(url_data_c)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        res = self.client.get(url_data_d)
        self.assertEqual(res.headers.get("X-Cache-Hit"), "1")
        # Deleting a competitor on event B should update event A, B and C not D
        bob.delete()
        res = self.client.get(url_data_a)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        res = self.client.get(url_data_b)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        res = self.client.get(url_data_c)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        res = self.client.get(url_data_d)
        self.assertEqual(res.headers.get("X-Cache-Hit"), "1")

    def test_events_endpoints(self):
        club = Club.objects.create(name="Test club", slug="club")
        event = Event.objects.create(
            club=club,
            name="Test event A",
            start_date=arrow.get().shift(hours=-2).datetime,
            end_date=arrow.get().shift(hours=-1).datetime,
        )
        device = Device.objects.create()
        device.add_location(arrow.get().shift(minutes=-72).timestamp(), 0.2, 0.1)
        competitor = Competitor.objects.create(
            name="Alice",
            short_name="A",
            event=event,
            device=device,
            start_time=arrow.get().shift(minutes=-75).datetime,
        )
        raster_map = Map.objects.create(
            club=club,
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
        event.map = raster_map
        event.save()
        url = self.reverse_and_check(
            "2d_rerun_race_status", "/woo/race_status/get_info.json", "api"
        )
        res = self.client.get(f"{url}?eventid={event.aid}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        url = self.reverse_and_check(
            "2d_rerun_race_data", "/woo/race_status/get_data.json", "api"
        )
        res = self.client.get(f"{url}?eventid={event.aid}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        url = self.reverse_and_check(
            "competitor_gpx_download",
            f"/competitors/{competitor.aid}/gpx",
            "api",
            {"competitor_id": competitor.aid},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertContains(res, 'lat="0.2" lon="0.1"')

        url = self.reverse_and_check(
            "event_zip", f"/events/{event.aid}/zip", "api", {"event_id": event.aid}
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_live_event_data(self):
        cache.clear()
        club = Club.objects.create(name="Test club", slug="club")
        event = Event.objects.create(
            club=club,
            name="Test event",
            open_registration=True,
            start_date=arrow.get().shift(minutes=-1).datetime,
            end_date=arrow.get().shift(hours=1).datetime,
        )
        url = self.reverse_and_check(
            "event_data", f"/events/{event.aid}/data", "api", {"event_id": event.aid}
        )

        device = Device.objects.create()

        Competitor.objects.create(
            name="Alice A",
            short_name="A",
            event=event,
            device=device,
            start_time=arrow.get().shift(seconds=-2).datetime,
        )

        device.add_location(
            arrow.get().shift(seconds=-1).timestamp(), 12.34567, 123.45678
        )

        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotEqual(res.data["competitors"][0]["locations_encoded"], "")
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        res = self.client.get(url)
        self.assertEqual(res.headers.get("X-Cache-Hit"), "1")
        key = res.data["key"]

        device.add_location(arrow.get().timestamp(), 12.34568, 123.45677)

        time.sleep((time.time() // 5 + 1) * 5 - time.time() + 0.1)
        res = self.client.get(f"{url}/{key}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))
        self.assertNotEqual(res.data["competitors"][0]["locations_encoded"], "")
        self.assertEqual(res.data["partial"], True)
        res = self.client.get(f"{url}/{key}")
        self.assertEqual(res.headers.get("X-Cache-Hit"), "1")

        event.save()
        res = self.client.get(url)
        self.assertIsNone(res.headers.get("X-Cache-Hit"))

    def test_gpsseuranta_proxy(self):
        uid = "20240911AVPR"
        url = self.reverse_and_check(
            "third_party_event_detail",
            f"/gpsseuranta/{uid}/",
            "api",
            {"provider": "gpsseuranta", "uid": uid},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        url = self.reverse_and_check(
            "third_party_event_data",
            f"/gpsseuranta/{uid}/data",
            "api",
            {"provider": "gpsseuranta", "uid": uid},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_loggator_proxy(self):
        uid = "TC24F12M"
        url = self.reverse_and_check(
            "third_party_event_detail",
            f"/loggator/{uid}/",
            "api",
            {"provider": "loggator", "uid": uid},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        url = self.reverse_and_check(
            "third_party_event_data",
            f"/loggator/{uid}/data",
            "api",
            {"provider": "loggator", "uid": uid},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)


class LocationApiTestCase(EssentialApiBase):
    def setUp(self):
        super().setUp()
        self.url = self.reverse_and_check("locations_api_gw", "/locations")

    def test_locations_api_gw_valid(self):
        dev_id = self.get_device_id()
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "device_id": dev_id,
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},{t+1}",
                "secret": settings.POST_LOCATION_SECRETS[0],
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        nb_points = len(Device.objects.get(aid=dev_id).locations)
        self.assertEqual(nb_points, 2)
        # Add more location
        res = self.client.post(
            self.url,
            {
                "device_id": dev_id,
                "latitudes": "1.3,1.4",
                "longitudes": "3.3,3.4",
                "timestamps": f"{t+2},{t+3}",
                "secret": settings.POST_LOCATION_SECRETS[0],
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        nb_points = len(Device.objects.get(aid=dev_id).locations)
        self.assertEqual(nb_points, 4)
        # post same location again
        res = self.client.post(
            self.url,
            {
                "device_id": dev_id,
                "latitudes": "1.3,1.4",
                "longitudes": "3.3,3.4",
                "timestamps": f"{t+2},{t+3}",
                "secret": settings.POST_LOCATION_SECRETS[0],
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        nb_points = len(Device.objects.get(aid=dev_id).locations)
        self.assertEqual(nb_points, 4)

    def test_locations_api_gw_invalid_cast(self):
        dev_id = self.get_device_id()
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "device_id": dev_id,
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},NaN",
                "secret": settings.POST_LOCATION_SECRETS[0],
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Invalid data format", errors[0])

    def test_locations_api_gw_invalid_lon(self):
        dev_id = self.get_device_id()
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "device_id": dev_id,
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,182",
                "timestamps": f"{t},{t+1}",
                "secret": settings.POST_LOCATION_SECRETS[0],
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Invalid longitude value", errors[0])

    def test_locations_api_gw_invalid_lat(self):
        dev_id = self.get_device_id()
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "device_id": dev_id,
                "latitudes": "1.1,100",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},{t+1}",
                "secret": settings.POST_LOCATION_SECRETS[0],
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Invalid latitude value", errors[0])

    def test_locations_api_gw_invalid_length(self):
        dev_id = self.get_device_id()
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "device_id": dev_id,
                "latitudes": "1.1,1.2",
                "longitudes": "3.1",
                "timestamps": f"{t},{t+1}",
                "secret": settings.POST_LOCATION_SECRETS[0],
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn(
            "Latitudes, longitudes, and timestamps, should have same amount of points",
            errors[0],
        )

    def test_locations_api_gw_bad_secret(self):
        dev_id = self.get_device_id()
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "device_id": dev_id,
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},{t+1}",
                "secret": "bad secret",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_locations_api_gw_no_secret_but_logged_in_as_apps(self):
        validated_apps_user = User.objects.create_user(
            "apps", f"apps{random.randrange(1000)}@example.com", "pa$$word123"
        )
        self.client.force_login(validated_apps_user)
        dev_id = self.get_device_id()
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "device_id": dev_id,
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},{t+1}",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data.get("device_id"), dev_id)
        self.assertEqual(res.data.get("location_count"), 2)

    def test_gpsseuranta_relay(self):
        def run_fake_server():
            with socket.socket() as server_sock:
                server_sock.bind(("127.0.0.1", 0))
                port = server_sock.getsockname()[1]
                settings.GPSSEURANTA_SERVER_ADDR = f"tcp://127.0.0.1:{port}"
                server_sock.listen(0)
                client, addr = server_sock.accept()
                self.data_recv = client.recv(1024)
                client.close()

        validated_apps_user = User.objects.create_user(
            "apps", f"apps{random.randrange(1000)}@example.com", "pa$$word123"
        )
        self.client.force_login(validated_apps_user)
        device = Device.objects.create()
        device.gpsseuranta_known = True
        device.gpsseuranta_relay_until = arrow.get().shift(hours=1).datetime
        device.save()
        t = 1749720074

        # Run a fake GPS Seuranta TCP server to collect data received
        server_thread = threading.Thread(target=run_fake_server)
        server_thread.start()
        res = self.client.post(
            self.url,
            {
                "device_id": device.aid,
                "latitudes": "0.0001,0.0002",
                "longitudes": "3.00044,3",
                "timestamps": f"{t},{t+1}",
            },
        )
        # Ensure the fake server thread is closed
        server_thread.join()

        self.assertEqual(
            self.data_recv,
            bytes(f"rc{device.aid}.613646474_150022_10.W9f.\n", encoding="ascii"),
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data.get("device_id"), device.aid)
        self.assertEqual(res.data.get("location_count"), 2)

    def test_locations_api_gw_no_secret_but_logged_in_as_random_user(self):
        self.client.force_login(self.user)
        dev_id = self.get_device_id()
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "device_id": dev_id,
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},{t+1}",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_locations_api_gw_old_dev_id_valid(self):
        # Original app didnt require any authentication to post location
        # The device id included alphabetical characters
        d = Device.objects.create(aid="abcd1234")
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "device_id": d.aid,
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},{t+1}",
                "secret": "bad secret",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_locations_api_gw_no_device(self):
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "device_id": "doesnotexist",
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},{t+1}",
                "secret": "bad secret",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("No such device ID", errors[0])


class RouteUploadApiTestCase(EssentialApiBase):
    def setUp(self):
        super().setUp()
        self.club = Club.objects.create(name="Test club", slug="club")
        self.event = Event.objects.create(
            club=self.club,
            name="Test event",
            open_registration=True,
            allow_route_upload=True,
            start_date=arrow.get().shift(seconds=-1).datetime,
            end_date=arrow.get().shift(hours=1).datetime,
        )
        self.club.admins.set([self.user])
        self.competitor = Competitor.objects.create(
            name="Alice", short_name="A", event=self.event
        )
        self.url = self.reverse_and_check(
            "competitor_route_upload",
            f"/competitors/{self.competitor.aid}/route",
            extra_kwargs={"competitor_id": self.competitor.aid},
        )

    def test_route_upload_api_valid(self):
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},{t + 1}",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            Competitor.objects.get(aid=self.competitor.aid).device.location_count, 2
        )
        # Upload again on same competitor without being an admin
        res = self.client.post(
            self.url,
            {
                "latitudes": "1.3,1.4",
                "longitudes": "3.3,3.4",
                "timestamps": f"{t+2},{t+3}",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Competitor already assigned a route", errors[0])
        # Upload again on same competitor while being an admin
        self.client.force_login(self.user)
        res = self.client.post(
            self.url,
            {
                "latitudes": "1.3,1.4,1.5",
                "longitudes": "3.3,3.4,3.5",
                "timestamps": f"{t+2},{t+3},{t+1}",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            Competitor.objects.get(aid=self.competitor.aid).device.location_count, 3
        )

    def test_route_upload_api_invalid_cast(self):
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},NaN",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Invalid time value", errors[0])

        t = time.time()
        res = self.client.post(
            self.url,
            {
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},Nope",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Invalid data format", errors[0])

        t = time.time()
        res = self.client.post(
            self.url,
            {
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,NaN",
                "timestamps": f"{t},{t+1}",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Invalid longitude value", errors[0])

    def test_route_upload_api_invalid_lon(self):
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,182",
                "timestamps": f"{t},{t+1}",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Invalid longitude value", errors[0])

    def test_route_upload_api_invalid_lat(self):
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "latitudes": "1.1,100",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},{t+1}",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Invalid latitude value", errors[0])

    def test_route_upload_api_invalid_length(self):
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn(
            "Latitudes, longitudes, and timestamps, should have same amount of points",
            errors[0],
        )

    def test_route_upload_api_not_enough_points(self):
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "latitudes": "1.1",
                "longitudes": "3.1",
                "timestamps": f"{t}",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn(
            "Minimum amount of locations is 2",
            errors[0],
        )

    def test_route_upload_api_not_allowed(self):
        self.event.allow_route_upload = False
        self.event.save()
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},{t+1}",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_route_upload_api_not_started(self):
        self.event.start_date = arrow.get().shift(minutes=1).datetime
        self.event.save()
        t = time.time()
        res = self.client.post(
            self.url,
            {
                "latitudes": "1.1,1.2",
                "longitudes": "3.1,3.2",
                "timestamps": f"{t},{t+1}",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn(
            "Event has not yet started",
            errors[0],
        )


class CompetitionTestCase(EssentialApiBase):
    def test_send_sos(self):
        self.club = Club.objects.create(name="Kemiön Kiilat", slug="kiilat")
        self.club.admins.set([self.user])
        EmailAddress.objects.create(
            user=self.user, email=self.user.email, primary=True, verified=True
        )
        device = Device.objects.create()
        event = Event.objects.create(
            club=self.club,
            name="Test event",
            open_registration=True,
            start_date=arrow.get().shift(hours=-2).datetime,
            end_date=arrow.get().shift(hours=1).datetime,
        )
        Competitor.objects.create(
            name="Alice A",
            short_name="A",
            event=event,
            device=device,
            start_time=arrow.get().shift(minutes=-70).datetime,
        )
        device.add_location(arrow.get().timestamp(), 12.34567, 123.45678)
        device.send_sos()
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(
            "Routechoices.com - SOS from competitor Alice" in mail.outbox[0].subject
        )
        self.assertTrue(
            "Latest SOS known location is latitude, longitude: 12.34567, 123.45678"
            in mail.outbox[0].body
        )
        self.assertEqual([self.user.email], mail.outbox[0].to)
        event.emergency_contacts = "beargrills@discovery.com"
        event.save()
        device.send_sos()
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(["beargrills@discovery.com"], mail.outbox[1].to)


class RegistrationApiTestCase(EssentialApiBase):
    def test_registration(self):
        device_id = self.get_device_id()
        club = Club.objects.create(name="Test club", slug="club")
        club.admins.set([self.user])
        event = Event.objects.create(
            club=club,
            name="Test event",
            open_registration=True,
            start_date=arrow.get().datetime,
            end_date=arrow.get().shift(hours=1).datetime,
        )
        url = self.reverse_and_check(
            "competitor_creation_view",
            "/competitors/",
        )
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": device_id,
                "name": "Alice",
                "short_name": "🇺🇸 A",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # name exists
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": device_id,
                "name": "Alice",
                "short_name": "🇺🇸 Al",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Name already in use in this event", errors[0])
        # short_name exists
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": device_id,
                "name": "Albert",
                "short_name": "🇺🇸 A",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Short name already in use in this event", errors[0])
        # bad start_time
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": device_id,
                "name": "Albert",
                "short_name": "🇺🇸 Al",
                "start_time": arrow.get().shift(hours=-1).datetime,
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn(
            "Competitor start time should be during the event time", errors[0]
        )
        # invalid start_time
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": device_id,
                "name": "Bob",
                "short_name": "🇺🇸 B",
                "start_time": "unreadable",
            },
            format="json",
        )
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Start time could not be parsed", errors[0])
        # no name
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": device_id,
                "name": "",
                "short_name": "🇺🇸 B",
            },
            format="json",
        )
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Name is missing", errors[0])
        # no name, no shortname
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": device_id,
            },
            format="json",
        )
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Name is missing", errors[0])
        # no data
        res = self.client.post(
            url,
            {},
            format="json",
        )
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        # no device
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": "does not exists",
                "name": "Bob",
                "short_name": "🇺🇸 B",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Device ID not found", errors[0])
        # ok
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": device_id,
                "name": "Bob",
                "short_name": "🇺🇸 B",
                "start_time": arrow.get().shift(minutes=+1).datetime,
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # ended event
        event.start_date = arrow.get().shift(hours=-1).datetime
        event.end_date = arrow.get().shift(minutes=-1).datetime
        event.save()
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": device_id,
                "name": "Charles",
                "short_name": "🇺🇸 C",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        errors = json.loads(res.content)
        self.assertEqual(len(errors), 1)
        self.assertIn("Registration is closed", errors[0])
        # ended event but allow route upload
        event.start_date = arrow.get().shift(hours=-1).datetime
        event.end_date = arrow.get().shift(minutes=-1).datetime
        event.allow_route_upload = True
        event.save()
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": device_id,
                "name": "Dick",
                "short_name": "🇺🇸 D",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # ok event
        event.end_date = arrow.get().shift(minutes=1).datetime
        event.allow_route_upload = False
        event.save()
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": device_id,
                "name": "Elsa",
                "short_name": "🇺🇸 E",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # private event not logged in
        event.end_date = arrow.get().shift(minutes=1).datetime
        event.privacy = PRIVACY_PRIVATE
        event.save()
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": device_id,
                "name": "Frank",
                "short_name": "🇺🇸 F",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # private event logged in as not admin
        user_not_admin = User.objects.create_user(
            "bob", "bob@example.com", "pa$$word123"
        )
        self.client.force_login(user_not_admin)
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": device_id,
                "name": "George",
                "short_name": "🇺🇸 G",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # private event logged in as admin ok
        self.client.force_login(self.user)
        res = self.client.post(
            url,
            {
                "event_id": event.aid,
                "device_id": device_id,
                "name": "Hugh",
                "short_name": "🇺🇸 H",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
