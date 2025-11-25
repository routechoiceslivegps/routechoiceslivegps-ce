import base64
import hashlib
import math
import os
import os.path
import re
import secrets
import struct
import time
import urllib
import urllib.request
import zoneinfo
from datetime import datetime
from math import cos, pi, sin, sqrt

import reverse_geocode
from curl_cffi import requests
from django.conf import settings
from django.http.response import Http404
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive, make_aware
from PIL import Image, ImageFile
from timezonefinder import TimezoneFinder
from user_sessions.templatetags.user_sessions import device as device_name

from routechoices.lib.random_strings import generate_random_string
from routechoices.lib.validators import validate_nice_slug

UTC_TZ = zoneinfo.ZoneInfo("UTC")

COUNTRIES = reverse_geocode.GeocodeData()._countries
TIMEZONEFINDER = TimezoneFinder(in_memory=True)
ORIGIN_SHIFT = 2 * math.pi * 6378137 / 2.0  # 20037508.342789244


class Point:
    x = None
    y = None

    def __init__(self, val, *args):
        if len(args) == 1:
            x, y = [val, args[0]]
        elif isinstance(val, Point):
            x, y = val.xy
        elif isinstance(val, tuple) or isinstance(val, list):
            x, y = val
        elif isinstance(val, dict):
            x = val.get("x")
            y = val.get("y")
        else:
            raise ValueError()
        self.x = x
        self.y = y

    @property
    def xy(self):
        return (self.x, self.y)

    def round(self, precision):
        return Point(
            round(self.x, precision),
            round(self.y, precision),
        )

    def __repr__(self):
        return f"x: {self.x}, y:{self.y}"


def country_code_at_coords(wgs84_coordinate):
    return reverse_geocode.get(wgs84_coordinate.latlon).get("country_code")


def timezone_at_coords(wgs84_coordinate):
    lat, lon = wgs84_coordinate.latlon
    return TIMEZONEFINDER.timezone_at(lng=lat, lat=lon)


def simplify_periods(ps):
    idx = 0
    ps = sorted(ps)
    while idx <= len(ps) - 2:
        start_a, end_a = ps[idx]
        start_b, end_b = ps[idx + 1]
        if start_b <= end_a:
            ps_o = ps[0:idx]
            ps_o.append((start_a, max(end_a, end_b)))
            ps_o += ps[idx + 2 :]
            ps = ps_o
        else:
            idx += 1
    return ps


def get_remote_image_sizes(uri):
    # Get file size *and* image size (None if not known)
    if not re.match("^https?://", uri.lower()):
        raise Exception("Invalid Protocol")
    with urllib.request.urlopen(uri) as file:
        size = file.headers.get("content-length")
        if size:
            size = int(size)
        p = ImageFile.Parser()
        while 1:
            data = file.read(1024)
            if not data:
                break
            p.feed(data)
            if p.image:
                return size, p.image.size
        return size, None


class MySite:
    domain = settings.RELYING_PARTY_ID
    name = settings.RELYING_PARTY_NAME


def avg_angles(a, b):
    d = ((b - a + 180) % 360) - 180
    return (a + d / 2) % 360


def get_current_site():
    return MySite()


def get_image_mime_from_request(requested_extension=None, default_mime=None):
    mime = default_mime
    if requested_extension:
        if requested_extension not in ("png", "webp", "avif", "jxl", "jpeg"):
            raise Http404()
        mime = f"image/{requested_extension}"
    return mime


def get_best_image_mime(request, default=None):
    accepted_mimes = request.COOKIES.get("accept-image", "").split(",")
    accepted_mimes += request.META.get("HTTP_ACCEPT", "").split(",")
    for mime in (
        "image/webp",
        "image/avif",
        "image/jxl",
    ):
        if mime in accepted_mimes:
            return mime
    return default


def git_master_hash():
    try:
        with open(os.path.join(settings.BASE_DIR, ".git", "HEAD")) as fp:
            rev = fp.read().strip()
        if ":" not in rev:
            return rev[:8]
        with open(os.path.join(settings.BASE_DIR, ".git", rev[5:])) as fp:
            return fp.read().strip()[:8]
    except Exception:
        return "dev"


def epoch_to_datetime(t):
    return datetime.utcfromtimestamp(int(t)).replace(tzinfo=UTC_TZ)


def set_content_disposition(filename, dl=True):
    prefix = "attachment; " if dl else ""
    return f"{prefix}filename*=UTF-8''{urllib.parse.quote(filename, safe='')}"


def safe64encode(b):
    if isinstance(b, str):
        b = b.encode("utf-8")
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def safe64encodedsha(txt):
    if isinstance(txt, str):
        txt = txt.encode("utf-8")
    h = hashlib.sha256()
    h.update(txt)
    return safe64encode(h.digest())


