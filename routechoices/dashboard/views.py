import csv
from copy import deepcopy
from io import StringIO

from allauth.account.adapter import get_adapter
from allauth.account.forms import default_token_generator
from allauth.account.models import EmailAddress
from allauth.account.signals import password_changed, password_reset
from allauth.account.utils import user_username
from allauth.account.views import EmailView
from allauth.decorators import rate_limit
from allauth.utils import build_absolute_uri
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Case, Prefetch, Q, Value, When
from django.dispatch import receiver
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.timezone import now
from django_hosts.resolvers import reverse
from hijack.views import ReleaseUserView
from kagi.views.backup_codes import BackupCodesView
from user_sessions.views import SessionDeleteOtherView

from invitations.forms import InviteForm
from routechoices.core.models import (
    Club,
    Competitor,
    Device,
    DeviceClubOwnership,
    Event,
    EventSet,
    ImeiDevice,
    Map,
    Notice,
)
from routechoices.dashboard.forms import (
    ClubDomainForm,
    ClubForm,
    CompetitorFormSet,
    DeviceForm,
    EventForm,
    EventSetForm,
    ExtraMapFormSet,
    MapForm,
    MergeMapsForm,
    NoticeForm,
    RequestInviteForm,
    UploadGPXForm,
    UploadKmzForm,
    UploadMapGPXForm,
    UserForm,
)
from routechoices.lib.helpers import (
    get_current_site,
    set_content_disposition,
    short_random_key,
)
from routechoices.lib.s3 import serve_from_s3
from routechoices.lib.streaming_response import StreamingHttpRangeResponse

DEFAULT_PAGE_SIZE = 25


def requires_club_in_session(function):
    def wrap(request, *args, **kwargs):
        club_slug = kwargs.pop("club_slug", None)
        if not club_slug:
            club_select_page = reverse("dashboard_club:select_view", host="dashboard")
            return redirect(f"{club_select_page}?next={request.path}")

        club = get_object_or_404(
            Club,
            is_personal_page=False,
            slug__iexact=club_slug,
            admins=request.user,
        )

        obj = None
        if obj_aid := kwargs.get("event_id"):
            obj = get_object_or_404(
                Event.objects.select_related("club"),
                aid=obj_aid,
                club=club,
            )
        elif obj_aid := kwargs.get("map_id"):
            obj = get_object_or_404(
                Map.objects.select_related("club"),
                aid=obj_aid,
                club=club,
            )
        elif obj_aid := kwargs.get("event_set_id"):
            obj = get_object_or_404(
                EventSet.objects.select_related("club"),
                aid=obj_aid,
                club=club,
            )
        request.object = obj
        request.club = club
        return function(request, *args, **kwargs)

    wrap.__doc__ = function.__doc__
    wrap.__name__ = function.__name__
    return wrap


@login_required
def home_view(request):
    participations = request.user.participations.select_related(
        "event", "event__club", "device"
    ).order_by("-event__start_date")
    has_more_participations = participations.count() > 5
    club_list = Club.objects.filter(admins=request.user, is_personal_page=False)
    return render(
        request,
        "dashboard/landing.html",
        {
            "clubs": club_list,
            "participations": participations[:5],
            "has_more_participations": has_more_participations,
        },
    )


@login_required
@requires_club_in_session
def club_invite_add_view(request):
    club = request.club
    email = request.GET.get("email")

    if request.method == "POST":
        form = InviteForm(request.POST, club=club)
        if form.is_valid():
            email = form.cleaned_data["email"]
            invite = form.save(email, club)
            invite.inviter = request.user
            invite.save()
            invite.send_invitation(request)
            messages.success(request, "Invite sent successfully")
            return redirect("dashboard_club:edit_view", club_slug=request.club.slug)
    else:
        form = InviteForm(initial={"email": email})
    return render(
        request,
        "dashboard/invite_add.html",
        {
            "club": club,
            "form": form,
        },
    )


@login_required
def club_request_invite_view(request):
    if request.method == "POST":
        form = RequestInviteForm(request.user, request.POST)
        if form.is_valid():
            club = form.cleaned_data["club"]
            current_site = get_current_site()
            url = build_absolute_uri(
                request,
                reverse(
                    "dashboard_club:send_invite_view",
                    host="dashboard",
                    kwargs={"club_slug": club.slug},
                ),
            )
            requester_email = (
                EmailAddress.objects.filter(user_id=request.user.id, primary=True)
                .first()
                .email
            )
            context = {
                "site_name": current_site.name,
                "email": requester_email,
                "club": club,
                "send_invite_url": f"{url}?email={requester_email}",
            }
            club_admins_ids = list(club.admins.values_list("id", flat=True))
            emails = list(
                EmailAddress.objects.filter(
                    user_id__in=club_admins_ids, primary=True
                ).values_list("email", flat=True)
            )
            get_adapter(request).send_mail(
                "account/email/request_invite",
                emails,
                context,
            )
            messages.success(request, "Invite requested successfully")
            return redirect("home_view")
    else:
        form = RequestInviteForm(user=request.user)
    return render(request, "dashboard/request_invite.html", {"form": form})


