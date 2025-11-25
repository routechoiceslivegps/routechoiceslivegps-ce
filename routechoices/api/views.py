import logging
import re
import time
import urllib.parse
from datetime import timedelta
from io import BytesIO
from zipfile import ZipFile

import arrow
import gps_data_codec
import orjson as json
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.gis.geoip2 import GeoIP2
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.base import ContentFile
from django.db.models import Prefetch, Q
from django.http import HttpRequest, HttpResponse
from django.http.response import Http404
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from django_hosts.resolvers import reverse
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import renderers, serializers, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from routechoices.core.models import (
    EVENT_CACHE_INTERVAL_ARCHIVED,
    EVENT_CACHE_INTERVAL_LIVE,
    LOCATION_LATITUDE_INDEX,
    LOCATION_LONGITUDE_INDEX,
    LOCATION_TIMESTAMP_INDEX,
    MAP_BLANK,
    MAP_CHOICES,
    PRIVACY_PRIVATE,
    PRIVACY_PUBLIC,
    PRIVACY_SECRET,
    Club,
    Competitor,
    Device,
    DeviceClubOwnership,
    Event,
    EventSet,
    ImeiDevice,
    Map,
    MapAssignation,
)
from routechoices.lib import cache
from routechoices.lib.duration_constants import (
    DURATION_ONE_MINUTE,
)
from routechoices.lib.helpers import (
    epoch_to_datetime,
    get_image_mime_from_request,
    git_master_hash,
    initial_of_name,
    random_device_id,
    safe64encodedsha,
    set_content_disposition,
    short_random_key,
    short_random_slug,
)
from routechoices.lib.other_gps_services.gpsseuranta import GpsSeurantaNet
from routechoices.lib.other_gps_services.livelox import Livelox
from routechoices.lib.other_gps_services.loggator import Loggator
from routechoices.lib.s3 import serve_from_s3, serve_image_from_s3
from routechoices.lib.streaming_response import StreamingHttpRangeResponse
from routechoices.lib.validators import (
    color_hex_validator,
    validate_imei,
    validate_latitude,
    validate_longitude,
    validate_nice_slug,
)

logger = logging.getLogger(__name__)

api_GET_view = api_view(["GET"])
api_GET_HEAD_view = api_view(["GET", "HEAD"])
api_POST_view = api_view(["POST"])
api_GET_POST_view = api_view(["GET", "POST"])


class PostDataThrottle(AnonRateThrottle):
    rate = "70/min"

    def allow_request(self, request, view):
        if request.method == "GET":
            return True
        return super().allow_request(request, view)


club_param = openapi.Parameter(
    "club",
    openapi.IN_QUERY,
    description="Filter by this club slug",
    type=openapi.TYPE_STRING,
)

event_param = openapi.Parameter(
    "event",
    openapi.IN_QUERY,
    description="Filter by this event slug or url",
    type=openapi.TYPE_STRING,
)


@swagger_auto_schema(
    method="post",
    auto_schema=None,
)
@api_POST_view
@permission_classes([IsAuthenticated])
def event_set_creation(request):
    club_slug = request.data.get("club_slug")
    name = request.data.get("name")
    if not name or not club_slug:
        raise ValidationError("Missing parameter")
    club = Club.objects.filter(admins=request.user, slug__iexact=club_slug).first()
    if not club:
        raise ValidationError("club not found")
    event_set, _ = EventSet.objects.get_or_create(club=club, name=name)
    return Response(
        {
            "value": event_set.id,
            "text": name,
        }
    )


