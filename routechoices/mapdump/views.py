from django.shortcuts import get_object_or_404, render

from routechoices.core.models import Club, Competitor


def home_view(request):
    user = request.user
    page = user.personal_page
    return render(
        request,
        "mapdump/home.html",
        {
            "user": user,
            "page": page,
        },
    )


def user_view(request, username):
    page = get_object_or_404(
        Club.objects.select_related("creator"),
        creator__username__iexact=username,
        is_personal_page=True,
    )
    efforts = Competitor.objects.filter(event__club=page)
    return render(
        request,
        "mapdump/user.html",
        {
            "user": page.creator,
            "page": page,
            "efforts": efforts,
        },
    )


def effort_view(request, aid):
    effort = get_object_or_404(
        Competitor.objects.select_related(
            "device", "event", "event__club", "event__club__creator"
        ),
        event__club__is_personal_page=True,
        aid=aid,
    )
    event = effort.event
    page = event.club
    user = page.creator
    return render(
        request,
        "mapdump/effort.html",
        {
            "page": page,
            "user": user,
            "effort": effort,
            "event": event,
        },
    )