@login_required
def club_select_view(request):
    club_list = Club.objects.filter(admins=request.user, is_personal_page=False)

    paginator = Paginator(club_list, DEFAULT_PAGE_SIZE)
    page = request.GET.get("page")
    next_page = request.GET.get("next", "")
    clubs = paginator.get_page(page)

    return render(
        request, "dashboard/club_list.html", {"clubs": clubs, "next": next_page}
    )


@login_required
def account_edit_view(request):
    if request.method == "POST":
        form = UserForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Changes saved.")
            return redirect("account_edit_view")
    else:
        form = UserForm(instance=request.user)
    return render(
        request,
        "dashboard/account_edit.html",
        {
            "user": request.user,
            "form": form,
        },
    )


@login_required
def account_delete_view(request):
    token_generator = default_token_generator
    token_generator.key_salt = "AccountDeletionTokenGenerator"
    user = request.user
    if request.method == "POST":
        conf_key = request.POST.get("confirmation_key")
        if conf_key:
            if token_generator.check_token(user, conf_key):
                request.user.delete()
                request.session.flush()
                messages.success(request, "Account deleted.")
                return redirect(reverse("site:landing_page", host="www"))
            return render(
                request,
                "dashboard/account_delete_confirm.html",
                {"confirmation_valid": False},
            )

        temp_key = token_generator.make_token(user)
        current_site = get_current_site()
        url = build_absolute_uri(
            request, reverse("account_delete_view", host="dashboard")
        )
        context = {
            "current_site": current_site,
            "user": user,
            "account_deletion_url": f"{url}?confirmation_key={temp_key}",
            "request": request,
        }
        context["username"] = user_username(user)
        requester_email = EmailAddress.objects.filter(
            user_id=request.user.id, primary=True
        ).first()
        if requester_email:
            requester_email = requester_email.email
        else:
            requester_email = request.user.email

        get_adapter(request).send_mail(
            "account/email/account_delete", requester_email, context
        )
        return render(
            request,
            "dashboard/account_delete.html",
            {"sent": True},
        )
    conf_key = request.GET.get("confirmation_key")
    if conf_key:
        if token_generator.check_token(user, conf_key):
            return render(
                request,
                "dashboard/account_delete_confirm.html",
                {
                    "confirmation_valid": True,
                    "confirmation_key": conf_key,
                },
            )
        return render(
            request,
            "dashboard/account_delete_confirm.html",
            {"confirmation_valid": False},
        )
    return render(
        request,
        "dashboard/account_delete.html",
        {"sent": False},
    )


@login_required
@requires_club_in_session
def device_list_view(request):
    club = request.club

    ordering_nickname_blank_last = Case(
        When(nickname="", then=Value(1)), default=Value(0)
    )
    ordering_timestamp_blank_last = Case(
        When(device___last_location_datetime=None, then=Value(1)), default=Value(0)
    )

    ordering_query = request.GET.get("sort_by")
    if ordering_query == "nickname_asc":
        ordering = [ordering_nickname_blank_last, "nickname", "device__aid"]
    elif ordering_query == "nickname_dsc":
        ordering = ["-nickname", "device__aid"]
    elif ordering_query == "device-id_asc":
        ordering = ["device__aid"]
    elif ordering_query == "device-id_dsc":
        ordering = ["-device__aid"]
    elif ordering_query == "seen_asc":
        ordering = ["device___last_location_datetime"]
    elif ordering_query == "seen_dsc":
        ordering = [ordering_timestamp_blank_last, "-device___last_location_datetime"]
    elif ordering_query == "battery_asc":
        ordering = ["device__battery_level"]
    elif ordering_query == "battery_dsc":
        ordering = ["-device__battery_level"]
    else:
        ordering = [ordering_nickname_blank_last, "nickname", "device__aid"]

    device_owned_list = (
        DeviceClubOwnership.objects.filter(club=club)
        .select_related("club", "device")
        .defer("device__locations_encoded")
        .order_by(*ordering)
    )
    paginator = Paginator(device_owned_list, DEFAULT_PAGE_SIZE)
    page = request.GET.get("page")
    devices = paginator.get_page(page)
    devices_listed = devices.object_list.values_list("device__id")
    competitors = (
        Competitor.objects.select_related("event")
        .filter(device_id__in=devices_listed, start_time__lt=now())
        .order_by("device_id", "-start_time")
    )
    competitors = competitors.distinct("device_id")
    last_usage = {}
    for competitor in competitors:
        last_usage[competitor.device_id] = f"{competitor.event} ({competitor})"
    return render(
        request,
        "dashboard/device_list.html",
        {"club": club, "devices": devices, "last_usage": last_usage},
    )


@login_required
@requires_club_in_session
def merge_maps(request):
    club = request.club
    if request.method == "POST":
        form = MergeMapsForm(club, request.POST)
        if form.is_valid():
            base = form.cleaned_data["base"]
            addend = form.cleaned_data["addend"]
            new_map = base.merge(addend)
            new_map.name = f"{base.name} + {addend.name}"[:255]
            new_map.club = club
            new_map.save()
            messages.success(request, "New map created")
            return redirect("dashboard_club:map:list_view", club_slug=club.slug)
    else:
        form = MergeMapsForm(club)

    return render(
        request,
        "dashboard/merge_maps.html",
        {
            "club": club,
            "form": form,
        },
    )