@swagger_auto_schema(
    method="get",
    operation_id="events_list",
    operation_description=(
        "List all public events sorted by decreasing start date. "
        "If you are identified it will list also all your events you have created,"
        " no matter their privacy settings. "
        "Will also list a secret event if its url is specified in the event parameter."
    ),
    tags=["Events"],
    manual_parameters=[club_param, event_param],
    responses={
        "200": openapi.Response(
            description="Success response",
            examples={
                "application/json": [
                    {
                        "id": "PlCG3xFS-f4",
                        "name": "Jukola 2019 - 1st Leg",
                        "start_date": "2019-06-15T20:00:00Z",
                        "end_date": "2019-06-16T00:00:00Z",
                        "slug": "Jukola-2019-1st-leg",
                        "club": {
                            "name": "Kangasala SK",
                            "slug": "ksk",
                        },
                        "privacy": "public",
                        "backdrop": "blank",
                        "open_registration": False,
                        "open_route_upload": False,
                        "url": "http://www.routechoices.com/ksk/Jukola-2019-1st-leg",
                    },
                    {
                        "id": "ohFYzJep1hI",
                        "name": "Jukola 2019 - 2nd Leg",
                        "start_date": "2019-06-15T21:00:00Z",
                        "end_date": "2019-06-16T00:00:00Z",
                        "slug": "Jukola-2019-2nd-leg",
                        "club": {
                            "name": "Kangasala SK",
                            "slug": "ksk",
                        },
                        "privacy": "public",
                        "open_registration": False,
                        "open_route_upload": False,
                        "url": "http://www.routechoices.com/ksk/Jukola-2019-2nd-leg",
                    },
                    "...",
                ]
            },
        ),
    },
)
@swagger_auto_schema(
    method="post",
    operation_id="event_create",
    operation_description="Create an event. This endpoint requires you to be identified.",
    tags=["Events"],
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            "club_slug": openapi.Schema(
                type=openapi.TYPE_STRING,
                description="Club Slug",
                example="halden-sk",
            ),
            "name": openapi.Schema(
                type=openapi.TYPE_STRING,
                description='Event name. Default to "Untitled + random string"',
                example="Night-O",
            ),
            "slug": openapi.Schema(
                type=openapi.TYPE_STRING,
                description="URL path name. Default random",
                example="night-o",
            ),
            "start_date": openapi.Schema(
                type=openapi.TYPE_STRING,
                description="Start time (YYYY-MM-DDThh:mm:ssZ). Default to now",
                example="2025-11-10T20:00:00Z",
            ),
            "end_date": openapi.Schema(
                type=openapi.TYPE_STRING,
                description=(
                    "End time, must be after the start_date (YYYY-MM-DDThh:mm:ssZ)"
                ),
                example="2025-11-10T22:00:00Z",
            ),
            "privacy": openapi.Schema(
                type=openapi.TYPE_STRING,
                description=(
                    "Privacy level (PUBLIC, SECRET or PRIVATE). Default to SECRET"
                ),
                enum=["PUBLIC", "SECRET", "PRIVATE"],
                example="PUBLIC",
                default="SECRET",
            ),
            "backdrop": openapi.Schema(
                type=openapi.TYPE_STRING,
                description=(
                    f"Backdrop map: one of {', '.join(m[0] for m in MAP_CHOICES)}."
                    " Default blank"
                ),
                example="osm",
                default="blank",
            ),
            "open_registration": openapi.Schema(
                type=openapi.TYPE_BOOLEAN,
                description=(
                    "Can public register themselves to the event. Default False"
                ),
                example=True,
                default=False,
            ),
            "open_route_upload": openapi.Schema(
                type=openapi.TYPE_BOOLEAN,
                description=(
                    "Can public upload their route to the event from GPS files,"
                    " Default False"
                ),
                example=True,
                default=False,
            ),
        },
        required=["club_slug", "end_date"],
    ),
    responses={
        "201": openapi.Response(
            description="Success response",
            examples={
                "application/json": {
                    "id": "PlCG3xFS-f4",
                    "name": "Jukola 2019 - 1st Leg",
                    "start_date": "2019-06-15T20:00:00Z",
                    "end_date": "2019-06-16T00:00:00Z",
                    "slug": "Jukola-2019-1st-leg",
                    "club": {
                        "name": "Kangasala SK",
                        "slug": "ksk",
                    },
                    "privacy": "public",
                    "backdrop": "blank",
                    "open_registration": False,
                    "open_route_upload": False,
                    "url": "http://www.routechoices.com/ksk/Jukola-2019-1st-leg",
                },
            },
        ),
    },
)
@api_GET_POST_view
def event_list(request):
    if request.method == "POST":
        if not request.user.is_authenticated:
            raise ValidationError("authentication required")
        club_slug = request.data.get("club_slug")
        if not club_slug:
            raise ValidationError("club_slug is required")
        club = Club.objects.filter(
            admins=request.user, slug__iexact=club_slug, is_personal_page=False
        ).first()
        if not club:
            raise ValidationError("club not found")
        if not club.can_modify_events:
            if club.subscription_paused:
                raise ValidationError("subscription paused")
            raise ValidationError("free trial expired")
        name = f"Untitled {short_random_slug()}"
        name_raw = request.data.get("name")
        if name_raw:
            name = name_raw

        slug = short_random_slug()
        slug_raw = request.data.get("slug")
        if slug_raw:
            try:
                validate_nice_slug(slug_raw)
            except Exception:
                raise ValidationError("slug invalid")
            else:
                slug = slug_raw

        start_date = arrow.now().datetime
        start_date_raw = request.data.get("start_date")
        if start_date_raw:
            try:
                start_date = arrow.get(start_date_raw).datetime
            except Exception:
                raise ValidationError("start_date invalid")

        end_date_raw = request.data.get("end_date")
        if not end_date_raw:
            raise ValidationError("end_date is required")
        try:
            end_date = arrow.get(end_date_raw).datetime
        except Exception:
            raise ValidationError("end_date_invalid")
        else:
            if end_date <= start_date:
                raise ValidationError("end_date invalid, should be after start_date")

        backdrop_map = request.data.get("backdrop", MAP_BLANK)
        if backdrop_map not in (m[0] for m in MAP_CHOICES):
            raise ValidationError("backdrop invalid")

        privacy = request.data.get("privacy", PRIVACY_SECRET)
        if privacy.lower() not in (PRIVACY_PUBLIC, PRIVACY_SECRET, PRIVACY_PRIVATE):
            raise ValidationError("privacy invalid")

        open_registration = False
        open_registration_raw = request.data.get("open_registration")
        if open_registration_raw:
            open_registration = True

        allow_route_upload = False
        allow_route_upload_raw = request.data.get("allow_route_upload")
        if allow_route_upload_raw:
            allow_route_upload = True

        event = Event(
            club=club,
            slug=slug,
            name=name,
            start_date=start_date,
            end_date=end_date,
            privacy=privacy,
            backdrop_map=backdrop_map,
            open_registration=open_registration,
            allow_route_upload=allow_route_upload,
        )
        try:
            event.full_clean()
        except Exception as e:
            raise ValidationError(e)
        event.save()
        output = {
            "id": event.aid,
            "name": event.name,
            "start_date": event.start_date,
            "end_date": event.end_date,
            "slug": event.slug,
            "club": {
                "name": club.name,
                "slug": club.slug.lower(),
            },
            "privacy": event.privacy,
            "backdrop": event.backdrop_map,
            "open_registration": event.open_registration,
            "open_route_upload": event.allow_route_upload,
            "url": request.build_absolute_uri(event.get_absolute_url()),
        }
        return Response(output, status=status.HTTP_201_CREATED)

    club_slug = request.GET.get("club")
    event_slug = request.GET.get("event")
    event_url = None

    if event_slug and "/" in event_slug:
        event_url = event_slug
        event_slug = None

    if (event_slug and club_slug) or event_url:
        privacy_arg = {"privacy__in": [PRIVACY_PUBLIC, PRIVACY_SECRET]}
    else:
        privacy_arg = {"privacy": PRIVACY_PUBLIC}

    headers = {}
    if request.user.is_authenticated:
        clubs = Club.objects.filter(admins=request.user, is_personal_page=False)
        events = Event.objects.filter(
            Q(**privacy_arg) | Q(club__in=clubs)
        ).select_related("club")
        headers["Cache-Control"] = "Private"
    else:
        events = Event.objects.filter(**privacy_arg).select_related("club")

    if club_slug:
        events = events.filter(club__slug__iexact=club_slug)
    if event_slug:
        events = events.filter(slug__iexact=event_slug)
    if event_url:
        url = urllib.parse.urlparse(event_url)
        domain = url.netloc
        if domain.endswith(f".{settings.PARENT_HOST}"):
            club_slug = domain[: -(len(settings.PARENT_HOST) + 1)]
            events.filter(club__slug__iexact=club_slug)
        else:
            events.filter(club__domain__iexact=domain)
        event_slug = url.path[1:]
        if event_slug.endswith("/"):
            event_slug = event_slug[:-1]
        events = events.filter(slug__iexact=event_slug)
    output = []
    for event in events:
        output.append(
            {
                "id": event.aid,
                "name": event.name,
                "start_date": event.start_date,
                "end_date": event.end_date,
                "slug": event.slug,
                "club": {
                    "name": event.club.name,
                    "slug": event.club.slug.lower(),
                },
                "privacy": event.privacy,
                "backdrop": event.backdrop_map,
                "open_registration": event.open_registration,
                "open_route_upload": event.allow_route_upload,
                "url": request.build_absolute_uri(event.get_absolute_url()),
            }
        )
    return Response(output, headers=headers)


@swagger_auto_schema(
    method="get",
    operation_id="club_list",
    operation_description="List all your clubs.",
    tags=["Clubs"],
    responses={
        "200": openapi.Response(
            description="Success response",
            examples={
                "application/json": [
                    {
                        "name": "Kangasala SK",
                        "slug": "ksk",
                        "url": "https://ksk.routechoices.com/",
                        "plan": "FREE_TRIAL",
                    },
                    {
                        "name": "Halden SK",
                        "slug": "halden-sk",
                        "url": "https://gps.haldensk.no/",
                        "plan": "BASIC_LIVE",
                    },
                    "...",
                ]
            },
        ),
    },
)
@api_GET_view
@permission_classes([IsAuthenticated])
def club_list_view(request):
    owned_clubs = Club.objects.filter(admins=request.user, is_personal_page=False)
    clubs = owned_clubs
    output = []
    for club in clubs:
        # TODO: Set plan in models
        plan = "NO_PLAN"
        if club.is_on_free_trial:
            plan = "FREE_TRIAL"
        elif club.can_modify_events:
            plan = "BASIC_LIVE"
        data = {
            "name": club.name,
            "slug": club.slug,
            "url": club.nice_url,
            "plan": plan,
        }
        output.append(data)
    return Response(output)


