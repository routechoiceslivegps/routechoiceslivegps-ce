from django.urls import re_path

from routechoices.tiles import views

urlpatterns = [
    re_path(r"^$", views.serve_tile, name="tile_service"),
    re_path(
        r"^proxy/(?P<country>ch|ee|se|uk)/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.webp$",
        views.serve_tile_proxy,
        name="proxy_tile_service",
    ),
]