@login_required
@requires_club_in_session
def device_add_view(request):
    club = request.club
    if request.method == "POST":
        # create a form instance and populate it with data from the request:
        form = DeviceForm(request.POST)
        # check whether it's valid:
        form.fields["device"].queryset = Device.objects.exclude(owners=club)
        if form.is_valid():
            device = form.cleaned_data["device"]
            ownership = DeviceClubOwnership()
            ownership.club = club
            ownership.device = device
            ownership.nickname = form.cleaned_data["nickname"]
            ownership.save()
            messages.success(request, "Device added successfully")
            return redirect(
                "dashboard_club:device:list_view", club_slug=request.club.slug
            )
    else:
        form = DeviceForm()
    form.fields["device"].queryset = Device.objects.none()
    return render(
        request,
        "dashboard/device_add.html",
        {
            "club": club,
            "form": form,
        },
    )


@login_required
def club_create_view(request):
    if request.method == "POST":
        # create a form instance and populate it with data from the request:
        form = ClubForm(request.POST, request.FILES)
        # check whether it's valid:
        if form.is_valid():
            club = form.save(commit=False)
            club.creator = request.user
            club.save()
            form.save_m2m()
            messages.success(request, "Club created successfully")
            return redirect("dashboard_club:edit_view", club_slug=club.slug)
    else:
        form = ClubForm(initial={"admins": request.user})
    form.fields["admins"].queryset = User.objects.filter(id=request.user.id)
    return render(
        request,
        "dashboard/club_create.html",
        {
            "form": form,
        },
    )


@login_required
@requires_club_in_session
def club_view(request):
    club = request.club
    if request.method == "POST":
        # create a form instance and populate it with data from the request:
        club_copy = deepcopy(club)
        form = ClubForm(request.POST, request.FILES, instance=club_copy)
        # check whether it's valid:
        if form.is_valid():
            form.save()
            messages.success(request, "Changes saved successfully")
            return redirect("dashboard_club:edit_view", club_slug=form.instance.slug)
    else:
        form = ClubForm(instance=club)
    form.fields["admins"].queryset = User.objects.filter(id__in=club.admins.all())
    return render(
        request,
        "dashboard/club_view.html",
        {
            "club": club,
            "form": form,
        },
    )


@login_required
@requires_club_in_session
def club_custom_domain_view(request):
    club = request.club
    if request.method == "POST":
        # create a form instance and populate it with data from the request:
        club_copy = deepcopy(club)
        form = ClubDomainForm(request.POST, instance=club_copy)
        # check whether it's valid:
        if form.is_valid():
            form.save()
            request.META["RESET_CSRF_ALLOWED_MIDDLEWARE"] = True
            messages.success(request, "Changes saved successfully")
            return redirect("dashboard_club:edit_view", club_slug=club.slug)
    else:
        form = ClubDomainForm(instance=club)
    return render(
        request,
        "dashboard/custom_domain.html",
        {
            "club": club,
            "form": form,
        },
    )


@login_required
@requires_club_in_session
def club_delete_view(request):
    club = request.club

    if request.method == "POST":
        # create a form instance and populate it with data from the request:
        password = request.POST.get("password")
        if not request.user.check_password(password):
            messages.error(request, "Invalid password")
            return redirect("dashboard_club:delete_view", club_slug=request.club.slug)
        club.delete()
        messages.success(request, "Club deleted")
        return redirect("dashboard_club:select_view")
    return render(
        request,
        "dashboard/club_delete.html",
        {
            "club": club,
        },
    )


@login_required
@requires_club_in_session
def map_list_view(request):
    club = request.club

    map_list = Map.objects.filter(club=club).select_related("club")
    paginator = Paginator(map_list, DEFAULT_PAGE_SIZE)
    page = request.GET.get("page")
    maps = paginator.get_page(page)
    return render(request, "dashboard/map_list.html", {"club": club, "maps": maps})


@login_required
@requires_club_in_session
def map_create_view(request):
    club = request.club

    if request.method == "POST":
        # create a form instance and populate it with data from the request:
        form = MapForm(request.POST, request.FILES)
        form.instance.club = club
        # check whether it's valid:
        if form.is_valid():
            form.save()
            messages.success(request, "Map created successfully")
            return redirect("dashboard_club:map:list_view", club_slug=request.club.slug)
    else:
        form = MapForm()
    return render(
        request,
        "dashboard/map_edit.html",
        {
            "club": club,
            "context": "create",
            "form": form,
        },
    )