def shortsafe64encodedsha(txt):
    return safe64encodedsha(txt)[:8]


# def safe64decode(b):
#     return base64.urlsafe_b64decode(b.encode() + b"==")


def int_base32(i):
    b = struct.pack(">Q", i)
    while b.startswith(b"\x00"):
        b = b[1:]
    return base64.b32encode(b).decode().rstrip("=")


def time_base32():
    t = int(time.time())
    return int_base32(t)


def deg2rad(deg):
    return deg * pi / 180


def get_device_name(ua):
    if ua.startswith("Routechoices-ios-tracker"):
        return "iOS"
    if ua.startswith("Routechoices-watch-tracker/"):
        return "Apple Watch"
    if ua.startswith("Dalvik"):
        return "Android"
    if ua.startswith("ConnectMobile"):
        return "Garmin"
    if ua.startswith("Traccar"):
        return "Traccar"
    if name := device_name(ua):
        return name
    return ua


def get_aware_datetime(date_str):
    ret = parse_datetime(date_str)
    if is_naive(ret):
        ret = make_aware(ret)
    return ret


def random_key():
    rand_bytes = bytes(struct.pack("Q", secrets.randbits(64)))
    b64 = safe64encode(rand_bytes)
    b64 = b64[:11]
    try:
        validate_nice_slug(b64)
    except Exception:
        return random_key()
    return b64


def short_random_key():
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    return generate_random_string(alphabet, 6)


def random_device_id():
    alphabet = "0123456789"
    start = generate_random_string(alphabet[1:], 1)
    return f"{start}{generate_random_string(alphabet, 7)}"


def short_random_slug():
    alphabet = "23456789abcdefghijkmnpqrstuvwxyz"
    return generate_random_string(alphabet, 6)


def solve_affine_matrix(matrix):
    a, b, c, d, e, f, g, h, i = matrix
    x = (((f - i) * (b - e)) - ((c - f) * (e - h))) / (
        ((d - g) * (b - e)) - ((a - d) * (e - h))
    )
    y = (((f - i) * (a - d)) - ((c - f) * (d - g))) / (
        ((e - h) * (a - d)) - ((b - e) * (d - g))
    )
    z = c - (a * x) - (b * y)
    return [x, y, z]


def flatten(something):
    if isinstance(something, (list, tuple, set, range)):
        for sub in something:
            yield from flatten(sub)
    else:
        yield something


def derive_affine_transform(ref_a_points, ref_b_points):
    matrix_x = list(
        flatten(
            list(
                (
                    ref_b_points[i].x,
                    ref_b_points[i].y,
                    ref_a_points[i].x,
                )
                for i in range(3)
            )
        )
    )
    matrix_y = list(
        flatten(
            list(
                (
                    ref_b_points[i].x,
                    ref_b_points[i].y,
                    ref_a_points[i].x,
                )
                for i in range(3)
            )
        )
    )
    print(matrix_x)
    x_coefs = solve_affine_matrix(matrix_x)
    y_coefs = solve_affine_matrix(matrix_y)

    def transform(xy):
        x, y = Point(xy).xy
        xt = x * x_coefs[0] + y * x_coefs[1] + x_coefs[2]
        yt = x * y_coefs[0] + y * y_coefs[1] + y_coefs[2]
        return xt, yt

    return transform


def wgs84_bound_from_3_ref_points(coords, image_points, image_size):
    print(coords)
    coords_xy_meters = list((val.xy_meters for val in coords))
    image_xy_to_meters = derive_affine_transform(coords_xy_meters, image_points)

    def image_xy_to_wgs84(xy):
        return XYMeters(image_xy_to_meters(Point(xy))).wgs84_coordinate

    width, height = image_size
    return (
        image_xy_to_wgs84((0, 0)),
        image_xy_to_wgs84((width, 0)),
        image_xy_to_wgs84((width, height)),
        image_xy_to_wgs84((0, height)),
    )


def adjugate_matrix(matrix):
    a, b, c, d, e, f, g, h, i = matrix
    return [
        e * i - f * h,
        c * h - b * i,
        b * f - c * e,
        f * g - d * i,
        a * i - c * g,
        c * d - a * f,
        d * h - e * g,
        b * g - a * h,
        a * e - b * d,
    ]


def multiply_matrices(a, b):
    c = [0] * 9
    for i in range(3):
        for j in range(3):
            for k in range(3):
                c[3 * i + j] += a[3 * i + k] * b[3 * k + j]
    return c


