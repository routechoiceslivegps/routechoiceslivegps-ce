import bisect
import json
import math
import re
import urllib.parse
from collections import defaultdict
from io import BytesIO
from operator import itemgetter

import arrow
import cairosvg
from curl_cffi import requests
from django.core.files.base import ContentFile
from PIL import Image, ImageDraw, ImageFont

from routechoices.core.models import Competitor, Event, Map
from routechoices.lib import cache
from routechoices.lib.duration_constants import (
    DURATION_FIVE_MINUTE,
)
from routechoices.lib.helpers import get_remote_image_sizes, initial_of_name, project
from routechoices.lib.other_gps_services.commons import (
    EventImportError,
    MapsImportError,
    ThirdPartyTrackingSolutionWithProxy,
)


class Livelox(ThirdPartyTrackingSolutionWithProxy):
    slug = "livelox"
    name = "Livelox"
    HEADERS = {
        "content-type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }

    def parse_init_data(self, uid):
        self.uid = uid
        cache_key = f"3rd_part_init_data:livelox:uid:{uid}"
        if cached := cache.get(cache_key):
            self.init_data = cached
            return
        if match := re.match(r"^(\d+)(-(\d+))?$", uid):
            details = {"classId": match[1]}
            if leg := match[3]:
                details["relayLeg"] = leg
        else:
            details = dict(urllib.parse.parse_qsl(uid))

        class_ids = []
        class_id = details.get("classId")
        if class_id:
            class_ids = [int(class_id)]

        relay_legs = []
        relay_leg = details.get("relayLeg")
        if relay_leg:
            relay_legs = [int(relay_leg)]

        post_data = json.dumps(
            {
                "classIds": class_ids,
                "courseIds": [],
                "relayLegs": relay_legs,
                "relayLegGroupIds": [],
            }
        )
        r = requests.post(
            "https://www.livelox.com/Data/ClassInfo",
            data=post_data,
            headers=self.HEADERS,
        )
        if r.status_code != 200:
            raise EventImportError(f"Can not fetch class info data {r.status_code}")
        self.init_data = r.json().get("general", {})

        if blobUrl := self.init_data.get("classBlobUrl"):
            r = requests.get(blobUrl)
        else:
            post_data = json.dumps(
                {
                    "classIds": class_ids,
                    "courseIds": [],
                    "relayLegs": relay_legs,
                    "relayLegGroupIds": [],
                    "includeMap": True,
                    "includeCourses": True,
                }
            )
            r = requests.post(
                "https://www.livelox.com/Data/ClassBlob",
                data=post_data,
                headers=self.HEADERS,
                timeout=60,
            )
        if r.status_code != 200:
            raise EventImportError("Can not fetch class blob data")
        self.init_data["xtra"] = r.json()
        self.init_data["relay_leg"] = int(relay_leg) if relay_leg else ""
        self.init_data["class_id"] = int(class_id)
        cache.set(cache_key, self.init_data, DURATION_FIVE_MINUTE)

    def get_competitor_device_id_prefix(self):
        return "LLX_"

    def is_live(self):
        return False

    def get_start_time(self):
        return arrow.get(
            self.init_data["class"]["event"]["timeInterval"]["start"]
        ).datetime

    def get_end_time(self):
        return arrow.get(
            self.init_data["class"]["event"]["timeInterval"]["end"]
        ).datetime

    def get_event(self):
        event = Event()
        event.club = self.club
        event_name = (
            f"{self.init_data['class']['event']['name']} - "
            f"{self.init_data['class']['name']}"
        )
        relay_leg = self.init_data["relay_leg"]
        if relay_leg:
            name = ""
            for leg in self.init_data["class"]["relayLegs"]:
                if leg.get("leg") == relay_leg:
                    name = leg["name"]
                    break
            else:
                name = f"#{relay_leg}"
            event_name += f" - {name}"
        event.name = event_name
        event.start_date = self.get_start_time()
        event.end_date = self.get_end_time()

        slug = str(self.init_data["class_id"])
        if relay_leg:
            slug += f"-{relay_leg}"
        event.slug = slug
        return event

    def get_map(self):
        try:
            map_data = self.init_data["xtra"]["map"]
            map_bounds = map_data["boundingQuadrilateral"]["vertices"]
            map_url = map_data["url"]
        except Exception:
            raise MapsImportError("Could not extract basic map info")

        map_obj = Map()
        map_obj.bound = map_bounds[::-1]

        length, size = get_remote_image_sizes(map_url)
        width, height = size
        map_obj.width = width
        map_obj.height = height

        return map_obj

    def get_map_file(self):
        try:
            map_data = self.init_data["xtra"]["map"]
            map_url = map_data["url"]
            map_resolution_orig = map_data["resolution"]
        except Exception:
            raise MapsImportError("Could not extract basic map info")

        map_obj = self.get_map()

        courses = []
        # first determine the course for this leg if is relay
        relay_leg = self.init_data["relay_leg"]
        groups = self.init_data["class"]["relayLegGroups"]
        if relay_leg and groups:
            course_ids = []
            for group in groups:
                if relay_leg in group["relayLegs"]:
                    course_ids += [c["id"] for c in group["courses"]]
            for course in self.init_data["xtra"]["courses"]:
                if course["id"] in course_ids:
                    courses.append(course)
        else:
            courses = self.init_data["xtra"]["courses"]

        r = requests.get(map_url)
        if r.status_code != 200:
            raise MapsImportError("Could not download image")

        img_blob = ContentFile(r.content)
        map_obj.image.save("imported_image", img_blob, save=False)

        im = Image.open(img_blob)
        width, height = im.size
        map_obj.width = width
        map_obj.height = height

        course_maps = []
        course_img_found = False

        upscale = 2
        map_drawing = Image.new(
            "RGBA",
            (map_obj.width * upscale, map_obj.height * upscale),
            (255, 255, 255, 0),
        )
        draw = ImageDraw.Draw(map_drawing)

        numbersLoc = defaultdict(list)
        for course in courses:
            for i, course_img_data in enumerate(course.get("courseImages")):
                course_bounds = course_img_data["boundingPolygon"]["vertices"]
                course_url = course_img_data["url"]

                course_map = Map(name=f"Course {i+1}")
                course_map.bound = course_bounds[::-1]

                r = requests.get(course_url)
                if r.status_code != 200:
                    raise MapsImportError("Could not download image")

                out = BytesIO()
                cairosvg.svg2png(
                    bytestring=r.content, write_to=out, unsafe=True, scale=4
                )

                img_blob = ContentFile(out.getbuffer())

                course_map.image.save("imported_image", img_blob, save=False)
                max_pixels = Image.MAX_IMAGE_PIXELS
                Image.MAX_IMAGE_PIXELS = None
                im = Image.open(img_blob)
                Image.MAX_IMAGE_PIXELS = max_pixels
                width, height = im.size
                course_map.width = width
                course_map.height = height
                course_maps.append(course_map)
                course_img_found = True
            if not course.get("courseImages") and not course_img_found:
                route = course["controls"]
                map_resolution = (
                    map_resolution_orig
                    * route[0]["control"].get("mapScale", 15000)
                    / 15000
                )

                map_projection = self.init_data["xtra"]["map"].get("projection")
                map_angle = 0
                if map_projection:
                    matrix = (
                        map_projection["matrix"][0]
                        + map_projection["matrix"][1]
                        + map_projection["matrix"][2]
                    )
                    p1x, p1y = project(matrix, 0, 0)
                    p2x, p2y = project(matrix, map_obj.width, 0)
                    map_angle = math.atan2(p2y - p1y, p2x - p1x)
                    map_angle = (
                        map_angle - math.floor(map_angle // (2 * math.pi)) * 2 * math.pi
                    )

                line_width = int(8 * map_resolution * upscale)
                circle_radius = 40 * map_resolution
                line_color = (185, 42, 247, 180)
                ctrls = [
                    map_obj.wsg84_to_map_xy(
                        c["control"]["position"]["latitude"],
                        c["control"]["position"]["longitude"],
                    )
                    for c in route
                ]
                for i, ctrl in enumerate(ctrls[:-1]):
                    pt = ctrl
                    next_pt = ctrls[i + 1]
                    angle = math.atan2(next_pt[1] - pt[1], next_pt[0] - pt[0])
                    if i == 0:
                        # draw start triangle
                        draw.line(
                            [
                                int(
                                    upscale * (pt[0] + circle_radius * math.cos(angle))
                                ),
                                int(
                                    upscale * (pt[1] + circle_radius * math.sin(angle))
                                ),
                                int(
                                    upscale
                                    * (
                                        pt[0]
                                        + circle_radius
                                        * math.cos(angle + 2 * math.pi / 3)
                                    )
                                ),
                                int(
                                    upscale
                                    * (
                                        pt[1]
                                        + circle_radius
                                        * math.sin(angle + 2 * math.pi / 3)
                                    )
                                ),
                                int(
                                    upscale
                                    * (
                                        pt[0]
                                        + circle_radius
                                        * math.cos(angle - 2 * math.pi / 3)
                                    )
                                ),
                                int(
                                    upscale
                                    * (
                                        pt[1]
                                        + circle_radius
                                        * math.sin(angle - 2 * math.pi / 3)
                                    )
                                ),
                                int(
                                    upscale * (pt[0] + circle_radius * math.cos(angle))
                                ),
                                int(
                                    upscale * (pt[1] + circle_radius * math.sin(angle))
                                ),
                            ],
                            fill=line_color,
                            width=line_width,
                            joint="curve",
                        )
                    # draw line between controls
                    if lines := route[i].get("connectionLines"):
                        for line in lines:
                            start = map_obj.wsg84_to_map_xy(
                                line["start"]["latitude"],
                                line["start"]["longitude"],
                            )
                            end = map_obj.wsg84_to_map_xy(
                                line["end"]["latitude"],
                                line["end"]["longitude"],
                            )
                            draw.line(
                                [
                                    int(start[0] * upscale),
                                    int(start[1] * upscale),
                                    int(end[0] * upscale),
                                    int(end[1] * upscale),
                                ],
                                fill=line_color,
                                width=line_width,
                            )
                    else:
                        draw.line(
                            [
                                int(
                                    upscale * (pt[0] + circle_radius * math.cos(angle))
                                ),
                                int(
                                    upscale * (pt[1] + circle_radius * math.sin(angle))
                                ),
                                int(
                                    upscale
                                    * (next_pt[0] - circle_radius * math.cos(angle))
                                ),
                                int(
                                    upscale
                                    * (next_pt[1] - circle_radius * math.sin(angle))
                                ),
                            ],
                            fill=line_color,
                            width=line_width,
                        )
                    # draw controls
                    if gaps := route[i + 1]["control"].get("circleGaps"):
                        for j, gap in enumerate(gaps):
                            start = gap["startAngle"] + gap["distance"]
                            end = (
                                (gaps[0]["startAngle"] + 2 * math.pi)
                                if j == (len(gaps) - 1)
                                else gaps[j + 1]["startAngle"]
                            )
                            draw.arc(
                                [
                                    int(upscale * (next_pt[0] - circle_radius)),
                                    int(upscale * (next_pt[1] - circle_radius)),
                                    int(upscale * (next_pt[0] + circle_radius)),
                                    int(upscale * (next_pt[1] + circle_radius)),
                                ],
                                fill=line_color,
                                width=line_width,
                                start=(-end) * 180 / math.pi,
                                end=(-start) * 180 / math.pi,
                            )
                    else:
                        draw.ellipse(
                            [
                                int(upscale * (next_pt[0] - circle_radius)),
                                int(upscale * (next_pt[1] - circle_radius)),
                                int(upscale * (next_pt[0] + circle_radius)),
                                int(upscale * (next_pt[1] + circle_radius)),
                            ],
                            outline=line_color,
                            width=line_width,
                        )
                    if i + 2 < len(ctrls):
                        text = route[i + 1].get("controlNumberText", f"{i+1}")
                        if controlLoc := route[i + 1].get("controlNumberPosition"):
                            loc = map_obj.wsg84_to_map_xy(
                                controlLoc["latitude"],
                                controlLoc["longitude"],
                            )
                            loc = [int(x * upscale) for x in loc]
                        else:
                            prev_ctrl = ctrls[i]
                            curr_ctrl = ctrls[i + 1]
                            next_ctrl = ctrls[i + 2]

                            prev_angle = math.atan2(
                                prev_ctrl[1] - curr_ctrl[1], prev_ctrl[0] - curr_ctrl[0]
                            )
                            next_angle = math.atan2(
                                next_ctrl[1] - curr_ctrl[1], next_ctrl[0] - curr_ctrl[0]
                            )
                            angle_diff = (
                                (next_angle - prev_angle + math.pi) % (2 * math.pi)
                            ) - math.pi
                            avg_angle = (prev_angle + angle_diff / 2) % (2 * math.pi)
                            opp_angle = avg_angle + math.pi
                            loc = (
                                int(
                                    upscale
                                    * (
                                        curr_ctrl[0]
                                        + math.cos(opp_angle) * 2 * circle_radius
                                    )
                                ),
                                int(
                                    upscale
                                    * (
                                        curr_ctrl[1]
                                        + math.sin(opp_angle) * 2 * circle_radius
                                    )
                                ),
                            )

                        numbersLoc[text].append((loc[0], loc[1]))
                    # draw finish
                    if i == (len(ctrls) - 2):
                        inner_circle_radius = 30 * map_resolution
                        draw.ellipse(
                            [
                                int(upscale * (next_pt[0] - inner_circle_radius)),
                                int(upscale * (next_pt[1] - inner_circle_radius)),
                                int(upscale * (next_pt[0] + inner_circle_radius)),
                                int(upscale * (next_pt[1] + inner_circle_radius)),
                            ],
                            outline=line_color,
                            width=line_width,
                        )
                fnt = ImageFont.truetype(
                    "routechoices/assets/fonts/arial.ttf",
                    int(upscale * circle_radius * 2),
                )
                finalLoc = defaultdict(list)
                for text, locs in numbersLoc.items():
                    left, top, right, bottom = fnt.getbbox(text)
                    width = right - left
                    height = bottom - top
                    text_size = (width, height)

                    def do_overlap(a, b):
                        if abs(a[0] - b[0]) > text_size[0]:
                            return False
                        if abs(a[1] - b[1]) > text_size[1]:
                            return False
                        return True

                    def simplify_overlaps(arr):
                        if not arr:
                            return []
                        a = arr[0]
                        arr_b = []
                        for b in arr[1:]:
                            if not do_overlap(a, b):
                                arr_b.append(b)
                        return [a] + simplify_overlaps(arr_b)

                    finalLoc[text] = simplify_overlaps(locs)

                for text, locs in finalLoc.items():
                    for loc in locs:
                        draw.text(
                            loc,
                            text,
                            font=fnt,
                            fill=line_color,
                            anchor="mm",
                        )

                out_buffer = BytesIO()
                params = {
                    "dpi": (72, 72),
                }
                map_drawing.save(out_buffer, "PNG", **params)
                f_new = ContentFile(out_buffer.getvalue())
                course_map = Map(name=f"Course {i+1}")
                course_map.calibration_string = map_obj.calibration_string
                course_map.image.save("imported_image", f_new, save=False)
                course_map.width = map_drawing.width
                course_map.height = map_drawing.height
                course_maps.append(course_map)
        if course_maps:
            try:
                map_obj = map_obj.overlay(*course_maps)
            except Exception:
                pass
        return ContentFile(map_obj.data)

    def get_competitor_devices_data(self, event):
        participant_data = [
            d for d in self.init_data["xtra"]["participants"] if d.get("routeData")
        ]
        time_offset = 22089888e5
        map_projection = self.init_data["xtra"]["map"].get("projection")
        if map_projection:
            matrix = (
                map_projection["matrix"][0]
                + map_projection["matrix"][1]
                + map_projection["matrix"][2]
            )

        map_obj = self.get_map()

        devices_data = {}
        for p in participant_data:
            pts = []
            p_data64 = p["routeData"]
            d = LiveloxBase64Reader(p_data64)
            pts_raw = d.readWaypoints()
            for pt in pts_raw:
                if map_projection:
                    px, py = project(matrix, pt[1] / 10, pt[2] / 10)
                    latlon = map_obj.map_xy_to_wsg84(px, py)
                    pts.append(
                        (int((pt[0] - time_offset) / 1e3), latlon["lat"], latlon["lon"])
                    )
                else:
                    pts.append(
                        (int((pt[0] - time_offset) / 1e3), pt[2] / 1e6, pt[1] / 1e6)
                    )
            if pts:
                devices_data[p["id"]] = pts

        cropped_devices_data = {}
        from_ts = event.start_date.timestamp()
        for dev_id, locations in devices_data.items():
            locations = sorted(locations, key=itemgetter(0))
            from_idx = bisect.bisect_left(locations, from_ts, key=itemgetter(0))
            locations = locations[from_idx:]
            cropped_devices_data[str(dev_id)] = locations

        return cropped_devices_data

    def get_competitors_data(self):
        competitors = {}
        participant_data = [
            d for d in self.init_data["xtra"]["participants"] if d.get("routeData")
        ]
        for p in participant_data:
            c_name = f"{p.get('firstName')} {p.get('lastName')}"
            c_sname = initial_of_name(c_name)
            competitors[str(p["id"])] = Competitor(
                name=c_name,
                short_name=c_sname,
            )
        return competitors


class LiveloxBase64Reader:
    base64util = {
        "usableBitsPerByte": 6,
        "headerBits": 8,
        "numberToLetter": (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        ),
        "pow2": [0] * 64,
        "bitLengthMaxValues": [0] * 65,
        "letterToNumber": {},
    }
    for i in range(64):
        base64util["pow2"][i] = 2**i
    for i in range(1, 65):
        base64util["bitLengthMaxValues"][i] = (
            base64util["bitLengthMaxValues"][i - 1] + base64util["pow2"][i - 1]
        )
    for i, letter in enumerate(base64util["numberToLetter"]):
        base64util["letterToNumber"][letter] = i
    base64util["letterToNumber"]["="] = 0

    def __init__(self, data):
        self.length = len(data)
        self.byte_array = [0] * self.length
        self.current_byte_pos = 0
        self.current_bit_pos = 0
        self.bits_read_in_current_byte = 0
        self.next_bit_position = None
        self.next_bits_read_in_current_byte = None
        self.byte = None
        self.value = None
        self.bits_left_to_read = None
        self.i = None
        self.bytes_read = None
        self.header = None

        for i in range(self.length):
            self.byte_array[i] = self.base64util["letterToNumber"][data[i]]

    def read_n_bits(self, n):
        self.value = 0
        self.bits_left_to_read = self.bits_read_in_current_byte + n
        self.bytes_read = 0
        while self.bits_left_to_read > 0:
            self.bits_left_to_read -= 6
            self.bytes_read += 1
        self.next_bit_position = self.current_bit_pos + n
        self.next_bits_read_in_current_byte = self.next_bit_position % 6
        self.i = 0
        while self.i < self.bytes_read:
            self.byte = self.byte_array[self.i + self.current_byte_pos]
            if self.i == 0:
                self.byte &= self.base64util["bitLengthMaxValues"][
                    6 - self.bits_read_in_current_byte
                ]
            if self.i < self.bytes_read - 1:
                self.value += (
                    self.base64util["pow2"][
                        (self.bytes_read - self.i - 1) * 6
                        - (
                            0
                            if self.next_bits_read_in_current_byte == 0
                            else (6 - self.next_bits_read_in_current_byte)
                        )
                    ]
                    * self.byte
                )
            else:
                if self.next_bits_read_in_current_byte > 0:
                    self.byte >>= 6 - self.next_bits_read_in_current_byte
                self.value += self.byte
            self.i += 1
        self.current_bit_pos = self.next_bit_position
        self.bits_read_in_current_byte = self.next_bits_read_in_current_byte
        self.current_byte_pos += (
            self.bytes_read
            if self.bits_read_in_current_byte == 0
            else (self.bytes_read - 1)
        )
        return self.value

    def read_value(self):
        self.header = self.read_n_bits(8)
        return (
            (-1 if (self.header & 2) else 1)
            * (1000 if (self.header & 1) else 1)
            * self.read_n_bits(self.header >> 2)
        )

    def readWaypoints(self):
        k = self.read_value()
        pts = []
        t = 0
        lat = 0
        lng = 0
        for _ in range(k):
            t += self.read_value()
            lat += self.read_value()
            lng += self.read_value()
            pts.append((t, lat, lng))
        return pts