@login_required
@requires_club_in_session
def map_edit_view(request, map_id):
    club = request.club
    raster_map = get_object_or_404(Map, aid=map_id)

    if request.method == "POST":
        # create a form instance and populate it with data from the request:
        raster_map_copy = deepcopy(raster_map)
        form = MapForm(request.POST, request.FILES, instance=raster_map_copy)
        form.instance.club = club
        # check whether it's valid:
        if form.is_valid():
            form.save()
            messages.success(request, "Changes saved successfully")
            return redirect("dashboard_club:map:list_view", club_slug=request.club.slug)
    else:
        form = MapForm(instance=raster_map)

    used_in = Event.objects.filter(
        Q(map_id=raster_map.id) | Q(map_assignations__map_id=raster_map.id)
    ).distinct()

    return render(
        request,
        "dashboard/map_edit.html",
        {
            "club": club,
            "context": "edit",
            "map": raster_map,
            "form": form,
            "used_in": used_in,
        },
    )


@login_required
@requires_club_in_session
def map_delete_view(request, map_id):
    club = request.club
    raster_map = get_object_or_404(Map, aid=map_id)

    if request.method == "POST":
        raster_map.delete()
        messages.success(request, "Map deleted")
        return redirect("dashboard_club:map:list_view", club_slug=request.club.slug)
    return render(
        request,
        "dashboard/map_delete.html",
        {
            "club": club,
            "map": raster_map,
        },
    )


@login_required
@requires_club_in_session
def map_gpx_upload_view(request):
    club = request.club

    if request.method == "POST":
        form = UploadMapGPXForm(request.POST, request.FILES)
        if form.is_valid():
            segments = form.cleaned_data["gpx_segments"]
            waypoints = form.cleaned_data["gpx_waypoints"]
            try:
                new_map = Map.from_points(segments, waypoints)
            except Exception:
                messages.error(request, "Failed to generate a map from this file")
            else:
                new_map.name = form.cleaned_data["gpx_file"].name[:-4]
                new_map.club = club
                new_map.save()

                messages.success(request, "The import of the map was successful!")
                return redirect(
                    "dashboard_club:map:list_view", club_slug=request.club.slug
                )
    else:
        form = UploadMapGPXForm()
    return render(
        request,
        "dashboard/map_gpx_upload.html",
        {
            "club": club,
            "form": form,
        },
    )


@login_required
@requires_club_in_session
def map_draw_view(request):
    club = request.club
    return render(
        request,
        "dashboard/map_draw.html",
        {
            "club": club,
        },
    )


@login_required
@requires_club_in_session
def map_kmz_upload_view(request):
    club = request.club

    if request.method == "POST":
        form = UploadKmzForm(request.POST, request.FILES)
        if form.is_valid():
            maps = form.cleaned_data["extracted_maps"]

            new_map = maps[0]
            if len(maps) > 1:
                new_map = new_map.merge(*maps[1:])
            new_map.club = club
            new_map.save()

            messages.success(request, "The import of the map was successful!")
            return redirect("dashboard_club:map:list_view", club_slug=request.club.slug)
    else:
        form = UploadKmzForm()
    return render(
        request,
        "dashboard/map_kmz_upload.html",
        {
            "club": club,
            "form": form,
        },
    )


@login_required
@requires_club_in_session
def event_set_list_view(request):
    club = request.club

    event_set_list = (
        EventSet.objects.filter(club=club)
        .select_related("club")
        .prefetch_related("events")
    )

    paginator = Paginator(event_set_list, DEFAULT_PAGE_SIZE)
    page = request.GET.get("page")
    event_sets = paginator.get_page(page)

    return render(
        request,
        "dashboard/event_set_list.html",
        {"club": club, "event_sets": event_sets},
    )


@login_required
@requires_club_in_session
def event_set_create_view(request):
    club = request.club
    if request.method == "POST":
        # create a form instance and populate it with data from the request:
        form = EventSetForm(request.POST, request.FILES, club=club)
        if form.is_valid():
            form.save()
            messages.success(request, "Event set created successfully")
            return redirect(
                "dashboard_club:event_set:list_view", club_slug=request.club.slug
            )
    else:
        form = EventSetForm(club=club)
    return render(
        request,
        "dashboard/event_set_edit.html",
        {
            "club": club,
            "context": "create",
            "form": form,
        },
    )


@login_required
@requires_club_in_session
def event_set_edit_view(request, event_set_id):
    club = request.club
    event_set = get_object_or_404(
        EventSet.objects.prefetch_related("events"),
        aid=event_set_id,
    )

    if request.method == "POST":
        event_set_copy = deepcopy(event_set)
        club_copy = deepcopy(club)
        form = EventSetForm(request.POST, instance=event_set_copy, club=club_copy)
        if form.is_valid():
            form.save()
            messages.success(request, "Changes saved successfully")
            return redirect(
                "dashboard_club:event_set:list_view", club_slug=request.club.slug
            )
    else:
        form = EventSetForm(instance=event_set, club=club)
    return render(
        request,
        "dashboard/event_set_edit.html",
        {
            "club": club,
            "context": "edit",
            "event_set": event_set,
            "form": form,
        },
    )


@login_required
@requires_club_in_session
def event_set_delete_view(request, event_set_id):
    event_set = get_object_or_404(EventSet, aid=event_set_id)
    if request.method == "POST":
        event_set.delete()
        messages.success(request, "Event set deleted")
        return redirect("dashboard_club:event:list_view", club_slug=request.club.slug)
    return render(
        request,
        "dashboard/event_set_delete.html",
        {
            "event_set": event_set,
        },
    )


