from curl_cffi import requests
from django.contrib.auth.models import User
from django.core.files.base import ContentFile

from routechoices.core.models import (
    PRIVACY_SECRET,
    Club,
    Competitor,
    Device,
    Event,
    Map,
    MapAssignation,
)
from routechoices.lib.helpers import epoch_to_datetime, safe64encodedsha


class EventImportError(Exception):
    pass


class MapsImportError(Exception):
    pass


class CompetitorsImportError(Exception):
    pass


class ThirdPartyTrackingSolution:
    name = None
    slug = None

    def __init__(self):
        self.uid = None
        self.club = self.get_or_create_club()

    def get_or_create_club(self):
        if not self.name or not self.slug:
            raise ValueError()
        admins = User.objects.filter(is_superuser=True)
        club, created = Club.objects.get_or_create(
            slug=self.slug, defaults={"name": self.name}
        )
        if created:
            club.admins.set(admins)
            club.save()
        return club

    def parse_init_data(self, uid):
        raise NotImplementedError()

    def get_or_create_event(self):
        raise NotImplementedError()

    def get_or_create_event_maps(self, event):
        raise NotImplementedError()

    def assign_maps_to_event(self, event):
        maps = self.get_or_create_event_maps(event)
        if maps:
            event.map = maps[0]
            for xtra_map in maps[1:]:
                MapAssignation.object.get_or_create(
                    name=xtra_map.name,
                    map=xtra_map,
                    event=event,
                )

    def get_or_create_event_competitors(self, event):
        raise NotImplementedError()

    def assign_competitors_to_event(self, event):
        start_date = None
        end_date = None
        competitors = self.get_or_create_event_competitors(event)
        for competitor in competitors:
            if competitor.device and competitor.device.location_count > 0:
                locations = competitor.device.locations
                from_date = locations[0][0]
                to_date = locations[-1][0]
                if not start_date or start_date > from_date:
                    start_date = from_date
                if not end_date or end_date < to_date:
                    end_date = to_date
        if start_date and end_date:
            event.start_date = epoch_to_datetime(start_date)
            event.end_date = epoch_to_datetime(end_date)

    def import_event(self, uid):
        self.parse_init_data(uid)
        event = self.get_or_create_event()
        self.assign_maps_to_event(event)
        self.assign_competitors_to_event(event)
        event.save()
        return event


class ThirdPartyTrackingSolutionWithProxy(ThirdPartyTrackingSolution):
    def get_competitor_device_id_prefix(self):
        raise NotImplementedError()

    def get_event(self):
        raise NotImplementedError()

    def get_map_url(self):
        raise NotImplementedError()

    def get_map(self):
        raise NotImplementedError()

    def get_map_file(self):
        r = requests.get(self.get_map_url())
        if r.status_code == 200:
            return ContentFile(r.content)
        return None

    def get_competitor_devices_data(self, event):
        raise NotImplementedError()

    def get_competitors_data(self):
        raise NotImplementedError()

    def get_or_create_event(self):
        tmp_event = self.get_event()
        event, _ = Event.objects.get_or_create(
            club=self.club,
            slug=tmp_event.slug,
            defaults={
                "name": tmp_event.name,
                "privacy": PRIVACY_SECRET,
                "start_date": tmp_event.start_date,
                "end_date": tmp_event.end_date,
            },
        )
        return event

    def get_or_create_event_maps(self, event):
        tmp_map = self.get_map()
        if not tmp_map:
            raise MapsImportError("Error importing map")
        map_obj, _ = Map.objects.get_or_create(
            name=f"{event.name} ({self.uid})",
            club=self.club,
            defaults={
                "width": tmp_map.width,
                "height": tmp_map.height,
                "calibration_string": tmp_map.calibration_string,
            },
        )
        if map_file := self.get_map_file():
            map_obj.image.save("map", map_file)
        return [map_obj]

    def get_or_create_event_competitors(self, event):
        devices_data = self.get_competitor_devices_data(event)
        device_map = {}
        for dev_id, locations in devices_data.items():
            dev_hash = safe64encodedsha(f"{dev_id}:{self.uid}")[:8]
            dev_hash = f"{self.get_competitor_device_id_prefix()}{dev_hash}"
            dev_obj, created = Device.objects.get_or_create(
                aid=dev_hash,
                defaults={"virtual": True},
            )
            dev_obj.add_locations(locations, reset=True, save=False)
            device_map[dev_id] = dev_obj

        competitors_map = self.get_competitors_data()
        competitors = []
        for cid, tmp_competitor in competitors_map.items():
            competitor, _ = Competitor.objects.get_or_create(
                name=tmp_competitor.name,
                short_name=tmp_competitor.short_name,
                start_time=tmp_competitor.start_time,
                event=event,
            )
            device = device_map.get(cid)
            if device:
                device.save()
                competitor.device = device
            competitor.save()
            competitors.append(competitor)

        return competitors