def multiply_matrix_vector(matrix, vector):
    a, b, c, d, e, f, g, h, i = matrix
    x, y, z = vector
    return [
        a * x + b * y + c * z,
        d * x + e * y + f * z,
        g * x + h * y + i * z,
    ]


def basis_to_points(a, b, c, d):
    matrix = [a.x, b.x, c.x, a.y, b.y, c.y, 1, 1, 1]
    x, y, z = multiply_matrix_vector(adjugate_matrix(matrix), [d.x, d.y, 1])
    return multiply_matrices(matrix, [x, 0, 0, 0, y, 0, 0, 0, z])


def general_2d_projection(from_bound, to_bound):
    s = basis_to_points(*from_bound)
    d = basis_to_points(*to_bound)
    return multiply_matrices(d, adjugate_matrix(s))


def project(matrix, xy_coordinate):
    x, y = xy_coordinate.xy
    a, b, c = multiply_matrix_vector(matrix, [x, y, 1])
    if c == 0:
        c = 0.000000001
    return Point(a / c, b / c)


def wgs84_bound_from_latlon_box(n, e, s, w, rot):
    a = (e + w) / 2
    b = (n + s) / 2
    squish = cos(deg2rad(b))
    x = squish * (e - w) / 2
    y = (n - s) / 2

    ne = Wgs84Coordinate(
        b + x * sin(deg2rad(rot)) + y * cos(deg2rad(rot)),
        a + (x * cos(deg2rad(rot)) - y * sin(deg2rad(rot))) / squish,
    )
    nw = Wgs84Coordinate(
        b - x * sin(deg2rad(rot)) + y * cos(deg2rad(rot)),
        a - (x * cos(deg2rad(rot)) + y * sin(deg2rad(rot))) / squish,
    )
    sw = Wgs84Coordinate(
        b - x * sin(deg2rad(rot)) - y * cos(deg2rad(rot)),
        a - (x * cos(deg2rad(rot)) - y * sin(deg2rad(rot))) / squish,
    )
    se = Wgs84Coordinate(
        b + x * sin(deg2rad(rot)) - y * cos(deg2rad(rot)),
        a + (x * cos(deg2rad(rot)) + y * sin(deg2rad(rot))) / squish,
    )
    return nw, ne, se, sw


def calibration_string_from_wgs84_bound(bound):
    return ",".join(
        (f"{corner.latitude:.5f},{corner.longitude:.5f}" for corner in bound)
    )


def initial_of_name(name):
    """Converts a name to initials and surname.

    Ensures all initials are capitalised, even if the
    first names aren't.

    Examples:

      >>> initial_of_name('Ram Chandra Giri')
      'R.C.Giri'
      >>> initial_of_name('Ram chandra Giri')
      'R.C.Giri'

    """
    parts = name.split()
    initials = [part[0].upper() for part in parts[:-1]]
    return ".".join(initials + [parts[-1]])


