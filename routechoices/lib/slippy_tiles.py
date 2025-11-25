import math

from routechoices.lib.helpers import Wgs84Coordinate


def wgs84_to_tile_xy(wgs84_coordinate, zoom):
    lat_deg, lon_deg = wgs84_coordinate.latlon
    lat_rad = math.radians(lat_deg)
    n = 2.0**zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (xtile, ytile)


def tile_xy_to_north_west_wgs84(xtile, ytile, zoom):
    n = 2.0**zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return Wgs84Coordinate(lat_deg, lon_deg)
