import json
import math
import tempfile
from io import BytesIO
from zoneinfo import ZoneInfo

import arrow
import geojson_validator
import gpxpy
from defusedxml import minidom
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.images import get_image_dimensions
from django.core.validators import FileExtensionValidator
from django.db.models import Q
from django.forms import (
    CharField,
    ChoiceField,
    DateTimeInput,
    FileField,
    Form,
    ModelChoiceField,
    ModelForm,
    inlineformset_factory,
)
from django.forms.widgets import TextInput
from django.utils.timezone import is_naive, make_aware
from PIL import Image

from routechoices.core.models import (
    WEBP_MAX_SIZE,
    Club,
    Competitor,
    Device,
    Event,
    EventSet,
    Map,
    MapAssignation,
    Notice,
)
from routechoices.lib.helpers import (
    check_dns_records,
    get_aware_datetime,
    initial_of_name,
)
from routechoices.lib.kmz import (
    extract_kml,
    get_maps_from_kml,
)
from routechoices.lib.validators import validate_domain_name, validate_nice_slug


def get_timezone_choices():
    import zoneinfo

    return [(tz, tz) for tz in sorted(zoneinfo.available_timezones())]


def from_timezone_to_utc(date, timezone):
    tz = ZoneInfo(timezone)
    date = date.replace(tzinfo=tz)
    date = date.astimezone(ZoneInfo("UTC"))
    return date


