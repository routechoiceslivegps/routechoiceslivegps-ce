import bisect
import html
from operator import itemgetter

import arrow
from curl_cffi import requests

from routechoices.core.models import Competitor, Event, Map
from routechoices.lib.helpers import (
    Point,
    Wgs84Coordinate,
    get_remote_image_sizes,
    wgs84_bound_from_3_ref_points,
)
from routechoices.lib.other_gps_services.commons import (
    EventImportError,
    ThirdPartyTrackingSolutionWithProxy,
)


class GpsSeurantaNet(ThirdPartyTrackingSolutionWithProxy):
    GPSSEURANTA_EVENT_URL = "https://tulospalvelu.fi/gps/"
    name = "GPS Seuranta"
    slug = "gpsseuranta"

    def parse_init_data(self, uid):
        self.requests = requests.Session(impersonate="chrome")
        self.uid = uid
        event_url = f"{self.GPSSEURANTA_EVENT_URL}{uid}/init.txt"
        r = self.requests.get(event_url)
        if r.status_code != 200:
            raise EventImportError("API returned error code" + event_url)
        event_data = {"COMPETITOR": []}
        for line in r.text.split("\n"):
            try:
                key, val = line.strip().split(":")
                if key != "COMPETITOR":
                    event_data[key] = val
                else:
                    event_data[key].append(val)
            except ValueError:
                continue
        self.init_data = event_data

    def get_competitor_device_id_prefix(self):
        return "SEU_"

    def is_live(self):
        return self.init_data["LIVE"] == "1"

    def get_start_time(self):
        min_start_time = arrow.utcnow()
        for c_id, c in self.get_competitors_data().items():
            if c.start_time:
                min_start_time = min(min_start_time, c.start_time)
        return min_start_time

    def get_end_time(self):
        if self.is_live():
            return arrow.utcnow().shift(hours=1).datetime
        return arrow.utcnow().datetime

    def get_event(self):
        event = Event()
        event.slug = self.uid
        event.club = self.club
        event.name = html.unescape(self.init_data.get("RACENAME", self.uid))[:255]
        event.start_date = self.get_start_time()
        event.end_date = self.get_end_time()
        event.send_interval = int(self.init_data.get("GRABINTERVAL", 10))
        return event

    def get_map_url(self):
        return f"{self.GPSSEURANTA_EVENT_URL}{self.uid}/map"

    def get_map(self):
        map_url = self.get_map_url()
        try:
            length, size = get_remote_image_sizes(map_url)
        except Exception:
            return None

        calibration_string = self.init_data.get("CALIBRATION")
        if not size or not calibration_string:
            return None

        calibration_values = [float(val) for val in calibration_string.split("|")]
        print(calibration_values)
        wgs84_coords = list(
            Wgs84Coordinate((calibration_values[1 + i * 4], calibration_values[i * 4]))
            for i in range(3)
        )
        print(wgs84_coords)
        image_points = list(
            Point((calibration_values[2 + i * 4], calibration_values[3 + i * 4]))
            for i in range(3)
        )

        width, height = size
        map_obj = Map(
            width=width,
            height=height,
        )
        map_obj.bound = wgs84_bound_from_3_ref_points(wgs84_coords, image_points, size)
        return map_obj

    def get_competitor_devices_data(self, event):
        devices_data = {}
        data_url = f"{self.GPSSEURANTA_EVENT_URL}{self.uid}/data.lst"
        r = self.requests.get(data_url)
        if r.status_code == 200:
            data_raw = r.text
            for line in data_raw.split("\n"):
                line_data = line.strip().split(".")
                if len(line_data) == 0:
                    continue
                dev_id = line_data[0]
                if "_" in dev_id:
                    dev_id, _ = dev_id.split("_", 1)
                new_locations = self.decode_data_line(line_data[1:])
                if not devices_data.get(dev_id):
                    devices_data[dev_id] = []
                devices_data[dev_id] += new_locations

        cropped_devices_data = {}
        from_ts = event.start_date.timestamp()
        for dev_id, locations in devices_data.items():
            locations = sorted(locations, key=itemgetter(0))
            from_idx = bisect.bisect_left(locations, from_ts, key=itemgetter(0))
            locations = locations[from_idx:]
            cropped_devices_data[dev_id] = locations

        return cropped_devices_data

    def get_competitors_data(self):
        competitors = {}
        for c_raw in self.init_data.get("COMPETITOR", []):
            c_data = c_raw.strip().split("|")
            start_time = None
            start_time_raw = (
                f"{c_data[1]}"
                f"{c_data[2].zfill(4) if len(c_data[2]) < 5 else c_data[2].zfill(6)}"
            )
            try:
                if len(start_time_raw) == 12:
                    start_time = arrow.get(start_time_raw, "YYYYMMDDHHmm")
                else:
                    start_time = arrow.get(start_time_raw, "YYYYMMDDHHmmss")
            except Exception:
                pass
            else:
                start_time = start_time.shift(
                    minutes=-int(self.init_data.get("TIMEZONE", 0))
                ).datetime
            competitors[c_data[0]] = Competitor(
                name=c_data[3],
                short_name=c_data[4],
                start_time=start_time,
            )
        return competitors

    @classmethod
    def decode_data_line(cls, data):
        if not data:
            return []
        o_pt = data[0].split("_")
        if o_pt[0] == "*" or o_pt[1] == "*" or o_pt[2] == "*":
            return []
        loc = [
            int(o_pt[0]) + 1136073600,  # ts
            int(o_pt[2]) / 1e5,  # lat
            int(o_pt[1]) / 5e4,  # lon
        ]
        locs = [tuple(loc)]

        def get_char_index(c):
            ascii_index = ord(c)
            if ascii_index < 65:
                return ascii_index - 79
            if ascii_index < 97:
                return ascii_index - 86
            return ascii_index - 92

        for p in data[1:]:
            if len(p) < 3:
                continue
            if "_" in p:
                pt = p.split("_")
                if pt[0] == "*":
                    pt[0] = 0
                if pt[1] == "*":
                    pt[1] = 0
                if pt[2] == "*":
                    pt[2] = 0
                dt = int(pt[0])
                dlon = int(pt[1])
                dlat = int(pt[2])
            else:
                dt = get_char_index(p[0])
                dlon = get_char_index(p[1])
                dlat = get_char_index(p[2])
            loc[0] += dt
            loc[1] += dlat / 1e5
            loc[2] += dlon / 5e4
            locs.append(tuple(loc))
        return locs
