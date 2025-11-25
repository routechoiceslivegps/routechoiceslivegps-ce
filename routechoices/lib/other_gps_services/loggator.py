from operator import itemgetter

import arrow
from curl_cffi import requests

from routechoices.core.models import Competitor, Event, Map
from routechoices.lib.helpers import get_remote_image_sizes
from routechoices.lib.other_gps_services.commons import (
    EventImportError,
    ThirdPartyTrackingSolutionWithProxy,
)


class Loggator(ThirdPartyTrackingSolutionWithProxy):
    LOGGATOR_EVENT_URL = "https://loggator.com/api/events/"
    name = "Loggator"
    slug = "loggator"

    def get_competitor_device_id_prefix(self):
        return "LOG_"

    def parse_init_data(self, uid):
        event_url = f"{self.LOGGATOR_EVENT_URL}{uid}.json"
        r = requests.get(event_url)
        if r.status_code != 200:
            raise EventImportError("API returned error code")
        self.init_data = r.json()
        self.uid = uid

    def get_event(self):
        event = Event()
        event.slug = self.init_data["event"]["slug"]
        event.club = self.club
        event.name = self.init_data["event"]["name"][:255]
        event.start_date = arrow.get(self.init_data["event"]["start_date"]).datetime
        event.end_date = arrow.get(self.init_data["event"]["end_date"]).datetime
        event.send_interval = 10
        return event

    def get_map_url(self):
        return self.init_data.get("map")["url"]

    def get_map(self, download_map=False):
        try:
            length, size = get_remote_image_sizes(self.get_map_url())
        except Exception:
            return None

        map_coords = self.init_data.get("map")["coordinates"]

        map_obj = Map()
        map_obj.width = size[0]
        map_obj.height = size[1]

        map_obj.bound = (
            (map_coords[pos]["lat"], map_coords[pos]["lng"])
            for pos in ("topLeft", "topRight", "bottomRight", "bottomLeft")
        )
        return map_obj

    def get_competitor_devices_data(self, event):
        devices_data = {}
        try:
            r = requests.get(self.init_data["tracks"], timeout=20)
        except Exception:
            return {}
        if r.status_code == 200:
            try:
                tracks_raw = r.json()["data"]
            except Exception:
                return {}
            tracks_pts = tracks_raw.split(";")
            for pt in tracks_pts:
                d = pt.split(",")
                dev_id = str(int(d[0]))
                if not devices_data.get(dev_id):
                    devices_data[dev_id] = []
                devices_data[dev_id].append((int(d[4]), float(d[1]), float(d[2])))

        cropped_devices_data = {}
        for dev_id, locations in devices_data.items():
            locations = sorted(locations, key=itemgetter(0))
            cropped_devices_data[dev_id] = locations

        return cropped_devices_data

    def get_competitors_data(self):
        competitors = {}
        for c_data in self.init_data["competitors"]:
            competitor = Competitor(
                name=c_data["name"],
                short_name=c_data["shortname"],
                start_time=arrow.get(c_data["start_time"]).datetime,
            )
            device_id = f"{c_data['device_id']}"
            competitors[device_id] = competitor
        return competitors