class UserForm(ModelForm):
    class Meta:
        model = User
        fields = ["username", "first_name", "last_name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = ""

    def clean_username(self):
        username = self.cleaned_data["username"]
        validate_nice_slug(username)
        return username


class MergeMapsForm(Form):
    def __init__(self, club, *args, **kwargs):
        self.club = club
        super().__init__(*args, **kwargs)
        qs = Map.objects.filter(club=club)
        self.fields["base"].queryset = qs
        self.fields["addend"].queryset = qs

    base = ModelChoiceField(
        label="Base map",
        queryset=Map.objects.none(),
    )
    addend = ModelChoiceField(
        label="Map to merge",
        queryset=Map.objects.none(),
    )

    def clean_addend(self):
        base = int(self.data.get("base"))
        addend = self.cleaned_data.get("addend")
        if base == addend.pk:
            raise ValidationError(
                "The map to merge should be different from the base map"
            )
        return addend


class RequestInviteForm(Form):
    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    club = ModelChoiceField(
        label="Club",
        help_text="Enter the club you want to be added as admin",
        queryset=Club.objects.filter(
            forbid_invite_request=False, is_personal_page=False
        ),
    )

    def clean_club(self):
        club = self.cleaned_data["club"]
        if self.user in club.admins.all():
            self.add_error("club", "You are already an admin of this club.")
        return club


class ClubForm(ModelForm):
    class Meta:
        model = Club
        fields = [
            "name",
            "slug",
            "admins",
            "forbid_invite_request",
            "website",
            "logo",
            "banner",
            "description",
        ]

    def clean_slug(self):
        slug = self.cleaned_data["slug"].lower()

        club_with_slug_qs = Club.objects.filter(
            Q(slug__iexact=slug)
            | Q(
                slug_changed_from=slug,
                slug_changed_at__gt=arrow.now().shift(hours=-72).datetime,
            ),
        )
        if self.instance:
            club_with_slug_qs = club_with_slug_qs.exclude(pk=self.instance.pk)
            if (
                self.instance.slug_changed_at
                and self.instance.slug_changed_at
                > arrow.now().shift(hours=-72).datetime
                and not (
                    (
                        self.instance.slug_changed_from
                        and slug == self.instance.slug_changed_from
                    )
                    or slug == self.instance.slug
                )
            ):
                self.add_error(
                    "slug",
                    "Domain prefix can be changed only once every 72 hours.",
                )

        if club_with_slug_qs.exists():
            self.add_error("slug", "Domain prefix already registered.")
        return slug

    def clean_banner(self):
        banner = self.cleaned_data["banner"]
        if not banner or "banner" not in self.changed_data:
            return banner
        w, h = get_image_dimensions(banner)
        if w < 600 or h < 315:
            raise ValidationError("The image is too small, minimum 600x315 pixels")
        fn = banner.name
        with Image.open(banner.file) as image:
            rgba_img = image.convert("RGBA")
            r = 1200 / 630
            if w < h * r:
                target_w = 1200
                r2 = w / target_w
                target_h = int(target_w / r)
                resized_image = rgba_img.resize((target_w, int(h / r2)))
                required_loss = resized_image.size[1] - target_h
                cropped_image = resized_image.crop(
                    box=(
                        0,
                        required_loss / 2,
                        1200,
                        resized_image.size[1] - required_loss / 2,
                    )
                )
            else:
                target_h = 630
                r2 = h / target_h
                target_w = int(target_h * r)
                resized_image = rgba_img.resize((int(w / r2), target_h))
                required_loss = resized_image.size[0] - target_w
                cropped_image = resized_image.crop(
                    box=(
                        required_loss / 2,
                        0,
                        resized_image.size[0] - required_loss / 2,
                        630,
                    )
                )
            out_buffer = BytesIO()
            cropped_image.resize((1200, 630))
            white_bg_img = Image.new("RGBA", (1200, 630), "WHITE")
            white_bg_img.paste(cropped_image, (0, 0), cropped_image)
            img = white_bg_img.convert("RGB")
            params = {
                "dpi": (72, 72),
            }
            img.save(out_buffer, "WEBP", **params)
            f_new = File(out_buffer, name=fn)
            return f_new

    def clean_logo(self):
        logo = self.cleaned_data["logo"]
        if not logo or "logo" not in self.changed_data:
            return logo
        w, h = get_image_dimensions(logo)
        minimum = 128
        if w < minimum or h < minimum:
            raise ValidationError(
                f"The image is too small, minimum {minimum}x{minimum} pixels"
            )
        fn = logo.name
        with Image.open(logo.file) as image:
            rgba_img = image.convert("RGBA")
            target = min([256, w, h])
            if w > h:
                resized_image = rgba_img.resize(
                    (target, int(target * image.size[1] / image.size[0]))
                )
                offsets = (0, int((target - resized_image.size[1]) / 2))
            else:
                resized_image = rgba_img.resize(
                    (int(target * image.size[0] / image.size[1]), target)
                )
                offsets = (int((target - resized_image.size[0]) / 2), 0)
            square_image = Image.new("RGBA", (target, target), (0, 0, 0, 0))
            square_image.paste(resized_image, offsets)
            out_buffer = BytesIO()
            params = {
                "dpi": (72, 72),
            }
            square_image.save(out_buffer, "PNG", **params)
            f_new = File(out_buffer, name=fn)
            return f_new


class ClubDomainForm(ModelForm):
    domain = CharField(
        max_length=128,
        label="Custom domain",
        help_text="eg: 'example.com'",
        validators=[validate_domain_name],
        required=False,
    )

    class Meta:
        model = Club
        fields = ("domain",)

    def clean_domain(self):
        domain = self.cleaned_data["domain"]
        if not domain:
            return domain
        if not check_dns_records(domain):
            self.add_error(
                "domain",
                f"DNS record for '{domain}' has not been set properly.",
            )
        matching_clubs = Club.objects.filter(domain__iexact=domain)
        if self.instance:
            matching_clubs = matching_clubs.exclude(pk=self.instance.pk)
        if matching_clubs.exists():
            self.add_error("domain", f"Domain '{domain}' already used by another club.")
        return domain.lower()


class DeviceForm(Form):
    device = ModelChoiceField(
        label="Device ID",
        help_text="Enter the device ID of the tracker",
        queryset=Device.objects.all(),
        to_field_name="aid",
    )
    nickname = CharField(max_length=12)


class MapForm(ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["image"].help_text = (
            "Image of map as a PNG, JPEG, GIF, WEBP, or PDF file"
        )
        self.fields["image"].widget.attrs["accept"] = "image/*, .pdf"

    class Meta:
        model = Map
        fields = ["name", "image", "calibration_string"]

    def clean_calibration_string(self):
        cc = self.cleaned_data["calibration_string"]
        try:
            cs = cc.split(",")
            if len(cs) != 8:
                raise ValueError()
            return ",".join([f"{float(c):.5f}" for c in cs])
        except Exception:
            raise ValidationError("Invalid format")

    def clean_image(self):
        f_orig = self.cleaned_data["image"]
        if "image" not in self.changed_data:
            return f_orig
        fn = f_orig.name
        with Image.open(f_orig.file) as image:
            rgba_img = image.convert("RGBA")
            format = "WEBP"
            if max(rgba_img.size[0], rgba_img.size[1]) > WEBP_MAX_SIZE:
                format = "PNG"
            out_buffer = BytesIO()
            params = {
                "dpi": (72, 72),
            }
            if format == "WEBP":
                params["quality"] = 80
            rgba_img.save(out_buffer, format, optimize=True, **params)
            f_new = File(out_buffer, name=fn)
            return f_new


class EventSetForm(ModelForm):
    def __init__(self, *args, **kwargs):
        self.club = kwargs.pop("club")
        super().__init__(*args, **kwargs)
        self.instance.club = self.club

    class Meta:
        model = EventSet
        fields = ["name", "create_page", "slug", "description", "list_secret_events"]

    def validate_unique(self):
        exclude = self._get_validation_exclusions()
        exclude.remove("club")
        try:
            self.instance.validate_unique(exclude=exclude)
        except ValidationError as e:
            self._update_errors(e)

    def clean_name(self):
        name = self.cleaned_data.get("name")
        qs = EventSet.objects.filter(club=self.club, name__iexact=name)
        if self.instance.id:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise ValidationError(
                "Name already used by another event set of this club."
            )
        return name

    def clean_slug(self):
        create_page = self.data.get("create_page")
        slug = self.cleaned_data.get("slug")
        if create_page:
            if not slug:
                raise ValidationError("URL must be set when creating a page.")
            qs = EventSet.objects.filter(
                club=self.club, create_page=True, slug__iexact=slug
            )
            if self.instance.id:
                qs = qs.exclude(id=self.instance.id)
            if qs.exists():
                raise ValidationError("URL already used by another event set.")
            elif Event.objects.filter(club=self.club, slug__iexact=slug).exists():
                raise ValidationError("URL already used by an event.")
        return slug


class EventForm(ModelForm):
    def __init__(self, *args, **kwargs):
        self.club = kwargs.pop("club")
        super().__init__(*args, **kwargs)
        self.fields["start_date"].help_text = '<span class="local_time"></span>'
        self.fields["end_date"].help_text = '<span class="local_time"></span>'
        self.fields["send_interval"].widget.attrs["min"] = 1
        self.instance.club = self.club

    timezone = ChoiceField(choices=get_timezone_choices)

    class Meta:
        model = Event
        fields = [
            "name",
            "slug",
            "event_set",
            "start_date",
            "end_date",
            "open_registration",
            "allow_route_upload",
            "privacy",
            # "on_events_page",
            "send_interval",
            "tail_length",
            "emergency_contacts",
            "backdrop_map",
            "geojson_layer",
            "map",
            "map_title",
            "timezone",
        ]
        widgets = {
            "start_date": DateTimeInput(
                attrs={"class": "datetimepicker", "autocomplete": "off"}
            ),
            "end_date": DateTimeInput(
                attrs={"class": "datetimepicker", "autocomplete": "off"}
            ),
        }

    def validate_unique(self):
        exclude = self._get_validation_exclusions()
        exclude.remove("club")
        try:
            self.instance.validate_unique(exclude=exclude)
        except ValidationError as e:
            self._update_errors(e)

    def clean(self):
        super().clean()
        club = self.club

        # Check club has rights to create new events
        if not club.can_modify_events:
            if club.subscription_paused:
                self.add_error(
                    None,
                    "Your subscription is currently paused, you cannot create or edit events.",
                )
            else:
                self.add_error(
                    None,
                    "Your 10 days free trial has now expired, you cannot create or edit events anymore.",
                )

        # Check that start date is before ends date
        start_date = self.cleaned_data.get("start_date")
        end_date = self.cleaned_data.get("end_date")
        if start_date and end_date and end_date < start_date:
            self.add_error("end_date", "End Date must be after than the Start Date.")

        # Check events by trial users is within trial period
        if club.is_on_free_trial and end_date > club.free_trial_end:
            self.add_error(
                None,
                "You can not create events that extend beyond the expiration date of your free trial. Please upgrade to our paid plan to continue.",
            )

        # Check name is unique for this event set
        name = self.cleaned_data.get("name")
        event_set = self.cleaned_data.get("event_set")

        if event_set:
            qs = Event.objects.filter(
                club_id=club.id,
                event_set_id=event_set,
                name__iexact=name,
            )
            if self.instance.id:
                qs = qs.exclude(id=self.instance.id)
            if qs.exists():
                self.add_error(
                    "name", "Name already used by another event in this event set."
                )

        # Check that slug is unique for this club
        slug = self.cleaned_data.get("slug")
        qs = Event.objects.filter(club_id=club.id, slug__iexact=slug)
        if self.instance.id:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            self.add_error("slug", "URL already used by another event.")
        elif EventSet.objects.filter(
            club_id=club.id, create_page=True, slug__iexact=slug
        ).exists():
            self.add_error("slug", "URL already used by an event set.")

    def clean_start_date(self):
        result = self.cleaned_data.get("start_date")
        timezone = self.data.get("timezone", "UTC")
        if result and timezone:
            result = from_timezone_to_utc(result, timezone)
        return result

    def clean_end_date(self):
        result = self.cleaned_data.get("end_date")
        timezone = self.data.get("timezone", "UTC")
        if result and timezone:
            result = from_timezone_to_utc(result, timezone)
        return result

    def clean_map(self):
        raster_map = self.cleaned_data.get("map")
        if raster_map:
            num_maps = int(self.data.get("map_assignations-TOTAL_FORMS", 1))
            start_count_maps = int(self.data.get("map_assignations-MIN_NUM_FORMS", 0))
            for i in range(start_count_maps, start_count_maps + num_maps):
                if (
                    self.data.get(f"map_assignations-{i}-map")
                    and self.data.get(f"map_assignations-{i}-DELETE") != "on"
                    and int(self.data.get(f"map_assignations-{i}-map")) == raster_map.id
                ):
                    raise ValidationError("Map assigned more than once in this event")
        return raster_map

    def clean_map_title(self):
        map_title = self.cleaned_data.get("map_title")
        num_maps = int(self.data.get("map_assignations-TOTAL_FORMS", 1))
        start_count_maps = int(self.data.get("map_assignations-MIN_NUM_FORMS", 0))
        for i in range(start_count_maps, start_count_maps + num_maps):
            if (
                self.data.get(f"map_assignations-{i}-title")
                and self.data.get(f"map_assignations-{i}-DELETE") != "on"
                and self.data.get(f"map_assignations-{i}-title") == map_title
            ):
                raise ValidationError("Map title given more than once in this event")
        return map_title

    def clean_geojson_layer(self):
        f_orig = self.cleaned_data["geojson_layer"]
        if "geojson_layer" not in self.changed_data:
            return f_orig
        if not f_orig:
            return f_orig
        data = f_orig.file.read()
        try:
            datajson = json.loads(data)
        except Exception:
            raise ValidationError("Invalid JSON File")
        try:
            errors = geojson_validator.validate_structure(datajson, check_crs=False)
        except Exception:
            raise ValidationError("Could not validate the GeoJSON File")
        if errors:
            raise ValidationError("Invalid GeoJSON File")
        return f_orig


class NoticeForm(ModelForm):
    class Meta:
        model = Notice
        fields = ("text",)


class ExtraMapForm(ModelForm):
    class Meta:
        model = MapAssignation
        fields = ("event", "map", "title")

    def clean_map(self):
        raster_map = self.cleaned_data.get("map")

        if not self.data.get("map"):
            raise ValidationError(
                "Extra maps can be set only if the main map field is set first"
            )

        if int(self.data.get("map")) == self.cleaned_data.get("map").id:
            raise ValidationError("Map assigned more than once in this event")

        map_occurence = 0
        num_maps = int(self.data.get("map_assignations-TOTAL_FORMS", 1))
        start_count_maps = int(self.data.get("map_assignations-MIN_NUM_FORMS", 0))
        for i in range(start_count_maps, start_count_maps + num_maps):
            if (
                self.data.get(f"map_assignations-{i}-map")
                and self.data.get(f"map_assignations-{i}-DELETE") != "on"
                and int(self.data.get(f"map_assignations-{i}-map")) == raster_map.id
            ):
                map_occurence += 1
                if map_occurence > 1:
                    raise ValidationError("Map assigned more than once in this event")
        return raster_map

    def clean_title(self):
        map_title = self.cleaned_data.get("title")
        main_map_title = self.data.get("map_title")
        if main_map_title and main_map_title == map_title:
            raise ValidationError("Map title given more than once in this event")

        title_occurence = 0
        num_maps = int(self.data.get("map_assignations-TOTAL_FORMS", 1))
        start_count_maps = int(self.data.get("map_assignations-MIN_NUM_FORMS", 0))
        for i in range(start_count_maps, start_count_maps + num_maps):
            if (
                self.data.get(f"map_assignations-{i}-title")
                and self.data.get(f"map_assignations-{i}-DELETE") != "on"
                and self.data.get(f"map_assignations-{i}-title") == map_title
            ):
                title_occurence += 1
                if title_occurence > 1:
                    raise ValidationError(
                        "Map title given more than once in this event"
                    )
        return map_title


class CompetitorForm(ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["start_time"].help_text = '<span class="local_time"></span>'
        self.fields["short_name"].required = False

    class Meta:
        model = Competitor
        fields = (
            "event",
            "name",
            "short_name",
            "device",
            "start_time",
            "color",
            "tags",
        )
        widgets = {
            "start_time": DateTimeInput(
                attrs={"class": "datetimepicker", "autocomplete": "off"}
            ),
            "color": TextInput(attrs={"class": "color-input"}),
            "tags": TextInput(attrs={"class": "tag-input"}),
        }

    def clean_short_name(self):
        name = self.cleaned_data.get("name")
        short_name = self.cleaned_data.get("short_name")
        if name and not short_name:
            short_name = initial_of_name(name)
        if not short_name:
            raise ValidationError("Field is required")
        return short_name

    def clean_start_time(self):
        result = self.cleaned_data.get("start_time")
        timezone = self.data.get("timezone", "UTC")
        if result and timezone:
            result = from_timezone_to_utc(result, timezone)
        start = result

        orig_event = self.cleaned_data.get("event")
        if self.data.get("start_date"):
            try:
                event_start = from_timezone_to_utc(
                    get_aware_datetime(self.data.get("start_date")),
                    timezone,
                )
            except Exception:
                event_start = orig_event.start_date
        else:
            event_start = orig_event.start_date
        if self.data.get("end_date"):
            try:
                event_end = from_timezone_to_utc(
                    get_aware_datetime(self.data.get("end_date")),
                    timezone,
                )
            except Exception:
                event_end = orig_event.end_date
        else:
            event_end = orig_event.end_date
        if (
            start
            and event_start
            and event_end
            and (event_start > start or start > event_end)
        ):
            self.add_error(
                "start_time", "Competitor start time should be during the event time"
            )
        return start


class UploadGPXForm(Form):
    competitor = ModelChoiceField(queryset=Competitor.objects.all())
    gpx_file = FileField(
        max_length=255, validators=[FileExtensionValidator(allowed_extensions=["gpx"])]
    )

    def clean_gpx_file(self):
        gpx_file = self.cleaned_data["gpx_file"]
        try:
            data = minidom.parseString(gpx_file.read())
            gpx_file = data.toxml(encoding="utf-8")
        except Exception:
            raise ValidationError("Couldn't read file")
        try:
            gpx = gpxpy.parse(gpx_file)
        except Exception:
            raise ValidationError("Couldn't parse GPX format")
        start_time = None
        end_time = None
        points = []
        missing_time_info = False
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    if point.time and point.latitude and point.longitude:
                        points.append(
                            (
                                int(point.time.timestamp()),
                                round(point.latitude, 5),
                                round(point.longitude, 5),
                            )
                        )
                        if not start_time:
                            start_time = point.time
                        end_time = point.time
                    elif point.latitude and point.longitude:
                        missing_time_info = True
        if len(points) == 0:
            if missing_time_info:
                raise ValidationError(
                    "File does not contain information about locations date/time"
                )
            raise ValidationError("File does not contain any points")
        if is_naive(start_time):
            start_time = make_aware(start_time)
        if is_naive(end_time):
            end_time = make_aware(end_time)
        self.cleaned_data["start_time"] = start_time
        self.cleaned_data["end_time"] = end_time
        self.cleaned_data["locations"] = points
        return gpx_file


class UploadMapGPXForm(Form):
    gpx_file = FileField(
        max_length=255, validators=[FileExtensionValidator(allowed_extensions=["gpx"])]
    )

    def clean_gpx_file(self):
        gpx_file = self.cleaned_data["gpx_file"]
        try:
            data = minidom.parseString(gpx_file.read())
            gpx_xml = data.toxml(encoding="utf-8")
        except Exception:
            raise ValidationError("Couldn't read file")
        try:
            gpx = gpxpy.parse(gpx_xml)
        except Exception:
            raise ValidationError("Couldn't parse GPX format")
        self.cleaned_data["gpx_data"] = gpx

        has_points = False

        segments = []
        waypoints = []

        prev_lon = None
        offset_lon = 0
        for point in gpx.waypoints:
            lon = point.longitude + offset_lon
            if prev_lon and abs(prev_lon - lon) > 180:
                offset_lon += math.copysign(
                    360, (prev_lon + 180) % 360 - (lon + 180) % 360
                )
                lon = point.longitude + offset_lon
            prev_lon = lon
            waypoints.append([round(point.latitude, 5), round(lon, 5)])
            has_points = True

        for route in gpx.routes:
            points = []
            prev_lon = None
            offset_lon = 0
            for point, _ in route.walk():
                lon = point.longitude + offset_lon
                if prev_lon and abs(prev_lon - lon) > 180:
                    offset_lon += math.copysign(
                        360, (prev_lon + 180) % 360 - (lon + 180) % 360
                    )
                    lon = point.longitude + offset_lon
                prev_lon = lon
                points.append([round(point.latitude, 5), round(lon, 5)])
            if len(points) > 1:
                has_points = True
                segments.append(points)

        for track in gpx.tracks:
            for segment in track.segments:
                points = []
                prev_lon = None
                offset_lon = 0
                for point in segment.points:
                    lon = point.longitude + offset_lon
                    if prev_lon and abs(prev_lon - lon) > 180:
                        offset_lon += math.copysign(
                            360, (prev_lon + 180) % 360 - (lon + 180) % 360
                        )
                        lon = point.longitude + offset_lon
                    prev_lon = lon
                    points.append([round(point.latitude, 5), round(lon, 5)])
                if len(points) > 1:
                    has_points = True
                    segments.append(points)

        if not has_points:
            raise ValidationError("Could not find enough points to draw a map")

        self.cleaned_data["gpx_segments"] = segments
        self.cleaned_data["gpx_waypoints"] = waypoints
        return gpx_file


class UploadKmzForm(Form):
    file = FileField(
        label="KML/KMZ file",
        max_length=255,
        validators=[FileExtensionValidator(allowed_extensions=["kmz", "kml"])],
    )

    def clean_file(self):
        file = self.cleaned_data["file"]
        tmp_extract_dir = None
        if file.name.lower().endswith(".kmz"):
            tmp_extract_dir = tempfile.mkdtemp("_kmz")
            try:
                kml = extract_kml(file, tmp_extract_dir)
            except Exception:
                raise ValidationError("Invalid KMZ: could not extract .kml")
        elif file.name.lower().endswith(".kml"):
            kml = file.read()
        new_maps = list(get_maps_from_kml(kml, tmp_extract_dir))
        if not new_maps:
            raise ValidationError("Could not find any Maps in that file!")
        return file


CompetitorFormSet = inlineformset_factory(
    Event,
    Competitor,
    form=CompetitorForm,
    extra=1,
    min_num=0,
    max_num=None,
    validate_min=True,
)

ExtraMapFormSet = inlineformset_factory(
    Event,
    MapAssignation,
    form=ExtraMapForm,
    extra=1,
    min_num=0,
    max_num=None,
    validate_min=True,
)