@swagger_auto_schema(
    method="get",
    operation_id="event_detail",
    operation_description=(
        "Read an event details. For private events you need "
        "to be identified as an admin of the event organiser to be able to get a valid answer."
    ),
    tags=["Events"],
    responses={
        "200": openapi.Response(
            description="Success response",
            examples={
                "application/json": {
                    "event": {
                        "id": "PlCG3xFS-f4",
                        "name": "Jukola 2019 - 1st Leg",
                        "start_date": "2019-06-15T20:00:00Z",
                        "end_date": "2019-06-16T00:00:00Z",
                        "slug": "Jukola-2019-1st-leg",
                        "club": {
                            "name": "Kangasala SK",
                            "slug": "ksk",
                        },
                        "privacy": "public",
                        "open_registration": False,
                        "open_route_upload": False,
                        "url": "https://ksk.routechoices.com/Jukola-2019-1st-leg",
                        "shortcut": "https://routechoic.es/ksk/Jukola-2019-1st-leg",
                        "backdrop": "osm",
                        "send_interval": 5,
                        "tail_length": 60,
                    },
                    "data_url": (
                        "https://www.routechoices.com/api/events/PlCG3xFS-f4/data"
                    ),
                    "announcement": "",
                    "maps": [
                        {
                            "coordinates": {
                                "top_left": {"lat": 61.45075, "lon": 24.18994},
                                "top_right": {"lat": 61.44656, "lon": 24.24721},
                                "bottom_right": {"lat": 61.42094, "lon": 24.23851},
                                "bottom_left": {"lat": 61.42533, "lon": 24.18156},
                            },
                            "rotation": 3.25,
                            "url": (
                                "https://www.routechoices.com/api/events/PlCG3xFS-f4/map"
                            ),
                            "title": "",
                            "hash": "u8cWoEiv",
                            "max_zoom": 18,
                            "modification_date": "2019-06-10T17:21:52.417000Z",
                            "default": True,
                            "id": "or6tmT19cfk",
                        }
                    ],
                }
            },
        ),
    },
)
@api_GET_view  # TODO: Add Patch and Delete method
def event_detail(request, event_id):
    event = (
        Event.objects.select_related("club", "notice", "map")
        .prefetch_related(
            Prefetch(
                "map_assignations",
                queryset=MapAssignation.objects.select_related("map"),
            )
        )
        .filter(aid=event_id)
        .first()
    )

    if not event:
        res = {"error": "No event match this id"}
        return Response(res)

    event.check_user_permission(request.user)

    output = {
        "event": {
            "id": event.aid,
            "name": event.name,
            "start_date": event.start_date,
            "end_date": event.end_date,
            "slug": event.slug,
            "club": {
                "name": event.club.name,
                "slug": event.club.slug.lower(),
            },
            "privacy": event.privacy,
            "open_registration": event.open_registration,
            "open_route_upload": event.allow_route_upload,
            "url": request.build_absolute_uri(event.get_absolute_url()),
            "shortcut": event.shortcut,
            "backdrop": event.backdrop_map,
            "send_interval": event.send_interval,
            "tail_length": event.tail_length,
        },
        "data_url": request.build_absolute_uri(
            reverse("event_data", host="api", kwargs={"event_id": event.aid})
        ),
        "announcement": "",
        "maps": [],
    }

    if event.start_date < now():
        output["announcement"] = event.notice.text if event.has_notice else ""

        if event.map:
            map_data = {
                "title": event.map_title,
                "coordinates": event.map.bound,
                "rotation": event.map.north_declination,
                "hash": event.map.hash,
                "max_zoom": event.map.max_zoom,
                "modification_date": event.map.modification_date,
                "default": True,
                "id": event.map.aid,
                "url": request.build_absolute_uri(
                    reverse(
                        "event_main_map_download",
                        host="api",
                        kwargs={"event_id": event.aid},
                    )
                ),
                "wms": True,
            }
            output["maps"].append(map_data)
        for i, m in enumerate(event.map_assignations.all()):
            map_data = {
                "title": m.title,
                "coordinates": m.map.bound,
                "rotation": m.map.north_declination,
                "hash": m.map.hash,
                "max_zoom": m.map.max_zoom,
                "modification_date": m.map.modification_date,
                "default": False,
                "id": m.map.aid,
                "url": request.build_absolute_uri(
                    reverse(
                        "event_map_download",
                        host="api",
                        kwargs={"event_id": event.aid, "index": (i + 2)},
                    )
                ),
                "wms": True,
            }
            output["maps"].append(map_data)

    if event.geojson_layer:
        output["geojson_url"] = event.get_geojson_url()

    headers = {"ETag": f'W/"{safe64encodedsha(json.dumps(output))}"'}
    if event.privacy == PRIVACY_PRIVATE:
        headers["Cache-Control"] = "Private"

    return Response(output, headers=headers)


@swagger_auto_schema(
    method="post",
    operation_id="competitor_create",
    operation_description="Create a competitor for a given event. Only those identified as admins of the event organiser can set the competitor color.",
    tags=["Competitors"],
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            "event_id": openapi.Schema(
                type=openapi.TYPE_STRING,
                description="Event ID",
            ),
            "name": openapi.Schema(
                type=openapi.TYPE_STRING,
                description="Full name",
            ),
            "short_name": openapi.Schema(
                type=openapi.TYPE_STRING,
                description="Short version of the name",
            ),
            "start_time": openapi.Schema(
                type=openapi.TYPE_STRING,
                description=(
                    "Start time, must be within the event schedule if provided"
                    " (YYYY-MM-DDThh:mm:ssZ)"
                ),
            ),
            "device_id": openapi.Schema(
                type=openapi.TYPE_STRING,
                description="Device ID",
            ),
            "color": openapi.Schema(
                type=openapi.TYPE_STRING,
                description="Color, hexadecimal format, e.g. #ff9900",
            ),
        },
        required=["event_id", "name"],
    ),
    responses={
        "201": openapi.Response(
            description="Success response",
            examples={
                "application/json": {
                    "id": "<id>",
                    "name": "<name>",
                    "short_name": "<short_name>",
                    "start_time": "<start_time>",
                    "device_id": "<device_id>",
                    "color": "<color>",
                }
            },
        ),
        "400": openapi.Response(
            description="Validation Error",
            examples={"application/json": ["<error message>"]},
        ),
    },
)
@api_POST_view
def create_competitor(request):
    event_id = request.data.get("event_id")
    if not event_id:
        raise ValidationError("Event ID is missing")
    event = Event.objects.select_related("club").filter(aid=event_id).first()
    if not event:
        raise ValidationError("No event match this id")

    is_event_admin = False
    if request.user.is_authenticated:
        is_event_admin = event.club.admins.filter(id=request.user.id).exists()

    if not event.open_registration:
        if not is_event_admin:
            raise PermissionDenied()

    if event.end_date < now() and not event.allow_route_upload:
        raise ValidationError("Registration is closed")

    errors = []

    name = request.data.get("name")

    if not name:
        errors.append("Name is missing")

    short_name = request.data.get("short_name")
    if name and not short_name:
        short_name = initial_of_name(name)

    start_time_query = request.data.get("start_time")
    if start_time_query:
        try:
            start_time = arrow.get(start_time_query).datetime
        except Exception:
            start_time = None
            errors.append("Start time could not be parsed")
    elif event.start_date < now() < event.end_date:
        start_time = now()
    else:
        start_time = event.start_date
    event_start = event.start_date
    event_end = event.end_date

    if start_time and (event_start > start_time or start_time > event_end):
        errors.append("Competitor start time should be during the event time")

    device_id = request.data.get("device_id")
    device = Device.objects.filter(aid=device_id).defer("locations_encoded").first()

    if not device and device_id:
        errors.append("Device ID not found")

    if not is_event_admin:
        if event.competitors.filter(name=name).exists():
            errors.append("Name already in use in this event")

        if event.competitors.filter(
            short_name=short_name
        ).exists() and request.data.get("short_name"):
            errors.append("Short name already in use in this event")
        if (
            device
            and Competitor.objects.filter(
                start_time=start_time, device_id=device.id
            ).exists()
        ):
            errors.append("This device is already registered for this same start time")

    if not is_event_admin:
        color = ""
    else:
        color = request.data.get("color", "")

    if color:
        try:
            color_hex_validator(color)
        except Exception:
            color = ""

    if errors:
        raise ValidationError(errors)

    user = None
    if request.user.is_authenticated:
        user = request.user

    comp = Competitor.objects.create(
        name=name,
        event=event,
        short_name=short_name,
        start_time=start_time,
        device=device,
        user=user,
        color=color,
    )

    output = {
        "id": comp.aid,
        "name": name,
        "short_name": short_name,
        "start_time": start_time,
    }
    if color:
        output["color"] = color

    if device:
        output["device_id"] = device.aid

    return Response(
        output,
        status=status.HTTP_201_CREATED,
    )