@login_required
@requires_club_in_session
def event_list_view(request):
    club = request.club

    event_list = Event.objects.filter(club=club).select_related("club", "event_set")

    paginator = Paginator(event_list, DEFAULT_PAGE_SIZE)
    page = request.GET.get("page")
    events = paginator.get_page(page)

    return render(
        request, "dashboard/event_list.html", {"club": club, "events": events}
    )


@login_required
@requires_club_in_session
def event_create_view(request):
    club = request.club

    map_list = Map.objects.filter(club=club)
    event_set_list = EventSet.objects.filter(club=club)

    all_devices_id = set(club.devices.values_list("id", flat=True))

    if request.method == "POST":
        # create a form instance and populate it with data from the request:
        form = EventForm(request.POST, request.FILES, club=club)
        form.fields["map"].queryset = map_list
        form.fields["event_set"].queryset = event_set_list

        competitors_formset = CompetitorFormSet(request.POST)

        extra_maps_formset = ExtraMapFormSet(request.POST)
        for mform in extra_maps_formset.forms:
            mform.fields["map"].queryset = map_list

        notice_form = NoticeForm(request.POST)
        # check whether it's valid:
        if all(
            [
                form.is_valid(),
                competitors_formset.is_valid(),
                notice_form.is_valid(),
                extra_maps_formset.is_valid(),
            ]
        ):
            event = form.save()
            competitors_formset.instance = event
            competitors_formset.save()
            extra_maps_formset.instance = event
            extra_maps_formset.save()
            notice = notice_form.save(commit=False)
            notice.event = event
            notice.save()
            messages.success(request, "Event created successfully")
            if request.POST.get("save_continue"):
                return redirect(
                    "dashboard_club:event:edit_view",
                    event_id=event.aid,
                    club_slug=request.club.slug,
                )
            return redirect(
                "dashboard_club:event:list_view", club_slug=request.club.slug
            )

        for cform in competitors_formset.forms:
            if "device" in cform.changed_data and (
                new_device := cform.cleaned_data.get("device")
            ):
                all_devices_id.add(new_device.id)
    else:
        form = EventForm(club=club)
        form.fields["map"].queryset = map_list
        form.fields["event_set"].queryset = event_set_list
        # form.fields["on_events_page"].initial = club.upgraded or club.o_club

        competitors_formset = CompetitorFormSet()

        extra_maps_formset = ExtraMapFormSet()

        for mform in extra_maps_formset.forms:
            mform.fields["map"].queryset = map_list

        notice_form = NoticeForm()

    dev_qs = (
        Device.objects.filter(id__in=all_devices_id)
        .defer("locations_encoded")
        .prefetch_related("club_ownerships")
    )
    cd = [
        {
            "full": (d.id, d.get_display_str(club)),
            "key": (d.get_nickname(club), d.get_display_str(club)),
        }
        for d in dev_qs
    ]
    cd.sort(key=lambda x: (x["key"][0] == "", x["key"]))
    c = [
        ["", "---------"],
    ] + [d["full"] for d in cd]
    for cform in competitors_formset.forms:
        cform.fields["device"].queryset = dev_qs
        cform.fields["device"].choices = c
    return render(
        request,
        "dashboard/event_edit.html",
        {
            "club": club,
            "context": "create",
            "form": form,
            "competitors_formset": competitors_formset,
            "extra_maps_formset": extra_maps_formset,
            "notice_form": notice_form,
        },
    )


MAX_COMPETITORS_DISPLAYED_IN_EVENT = 100


