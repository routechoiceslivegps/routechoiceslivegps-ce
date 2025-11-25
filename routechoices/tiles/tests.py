import tempfile
from pathlib import Path

import arrow
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIClient, override_settings

from routechoices.api.tests import EssentialApiBase
from routechoices.core.models import Club, Event, Map, MapAssignation


@override_settings(MEDIA_ROOT=Path(tempfile.gettempdir()))
class MapApiTestCase(EssentialApiBase):
    def test_get_tile(self):
        client = APIClient(HTTP_HOST="tiles.routechoices.dev")
        url = self.reverse_and_check("tile_service", "/", "tiles")
        club = Club.objects.create(name="Test club", slug="club")
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
        event = Event.objects.create(
            club=club,
            name="Test event",
            open_registration=True,
            start_date=arrow.get().shift(minutes=-1).datetime,
            end_date=arrow.get().shift(hours=1).datetime,
            map=raster_map,
        )
        res = client.get(
            (f"{url}?z=17&x=74352&y=36993&layers={event.aid}&" f"format=image%2Fjpeg")
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # requesting 2nd non existing map of event
        res = client.get(
            (
                f"{url}?z=17&x=74352&y=36993&layers={event.aid}%2F2&"
                f"format=image%2Fjpeg"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

        raster_map2 = Map.objects.create(
            club=club,
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
        MapAssignation.objects.create(event=event, map=raster_map2, title="Other route")

        # requesting 2nd existing map of event
        res = client.get(
            (
                f"{url}?z=17&x=74352&y=36993&layers={event.aid}%2F2&"
                f"format=image%2Fjpeg"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["content-type"], "image/jpeg")

        # serve avif if accepted
        res = client.get(
            (f"{url}?z=17&x=74352&y=36993&layers={event.aid}&" f"format=image%2Fjpeg"),
            HTTP_ACCEPT="image/avif",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["content-type"], "image/avif")

        # requesting 3rd non existing map of event
        res = client.get(
            (
                f"{url}?z=17&x=74352&y=36993&layers={event.aid}%2F3&"
                f"format=image%2Fjpeg"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

        # serve png if asked
        res = client.get(
            (f"{url}?z=17&x=74352&y=36993&layers={event.aid}&format=image%2Fpng")
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["content-type"], "image/png")

        # serve png if asked apng
        res = client.get(
            (f"{url}?z=17&x=74352&y=36993&layers={event.aid}&format=image%2Fapng")
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["content-type"], "image/png")

        # serve webp if asked
        res = client.get(
            (f"{url}?z=17&x=74352&y=36993&layers={event.aid}&format=image%2Fwebp")
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["content-type"], "image/webp")

        # serve avif if asked
        res = client.get(
            (f"{url}?z=17&x=74352&y=36993&layers={event.aid}&format=image%2Favif")
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["content-type"], "image/avif")

        # serve jxl if asked
        res = client.get(
            (f"{url}?z=17&x=74352&y=36993&layers={event.aid}&format=image%2Fjxl")
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["content-type"], "image/jxl")

        # return error if invalid tiles is asked
        res = client.get((f"{url}?z=17&x=hello&y=36993&layers={event.aid}"))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # return error if invalid layer
        res = client.get((f"{url}?z=17&x=74352&y=36993&layers={event.aid}/0"))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # return error if param is missing
        res = client.get((f"{url}?z=17&y=36993&layers={event.aid}"))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # return error if mime is not supported
        res = client.get(
            (f"{url}?z=17&x=74352&y=36993&layers={event.aid}&format=image%2Fpdf")
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_proxy(self):
        client = APIClient(HTTP_HOST="tiles.routechoices.dev")
        url = self.reverse_and_check(
            "proxy_tile_service",
            "/proxy/uk/1/2/3.webp",
            "tiles",
            {"country": "uk", "z": 1, "x": 2, "y": 3},
        )
        test_tiles = [
            "ch/16/34026/23199",
            "ee/15/18662/9672",
            "se/13/4459/2405",
            "uk/13/4062/2695",
        ]
        for url in test_tiles:
            res = client.get(f"/proxy/{url}.webp")
            self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_should_hit_cache(self):
        cache.clear()
        client = APIClient(HTTP_HOST="tiles.routechoices.dev")
        url = self.reverse_and_check("tile_service", "/", "tiles")
        club = Club.objects.create(name="Test club", slug="club")
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
        event = Event.objects.create(
            club=club,
            name="Test event",
            open_registration=True,
            start_date=arrow.get().shift(minutes=-1).datetime,
            end_date=arrow.get().shift(hours=1).datetime,
            map=raster_map,
        )
        base_url = f"{url}?z=17&layers={event.aid}&" "format=image%2Fjpeg"
        intersecting_bbox = "&x=74352&y=36993"
        non_intersecting_bbox = "&x=742&y=36993"
        non_intersecting_bbox_2 = "&x=352&y=36993"
        # tile that intersect, first query should not hit cache
        res = client.get(
            f"{base_url}{intersecting_bbox}",
        )
        self.assertEqual(res.headers["X-Cache-Hit"], "0")
        # same tile, 2nd query should hit cache
        res = client.get(
            f"{base_url}{intersecting_bbox}",
        )
        self.assertEqual(res.headers["X-Cache-Hit"], "1")
        # if map change cache fetching the same tile should not hit cache
        raster_map.calibration_string = (
            "61.45075,24.18994,61.44656,24.24721,61.42094,24.23851,61.42533,24.18155"
        )
        raster_map.save()
        res = client.get(
            f"{base_url}{intersecting_bbox}",
        )
        self.assertEqual(res.headers["X-Cache-Hit"], "0")
        # tile that dont intersect, first query should not hit cache
        res = client.get(
            f"{base_url}{non_intersecting_bbox}",
        )
        self.assertEqual(res.headers["X-Cache-Hit"], "0")
        # same tile, 2nd query should hit cache
        res = client.get(f"{base_url}&bbox={non_intersecting_bbox}")
        self.assertEqual(res.headers["X-Cache-Hit"], "1")
        # another tile that dont intersect, should hit cache of blank tiles
        res = client.get(
            f"{base_url}{non_intersecting_bbox_2}",
        )
        self.assertEqual(res.headers["X-Cache-Hit"], "2")
