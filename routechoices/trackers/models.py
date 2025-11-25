import bisect
import logging
from operator import itemgetter

import flag
import gps_data_codec
import gpxpy
import gpxpy.gpx
from allauth.account.models import EmailAddress
from dateutil.parser import parse as parse_date
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.validators import MaxValueValidator, validate_slug
from django.db import models
from django.db.models import F, Max, Model
from django.utils.timezone import now

from routechoices.core.models import Club as Group
from routechoices.core.models import gps_seuranta_net_client
from routechoices.lib.helpers import (
    COUNTRIES,
    country_code_at_coords,
    epoch_to_datetime,
    get_current_site,
    random_device_id,
    simplify_periods,
    timezone_at_coords,
)
from routechoices.lib.jxl import register_jxl_opener
from routechoices.lib.validators import (
    validate_latitude,
    validate_longitude,
)

register_jxl_opener()

logger = logging.getLogger(__name__)

EVENT_CACHE_INTERVAL_LIVE = 5
EVENT_CACHE_INTERVAL_ARCHIVED = 7 * 24 * 3600

WEBP_MAX_SIZE = 16383

LOCATION_TIMESTAMP_INDEX = 0
LOCATION_LATITUDE_INDEX = 1
LOCATION_LONGITUDE_INDEX = 2

WGS84_LATITUDE_INDEX = 0
WGS84_LONGITUDE_INDEX = 1

END_FREE_OCLUB = parse_date("2026-01-01T00:00:00Z")


class LocationsField:
    pass


class LocationField:
    pass


class BoundField:
    pass


class CompressedLocationsField:
    pass


class SomewhereOnEarth:
    def get_earth_coords(self):
        raise NotImplementedError("You must implement the get_earth_coords method")

    @property
    def country_code(self):
        if coords := self.earth_coords:
            return country_code_at_coords(coords)
        return None

    @property
    def timezone(self):
        if coords := self.earth_coords:
            return timezone_at_coords(coords)
        return None

    @property
    def country_name(self):
        if cc := self.country_code:
            return COUNTRIES.get(cc, "")
        return "Unknown"

    @property
    def country_flag(self):
        if cc := self.country_code:
            return flag.flag(cc)
        return "🌍"


class HasLocationStorage(Model, SomewhereOnEarth):
    locations = LocationsField()
    _first_location = LocationField()
    _last_location = LocationField()
    _location_count = models.PositiveIntegerField()
    _bound = BoundField(null=True)

    def add_locations(self, locations):
        raise NotImplementedError

    def add_location(self, location):
        return self.add_locations([location])


class Gps(HasLocationStorage):
    creation_date = models.DateTimeField(auto_now_add=True)
    modification_date = models.DateTimeField(auto_now=True)
    public_id = models.CharField(
        default=random_device_id,
        max_length=12,
        unique=True,
        db_index=True,
        validators=[
            validate_slug,
        ],
    )
    battery_level = models.PositiveIntegerField(
        null=True, default=None, validators=[MaxValueValidator(100)], blank=True
    )
    owners = models.ManyToManyField(
        Group,
        through="GpsOwnership",
        related_name="devices",
        through_fields=("device", "group"),
    )


