from io import BytesIO

import cv2
import numpy as np
from curl_cffi import requests
from django.conf import settings
from django.http import Http404
from PIL import Image
from pyproj import Transformer

from routechoices.lib import cache

from .slippy_tiles import (
    tile_xy_to_north_west_wgs84,
)

TILE_CACHE_TIMEOUT = 7 * 24 * 3600  # 1 days
REMOTE_IMG_CACHE_TIMEOUT = 300  # 5 minutes


class CustomCrsWms2WebMercatorWmtsProxy:
    def __init__(self, proj_def, url):
        self.proj_def = proj_def
        self.url = url
        self.session = requests.Session(impersonate="chrome")

    def wgs84_to_crs(self, wgs84_coordinate):
        lat, lon = wgs84_coordinate.latlon
        return Transformer.from_crs(
            "+proj=latlon",
            self.proj_def,
        ).transform(lon, lat)

    def tile_xy_crs_bound(self, z, x, y):
        north_west = self.wgs84_to_crs(tile_xy_to_north_west_wgs84(x, y, z))
        north_east = self.wgs84_to_crs(tile_xy_to_north_west_wgs84(x + 1, y, z))
        south_east = self.wgs84_to_crs(tile_xy_to_north_west_wgs84(x + 1, y + 1, z))
        south_west = self.wgs84_to_crs(tile_xy_to_north_west_wgs84(x, y + 1, z))
        return north_west, north_east, south_east, south_west

    def get_tile(self, z, x, y):
        cache_key = f"tile_proxy:wms2web_output_tile:{self.url}:{x}:{y}:{z}"
        if cached := cache.get(cache_key):
            return cached
        north_west, north_east, south_east, south_west = self.tile_xy_crs_bound(z, x, y)
        min_x = min(north_west[0], north_east[0], south_east[0], south_west[0])
        max_x = max(north_west[0], north_east[0], south_east[0], south_west[0])
        min_y = min(north_west[1], north_east[1], south_east[1], south_west[1])
        max_y = max(north_west[1], north_east[1], south_east[1], south_west[1])

        tile_url = self.url.format(min_x=min_x, max_x=max_x, min_y=min_y, max_y=max_y)

        try:
            res = self.session.get(
                tile_url,
                timeout=10,
            )
            res.raise_for_status()
        except Exception:
            data = None
        else:
            data = res.content
        if not data:
            im = Image.new(mode="RGB", size=(256, 256), color=(255, 255, 255))
            img = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGRA)
            _, buffer = cv2.imencode(".webp", img, [int(cv2.IMWRITE_WEBP_QUALITY), 40])
            data_out = BytesIO(buffer)

            cache.set(cache_key, data_out, TILE_CACHE_TIMEOUT)

            return data_out

        img_alpha = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)

        scale_x = (max_x - min_x) / 512
        scale_y = (max_y - min_y) / 512

        p1 = np.float32(
            [
                [0, 0],
                [256, 0],
                [256, 256],
                [0, 256],
            ]
        )

        p2 = np.float32(
            [
                [(north_west[0] - min_x) / scale_x, (max_y - north_west[1]) / scale_y],
                [(north_east[0] - min_x) / scale_x, (max_y - north_east[1]) / scale_y],
                [(south_east[0] - min_x) / scale_x, (max_y - south_east[1]) / scale_y],
                [(south_west[0] - min_x) / scale_x, (max_y - south_west[1]) / scale_y],
            ]
        )

        coeffs, mask = cv2.findHomography(p2, p1, cv2.RANSAC, 5.0)
        img = cv2.warpPerspective(
            img_alpha,
            coeffs,
            (256, 256),
            flags=cv2.INTER_AREA,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255, 0),
        )
        _, buffer = cv2.imencode(".webp", img, [int(cv2.IMWRITE_WEBP_QUALITY), 40])
        data_out = BytesIO(buffer)

        cache.set(cache_key, data_out, TILE_CACHE_TIMEOUT)

        return data_out