@swagger_auto_schema(
    method="patch",
    operation_id="competitor_update",
    operation_description="Edit a competitor. Only those identified as admins of the event organiser can set the competitor color. You need to be identified as the user that created the competitor or as an events organiser admin to get a valid answer.",
    tags=["Competitors"],
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            "device_id": openapi.Schema(
                type=openapi.TYPE_STRING,
                description="Device ID",
            ),
            "name": openapi.Schema(
                type=openapi.TYPE_STRING,
                description="Full name",
            ),
            "short_name": openapi.Schema(
                type=openapi.TYPE_STRING,
                description="Short version of the name",
            ),
            "color": openapi.Schema(
                type=openapi.TYPE_STRING,
                description="Color, hexadecimal format, e.g. #ff9900",
            ),
        },
    ),
    responses={
        "200": openapi.Response(
            description="Success response",
            examples={"application/json": {"status": "ok"}},
        ),
        "400": openapi.Response(
            description="Validation Error",
            examples={"application/json": ["<error message>"]},
        ),
    },
)
@swagger_auto_schema(
    method="delete",
    operation_id="competitor_delete",
    operation_description="Delete a competitor. You need to be identified as the user that created the competitor or as an event organiser admin to get a valid answer.",
    tags=["Competitors"],
    responses={
        "204": openapi.Response(
            description="Success response", examples={"application/json": ""}
        ),
        "400": openapi.Response(
            description="Validation Error",
            examples={"application/json": ["<error message>"]},
        ),
    },
)
@api_view(["DELETE", "PATCH"])
@permission_classes([IsAuthenticated])
def competitor_api(request, competitor_id):
    competitor = (
        Competitor.objects.select_related("event", "event__club")
        .filter(aid=competitor_id)
        .first()
    )
    if not competitor:
        res = {"error": "No competitor match this id"}
        return Response(res)

    event = competitor.event
    other_competitors = event.competitors.exclude(id=competitor.id)

    is_user_event_admin = event.club.admins.filter(id=request.user.id).exists()
    if not is_user_event_admin and competitor.user != request.user:
        raise PermissionDenied()

    if request.method == "DELETE":
        competitor.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    new_name = request.data.get("name")
    new_short_name = request.data.get("short_name")
    new_device_id = request.data.get("device_id")
    new_device = None
    if is_user_event_admin:
        new_color = request.data.get("color")
    else:
        new_color = None

    if new_name:
        new_name = new_name[:64]
    if new_short_name == "":
        new_short_name = initial_of_name(competitor.name)
    if new_short_name:
        new_short_name = new_short_name[:32]
    if new_device_id:
        dev = Device.objects.filter(aid=new_device_id).first()
        if not dev:
            raise ValidationError("Invalid device ID")
        new_device = dev
    if new_color is not None:
        try:
            color_hex_validator(new_color)
        except Exception:
            new_color = ""
        new_color = new_color

    if not is_user_event_admin:
        if new_name and other_competitors.filter(name=new_name).exists():
            raise ValidationError("Name already in use in this event")

        if (
            new_short_name
            and request.data.get("short_name")
            and other_competitors.filter(short_name=new_short_name).exists()
        ):
            raise ValidationError("Short name already in use in this event")

        if (
            new_device
            and Competitor.objects.exclude(id=competitor.id)
            .filter(
                device_id=new_device.id,
                start_time=competitor.start_time,
            )
            .exists()
        ):
            raise ValidationError(
                "This device is already registered for the same start time"
            )

    if new_name:
        competitor.name = new_name
    if new_short_name:
        competitor.short_name = new_short_name
    if new_device:
        competitor.device = new_device
    if new_color:
        competitor.color = new_color

    if new_name or new_short_name or new_device_id or new_color:
        competitor.save()
        return Response({"status": "ok"})
    else:
        raise ValidationError("No data submitted")


@swagger_auto_schema(
    method="post",
    operation_id="competitor_route_upload",
    operation_description=(
        "Upload a full route for an existing competitor (Deletes existing location data). You need to be identified as the user that created the competitor or as an event organiser admin to get a valid answer unless the events allow route upload and that the competitor has no locations data yet assigned."
    ),
    tags=["Competitors"],
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            "latitudes": openapi.Schema(
                type=openapi.TYPE_STRING,
                description=(
                    "A list of locations latitudes (in degrees) separated by commas"
                ),
                example="60.12345,60.12346,60.12347",
            ),
            "longitudes": openapi.Schema(
                type=openapi.TYPE_STRING,
                description=(
                    "A list of locations longitudes (in degrees) separated by commas"
                ),
                example="20.12345,20.12346,20.12347",
            ),
            "timestamps": openapi.Schema(
                type=openapi.TYPE_STRING,
                description=(
                    "A list of locations timestamps "
                    "(UNIX epoch in seconds) separated by commas"
                ),
                example="1661489045,1661489046,1661489047",
            ),
        },
        required=["latitudes", "longitudes", "timestamps"],
    ),
    responses={
        "201": openapi.Response(
            description="Success response",
            examples={"application/json": {"status": "ok", "location_count": "3"}},
        ),
        "400": openapi.Response(
            description="Validation Error",
            examples={"application/json": ["<error message>"]},
        ),
    },
)
@api_POST_view
def competitor_route_upload(request, competitor_id):
    competitor = (
        Competitor.objects.select_related("event", "event__club", "device")
        .filter(aid=competitor_id)
        .first()
    )
    if not competitor:
        res = {"error": "No competitor match this id"}
        return Response(res)
    event = competitor.event

    is_user_event_admin = request.user.is_authenticated and (
        event.club.admins.filter(id=request.user.id).exists()
        or request.user == competitor.user
    )

    if not event.allow_route_upload:
        raise PermissionDenied()

    if not is_user_event_admin and competitor.locations:
        raise ValidationError("Competitor already assigned a route")

    if event.start_date > now():
        raise ValidationError("Event has not yet started")

    try:
        lats = [float(x) for x in request.data.get("latitudes", "").split(",") if x]
        lons = [float(x) for x in request.data.get("longitudes", "").split(",") if x]
        times = [float(x) for x in request.data.get("timestamps", "").split(",") if x]
    except ValueError:
        raise ValidationError("Invalid data format")

    if not (len(lats) == len(lons) == len(times)):
        raise ValidationError(
            "Latitudes, longitudes, and timestamps, should have same amount of points"
        )

    if len(lats) < 2:
        raise ValidationError("Minimum amount of locations is 2")

    loc_array = []
    start_time = None
    for tim, lat, lon in zip(times, lats, lons):
        if tim and lat and lon:
            try:
                validate_longitude(lon)
            except Exception:
                raise ValidationError("Invalid longitude value")
            try:
                validate_latitude(lat)
            except Exception:
                raise ValidationError("Invalid latitude value")
            try:
                int(tim)
            except Exception:
                raise ValidationError("Invalid time value")
            if event.start_date.timestamp() <= tim <= event.end_date.timestamp():
                if not start_time or tim < start_time:
                    start_time = int(tim)
                loc_array.append((int(tim), lat, lon))

    device = None
    if len(loc_array) > 0:
        device = Device.objects.create(
            aid=f"{short_random_key()}_GPX",
            user_agent=request.session.user_agent[:200],
            virtual=True,
        )
        device.add_locations(loc_array)
        competitor.device = device
        competitor.start_time = epoch_to_datetime(start_time)
        competitor.save()

    if len(loc_array) == 0:
        raise ValidationError("No locations within event schedule were detected")

    return Response(
        {
            "id": competitor.aid,
            "location_count": len(loc_array),
        },
        status=status.HTTP_201_CREATED,
    )


