import json
import re
import tempfile

import arrow
from curl_cffi import requests
from django.core.files.base import ContentFile
from PIL import Image

from routechoices.core.models import PRIVACY_SECRET, Competitor, Device, Event, Map
from routechoices.lib.helpers import safe64encodedsha
from routechoices.lib.other_gps_services.commons import (
    CompetitorsImportError,
    EventImportError,
    MapsImportError,
    ThirdPartyTrackingSolution,
)


class SportRec(ThirdPartyTrackingSolution):
    slug = "sportrec"
    name = "SportRec"

    def parse_init_data(self, uid):
        r = requests.get(f"https://sportrec.eu/gps/{uid}")
        if r.status_code != 200:
            raise EventImportError("API returned error code")
        page = r.text
        if match := re.search(r"'competitioninfo/(?P<id>[^'?]+)", page):
            event_id = match.group("id")
        else:
            raise EventImportError("Cannot determine event id")
        r = requests.get(f"https://sportrec.eu/gps/competitioninfo/{event_id}")
        if r.status_code != 200:
            raise EventImportError("Cannot fetch event data")
        self.init_data = r.json()
        self.uid = uid

    def get_or_create_event(self):
        event_name = self.init_data["competition"]["title"]
        event, _ = Event.objects.get_or_create(
            club=self.club,
            slug=self.uid,
            defaults={
                "name": event_name[:255],
                "privacy": PRIVACY_SECRET,
                "start_date": arrow.get(
                    self.init_data["competition"]["time_start"]
                ).datetime,
                "end_date": arrow.get(
                    self.init_data["competition"]["time_finish"]
                ).datetime,
            },
        )
        return event

    def get_or_create_event_maps(self, event):
        if not self.init_data["hasMap"]:
            return []
        map_url = f"https://sportrec.eu/gps/map/{self.init_data['competition']['hashlink']}/{self.init_data['competition']['id']}.png"
        map_obj, created = Map.objects.get_or_create(
            name=event.name,
            club=self.club,
        )
        r = requests.get(map_url)
        if r.status_code != 200:
            map_obj.delete()
            raise MapsImportError("API returned error code")
        try:
            map_file = ContentFile(r.content)
            map_obj.image.save("imported_image", map_file, save=False)
            im = Image.open(map_file)
            width, height = im.size
            map_obj.width = width
            map_obj.height = height
            map_obj.image.update_dimension_fields(force=True)
            bound = self.init_data["competition"]["bounds"].values()
            map_obj.bound = bound
            map_obj.save()
        except Exception:
            map_obj.delete()
            raise MapsImportError("Error importing map")
        else:
            return [map_obj]

    def get_or_create_event_competitors(self, event):
        data_url = f"https://sportrec.eu/gps/competitionhistory2/{self.init_data['competition']['hashlink']}?live=0"
        response = requests.get(data_url, stream=True)
        if response.status_code != 200:
            raise CompetitorsImportError(
                f"API returned error code {response.status_code}"
            )

        device_map = {}
        with tempfile.TemporaryFile() as lf:
            for block in response.iter_content(1024 * 8):
                if not block:
                    break
                lf.write(block)
            lf.flush()
            lf.seek(0)
            try:
                device_data = json.load(lf)
            except Exception:
                raise CompetitorsImportError("Invalid JSON")
        try:
            for dev_id in device_data.keys():
                locations = []
                for time in device_data[dev_id].keys():
                    pos = device_data[dev_id][time]["p"]
                    locations.append(
                        (int(float(time) / 1e3), float(pos[0]), float(pos[1]))
                    )
                device_map[dev_id] = locations
        except Exception:
            raise CompetitorsImportError("Unexpected data structure")
        competitors = []
        for c_data in self.init_data["participants"].values():
            competitor, _ = Competitor.objects.get_or_create(
                name=c_data["fullname"],
                short_name=c_data["shortname"],
                event=event,
            )
            dev_id = c_data["device_id"]
            dev_data = device_map.get(dev_id)
            dev_obj = None
            if dev_data:
                dev_obj, created = Device.objects.get_or_create(
                    aid="SPR_"
                    + safe64encodedsha(
                        f"{dev_id}:{self.init_data['competition']['hashlink']}"
                    )[:8],
                    defaults={
                        "virtual": True,
                    },
                )
                dev_obj.add_locations(dev_data, reset=True)
                competitor.device = dev_obj
            if start_time := c_data.get("time_start"):
                competitor.start_time = arrow.get(start_time).datetime
            competitor.save()
            competitors.append(competitor)
        return competitors