class CustomCrsWmts2WebMercatorWmtsProxy:
    def __init__(
        self, proj_def, tile_size, scalefactor, x_offset, y_offset, z_offset, url
    ):
        self.proj_def = proj_def
        self.tile_size = tile_size
        self.scalefactor = scalefactor
        self.x_offset = x_offset
        self.y_offset = y_offset
        self.z_offset = z_offset
        self.url = url
        self.session = requests.Session(impersonate="chrome")

    def wgs84_to_crs(self, wgs84_coordinate):
        lat, lon = wgs84_coordinate.latlon
        return Transformer.from_crs(
            "+proj=latlon",
            self.proj_def,
        ).transform(lon, lat)

    def crs_to_tile_xy(self, x, y, zoom):
        scale = self.tile_size * self.scalefactor / 2**zoom
        tile_x = int((x - self.x_offset) / scale)
        tile_y = int((self.y_offset - y) / scale)
        return tile_x, tile_y

    def crs_tile_xy_to_crs_north_west_coords(self, tile_x, tile_y, zoom):
        scale = self.tile_size * self.scalefactor / (2**zoom)
        x = tile_x * scale + self.x_offset
        y = self.y_offset - tile_y * scale
        return (x, y)

    def wgs84_to_crs_tile_coordinates(self, wgs84_coordinate, z):
        zoom = z - self.z_offset
        x, y = self.wgs84_to_crs(wgs84_coordinate)
        tile_x, tile_y = self.crs_to_tile_xy(x, y, zoom)
        x_min, y_max = self.crs_tile_xy_to_crs_north_west_coords(tile_x, tile_y, zoom)
        x_max, y_min = self.crs_tile_xy_to_crs_north_west_coords(
            tile_x + 1, tile_y + 1, zoom
        )

        tile_height = y_max - y_min
        tile_width = x_max - x_min

        offset_x = (x - x_min) / tile_width * self.tile_size
        offset_y = (y_max - y) / tile_height * self.tile_size

        return offset_x, offset_y, tile_x, tile_y

    def get_crs_tile(self, z, y, x):
        cache_key = f"tile_proxy:remote_tile:{self.url}:{x}:{y}:{z}"
        if cached := cache.get(cache_key):
            return cached

        url = self.url.format(x=x, y=y, z=z)

        try:
            res = self.session.get(url, timeout=10)
            res.raise_for_status()
        except Exception as e:
            print(e, flush=True)
            return None
        im = Image.open(BytesIO(res.content))

        cache.set(cache_key, im, timeout=REMOTE_IMG_CACHE_TIMEOUT)
        return im

    def get_tile(self, z, x, y):
        cache_key = f"tile_proxy:wmts2web_output_tile:{self.url}:{x}:{y}:{z}"
        if cached := cache.get(cache_key):
            return cached
        try:
            north, west = tile_xy_to_north_west_wgs84(x, y, z).latlon
            south, east = tile_xy_to_north_west_wgs84(x + 1, y + 1, z).latlon

            nw_x, nw_y, nw_tile_x, nw_tile_y = self.latlon_to_crs_tile_coordinates(
                north, west, z
            )
            ne_x, ne_y, ne_tile_x, ne_tile_y = self.latlon_to_crs_tile_coordinates(
                north, east, z
            )
            se_x, se_y, se_tile_x, se_tile_y = self.latlon_to_crs_tile_coordinates(
                south, east, z
            )
            sw_x, sw_y, sw_tile_x, sw_tile_y = self.latlon_to_crs_tile_coordinates(
                south, west, z
            )
        except OverflowError:
            raise Http404()

        tile_min_x = min(nw_tile_x, ne_tile_x, se_tile_x, sw_tile_x)
        tile_max_x = max(nw_tile_x, ne_tile_x, se_tile_x, sw_tile_x)
        tile_min_y = min(nw_tile_y, ne_tile_y, se_tile_y, sw_tile_y)
        tile_max_y = max(nw_tile_y, ne_tile_y, se_tile_y, sw_tile_y)

        src_tile_size = self.tile_size
        dst_tile_size = 256

        img_width = (tile_max_x - tile_min_x + 1) * src_tile_size
        img_height = (tile_max_y - tile_min_y + 1) * src_tile_size

        p1 = np.float32(
            [
                [0, 0],
                [dst_tile_size, 0],
                [dst_tile_size, dst_tile_size],
                [0, dst_tile_size],
            ]
        )

        p2 = np.float32(
            [
                [
                    nw_x + (nw_tile_x - tile_min_x) * src_tile_size,
                    nw_y + (nw_tile_y - tile_min_y) * src_tile_size,
                ],
                [
                    ne_x + (ne_tile_x - tile_min_x) * src_tile_size,
                    ne_y + (ne_tile_y - tile_min_y) * src_tile_size,
                ],
                [
                    se_x + (se_tile_x - tile_min_x) * src_tile_size,
                    se_y + (se_tile_y - tile_min_y) * src_tile_size,
                ],
                [
                    sw_x + (sw_tile_x - tile_min_x) * src_tile_size,
                    sw_y + (sw_tile_y - tile_min_y) * src_tile_size,
                ],
            ]
        )
        tiles = []
        for yy in range(tile_min_y, tile_max_y + 1):
            for xx in range(tile_min_x, tile_max_x + 1):
                tile = (z - self.z_offset, yy, xx)
                tiles.append(tile)

        im = Image.new(mode="RGB", size=(img_width, img_height), color=(255, 255, 255))
        if len(tiles) > 9:
            buffer = BytesIO()
            im.save(
                buffer,
                "WEBP",
                optimize=True,
                quality=40,
            )
            return buffer
        for tile in tiles:
            z, yy, xx = tile
            tile_img = self.get_crs_tile(z, yy, xx)
            if tile_img:
                Image.Image.paste(
                    im,
                    tile_img,
                    (
                        int(src_tile_size * (xx - tile_min_x)),
                        int(src_tile_size * (yy - tile_min_y)),
                    ),
                )
            else:
                cache_key = None
        coeffs, mask = cv2.findHomography(p2, p1, cv2.RANSAC, 5.0)
        img_alpha = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGRA)
        img = cv2.warpPerspective(
            img_alpha,
            coeffs,
            (dst_tile_size, dst_tile_size),
            flags=cv2.INTER_AREA,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255, 0),
        )
        _, buffer = cv2.imencode(".webp", img, [int(cv2.IMWRITE_WEBP_QUALITY), 40])
        data_out = BytesIO(buffer)
        if cache_key:
            cache.set(cache_key, data_out, TILE_CACHE_TIMEOUT)

        return data_out


