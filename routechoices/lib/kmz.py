import os
import os.path
import zipfile
from io import BytesIO

from curl_cffi import requests
from defusedxml import minidom
from django.core.files import File

from routechoices.core.models import Map
from routechoices.lib.helpers import (
    Wgs84Coordinate,
    is_valid_pil_image,
    wgs84_bound_from_latlon_box,
)


def get_maps_from_kml(kml, root_dir):
    overlays = extract_ground_overlay_info(kml)
    for name, image_path, bound in overlays:
        if not name:
            name = "Untitled"
        try:
            file_buffer = extract_kml_image_buffer(image_path, root_dir)
        except ValueError:
            continue
        if not is_valid_pil_image(file_buffer):
            continue
        image_file = File(file_buffer)
        new_map = Map(
            name=name,
        )
        new_map.bound = bound
        new_map.image.save("file", image_file, save=False)
        yield new_map


def extract_kml(file, root_dir):
    zf = zipfile.ZipFile(file)
    zf.extractall(root_dir)
    if os.path.exists(os.path.join(root_dir, "Doc.kml")):
        doc_file = "Doc.kml"
    elif os.path.exists(os.path.join(root_dir, "doc.kml")):
        doc_file = "doc.kml"
    else:
        raise ValueError("No valid doc.kml file")
    with open(os.path.join(root_dir, doc_file), "r", encoding="utf-8") as f:
        kml = f.read().encode("utf8")
    return kml


def extract_wgs84_bound_from_kml_ground_overlay(go):
    latlon_box_nodes = go.getElementsByTagName("LatLonBox")
    latlon_quad_nodes = go.getElementsByTagNameNS("*", "LatLonQuad")
    if len(latlon_box_nodes):
        latlon_box = latlon_box_nodes[0]
        north, east, south, west, rot = (
            float(latlon_box.getElementsByTagName(val)[0].firstChild.nodeValue)
            for val in ("north", "east", "south", "west", "rotation")
        )
        nw, ne, se, sw = wgs84_bound_from_latlon_box(north, east, south, west, rot)
    elif len(latlon_quad_nodes):
        latlon_quad = latlon_quad_nodes[0]
        corners_lonlat = (
            latlon_quad.getElementsByTagName("coordinates")[0]
            .firstChild.nodeValue.strip()
            .split(" ")
        )
        sw, se, ne, nw = (
            Wgs84Coordinate(float(x) for x in cc.split(",", 1)[::-1])
            for cc in corners_lonlat
        )
    else:
        raise Exception("Invalid GroundOverlay: Missing Geo Calibration")
    return (nw, ne, se, sw)


def extract_kml_image_buffer(image_path, root_dir=None):
    file_data = None
    if image_path.startswith("http://") or image_path.startswith("https://"):
        try:
            r = requests.get(image_path, timeout=10)
            r.raise_for_status()
        except Exception:
            raise ValueError("File contains an unreachable image URL")
        else:
            file_data = r.content
    elif root_dir:
        image_path = os.path.abspath(os.path.join(root_dir, image_path))
        if not image_path.startswith(root_dir):
            raise ValueError("File contains an illegal image path")
        with open(image_path, "rb") as fp:
            file_data = fp.read()
    else:
        raise ValueError("File contains an illegal image path")
    return BytesIO(file_data)


def extract_ground_overlay_info(kml):
    doc = minidom.parseString(kml)
    out = []
    main_name = name = "Untitled"
    try:
        main_name = doc.getElementsByTagName("name")[0].firstChild.nodeValue
    except Exception:
        pass
    for go in doc.getElementsByTagName("GroundOverlay"):
        try:
            name = go.getElementsByTagName("name")[0].firstChild.nodeValue
        except Exception:
            name = "Untitled"
        try:
            href = (
                go.getElementsByTagName("Icon")[0]
                .getElementsByTagName("href")[0]
                .firstChild.nodeValue
            )
            bound = extract_wgs84_bound_from_kml_ground_overlay
        except Exception:
            raise ValueError()
        if name == main_name:
            fullname = name
        else:
            fullname = f"{main_name} - {name}"
        out.append((fullname, href, bound))
    return out