@swagger_auto_schema(
    method="get",
    operation_id="event_data",
    operation_description="Read competitors data from an event. You need to be identified as event organiser admin to list private events data.",
    tags=["Events"],
    responses={
        "200": openapi.Response(
            description="Success response",
            examples={
                "application/json": {
                    "competitors": [
                        {
                            "id": "pwaCro4TErI",
                            "locations_encoded": "<encoded data>",
                            "name": "Olav Lundanes (Halden SK)",
                            "short_name": "Halden SK",
                            "start_time": "2019-06-15T20:00:00Z",
                            "battery_level": 84,
                            "color": "#ff0000",
                            "categories": ["Black", "HE"],
                        }
                    ],
                    "nb_points": 0,
                    "duration": 0.009621381759643555,
                    "timestamp": 1615986763.638066,
                    "key": 123456,
                }
            },
        ),
    },
)
@api_GET_view
def event_data(request, event_id):
    t0_perf = time.perf_counter()
    t0 = time.time()

    cache_ts = int(t0 // EVENT_CACHE_INTERVAL_LIVE)
    cache_key = f"event:{event_id}:data:{cache_ts}:live"
    if data := cache.get(cache_key):
        headers = {
            "ETag": f'W/"{safe64encodedsha(json.dumps(data))}"',
            "X-Cache-Hit": 1,
        }
        return Response(data, headers=headers)

    event = (
        Event.objects.select_related("club")
        .filter(aid=event_id, start_date__lt=now())
        .first()
    )
    if not event:
        response = {"error": "No event match this id"}
        return Response(response)

    if not event.is_live:
        cache_ts = int(t0 // EVENT_CACHE_INTERVAL_ARCHIVED)
        cache_key = f"event:{event_id}:data:{cache_ts}:archived"
        if data := cache.get(cache_key):
            headers = {
                "ETag": f'W/"{safe64encodedsha(json.dumps(data))}"',
                "X-Cache-Hit": 1,
            }
            return Response(data, headers=headers)

    event.check_user_permission(request.user)

    total_nb_pts = 0
    competitors_data = []

    for competitor, from_date, end_date in event.iterate_competitors():
        locations_encoded = ""
        if competitor.device_id:
            locations_encoded, nb_pts = competitor.device.get_locations_between_dates(
                from_date, end_date, encode=True
            )
            total_nb_pts += nb_pts
        competitor_data = {
            "id": competitor.aid,
            "locations_encoded": locations_encoded,
            "name": competitor.name,
            "short_name": competitor.short_name,
            "start_time": competitor.start_time,
        }
        if competitor.tags:
            competitor_data["categories"] = competitor.categories
        if competitor.color:
            competitor_data["color"] = competitor.color
        if event.is_live and competitor.device_id:
            competitor_data["battery_level"] = competitor.device.battery_level
        competitors_data.append(competitor_data)

    response = {
        "competitors": competitors_data,
        "nb_points": total_nb_pts,
        "duration": (time.perf_counter() - t0_perf),
        "timestamp": time.time(),
        "key": cache_ts,
    }

    cache.set(
        cache_key,
        response,
        60
        + (
            EVENT_CACHE_INTERVAL_LIVE
            if event.is_live
            else EVENT_CACHE_INTERVAL_ARCHIVED
        ),
    )

    headers = {"ETag": f'W/"{safe64encodedsha(json.dumps(response))}"'}
    if event.privacy == PRIVACY_PRIVATE:
        headers["Cache-Control"] = "Private"

    return Response(response, headers=headers)


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_view
def event_new_data(request, event_id, key):
    t0_perf = time.perf_counter()
    t0 = time.time()

    cache_ts = int(t0 // EVENT_CACHE_INTERVAL_LIVE)
    cache_key = f"event:{event_id}:data-diff:{key}:{cache_ts}"

    if cache_ts == key:
        response = {
            "competitors": [],
            "duration": (time.perf_counter() - t0_perf),
            "timestamp": time.time(),
            "key": cache_ts,
            "partial": 1,
        }
        headers = {"ETag": f'W/"{safe64encodedsha(json.dumps(response))}"'}
        return Response(response, headers=headers)

    if cached_resp := cache.get(cache_key):
        return Response(cached_resp, headers={"X-Cache-Hit": 1})

    src_cache_key = f"event:{event_id}:data:{key}:live"
    prev_data = cache.get(src_cache_key)
    if not prev_data:
        return Response(
            "Previous version is not cached anymore",
            status=status.HTTP_410_GONE,
        )

    req = HttpRequest()
    req.method = "GET"
    req.user = request.user
    req.session = request.session
    current_resp = event_data(req, event_id)
    if not current_resp.data or current_resp.data.get("error"):
        raise Http404()

    current_data = current_resp.data

    prev_competitors = {}
    for competitor in prev_data.get("competitors", []):
        prev_competitors[competitor["id"]] = competitor

    competitors_data = []

    for competitor in current_data.get("competitors", []):
        if categories := competitor.get("categories"):
            competitor["categories"] = " ".join(categories)
        if old_match := prev_competitors.get(competitor["id"]):
            if categories := old_match.get("categories"):
                old_match["categories"] = " ".join(categories)

            old_version = set(old_match.items())
            new_version = set(competitor.items())
            diff = dict(new_version - old_version)

            if not diff:
                continue

            diff["id"] = competitor.get("id")
            if "categories" in diff:
                diff["categories"] = diff["categories"].split(" ")
            if "locations_encoded" in diff:
                if old_location_encoded := old_match.get("locations_encoded"):
                    diff["locations_encoded"] = gps_data_codec.encoded_diff(
                        old_location_encoded, competitor.get("locations_encoded")
                    )
                else:
                    diff["locations_encoded"] = competitor.get("locations_encoded")
            competitors_data.append(diff)
        else:
            competitors_data.append(competitor)
    response = {
        "competitors": competitors_data,
        "duration": (time.perf_counter() - t0_perf),
        "timestamp": time.time(),
        "key": current_data.get("key"),
        "partial": 1,
    }

    headers = {"ETag": f'W/"{safe64encodedsha(json.dumps(response))}"'}
    if cache_control := current_resp.headers.get("Cache-Control"):
        headers["Cache-Control"] = cache_control

    cache.set(cache_key, response, DURATION_ONE_MINUTE)

    return Response(response, headers=headers)


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_view
def event_zip(request, event_id):
    event = (
        Event.objects.select_related("club")
        .filter(aid=event_id, start_date__lt=now())
        .select_related("map")
        .prefetch_related(
            Prefetch(
                "map_assignations",
                queryset=MapAssignation.objects.select_related("map"),
            )
        )
        .first()
    )
    if not event:
        response = {"error": "No event match this id"}
        return Response(response)
    event.check_user_permission(request.user)

    archive = BytesIO()
    with ZipFile(archive, "w") as fp:
        for competitor, from_date, end_date in event.iterate_competitors():
            if competitor.device_id:
                data = competitor.device.gpx(from_date, end_date)
                filename = f"gpx/{competitor.name} [{competitor.aid}].gpx"
                with fp.open(filename, "w") as gpx_file:
                    gpx_file.write(data.encode("utf-8"))
        raster_maps = []
        if event.map:
            raster_maps.append((event.map, event.map_title or "Main map"))
        for ass in event.map_assignations.all():
            raster_maps.append((ass.map, ass.title))
        for raster_map, title in raster_maps:
            data = raster_map.kmz
            filename = f"kmz/{title}.kmz"
            with fp.open(filename, "w") as kmz_file:
                kmz_file.write(data)
    response_data = archive.getvalue()
    headers = {"ETag": f'W/"{safe64encodedsha(response_data)}"'}
    if event.privacy == PRIVACY_PRIVATE:
        headers["Cache-Control"] = "Private"
    response = StreamingHttpRangeResponse(
        request, response_data, content_type="application/zip", headers=headers
    )
    response["Content-Disposition"] = set_content_disposition(f"{event.name}.zip")
    return response


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_view
def ip_latlon(request):
    headers = {"Cache-Control": "Private"}
    try:
        g = GeoIP2()
        lat, lon = g.lat_lon(request.META["REMOTE_ADDR"])
    except Exception:
        return Response({"status": "fail"}, headers=headers)
    return Response({"status": "success", "lat": lat, "lon": lon}, headers=headers)


@swagger_auto_schema(
    method="post",
    operation_id="device_add_locations",
    operation_description="Uploads some locations for a given device. You need to be identified to get a valid answer.",
    tags=["Devices"],
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            "device_id": openapi.Schema(
                type=openapi.TYPE_STRING,
                description="<device id>",
            ),
            "latitudes": openapi.Schema(
                type=openapi.TYPE_STRING,
                description=(
                    "List of locations latitudes (in degrees) separated by commas"
                ),
                example="60.12345,60.12346,60.12347",
            ),
            "longitudes": openapi.Schema(
                type=openapi.TYPE_STRING,
                description=(
                    "List of locations longitudes (in degrees) separated by commas"
                ),
                example="20.12345,20.12346,20.12347",
            ),
            "timestamps": openapi.Schema(
                type=openapi.TYPE_STRING,
                description=(
                    "List of locations timestamps "
                    "(UNIX epoch in seconds) separated by commas"
                ),
                example="1661489045,1661489046,1661489047",
            ),
            "battery": openapi.Schema(
                type=openapi.TYPE_INTEGER,
                description="Battery load percentage value",
                example="85",
            ),
        },
        required=["device_id", "latitudes", "longitudes", "timestamps"],
    ),
    responses={
        "201": openapi.Response(
            description="Success response",
            examples={
                "application/json": {
                    "status": "ok",
                    "device_id": "<device id>",
                    "location_count": "3",
                }
            },
        ),
        "400": openapi.Response(
            description="Validation Error",
            examples={"application/json": ["<error message>"]},
        ),
    },
)
@api_POST_view
@throttle_classes([PostDataThrottle])
def locations_api_gw(request):
    secret_provided = request.data.get(
        "secret"
    )  # secret was used in legacy apps before v1.6.0
    battery_level_posted = request.data.get("battery")
    device_id = request.data.get("device_id")
    if not device_id:
        raise ValidationError("Missing device_id parameter")
    device_id = str(device_id)
    if re.match(r"^[0-9]+$", device_id):
        if secret_provided not in settings.POST_LOCATION_SECRETS and (
            not request.user.is_authenticated or not request.user.is_superuser
        ):
            raise PermissionDenied(
                "Authentication Failed. Only validated apps are allowed"
            )

    device = Device.objects.filter(aid=device_id).first()
    if not device:
        raise ValidationError("No such device ID")

    device_user_agent = request.session.user_agent[:200]
    if not device.user_agent or device_user_agent != device.user_agent:
        device.user_agent = device_user_agent

    try:
        lats = [float(x) for x in request.data.get("latitudes", "").split(",") if x]
        lons = [float(x) for x in request.data.get("longitudes", "").split(",") if x]
        times = [
            int(float(x)) for x in request.data.get("timestamps", "").split(",") if x
        ]
    except ValueError:
        raise ValidationError("Invalid data format")
    if not (len(lats) == len(lons) == len(times)):
        raise ValidationError(
            "Latitudes, longitudes, and timestamps, should have same amount of points"
        )
    loc_array = []
    for tim, lat, lon in zip(times, lats, lons):
        if tim and lat and lon:
            try:
                validate_longitude(lon)
            except DjangoValidationError:
                raise ValidationError("Invalid longitude value")
            try:
                validate_latitude(lat)
            except DjangoValidationError:
                raise ValidationError("Invalid latitude value")
            loc_array.append((tim, lat, lon))

    if battery_level_posted:
        try:
            battery_level = int(battery_level_posted)
        except Exception:
            pass
            # raise ValidationError("Invalid battery_level value type")
            # Do not raise exception to stay compatible with legacy apps
        else:
            if battery_level < 0 or battery_level > 100:
                # raise ValidationError("battery_level value not in 0-100 range")
                # Do not raise exception to stay compatible with legacy apps
                pass
            else:
                device.battery_level = battery_level

    if len(loc_array) > 0:
        device.add_locations(loc_array, save=False)
    device.save()
    return Response(
        {"status": "ok", "location_count": len(loc_array), "device_id": device.aid},
        status=status.HTTP_201_CREATED,
    )


class DataRenderer(renderers.BaseRenderer):
    media_type = "application/download"
    format = "raw"
    charset = None
    render_style = "binary"

    def render(self, data, media_type=None, renderer_context=None):
        return data


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_view
def get_version(request):
    return Response({"v": git_master_hash()})


@swagger_auto_schema(
    method="post",
    operation_id="device_create",
    operation_description="Request a device ID. You need to be identified to get a valid answer unless you provide a valid IMEI.",
    tags=["Devices"],
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            "imei": openapi.Schema(
                type=openapi.TYPE_STRING,
                example="<IMEI>",
                description="Hardware GPS tracking device IMEI (Optional)",
            ),
        },
        required=[],
    ),
    responses={
        "200": openapi.Response(
            description="Success response",
            examples={
                "application/json": {
                    "status": "ok",
                    "imei": "<IMEI>",
                    "device_id": "<device_id>",
                }
            },
        ),
        "400": openapi.Response(
            description="Validation Error",
            examples={"application/json": ["<error message>"]},
        ),
    },
)
@api_POST_view
def create_device_id(request):
    imei = request.data.get("imei")
    if imei:
        try:
            validate_imei(imei)
        except Exception as e:
            raise ValidationError(str(e.message))
        status_code = status.HTTP_200_OK
        try:
            idevice = (
                ImeiDevice.objects.select_related("device")
                .defer("device__locations_encoded")
                .get(imei=imei)
            )
        except ImeiDevice.DoesNotExist:
            device = Device.objects.create()
            idevice = ImeiDevice.objects.create(imei=imei, device=device)
            status_code = status.HTTP_201_CREATED
        else:
            device = idevice.device
            if re.search(r"[^0-9]", device.aid):
                if not device.competitor_set.filter(
                    event__end_date__gte=now()
                ).exists():
                    device.aid = random_device_id()
                    status_code = status.HTTP_201_CREATED
        return Response(
            {"status": "ok", "device_id": device.aid, "imei": imei}, status=status_code
        )
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise PermissionDenied(
            "Authentication Failed, Only validated apps can create new device IDs"
        )
    device = Device.objects.create(user_agent=request.session.user_agent[:200])
    return Response(
        {"status": "ok", "device_id": device.aid}, status=status.HTTP_201_CREATED
    )


