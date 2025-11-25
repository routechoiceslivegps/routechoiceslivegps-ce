from django.http import HttpResponse
from django.http.response import HttpResponseBadRequest
from django.views.decorators.http import condition
from rest_framework import status

from routechoices.core.models import (
    PRIVACY_PRIVATE,
    Event,
)
from routechoices.lib.helpers import (
    get_best_image_mime,
    safe64encodedsha,
    wgs84_to_meters,
)
from routechoices.lib.streaming_response import StreamingHttpRangeResponse


def common_wms(function):
    def wrap(request, *args, **kwargs):
        get_params = {}
        for key in request.GET.keys():
            get_params[key.lower()] = request.GET[key]

        if get_params.get("service", "").lower() != "wms":
            return HttpResponseBadRequest("Service must be WMS")

        if get_params.get("request", "").lower() == "getmap":
            asked_mime = get_params.get("format", "image/png").lower()
            better_mime = get_best_image_mime(request)
            if asked_mime in (
                "image/apng",
                "image/png",
                "image/webp",
                "image/avif",
                "image/jxl",
            ):
                img_mime = asked_mime
                if img_mime == "image/apng":
                    img_mime = "image/png"
            elif asked_mime == "image/jpeg" and not better_mime:
                img_mime = "image/jpeg"
            elif better_mime:
                img_mime = better_mime
            else:
                return HttpResponseBadRequest("invalid image format")

            layers_raw = get_params.get("layers")
            bbox_raw = get_params.get("bbox")
            width_raw = get_params.get("width")
            heigth_raw = get_params.get("height")
            if not layers_raw or not bbox_raw or not width_raw or not heigth_raw:
                return HttpResponseBadRequest("missing mandatory parameters")

            try:
                out_w, out_h = int(width_raw), int(heigth_raw)
            except Exception:
                return HttpResponseBadRequest("invalid size parameters")

            srs = get_params.get("srs")
            try:
                if srs in ("CRS:84", "EPSG:4326"):
                    bound = (float(x) for x in bbox_raw.split(","))
                    min_lat, min_lon, max_lat, max_lon = bound
                    if srs == "EPSG:4326":
                        min_lon, min_lat, max_lon, max_lat = bound

                    min_x, min_y = wgs84_to_meters(min_lat, min_lon).xy
                    max_x, max_y = wgs84_to_meters(max_lat, max_lon).xy
                elif srs == "EPSG:3857":
                    min_x, min_y, max_x, max_y = (float(x) for x in bbox_raw.split(","))
                else:
                    return HttpResponseBadRequest("SRS not supported")
            except Exception:
                return HttpResponseBadRequest("invalid bound parameters")

            try:
                if "/" in layers_raw:
                    event_id, map_index = layers_raw.split("/")
                    map_index = int(map_index)
                    if map_index <= 0:
                        raise ValueError()
                else:
                    event_id = layers_raw
                    map_index = 1
            except Exception:
                return HttpResponseBadRequest("invalid parameters")

            event, raster_map, _ = Event.get_public_map_at_index(
                request.user, event_id, map_index
            )

            request.event = event
            request.raster_map = raster_map
            request.image_request = {
                "mime": img_mime,
                "width": out_w,
                "height": out_h,
            }
            request.bound = {
                "min_x": min_x,
                "max_x": max_x,
                "min_y": min_y,
                "max_y": max_y,
            }
        return function(request, *args, **kwargs)

    wrap.__doc__ = function.__doc__
    wrap.__name__ = function.__name__
    return wrap


def tile_etag(request):
    get_params = {}
    for key in request.GET.keys():
        get_params[key.lower()] = request.GET[key]
    if get_params.get("request", "").lower() == "getmap":
        key = request.raster_map.get_tile_cache_key(
            request.image_request["width"],
            request.image_request["height"],
            request.image_request["mime"],
            request.bound["min_x"],
            request.bound["max_x"],
            request.bound["min_y"],
            request.bound["max_y"],
        )
        return safe64encodedsha(key)
    return None


@common_wms
@condition(etag_func=tile_etag)
def wms_service(request):
    get_params = {}
    for key in request.GET.keys():
        get_params[key.lower()] = request.GET[key]
    if get_params.get("request", "").lower() == "getmap":
        data_out, cache_hit = request.raster_map.get_tile(
            request.image_request["width"],
            request.image_request["height"],
            request.image_request["mime"],
            request.bound["min_x"],
            request.bound["max_x"],
            request.bound["min_y"],
            request.bound["max_y"],
        )
        headers = {"X-Cache-Hit": cache_hit}
        if request.event.privacy == PRIVACY_PRIVATE:
            headers = {"Cache-Control": "Private"}
        return StreamingHttpRangeResponse(
            request,
            data_out,
            content_type=request.image_request["mime"],
            headers=headers,
        )
    return HttpResponse(status=status.HTTP_501_NOT_IMPLEMENTED)
