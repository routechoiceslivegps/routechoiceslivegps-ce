from django_hosts import host, patterns

host_patterns = patterns(
    "",
    host("dashboard", "routechoices.dashboard.urls", name="dashboard"),
    host("www", "routechoices.site.urls", name="www"),
    host("admin", "routechoices.admin.urls", name="admin"),
    host("api", "routechoices.api.urls", name="api"),
    # host("live", "routechoices.live.urls", name="live"),
    host("map", "routechoices.map.urls", name="map"),
    # host("my", "routechoices.mapdump.urls", name="mapdump"),
    host("registration", "routechoices.registration.urls", name="registration"),
    host("tiles", "routechoices.tiles.urls", name="tiles"),
    host("wms", "routechoices.wms.urls", name="wms"),
    host(
        r"(?P<club_slug>[a-zA-Z0-9][a-zA-Z0-9-]+)",
        "routechoices.club.urls",
        name="clubs",
    ),
)