@swagger_auto_schema(
    method="get",
    operation_id="server_time_get",
    operation_description="Return the server unix epoch time.",
    tags=["Miscellaneous"],
    responses={
        "200": openapi.Response(
            description="Success response",
            examples={"application/json": {"time": 1615987017.7934635}},
        ),
    },
)
@swagger_auto_schema(
    method="post",
    operation_id="server_time_post",
    operation_description="Return the server unix epoch time.",
    tags=["Miscellaneous"],
    responses={
        "200": openapi.Response(
            description="Success response",
            examples={"application/json": {"time": 1615987017.7934635}},
        ),
    },
)
@api_GET_POST_view
def get_time(request):
    return Response({"time": time.time()}, headers={"Cache-Control": "no-cache"})


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_view
@permission_classes([IsAuthenticated])
def user_search(request):
    users = []
    q = request.GET.get("q")
    if q and len(q) > 2:
        users = User.objects.filter(username__icontains=q).values_list(
            "id", "username"
        )[:10]
    return Response({"results": [{"id": u[0], "username": u[1]} for u in users]})


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_view
@permission_classes([IsAuthenticated])
def user_view(request):
    user = request.user
    clubs = Club.objects.filter(admins=user, is_personal_page=False)
    output = {
        "username": user.username,
        "clubs": [{"name": c.name, "slug": c.slug} for c in clubs],
        "has_mapdump": user.has_personal_page,
    }
    return Response(output)


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_view
def device_search(request):
    devices = []
    aid = request.GET.get("aid", "") == "true"
    q = request.GET.get("q")
    if q and len(q) > 4:
        devices = Device.objects.filter(aid__startswith=q, virtual=False).values_list(
            "id", "aid"
        )[:10]
    return Response(
        {"results": [{"id": d[1 if aid else 0], "device_id": d[1]} for d in devices]}
    )


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_view
def device_info(request, device_id):
    device = Device.objects.filter(aid=device_id, virtual=False).first()
    if not device:
        res = {"error": "No device match this id"}
        return Response(res)

    return Response(
        {
            "id": device.aid,
            "last_position": (
                {
                    "timestamp": device.last_location[LOCATION_TIMESTAMP_INDEX],
                    "coordinates": {
                        "latitude": device.last_location[LOCATION_LATITUDE_INDEX],
                        "longitude": device.last_location[LOCATION_LONGITUDE_INDEX],
                    },
                }
                if device.last_location
                else None
            ),
        }
    )


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_view
def device_registrations(request, device_id):
    device = get_object_or_404(Device, aid=device_id, virtual=False)
    competitors = device.competitor_set.filter(event__end_date__gte=now())
    return Response({"count": competitors.count()})


