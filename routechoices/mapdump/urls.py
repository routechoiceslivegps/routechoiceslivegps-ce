from django.urls import path, re_path

from routechoices.mapdump import views

urlpatterns = [
    path("", views.home_view, name="home_view"),
    re_path(r"^map/(?P<aid>[0-9a-zA-Z_-]+)/?", views.effort_view, name="effort_view"),
    re_path(r"^(?P<username>[0-9a-zA-Z_-]+)/?$", views.user_view, name="user_view"),
]