@login_required
@requires_club_in_session
def event_edit_view(request, event_id):
    club = request.club
    event = get_object_or_404(
        Event.objects.prefetch_related("notice", "competitors"),
        aid=event_id,
    )

    map_list = Map.objects.filter(club=club)
    event_set_list = EventSet.objects.filter(club=club)

    use_competitor_formset = (
        event.competitors.count() < MAX_COMPETITORS_DISPLAYED_IN_EVENT
    )
    if use_competitor_formset:
        comp_devices_id = event.competitors.values_list("device", flat=True)
        own_devices_id = club.devices.values_list("id", flat=True)
    else:
        comp_devices_id = []
        own_devices_id = []

    all_devices_id = set(list(comp_devices_id) + list(own_devices_id))

    if request.method == "POST":
        # create a form instance and populate it with data from the request:
        event_copy = deepcopy(event)

        form = EventForm(request.POST, request.FILES, instance=event_copy, club=club)
        form.fields["map"].queryset = map_list
        form.fields["event_set"].queryset = event_set_list

        extra_maps_formset = ExtraMapFormSet(request.POST, instance=event_copy)
        for mform in extra_maps_formset.forms:
            mform.fields["map"].queryset = map_list

        competitors_formset = CompetitorFormSet(
            request.POST,
            instance=event_copy,
        )

        notice_form_args = {}
        if event.has_notice:
            notice_form_args["instance"] = event.notice
        notice_form = NoticeForm(request.POST, **notice_form_args)

        if all(
            [
                form.is_valid(),
                competitors_formset.is_valid(),
                extra_maps_formset.is_valid(),
                notice_form.is_valid(),
            ]
        ):
            form.save()
            extra_maps_formset.save()
            competitors_formset.save()

            prev_notice = ""
            if event.has_notice:
                notice_form.instance = event.notice
                event.notice.refresh_from_db()
                prev_notice = event.notice.text
            if prev_notice != notice_form.cleaned_data["text"]:
                if not event.has_notice:
                    notice = Notice(event=event)
                else:
                    notice = event.notice
                notice.text = notice_form.cleaned_data["text"]
                notice.save()

            messages.success(request, "Changes saved successfully")
            if request.POST.get("save_continue"):
                return redirect(
                    "dashboard_club:event:edit_view",
                    event_id=event.aid,
                    club_slug=request.club.slug,
                )
            return redirect(
                "dashboard_club:event:list_view", club_slug=request.club.slug
            )

        for cform in competitors_formset.forms:
            if "device" in cform.changed_data and (
                new_device := cform.cleaned_data.get("device")
            ):
                all_devices_id.add(new_device.id)
    else:
        form = EventForm(instance=event, club=club)
        form.fields["map"].queryset = map_list
        form.fields["event_set"].queryset = event_set_list

        competitors_formset_args = {}
        if not use_competitor_formset:
            competitors_formset_args["queryset"] = Competitor.objects.none()
        competitors_formset = CompetitorFormSet(
            instance=event, **competitors_formset_args
        )

        extra_maps_formset = ExtraMapFormSet(instance=event)
        for mform in extra_maps_formset.forms:
            mform.fields["map"].queryset = map_list

        notice_form_args = {}
        if event.has_notice:
            notice_form_args["instance"] = event.notice
        notice_form = NoticeForm(**notice_form_args)

    dev_qs = (
        Device.objects.filter(id__in=all_devices_id)
        .defer("locations_encoded")
        .prefetch_related("club_ownerships")
    )
    cd = [
        {
            "full": (d.id, d.get_display_str(club)),
            "key": (d.get_nickname(club), d.get_display_str(club)),
        }
        for d in dev_qs
    ]
    cd.sort(key=lambda x: (x["key"][0] == "", x["key"]))
    c = [
        ["", "---------"],
    ] + [d["full"] for d in cd]
    for cform in competitors_formset.forms:
        cform.fields["device"].queryset = dev_qs
        cform.fields["device"].choices = c

    return render(
        request,
        "dashboard/event_edit.html",
        {
            "club": club,
            "context": "edit",
            "event": event,
            "form": form,
            "competitors_formset": competitors_formset,
            "extra_maps_formset": extra_maps_formset,
            "notice_form": notice_form,
            "use_competitor_formset": use_competitor_formset,
        },
    )


COMPETITORS_PAGE_SIZE = 50


@login_required
@requires_club_in_session
def event_competitors_view(request, event_id):
    club = request.club
    event = get_object_or_404(
        Event.objects.prefetch_related("notice", "competitors"),
        aid=event_id,
    )

    if event.competitors.count() < MAX_COMPETITORS_DISPLAYED_IN_EVENT:
        raise Http404()
    page = request.GET.get("page", 1)
    search_query = request.GET.get("q", "")

    qs = event.competitors.all()
    if search_query:
        qs = qs.filter(
            Q(device__aid__icontains=search_query)
            | Q(name__icontains=search_query)
            | Q(short_name__icontains=search_query)
        )

    competitor_paginator = Paginator(qs, COMPETITORS_PAGE_SIZE)
    try:
        competitors = competitor_paginator.page(page)
    except Exception:
        raise Http404()
    comps = Competitor.objects.filter(id__in=[c.id for c in competitors.object_list])
    comp_devices_id = [c.device_id for c in competitors.object_list]
    own_devices_id = club.devices.values_list("id", flat=True)
    all_devices_id = set(list(comp_devices_id) + list(own_devices_id))
    if request.method == "POST":
        # create a form instance and populate it with data from the request:
        event_copy = deepcopy(event)
        formset = CompetitorFormSet(
            request.POST,
            instance=event_copy,
        )
        # check whether it's valid:
        if formset.is_valid():
            formset.save()
            messages.success(request, "Changes saved successfully")
            return redirect(
                "dashboard_club:event:edit_view",
                event_id=event.aid,
                club_slug=request.club.slug,
            )

        for cform in formset.forms:
            if "device" in cform.changed_data and (
                new_device := cform.cleaned_data.get("device")
            ):
                all_devices_id.add(new_device.id)
    else:
        formset = CompetitorFormSet(
            instance=event,
            queryset=comps,
        )
        formset.extra = 0

    dev_qs = (
        Device.objects.filter(id__in=all_devices_id)
        .defer("locations_encoded")
        .prefetch_related("club_ownerships")
    )
    c = [
        ["", "---------"],
    ] + [[d.id, d.get_display_str(club)] for d in dev_qs]
    for cform in formset.forms:
        cform.fields["device"].queryset = dev_qs
        cform.fields["device"].choices = c

    return render(
        request,
        "dashboard/event_competitors.html",
        {
            "club": club,
            "event": event,
            "formset": formset,
            "competitors": competitors,
            "search_query": search_query,
        },
    )