class GpsFile(HasLocationStorage):
    creation_date = models.DateTimeField(auto_now_add=True)
    modification_date = models.DateTimeField(auto_now=True)
    user_agent = models.CharField(max_length=200, blank=True)
    virtual = models.BooleanField(default=False)

    gpsseuranta_known = models.BooleanField(default=False)
    gpsseuranta_relay_until = models.DateTimeField(null=True, blank=True)

    _last_location_datetime = models.DateTimeField(
        null=True, blank=True, editable=False
    )
    _last_location_latitude = models.DecimalField(
        null=True, blank=True, editable=False, max_digits=10, decimal_places=5
    )
    _last_location_longitude = models.DecimalField(
        null=True, blank=True, editable=False, max_digits=10, decimal_places=5
    )
    _location_count = models.PositiveIntegerField(editable=False, default=0)

    class Meta:
        ordering = ["aid"]
        verbose_name = "device"
        verbose_name_plural = "devices"

    def __str__(self):
        return self.aid

    def get_display_str(self, club=None):
        original_device = self.get_original_device()
        if original_device:
            device = original_device
        else:
            device = self

        owner = None
        # Use this instead of .filter(club=club).first()
        # as club_ownership are already loaded avoiding n+1 query
        for ownership in device.club_ownerships.all():
            if ownership.club_id == club.id:
                owner = ownership
                break
        return (
            f"{device.aid}{f' ({owner.nickname})' if owner and owner.nickname else ''}"
            f"{'*' if original_device else ''}"
        )

    def get_nickname(self, club):
        original_device = self.get_original_device()
        if original_device:
            device = original_device
        else:
            device = self
        owner = None
        # Use this instead of .filter(club=club).first()
        # as club_ownership are already loaded avoiding n+1 query
        for ownership in device.club_ownerships.all():
            if ownership.club_id == club.id:
                owner = ownership
                break
        if owner and owner.nickname:
            return owner.nickname
        return ""

    @property
    def battery_level_0_4(self):
        # 0: 0-15
        # 1: 15-35
        # 2: 35-55
        # 3: 55-75
        # 4: 75-100
        return min(4, round((self.battery_level - 5) / 20))

    @property
    def battery_level_text(self):
        return ["empty", "quarter", "half", "three-quarters", "full"][
            self.battery_level_0_4
        ]

    @property
    def locations(self):
        if not self.locations_encoded:
            return []
        return gps_data_codec.decode(self.locations_encoded)

    def erase_locations(self):
        self.locations_encoded = ""
        self._last_location_datetime = None
        self._last_location_latitude = None
        self._last_location_longitude = None
        self._location_count = 0

    def update_cached_data(self):
        self._location_count = self.location_count
        if self._location_count > 0:
            last_loc = self.locations[-1]
            self._last_location_datetime = epoch_to_datetime(
                last_loc[LOCATION_TIMESTAMP_INDEX]
            )
            self._last_location_latitude = last_loc[LOCATION_LATITUDE_INDEX]
            self._last_location_longitude = last_loc[LOCATION_LONGITUDE_INDEX]
        else:
            self._last_location_datetime = None
            self._last_location_latitude = None
            self._last_location_longitude = None

    def get_locations_between_dates(self, from_date, end_date, /, *, encode=False):
        from_ts = int(round(from_date.timestamp()))
        end_ts = int(round(end_date.timestamp()))

        if encode:
            return gps_data_codec.extract_encoded_interval(
                self.locations_encoded, from_ts, end_ts
            )

        locs = self.locations
        if locs and locs[0][0] >= from_ts:
            from_idx = 0
        else:
            from_idx = bisect.bisect_left(locs, from_ts, key=itemgetter(0))
        if locs and locs[-1][0] <= end_ts:
            end_idx = None
        else:
            end_idx = bisect.bisect_right(locs, end_ts, key=itemgetter(0))
        return locs[from_idx:end_idx], len(locs)

    def get_active_periods(self):
        periods_used = []
        competitors = self.competitor_set.all()
        for competitor in competitors:
            event = competitor.event
            start = event.start_date
            if competitor.start_time:
                start = competitor.start_time
            periods_used.append((start, event.end_date))
        return simplify_periods(periods_used)

    def remove_unused_location(self, /, *, until, save=False):
        location_count = self.location_count
        if location_count == 0:
            return
        periods_used = [(until, max(now(), self.last_location_datetime))]
        periods_used += self.get_active_periods()
        periods_to_keep = simplify_periods(periods_used)

        valid_locs = self.get_locations_over_periods(periods_to_keep)
        deleted_location_count = location_count - len(valid_locs)
        if deleted_location_count:
            self.add_locations(valid_locs, reset=True, save=save)

    def get_locations_over_periods(self, periods):
        locs = []
        for period in periods:
            p_locs, _ = self.get_locations_between_dates(*period)
            locs += p_locs
        return locs

    def gpx(self, from_date, end_date):
        current_site = get_current_site()
        gpx = gpxpy.gpx.GPX()
        gpx.creator = current_site.name
        gpx_track = gpxpy.gpx.GPXTrack()
        gpx.tracks.append(gpx_track)

        gpx_segment = gpxpy.gpx.GPXTrackSegment()
        locs, n = self.get_locations_between_dates(from_date, end_date)
        for location in locs:
            gpx_segment.points.append(
                gpxpy.gpx.GPXTrackPoint(
                    location[LOCATION_LATITUDE_INDEX],
                    location[LOCATION_LONGITUDE_INDEX],
                    time=epoch_to_datetime(location[LOCATION_TIMESTAMP_INDEX]),
                )
            )
        gpx_track.segments.append(gpx_segment)
        return gpx.to_xml()

    def add_locations(self, new_locations, /, *, reset=False, save=True):
        if reset:
            self.erase_locations()

        if not new_locations:
            if save:
                self.save()
            return

        sorted_new_locations = list(
            sorted(new_locations, key=itemgetter(LOCATION_TIMESTAMP_INDEX))
        )
        freshness_cutoff = None
        if self._last_location_datetime:
            freshness_cutoff = self._last_location_datetime.timestamp()

        fresh_new_locs = []
        old_new_locs = []
        prev_ts = None
        for loc in sorted_new_locations:
            try:
                ts = int(loc[LOCATION_TIMESTAMP_INDEX])
            except Exception:
                continue
            if ts == prev_ts:
                continue
            try:
                lat = round(float(loc[LOCATION_LATITUDE_INDEX]), 5)
                lon = round(float(loc[LOCATION_LONGITUDE_INDEX]), 5)
                validate_latitude(lat)
                validate_longitude(lon)
            except Exception:
                continue
            prev_ts = ts

            validated_loc = (ts, lat, lon)
            if freshness_cutoff is not None and ts <= freshness_cutoff:
                old_new_locs.append(validated_loc)
            else:
                fresh_new_locs.append(validated_loc)

        if not fresh_new_locs and not old_new_locs:
            if save:
                self.save()
            return

        cleaned_old_new_locs = []
        if old_new_locs:
            locations = self.locations
            existing_ts = set(list(zip(*locations))[LOCATION_TIMESTAMP_INDEX])
            for loc in old_new_locs:
                ts = int(loc[LOCATION_TIMESTAMP_INDEX])
                if ts in existing_ts:
                    continue
                cleaned_old_new_locs.append(loc)
                existing_ts.add(ts)
            if cleaned_old_new_locs:
                locations += cleaned_old_new_locs
                sorted_locations = list(
                    sorted(locations, key=itemgetter(LOCATION_TIMESTAMP_INDEX))
                )
                self.locations_encoded = gps_data_codec.encode(sorted_locations)
        if fresh_new_locs:
            # Only fresher points, can append string
            locs_to_encode = []
            if self.last_location is not None:
                locs_to_encode = [self.last_location]
            # Encoding magic
            locs_to_encode += fresh_new_locs
            encoded_addition = gps_data_codec.encode(locs_to_encode)
            if self.last_location is not None:
                offset = 0
                number_count = 0
                for i, character in enumerate(encoded_addition):
                    if ord(character) - 63 < 0x20:
                        number_count += 1
                        if number_count == 3:
                            offset = i + 1
                            break
                encoded_addition = encoded_addition[offset:]
            self.locations_encoded += encoded_addition
            # Updating cache
            new_last_loc = fresh_new_locs[-1]
            self._last_location_datetime = epoch_to_datetime(
                new_last_loc[LOCATION_TIMESTAMP_INDEX]
            )
            self._last_location_latitude = new_last_loc[LOCATION_LATITUDE_INDEX]
            self._last_location_longitude = new_last_loc[LOCATION_LONGITUDE_INDEX]

        added_locs = cleaned_old_new_locs + fresh_new_locs
        if added_locs:
            self._location_count += len(added_locs)
            if save:
                self.save()
            archived_events_affected = self.get_events_between_dates(
                epoch_to_datetime(added_locs[0][LOCATION_TIMESTAMP_INDEX]),
                epoch_to_datetime(added_locs[-1][LOCATION_TIMESTAMP_INDEX]),
                should_be_ended=True,
            )
            for archived_event_affected in archived_events_affected:
                archived_event_affected.invalidate_cache()
            if self.should_relay_to_gpsseuranta:
                gps_seuranta_net_client.send(f"rc{self.aid}", added_locs)
        elif save:
            self.save()

    @property
    def should_relay_to_gpsseuranta(self):
        return (
            self.gpsseuranta_known
            and self.gpsseuranta_relay_until
            and now() <= self.gpsseuranta_relay_until
        )

    def add_location(self, timestamp, lat, lon, /, *, reset=False, save=True):
        self.add_locations(
            [
                (timestamp, lat, lon),
            ],
            reset=reset,
            save=save,
        )

    @property
    def location_count(self):
        # This use a property of the GPS encoding format
        n = 0
        encoded = self.locations_encoded
        for x in encoded:
            if ord(x) - 63 < 0x20:
                n += 1
        return n // 3

    @property
    def last_location(self):
        if not self._location_count:
            return None
        return (
            int(self._last_location_datetime.timestamp()),
            self._last_location_latitude,
            self._last_location_longitude,
        )

    @property
    def earth_coords(self):
        if loc := self.last_location:
            return [loc[1], loc[2]]
        return None

    @property
    def last_location_datetime(self):
        return self._last_location_datetime

    def get_competitors_at_date(self, at, /, *, load_events=False):
        qs = (
            self.competitor_set.all()
            .filter(start_time__lte=at, event__end_date__gte=at)
            .annotate(max_start=Max("start_time"))
            .filter(start_time=F("max_start"))
        )
        if load_events:
            qs = qs.select_related("event")
        return qs

    def get_events_between_dates(self, from_date, to_date, /, *, should_be_ended=False):
        if not self.pk:
            return []
        qs = (
            self.competitor_set.all()
            .filter(
                event__end_date__gte=from_date,
                start_time__lte=to_date,
            )
            .select_related("event")
            .only("event")
            .order_by("start_time")
        )
        if should_be_ended:
            qs = qs.filter(event__end_date__lt=now())
        return {c.event for c in qs}

    def get_events_at_date(self, at):
        return self.get_events_between_dates(at, at)

    def get_last_competitor(self, load_event=False):
        qs = self.competitor_set.order_by("-start_time")
        if load_event:
            qs = qs.select_related("event")
        return qs.first()

    def get_last_event(self):
        c = self.get_last_competitor(load_event=True)
        if c:
            return c.event
        return None

    def get_original_device(self):
        if (
            self.aid.endswith("_ARC")
            and hasattr(self, "original_ref")
            and self.original_ref is not None
        ):
            return self.original_ref.original
        return None

    def send_sos(self):
        lat = None
        lon = None

        competitors = self.get_competitors_at_date(now(), load_events=True)

        if self.last_location:
            _, lat, lon = self.last_location

        if not competitors:
            return self.aid, lat, lon, None
        all_to_emails = set()
        for competitor in competitors:
            event = competitor.event
            to_emails = set()
            if event.emergency_contacts:
                to_emails = event.emergency_contacts.split(" ")
            else:
                club = event.club
                admin_ids = list(club.admins.values_list("id", flat=True))
                to_emails = list(
                    EmailAddress.objects.filter(
                        primary=True, user__in=admin_ids
                    ).values_list("email", flat=True)
                )
            if to_emails:
                msg = EmailMessage(
                    (
                        f"Routechoices.com - SOS from competitor {competitor.name}"
                        f" in event {event.name} [{now().isoformat()}]"
                    ),
                    (
                        f"Competitor {competitor.name} has triggered the SOS button"
                        f" of his GPS tracker during event {event.name}\r\n\r\n"
                        "Latest SOS known location is latitude, longitude: "
                        f"{lat}, {lon}"
                    ),
                    settings.DEFAULT_FROM_EMAIL,
                    list(to_emails),
                )
                msg.send()
            all_to_emails = all_to_emails.union(to_emails)
        return self.aid, lat, lon, list(all_to_emails)


