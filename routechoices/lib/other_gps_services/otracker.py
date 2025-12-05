import json
import tempfile

import arrow
from bs4 import BeautifulSoup
from curl_cffi import requests
from django.core.files.base import ContentFile
from PIL import Image

from routechoices.core.models import (PRIVACY_SECRET, Competitor, Device,
                                      Event, Map)
from routechoices.lib.helpers import Wgs84Coordinate, safe64encodedsha
from routechoices.lib.other_gps_services.commons import (
    CompetitorsImportError, EventImportError, MapsImportError,
    ThirdPartyTrackingSolution)


class OTracker(ThirdPartyTrackingSolution):
    slug = "otracker"
    name = "OTracker"

    def parse_init_data(self, uid):
        rp = requests.get(f"https://otracker.lt/events/{uid}")
        if rp.status_code != 200:
            raise EventImportError("API returned error code")
        soup = BeautifulSoup(rp.text, "html.parser")
        name = soup.find("title").string[:-13]

        r = requests.get(f"https://otracker.lt/data/events/{uid}")
        if r.status_code != 200:
            raise EventImportError("API returned error code")
        self.init_data = r.json()
        self.init_data["event"]["name"] = name
        self.uid = uid

    def get_or_create_event(self):
        start_date = arrow.get(
            self.init_data["event"]["replay_time"]["from_ts"]
        ).datetime
        end_date = arrow.get(self.init_data["event"]["replay_time"]["to_ts"]).datetime
        event, _ = Event.objects.get_or_create(
            club=self.club,
            slug=safe64encodedsha(self.uid)[:50],
            defaults={
                "name": self.init_data["event"]["name"][:255],
                "privacy": PRIVACY_SECRET,
                "start_date": start_date,
                "end_date": end_date,
            },
        )
        return event

    def get_or_create_event_maps(self, event):
        map_data = self.init_data["event"]["map_image"]
        map_url = map_data["url"]
        if not map_url:
            return []
        r = requests.get(map_url)
        if r.status_code != 200:
            raise MapsImportError("API returned error code")
        map_obj, _ = Map.objects.get_or_create(
            name=event.name,
            club=self.club,
        )
        try:
            map_file = ContentFile(r.content)
            map_opt = map_data["options"]
            bound = []
            for corner in ("tl", "tr", "br", "bl"):
                bound.append(
                    Wgs84Coordinate((map_opt[corner]["lat"], map_opt[corner]["lon"]))
                )
            map_obj.image.save("imported_image", map_file, save=False)
            im = Image.open(map_file)
            width, height = im.size
            map_obj.width = width
            map_obj.height = height
            map_obj.bound = bound
            map_obj.save()
        except Exception:
            map_obj.delete()
            raise MapsImportError("Error importing map")
        else:
            return [map_obj]

    def get_or_create_event_competitors(self, event):
        data_url = (
            f"https://otracker.lt/data/locations/history/{self.uid}?map_type=tileimage"
        )
        response = requests.get(data_url, stream=True)
        if response.status_code != 200:
            raise CompetitorsImportError("API returned error code")
        with tempfile.TemporaryFile() as lf:
            for block in response.iter_content(1024 * 8):
                if not block:
                    break
                lf.write(block)
            lf.flush()
            lf.seek(0)

            try:
                orig_device_map = json.load(lf)
            except Exception:
                raise CompetitorsImportError("Invalid JSON")
        device_map = {}
        event_offset_time = self.init_data["event"]["replay_time"]["from_ts"]
        try:
            for d in orig_device_map:
                device_map[d] = [
                    (x["fix_time"] + event_offset_time, x["lat"], x["lon"])
                    for x in orig_device_map[d]
                ]
        except Exception:
            raise CompetitorsImportError("Unexpected data structure")

        competitors = []
        for c_data in self.init_data["competitors"].values():
            start_time = c_data.get("sync_offset") + event_offset_time
            competitor, _ = Competitor.objects.get_or_create(
                name=c_data["name"],
                short_name=c_data["short_name"],
                event=event,
            )
            competitor.start_time = arrow.get(start_time).datetime
            dev_id = str(c_data["id"])
            dev_data = device_map.get(dev_id)
            dev_obj = None
            if dev_data:
                dev_obj, created = Device.objects.get_or_create(
                    aid="OTR_" + safe64encodedsha(f"{dev_id}:{self.uid}")[:8],
                    defaults={
                        "virtual": True,
                    },
                )
                dev_obj.add_locations(dev_data, reset=True)
                competitor.device = dev_obj
            competitor.save()
            competitors.append(competitor)
        return competitors