mapant_ch_proxy = CustomCrsWmts2WebMercatorWmtsProxy(
    "+proj=somerc +lat_0=46.9524055555556 +lon_0=7.43958333333333 +k_0=1 +x_0=2600000 +y_0=1200000 +ellps=bessel +towgs84=674.374,15.056,405.346,0,0,0,0 +units=m +no_defs +type=crs",
    1000,
    512,
    2480000,
    1302000,
    7,
    "https://www.mapant.ch/wmts.php?layer=MapAnt%20Switzerland&style=default&tilematrixset=2056&Service=WMTS&Request=GetTile&Version=1.0.0&Format=image%2Fpng&TileMatrix={z}&TileCol={x}&TileRow={y}",
)

mapant_ee_proxy = CustomCrsWms2WebMercatorWmtsProxy(
    "+proj=lcc +lat_0=57.5175539305556 +lon_0=24 +lat_1=59.3333333333333 +lat_2=58 +x_0=500000 +y_0=6375000 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs +type=crs",
    "https://mapantee.gokartor.se/ogc/wms.php?v=1&request=GetMap&width=512&height=512&bbox={min_x},{min_y},{max_x},{max_y}",
)

mapant_se_proxy = CustomCrsWmts2WebMercatorWmtsProxy(
    "+proj=utm +zone=33 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs +type=crs",
    256,
    16384,
    265000,
    7680000,
    2,
    "https://kartor.gokartor.se/Master/{z}/{y}/{x}.png",
)

leisure_uk_proxy = CustomCrsWmts2WebMercatorWmtsProxy(
    "+proj=tmerc +lat_0=49 +lon_0=-2 +k=0.9996012717 +x_0=400000 +y_0=-100000 +ellps=airy +towgs84=370,-108,434,0,0,0,0 +units=m +no_defs +type=crs",
    256,
    896,
    -238375,
    1376256,
    7,
    "https://api.os.uk/maps/raster/v1/zxy/Leisure_27700/{z}/{x}/{y}.png?key="
    + settings.ORDNANCE_SURVEY_API_KEY,
)
