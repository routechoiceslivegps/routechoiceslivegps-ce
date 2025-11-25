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
        client = APIClient(HTTP_HOST="wms.routechoices.dev")
        url = self.reverse_and_check("wms_service", "/", "wms")
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
        query_espg = "srs=EPSG%3A3857&"
        query_bbox = "bbox=2690583,8727274,2693029,8729720"
        query_size = "width=512&height=512&"
        query_junk = "styles=&transparent=false&version=1.1.1&"
        def_query = f"{query_junk}{query_espg}{query_size}{query_bbox}"
        res = client.get(
            (
                f"{url}?service=WMS&request=GetMap&layers={event.aid}&"
                f"format=image%2Fjpeg&{def_query}"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # requesting 2nd non existing map of event
        res = client.get(
            (
                f"{url}?service=WMS&request=GetMap&layers={event.aid}%2F2&"
                f"format=image%2Fjpeg&{def_query}"
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
                f"{url}?service=WMS&request=GetMap&layers={event.aid}%2F2&"
                f"format=image%2Fjpeg&{def_query}"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["content-type"], "image/jpeg")

        # serve avif if accepted
        res = client.get(
            (
                f"{url}?service=WMS&request=GetMap&layers={event.aid}&"
                f"format=image%2Fjpeg&{def_query}"
            ),
            HTTP_ACCEPT="image/avif",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["content-type"], "image/avif")

        # requesting 3rd non existing map of event
        res = client.get(
            (
                f"{url}?service=WMS&request=GetMap&layers={event.aid}%2F3&"
                f"format=image%2Fjpeg&{def_query}"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

        # serve png if asked
        res = client.get(
            (
                f"{url}?service=WMS&request=GetMap&layers={event.aid}&"
                f"format=image%2Fpng&{def_query}"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["content-type"], "image/png")

        # serve webp if asked
        res = client.get(
            (
                f"{url}?service=WMS&request=GetMap&layers={event.aid}&"
                f"format=image%2Fwebp&{def_query}"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["content-type"], "image/webp")

        # serve jxl if asked
        res = client.get(
            (
                f"{url}?service=WMS&request=GetMap&layers={event.aid}&"
                f"format=image%2Fjxl&{def_query}"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["content-type"], "image/jxl")

        # serve avif if asked
        res = client.get(
            (
                f"{url}?service=WMS&request=GetMap&layers={event.aid}&"
                f"format=image%2Favif&{def_query}"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["content-type"], "image/avif")

        # return error if gif is asked
        res = client.get(
            (
                f"{url}?service=WMS&request=GetMap&layers={event.aid}&"
                f"format=image%2Fgif&{def_query}"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # return ok if srs is CRS:84
        def_query = f"{query_junk}srs=CRS%3A84&{query_size}bbox=61.40,24.18,61.5,24.25"
        res = client.get(
            (
                f"{url}?service=WMS&request=GetMap&layers={event.aid}&"
                f"format=image%2Fjpeg&{def_query}"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # return ok if srs is EPSG:4326
        def_query = (
            f"{query_junk}srs=EPSG%3A4326&{query_size}bbox=24.18,61.40,24.25,61.5"
        )
        res = client.get(
            (
                f"{url}?service=WMS&request=GetMap&layers={event.aid}&"
                f"format=image%2Fjpeg&{def_query}"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # return 400 if srs is not supported
        def_query = (
            f"{query_junk}srs=EPSG%3A4327&{query_size}bbox=24.18,61.40,24.25,61.5"
        )
        res = client.get(
            (
                f"{url}?service=WMS&request=GetMap&layers={event.aid}&"
                f"format=image%2Fjpeg&{def_query}"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # return 400 if bound is invalid
        def_query = f"{query_junk}{query_espg}{query_size}{query_bbox}invalid"
        res = client.get(
            (
                f"{url}?service=WMS&request=GetMap&layers={event.aid}&"
                f"format=image%2Fjpeg&{def_query}"
            )
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_should_hit_cache(self):
        cache.clear()
        client = APIClient(HTTP_HOST="wms.routechoices.dev")
        url = self.reverse_and_check("wms_service", "/", "wms")
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
        base_url = (
            f"{url}?service=WMS&request=GetMap&layers={event.aid}&styles=&"
            "format=image%2Fjpeg&transparent=false&version=1.1.1&"
            "width=512&height=512&srs=EPSG%3A3857"
        )
        intersecting_bbox = (
            "2690583.395638204,8727274.141488286,2693029.3805433298,8729720.12639341"
        )
        non_intersecting_bbox = (
            "-7903588.724687226,5234407.696968872,-7902977.228460945,5235019.193195154"
        )
        non_intersecting_bbox_2 = (
            "-7903587.724687226,5234406.696968872,-7902976.228460944,5235018.193195154"
        )
        # tile that intersect, first query should not hit cache
        res = client.get(
            f"{base_url}&bbox={intersecting_bbox}",
        )
        self.assertEqual(res.headers["X-Cache-Hit"], "0")
        # same tile, 2nd query should hit cache
        res = client.get(
            f"{base_url}&bbox={intersecting_bbox}",
        )
        self.assertEqual(res.headers["X-Cache-Hit"], "1")
        # if map change cache fetching the same tile should not hit cache
        raster_map.calibration_string = (
            "61.45075,24.18994,61.44656,24.24721,61.42094,24.23851,61.42533,24.18155"
        )
        raster_map.save()
        res = client.get(
            f"{base_url}&bbox={intersecting_bbox}",
        )
        self.assertEqual(res.headers["X-Cache-Hit"], "0")
        # tile that dont intersect, first query should not hit cache
        res = client.get(
            f"{base_url}&bbox={non_intersecting_bbox}",
        )
        self.assertEqual(res.headers["X-Cache-Hit"], "0")
        # same tile, 2nd query should hit cache
        res = client.get(f"{base_url}&bbox={non_intersecting_bbox}")
        self.assertEqual(res.headers["X-Cache-Hit"], "1")
        # another tile that dont intersect, should hit cache of blank tiles
        res = client.get(
            f"{base_url}&bbox={non_intersecting_bbox_2}",
        )
        self.assertEqual(res.headers["X-Cache-Hit"], "2")