@login_required
@requires_club_in_session
def event_competitors_csv_view(request, event_id):
    club = request.club
    event = get_object_or_404(
        Event,
        aid=event_id,
        club=club,
    )
    qs = event.competitors.select_related("device").all()
    csvfile = StringIO()
    datawriter = csv.writer(csvfile, delimiter=";")

    for c in qs:
        device_id = c.device.aid if c.device else ""
        datawriter.writerow(
            [c.name, c.short_name, c.start_time.isoformat(), device_id, c.color, c.tags]
        )

    response = StreamingHttpRangeResponse(
        request, csvfile.getvalue().encode("utf-8"), content_type="text/csv"
    )
    filename = f"{event.name}_competitors.csv"
    response["Content-Disposition"] = set_content_disposition(filename)
    return response


@login_required
@requires_club_in_session
def event_competitors_printer_view(request, event_id):
    club = request.club
    event = get_object_or_404(
        Event.objects.prefetch_related(
            "notice",
            Prefetch(
                "competitors", queryset=Competitor.objects.select_related("device")
            ),
        ),
        aid=event_id,
    )

    competitors = event.competitors.all()
    for competitor in competitors:
        competitor.device_display_str = (
            competitor.device.get_display_str(club) if competitor.device else "-"
        )
    return render(
        request,
        "dashboard/event_competitors_printer.html",
        {
            "club": club,
            "event": event,
            "competitors": competitors,
        },
    )


@login_required
@requires_club_in_session
def event_delete_view(request, event_id):
    event = get_object_or_404(Event, aid=event_id)

    if request.method == "POST":
        event.delete()
        messages.success(request, "Event deleted")
        return redirect("dashboard_club:event:list_view", club_slug=request.club.slug)

    return render(
        request,
        "dashboard/event_delete.html",
        {
            "event": event,
        },
    )


@login_required
def dashboard_map_download(request, map_id, *args, **kwargs):
    if request.user.is_superuser:
        raster_map = get_object_or_404(
            Map,
            image__startswith="maps/",
            image__contains=map_id,
        )
    else:
        club_list = Club.objects.filter(admins=request.user)
        raster_map = get_object_or_404(
            Map, image__startswith="maps/", image__contains=map_id, club__in=club_list
        )
    file_path = raster_map.path
    mime_type = raster_map.mime_type
    return serve_from_s3(
        settings.AWS_S3_BUCKET,
        request,
        file_path,
        filename=(
            f"{raster_map.name}_"
            f"{raster_map.calibration_string_for_naming}_."
            f"{mime_type[6:]}"
        ),
        mime=mime_type,
        dl=False,
    )


@login_required
def dashboard_logo_download(request, club_id, *args, **kwargs):
    if request.user.is_superuser:
        club = get_object_or_404(Club, aid=club_id, logo__isnull=False)
    else:
        club = Club.objects.filter(
            admins=request.user, aid=club_id, logo__isnull=False
        ).first()
    if not club:
        raise Http404()
    file_path = club.logo.name
    return serve_from_s3(
        settings.AWS_S3_BUCKET,
        request,
        file_path,
        filename=f"{club.name}.png",
        mime="image/png",
        dl=False,
    )


@login_required
def dashboard_banner_download(request, club_id, *args, **kwargs):
    if request.user.is_superuser:
        club = get_object_or_404(Club, aid=club_id, banner__isnull=False)
    else:
        club = Club.objects.filter(
            admins=request.user, aid=club_id, banner__isnull=False
        ).first()
    if not club:
        raise Http404()
    file_path = club.banner.name
    return serve_from_s3(
        settings.AWS_S3_BUCKET,
        request,
        file_path,
        filename=f"{club.name}.webp",
        mime="image/webp",
        dl=False,
    )


@login_required
def dashboard_geojson_download(request, event_id, *args, **kwargs):
    if request.user.is_superuser:
        event = get_object_or_404(Event, aid=event_id, geojson_layer__isnull=False)
    else:
        club_list = Club.objects.filter(admins=request.user)
        event = Event.objects.filter(
            club__in=club_list, aid=event_id, geojson_layer__isnull=False
        ).first()
    if not event:
        raise Http404()
    file_path = event.geojson_layer.name
    return serve_from_s3(
        settings.AWS_S3_BUCKET,
        request,
        file_path,
        filename=f"{event.name}.geojson",
        mime="application/json",
        dl=False,
    )