class GpsGroupOwnership(Model):
    gps = models.ForeignKey(
        Group, related_name="group_ownerships", on_delete=models.CASCADE
    )
    group = models.ForeignKey(
        Group, related_name="tracker_ownerships", on_delete=models.CASCADE
    )

    creation_date = models.DateTimeField(auto_now_add=True)
    nickname = models.CharField(max_length=12, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                name="gps_ownership_by_group_device_uc",
                fields=("gps", "group"),
            )
        ]
        verbose_name = "Gps ownership"
        verbose_name_plural = "Gps ownerships"

    def __str__(self):
        return f"{self.gps} of {self.group.name}"


class GpsSharingPeriod(Model):
    gps = models.ForeignKey(Gps, related_name="shares", on_delete=models.CASCADE)
    group = models.ForeignKey(
        Group, related_name="shared_gps_set", on_delete=models.CASCADE
    )

    creation_date = models.DateTimeField(auto_now_add=True)

    from_date = models.DateTimeField(auto_now_add=True)
    until_date = models.DateTimeField()

    class Meta:
        ordering = ["-until_date"]
        verbose_name = "Gps Sharing Period"
        verbose_name_plural = "Gps Sharing Periods"

    def __str__(self):
        return f"{self.tracker} sharing to {self.group} from {self.from_date} until {self.until_date}"