@swagger_auto_schema(
    methods=["patch", "delete"],
    auto_schema=None,
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def device_ownership_api_view(request, club_slug, device_id):
    club = get_object_or_404(
        Club.objects.filter(admins=request.user, is_personal_page=False),
        slug__iexact=club_slug,
    )
    device = get_object_or_404(Device, aid=device_id, virtual=False)

    ownership, created = DeviceClubOwnership.objects.get_or_create(
        device=device, club=club
    )
    if request.method == "PATCH":
        nick = request.data.get("nickname")
        if nick and len(nick) > 12:
            if created:
                ownership.delete()
            raise ValidationError("Can not be more than 12 characters")

        activate_gpsseuranta = request.data.get("activate-gpsseuranta-relay")
        if activate_gpsseuranta:
            if not device.gpsseuranta_known:
                raise ValidationError("Device is not known by GPSSeuranta.net")

        response = {}
        if activate_gpsseuranta:
            device.gpsseuranta_relay_until = now() + timedelta(hours=24)
            device.save()
            response["gpsseuranta_until"] = device.gpsseuranta_relay_until
        if nick:
            ownership.nickname = nick
            ownership.save()
            response["nickname"] = nick

        return Response(response)

    if request.method == "DELETE":
        ownership.delete()
        return HttpResponse(status=status.HTTP_204_NO_CONTENT)


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_HEAD_view
def event_map_download(request, event_id, index="1", **kwargs):
    event, raster_map, title = Event.get_public_map_at_index(
        request.user, event_id, index
    )
    headers = {}
    if event.privacy == PRIVACY_PRIVATE:
        headers["Cache-Control"] = "Private"

    mime = get_image_mime_from_request(kwargs.get("extension"), raster_map.mime_type)

    resp = serve_image_from_s3(
        request,
        raster_map.image,
        (f"{event.name} - {title}_" f"{raster_map.calibration_string_for_naming}_"),
        mime=mime,
    )
    return resp


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_HEAD_view
def event_kmz_download(request, event_id, index="1"):
    event, raster_map, title = Event.get_public_map_at_index(
        request.user, event_id, index
    )
    kmz_data = raster_map.kmz

    headers = {}
    if event.privacy == PRIVACY_PRIVATE:
        headers["Cache-Control"] = "Private"

    filename = f"{event.name} - {title}.kmz"
    response = StreamingHttpRangeResponse(
        request,
        kmz_data,
        content_type="application/vnd.google-earth.kmz",
        headers=headers,
    )
    response["ETag"] = f'W/"{safe64encodedsha(kmz_data)}"'
    response["Content-Disposition"] = set_content_disposition(filename)
    return response


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_HEAD_view
def event_geojson_download(request, event_id):
    event = get_object_or_404(
        Event.objects.exclude(geojson_layer="").exclude(geojson_layer__isnull=True),
        aid=event_id,
        start_date__lt=now(),
    )
    event.check_user_permission(request.user)

    headers = {}
    if event.privacy == PRIVACY_PRIVATE:
        headers["Cache-Control"] = "Private"

    filename = f"{event.name}.geojson"
    return serve_from_s3(
        settings.AWS_S3_BUCKET,
        request,
        event.geojson_layer.file.name,
        filename=filename,
        mime="application/json",
        headers=headers,
    )


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_HEAD_view
@permission_classes([IsAuthenticated])
def map_kmz_download(request, map_id, *args, **kwargs):
    club_list = Club.objects.filter(admins=request.user, is_personal_page=False)
    raster_map = get_object_or_404(Map, aid=map_id, club__in=club_list)
    kmz_data = raster_map.kmz
    response = StreamingHttpRangeResponse(
        request,
        kmz_data,
        content_type="application/vnd.google-earth.kmz",
        headers={"Cache-Control": "Private"},
    )
    filename = f"{raster_map.name}.kmz"
    response["Content-Disposition"] = set_content_disposition(filename)
    return response


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_HEAD_view
def competitor_gpx_download(request, competitor_id):
    competitor = get_object_or_404(
        Competitor.objects.select_related("event", "event__club", "device"),
        aid=competitor_id,
        start_time__lt=now(),
    )
    event = competitor.event

    event.check_user_permission(request.user)

    gpx_data = competitor.gpx

    headers = {}
    if event.privacy == PRIVACY_PRIVATE:
        headers["Cache-Control"] = "Private"

    response = StreamingHttpRangeResponse(
        request,
        gpx_data.encode(),
        content_type="application/gpx+xml",
        headers=headers,
    )
    filename = f"{competitor.event.name} - {competitor.name}.gpx"
    response["Content-Disposition"] = set_content_disposition(filename)
    return response


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_view
def two_d_rerun_race_status(request):
    event_id = request.GET.get("eventid")
    if not event_id:
        raise Http404()
    map_idx = 1
    if "/" in event_id:
        event_id, map_idx = event_id.split("/", 1)
        try:
            map_idx = int(map_idx)
        except Exception:
            raise Http404()
    if map_idx < 1:
        raise Http404()
    event, raster_map, _ = Event.get_public_map_at_index(
        request.user, event_id, map_idx, load_competitors=True
    )

    event.check_user_permission(request.user)

    response_json = {
        "status": "OK",
        "racename": event.name,
        "racestarttime": event.start_date,
        "raceendtime": event.end_date,
        "mapurl": (f"{event.get_absolute_map_url()}-{map_idx}?.jpg"),
        "caltype": "3point",
        "mapw": raster_map.width,
        "maph": raster_map.height,
        "calibration": [
            [
                raster_map.bound[0].longitude,
                raster_map.bound[0].latitude,
                0,
                0,
            ],
            [
                raster_map.bound[1].longitude,
                raster_map.bound[1].latitude,
                raster_map.width,
                0,
            ],
            [
                raster_map.bound[2].longitude,
                raster_map.bound[2].latitude,
                0,
                raster_map.height,
            ],
        ],
        "competitors": [],
    }
    for c in event.competitors.all():
        response_json["competitors"].append([c.aid, c.name, c.start_time])

    response_raw = str(json.dumps(response_json), "utf-8")
    content_type = "application/json"
    callback = request.GET.get("callback")
    if callback:
        response_raw = f"/**/{callback}({response_raw});"
        content_type = "text/javascript; charset=utf-8"

    headers = {}
    if event.privacy == PRIVACY_PRIVATE:
        headers["Cache-Control"] = "Private"

    return HttpResponse(response_raw, content_type=content_type, headers=headers)


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_view
def two_d_rerun_race_data(request):
    event_id = request.GET.get("eventid")
    if "/" in event_id:
        event_id, _ = event_id.split("/", 1)
    if not event_id:
        raise Http404()
    event = get_object_or_404(
        Event.objects.prefetch_related(
            Prefetch(
                "competitors",
                queryset=Competitor.objects.select_related("device").order_by(
                    "start_time", "name"
                ),
            )
        ),
        aid=event_id,
        start_date__lt=now(),
    )

    event.check_user_permission(request.user)

    total_nb_pts = 0
    results = []
    for competitor, from_date, end_date in event.iterate_competitors():
        if competitor.device_id:
            locations, nb_pts = competitor.device.get_locations_between_dates(
                from_date, end_date
            )
            total_nb_pts += nb_pts
            results += [
                [
                    competitor.aid,
                    location[LOCATION_LATITUDE_INDEX],
                    location[LOCATION_LONGITUDE_INDEX],
                    0,
                    epoch_to_datetime(location[LOCATION_TIMESTAMP_INDEX]),
                ]
                for location in locations
            ]
    response_json = {
        "containslastpos": 1,
        "lastpos": total_nb_pts,
        "status": "OK",
        "data": results,
    }
    response_raw = str(json.dumps(response_json), "utf-8")
    content_type = "application/json"
    callback = request.GET.get("callback")
    if callback:
        response_raw = f"/**/{callback}({response_raw});"
        content_type = "text/javascript; charset=utf-8"

    headers = {}
    if event.privacy == PRIVACY_PRIVATE:
        headers["Cache-Control"] = "Private"

    return HttpResponse(
        response_raw,
        content_type=content_type,
        headers=headers,
    )


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_view
def third_party_event(request, provider, uid):
    cache_key = f"3rd_party_event_detail:{uid}"
    if data := cache.get(cache_key):
        return Response(data, headers={"X-Cache-Hit": 1})

    if provider == "gpsseuranta":
        proxy = GpsSeurantaNet()
    elif provider == "loggator":
        proxy = Loggator()
    elif provider == "livelox":
        proxy = Livelox()
    else:
        raise Http404()
    try:
        proxy.parse_init_data(uid)
    except Exception:
        raise Http404()

    event = proxy.get_event()
    event.map = proxy.get_map()

    output = {
        "event": {
            "id": event.aid,
            "name": event.name,
            "start_date": event.start_date,
            "end_date": event.end_date,
            "slug": event.slug,
            "club": {
                "name": provider,
                "slug": provider,
            },
            "privacy": "secret",
            "open_registration": False,
            "open_route_upload": False,
            "url": request.build_absolute_uri(event.get_absolute_url()),
            "shortcut": "",
            "backdrop": "blank",
            "send_interval": event.send_interval,
            "tail_length": event.tail_length,
        },
        "data_url": request.build_absolute_uri(event.get_api_data_url()),
        "announcement": "",
        "maps": [],
    }
    if event.map:
        map_data = {
            "title": "main",
            "coordinates": event.map.bound,
            "rotation": event.map.north_declination,
            "hash": event.map.hash,
            "max_zoom": event.map.max_zoom,
            "modification_date": event.map.modification_date,
            "default": True,
            "id": uid,
            "url": f"{event.club.nice_url}{event.slug}/map",
            "wms": False,
        }
        output["maps"].append(map_data)

    cache.set(cache_key, output, DURATION_ONE_MINUTE)

    return Response(output)


@swagger_auto_schema(
    method="get",
    auto_schema=None,
)
@api_GET_view
def third_party_event_data(request, provider, uid):
    cache_key = f"3rd_party_event_data:{uid}"
    if data := cache.get(cache_key):
        return Response(data, headers={"X-Cache-Hit": 1})

    if provider == "gpsseuranta":
        proxy = GpsSeurantaNet()
    elif provider == "loggator":
        proxy = Loggator()
    elif provider == "livelox":
        proxy = Livelox()
    else:
        raise Http404()
    try:
        proxy.parse_init_data(uid)
    except Exception:
        raise Http404()

    event = proxy.get_event()
    dev_data = proxy.get_competitor_devices_data(event)
    competitors_data = proxy.get_competitors_data()
    output = {"competitors": [], "key": None}
    for c_id, competitor in competitors_data.items():
        locs = dev_data.get(c_id, [])
        output["competitors"].append(
            {
                "id": c_id,
                "locations_encoded": gps_data_codec.encode(locs),
                "name": competitor.name,
                "short_name": competitor.short_name,
                "start_time": competitor.start_time,
            }
        )

    cache.set(cache_key, output, 10)

    return Response(output)


class RelativeURLField(serializers.ReadOnlyField):
    """
    Field that returns a link to the relative url.
    """

    def to_representation(self, value):
        request = self.context.get("request")
        url = request and request.build_absolute_uri(value) or ""
        return url


class MapSerializer(serializers.ModelSerializer):
    class Meta:
        model = Map
        fields = (
            "bound",
            "size",
        )


class EventSerializer(serializers.ModelSerializer):
    map = MapSerializer()

    class Meta:
        model = Event
        field = (
            "name",
            "map",
        )


class EffortSerializer(serializers.ModelSerializer):
    id = serializers.ReadOnlyField(source="aid")
    event = EventSerializer()

    class Meta:
        model = Competitor
        fields = (
            "id",
            "name",
            "short_name",
            "start_time",
            "timezone",
            "country_code",
            "distance",
            "duration",
            "event",
        )


@swagger_auto_schema(
    method="post",
    auto_schema=None,
)
@api_POST_view
@permission_classes([IsAuthenticated])
def md_create_effort_view(request):
    user = request.user
    club = user.personal_page
    if not club:
        raise PermissionDenied()

    effort_name = request.data.get("name")

    map_image_file = request.FILES.get("map_image")
    map_corners_coords = request.data.get("map_image_corners_coords")
    map = Map(
        name=f"{effort_name} map",
        calibration_string=map_corners_coords,
    )
    map.image.save(
        "tmp_name",
        ContentFile(map_image_file.file.getbuffer()),
    )

    gps_data_raw = request.data.get("gps_data")
    gps_data = json.loads(gps_data_raw)
    device = Device(virtual=True)
    device.add_locations(gps_data)
    device.save()

    trk_points = device.locations

    event = Event(
        name=effort_name,
        slug=short_random_slug(),
        start_date=epoch_to_datetime(trk_points[0][0]),
        end_date=epoch_to_datetime(trk_points[-1][0]),
        map=map,
    )
    event.save()

    effort = Competitor.objects.create(
        name=user.username,
        short_name=user.username,
        user=user,
        event=event,
        device=device,
    )
    return Response(EffortSerializer(effort).data, status_code=status.HTTP_201_CREATED)