@login_required
@requires_club_in_session
def event_route_upload_view(request, event_id):
    event = get_object_or_404(
        Event.objects.prefetch_related("competitors"), aid=event_id
    )
    competitors = event.competitors.order_by("name")
    if request.method == "POST":
        # create a form instance and populate it with data from the request:
        form = UploadGPXForm(request.POST, request.FILES)
        form.fields["competitor"].queryset = competitors

        if form.is_valid():
            competitor = form.cleaned_data["competitor"]
            start_time = form.cleaned_data["start_time"]
            end_time = form.cleaned_data["end_time"]
            locations = form.cleaned_data["locations"]

            device = Device.objects.create(
                aid=f"{short_random_key()}_GPX",
                user_agent=request.session.user_agent[:200],
                virtual=True,
            )
            device.add_locations(locations)

            competitor.device = device
            if start_time and event.start_date <= start_time <= event.end_date:
                competitor.start_time = start_time
            competitor.save()

            messages.success(request, "The upload of the GPX file was successful!")
            if start_time < event.start_date or end_time > event.end_date:
                messages.warning(
                    request, "Some points were outside of the event schedule..."
                )
            return redirect(
                "dashboard_club:event:edit_view",
                event_id=event.aid,
                club_slug=request.club.slug,
            )
    else:
        form = UploadGPXForm()
        form.fields["competitor"].queryset = competitors
    return render(
        request,
        "dashboard/event_gpx_upload.html",
        {
            "event": event,
            "form": form,
        },
    )


@login_required
@requires_club_in_session
def quick_event(request):
    club = request.club
    all_devices_id = set(club.devices.values_list("id", flat=True))
    dev_qs = (
        Device.objects.filter(id__in=all_devices_id)
        .defer("locations_encoded")
        .prefetch_related("club_ownerships")
    )
    devices = sorted(
        [
            {
                "aid": d.aid,
                "name": d.get_display_str(club),
                "key": (d.get_nickname(club), d.get_display_str(club)),
            }
            for d in dev_qs
        ],
        key=lambda x: (x["key"][0] == "", x["key"]),
    )
    return render(
        request,
        "dashboard/quick_event.html",
        {
            "club": club,
            "devices": devices,
        },
    )


@receiver(password_reset)
@receiver(password_changed)
def logoutOtherSessionsAfterPassChange(request, user, **kwargs):
    user.session_set.exclude(session_key=request.session.session_key).delete()


class CustomSessionDeleteOtherView(SessionDeleteOtherView):
    def get_success_url(self):
        return str(reverse("account_session:list_view", host="dashboard"))


@method_decorator(rate_limit(action="manage_email"), name="dispatch")
class CustomEmailView(EmailView):
    def get_context_data(self, **kwargs):
        ret = super().get_context_data(**kwargs)
        ret["user_emailaddresses"] = self.request.user.emailaddress_set.order_by(
            "-primary", "-verified", "email"
        )
        return ret


email_view = login_required(CustomEmailView.as_view())


class CustomBackupCodesView(BackupCodesView):
    def post(self, request):
        request.user.backup_codes.all().delete()
        for i in range(12):
            request.user.backup_codes.create_backup_code()
        return HttpResponseRedirect(request.build_absolute_uri())


backup_codes = login_required(CustomBackupCodesView.as_view())


@login_required
@requires_club_in_session
def upgrade(request):
    club = request.club
    return render(
        request,
        "dashboard/upgrade.html",
        {
            "club": club,
        },
    )


@login_required
@requires_club_in_session
def device_list_download(request):
    club = request.club
    devices_qs = (
        DeviceClubOwnership.objects.filter(club_id=club.id)
        .select_related("device")
        .defer("device__locations_encoded")
        .order_by("nickname")
    )
    devices = {
        own.device.aid: {"nickname": own.nickname, "aid": own.device.aid}
        for own in devices_qs
    }
    imeis = ImeiDevice.objects.filter(
        device_id__in=[device.device.id for device in devices_qs]
    )
    for imei in imeis:
        devices[imei.device.aid]["imei"] = imei.imei

    csvfile = StringIO()
    datawriter = csv.writer(csvfile, delimiter=";")
    datawriter.writerow(["Nickname", "Device ID", "IMEI"])
    for dev in devices.values():
        datawriter.writerow([dev.get("nickname"), dev.get("aid"), dev.get("imei", "")])

    response = StreamingHttpRangeResponse(
        request, csvfile.getvalue().encode("utf-8"), content_type="text/csv"
    )
    filename = f"device_list_{club.slug}.csv"
    response["Content-Disposition"] = set_content_disposition(filename)
    return response


class MyReleaseUserView(ReleaseUserView):
    def get_redirect_url(self):
        """Return the user-originating redirect URL if it's safe."""
        redirect_to = self.request.POST.get(
            self.redirect_field_name, self.request.GET.get(self.redirect_field_name, "")
        )
        url_is_safe = url_has_allowed_host_and_scheme(
            url=redirect_to,
            allowed_hosts=settings.REDIRECT_ALLOWED_DOMAINS,
        )
        return redirect_to if url_is_safe else ""


@login_required
def participations_view(request):
    participations = request.user.participations.select_related(
        "event", "event__club", "device"
    ).order_by("-event__start_date")

    if request.GET.get("info-edited", None):
        messages.success(request, "Info updated!")
    if request.GET.get("route-uploaded", None):
        messages.success(request, "Data uploaded!")
    if request.GET.get("withdrawn", None):
        messages.success(request, "Participation withdrawn!")

    return render(
        request,
        "dashboard/participations.html",
        {
            "user": request.user,
            "participations": participations,
        },
    )