def check_cname_record(domain):
    try:
        resp = requests.get(
            f"https://one.one.one.one/dns-query?type=CNAME&name={urllib.parse.quote(domain)}",
            headers={"accept": "application/dns-json"},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception:
        return False

    data = resp.json()

    if data.get("Status") != 0:
        return False

    answer = data.get("Answer", [])
    for ans in answer:
        if ans.get("data") == "cname.routechoices.com." and ans.get("type") == 5:
            return True
    return False


def check_a_record(domain):
    try:
        resp = requests.get(
            f"https://one.one.one.one/dns-query?type=A&name={urllib.parse.quote(domain)}",
            headers={"accept": "application/dns-json"},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception:
        return False

    data = resp.json()

    if data.get("Status") != 0:
        return False

    answer = data.get("Answer", [])
    for ans in answer:
        # TODO: Use env variable for setting IP address
        if ans.get("data") == "95.217.207.162" and ans.get("type") == 1:
            return True
    return False


def check_dns_records(domain):
    return check_cname_record(domain) or check_a_record(domain)


def is_valid_pil_image(data):
    try:
        with Image.open(data) as img:
            img.verify()
            return True
    except (IOError, SyntaxError):
        return False


def delete_domain(domain):
    ngx_conf = os.path.join(
        settings.BASE_DIR, "nginx", "custom_domains", f"{domain}.conf"
    )
    crt_file = os.path.join(settings.BASE_DIR, "nginx", "certs", f"{domain}.crt")
    key_file = os.path.join(settings.BASE_DIR, "nginx", "certs", f"{domain}.key")
    act_file = os.path.join(
        settings.BASE_DIR, "nginx", "certs", "accounts", f"{domain}.key"
    )
    for file in (ngx_conf, crt_file, key_file, act_file):
        if os.path.exists(file):
            os.remove(file)


def distance_xy(ax, ay, bx, by):
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2)


def distance_between_locations(a, b):
    R = 6371009  # https://en.wikipedia.org/wiki/Great-circle_distance
    a = Wgs84Coordinate(a[1:])
    b = Wgs84Coordinate(b[1:])
    a_lat = math.radians(a.latitude)
    a_lon = math.radians(a.longitude)
    b_lat = math.radians(b.latitude)
    b_lon = math.radians(b.longitude)
    dlon = b_lon - a_lon
    dlat = b_lat - a_lat
    angle = (
        math.sin(dlat / 2) ** 2
        + math.cos(a_lat) * math.cos(b_lat) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(angle), math.sqrt(1 - angle))


def simplify_line(points, tolerance=11):
    if not points:
        return points
    new_coords = [points[0]]
    for p in points[1:-1]:
        last = new_coords[-1]
        dist = sqrt(pow(last[0] - p[0], 2) + pow(last[1] - p[1], 2))
        if dist > tolerance:
            new_coords.append(p)
    new_coords.append(points[-1])
    return new_coords


def gpsseuranta_encode_small_number(val):
    if val < -21:
        return chr(79 + val)
    if val < 5:
        return chr(86 + val)
    return chr(92 + val)


def gpsseuranta_encode_data(competitor_id, locations):
    out = ""
    chunks = []
    nb_pt_per_line = 29
    for i in range(len(locations) // nb_pt_per_line + 1):
        chunks.append(locations[i * nb_pt_per_line : (i + 1) * nb_pt_per_line])
    for chunk in chunks:
        prev_pt = None
        for pt in chunk:
            t = pt[0] - 1136073600
            lng = round(pt[2] * 5e4)
            lat = round(pt[1] * 1e5)
            if prev_pt is None:
                out += f"{competitor_id}.{t}_{lng}_{lat}."
            else:
                dt = t - prev_pt[0]
                dlat = lat - prev_pt[1]
                dlng = lng - prev_pt[2]
                if abs(dt) < 31 and abs(dlat) < 31 and abs(dlng) < 31:
                    out += gpsseuranta_encode_small_number(dt)
                    out += gpsseuranta_encode_small_number(dlng)
                    out += gpsseuranta_encode_small_number(dlat)
                    out += "."
                else:
                    out += f"{dt}_{dlng}_{dlat}."
            prev_pt = [t, lat, lng]
        out += "\n"
    return out


class XYMeters(Point):
    @property
    def wgs84_coordinate(self):
        mx, my = self.xy
        lon = (mx / ORIGIN_SHIFT) * 180.0
        lat = (
            180
            / math.pi
            * (
                2 * math.atan(math.exp((my / ORIGIN_SHIFT) * 180.0 * math.pi / 180.0))
                - math.pi / 2.0
            )
        )
        return Wgs84Coordinate(lat, lon)

    @property
    def latitude(self):
        return self.wgs84_coordinate.latitude

    @property
    def longitude(self):
        return self.wgs84_coordinate.longitude

    def __repr__(self):
        return f"mx: {self.x}, my:{self.y}"


class Wgs84Coordinate:
    latitude = None
    longitude = None

    def __init__(self, val, *args):
        if len(args) == 1:
            lat, lon = val, args[0]
        elif isinstance(val, Wgs84Coordinate):
            lat, lon = val.latlon
        elif isinstance(val, XYMeters):
            lat, lon = val.wgs84_coordinate
        elif isinstance(val, tuple) or isinstance(val, list):
            lat, lon = val
        elif isinstance(val, dict):
            lat, lon = (val.get("x"), val.get("y"))
        else:
            raise ValueError()

        self.latitude = lat
        self.longitude = lon

    @property
    def latlon(self):
        return self.latitude, self.longitude

    @property
    def xy_meters(self):
        lat, lon = self.latlon
        mx = lon * ORIGIN_SHIFT / 180.0
        my = (
            math.log(math.tan((90 + lat) * math.pi / 360.0))
            / (math.pi / 180.0)
            * ORIGIN_SHIFT
            / 180.0
        )
        return XYMeters(mx, my)

    def __repr__(self):
        return f"{self.latitude}, {self.longitude}"


def wgs84_to_meters(val):
    """
    Converts given lat/lon in WGS84 Datum to XY in Spherical Mercator
    EPSG:900913
    """
    return Wgs84Coordinate(val).xy_meters


def meters_to_wgs84(val):
    """
    Converts X/Y point from Spherical Mercator EPSG:900913 to lat/lon
    in WGS84 Datum
    """
    return XYMeters(val).wgs84_coordinate


def triangle_area(side_length):
    a, b, c = side_length
    return
