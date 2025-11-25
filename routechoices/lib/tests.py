from unittest.mock import Mock, patch

from django.core.validators import ValidationError
from django.http.response import Http404
from django.test import TestCase, override_settings

from . import plausible
from .helpers import (
    check_dns_records,
    get_device_name,
    get_image_mime_from_request,
    simplify_periods,
    wgs84_bound_from_3_ref_points,
)
from .kmz import extract_ground_overlay_info

# from .mtb_decoder import MtbDecoder
from .validators import (
    validate_calibration_string,
    validate_domain_slug,
    validate_emails,
    validate_esn,
    validate_imei,
    validate_latitude,
    validate_longitude,
    validate_nice_slug,
)


@override_settings(ANALYTICS_API_KEY=True)
class PlausibleTestCase(TestCase):
    @patch("curl_cffi.requests.get")
    def test_domain_setup(self, mock_get):
        mock_response = Mock()
        expected_dict = {}
        mock_response.json.return_value = expected_dict
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        self.assertTrue(plausible.is_domain_setup("example.com"))
        mock_response.status_code = 404
        mock_get.return_value = mock_response
        self.assertFalse(plausible.is_domain_setup("example.com"))

    @patch("curl_cffi.requests.post")
    def test_create_domain(self, mock_post):
        mock_response = Mock()
        expected_dict = {}
        mock_response.json.return_value = expected_dict
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        self.assertTrue(plausible.create_domain("gps.example.com"))
        mock_response.status_code = 404
        mock_post.return_value = mock_response
        self.assertFalse(plausible.create_domain("gps.example.com"))

    @patch("curl_cffi.requests.put")
    def test_change_domain(self, mock_put):
        mock_response = Mock()
        expected_dict = {}
        mock_response.json.return_value = expected_dict
        mock_response.status_code = 200
        mock_put.return_value = mock_response
        self.assertTrue(plausible.change_domain("olddomain.com", "gps.example.com"))
        mock_response.status_code = 400
        mock_put.return_value = mock_response
        self.assertFalse(plausible.change_domain("olddomain.com", "gps.example.com"))

    @patch("curl_cffi.requests.delete")
    def test_delete_domain(self, mock_delete):
        mock_response = Mock()
        expected_dict = {}
        mock_response.json.return_value = expected_dict
        mock_response.status_code = 200
        mock_delete.return_value = mock_response
        self.assertTrue(plausible.delete_domain("gps.example.com"))
        mock_response.status_code = 400
        mock_delete.return_value = mock_response
        self.assertFalse(plausible.delete_domain("gps.example.com"))

    @patch("routechoices.lib.plausible.is_domain_setup")
    @patch("curl_cffi.requests.put")
    def test_create_link(self, mock_put, mock_is_domain_setup):
        mock_is_domain_setup_return = Mock()
        mock_is_domain_setup_return.return_value = True
        mock_is_domain_setup.return_value = mock_is_domain_setup_return

        mock_response = Mock()
        expected_dict = {"url": "https://plausible.example.com/abc123"}
        mock_response.json.return_value = expected_dict
        mock_response.status_code = 200
        mock_put.return_value = mock_response

        link, created = plausible.create_shared_link("gps.example.com", "Hello")
        self.assertTrue(created)
        self.assertEqual(link, "https://plausible.example.com/abc123")
        mock_response.status_code = 400
        mock_put.return_value = mock_response
        _, created = plausible.create_shared_link("gps.example.com", "Hello")
        self.assertFalse(created)


class HelperTestCase(TestCase):
    def wgs84_bound_from_3_ref_points(self):
        cal = wgs84_bound_from_3_ref_points(
            "9.5480564597566|46.701263850274|1|1|9.5617738453051|46.701010852567|4961|1|9.5475331306949|46.687915214433|1|7016",
            4961,
            7016,
        )
        self.assertEqual(
            cal,
            [
                46.70127,
                9.54805,
                46.70101,
                9.56177,
                46.68766,
                9.56125,
                46.68792,
                9.54753,
            ],
        )

    def test_check_dns(self):
        self.assertTrue(check_dns_records("latlong.uk"))
        self.assertTrue(check_dns_records("where.rapha.run"))
        self.assertFalse(check_dns_records("doesnotexist.kiilat.com"))
        self.assertFalse(check_dns_records("kiilat.com"))

    def test_import_kml(self):
        kml = '<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Folder><name>Ground Overlays</name><description>Examples of ground overlays</description><GroundOverlay><name>Large-scale overlay on terrain</name><description>Overlay shows Mount Etna erupting on July 13th, 2001.</description><Icon><href>https://developers.google.com/kml/documentation/images/etna.jpg</href></Icon><LatLonBox><north>37.91904192681665</north><south>37.46543388598137</south><east>15.35832653742206</east><west>14.60128369746704</west><rotation>-0.1556640799496235</rotation></LatLonBox></GroundOverlay></Folder></kml>'
        name, url, coordinates = extract_ground_overlay_info(kml)[0]
        self.assertEqual(name, "Ground Overlays - Large-scale overlay on terrain")
        self.assertEqual(
            url, "https://developers.google.com/kml/documentation/images/etna.jpg"
        )
        self.assertEqual(
            coordinates,
            "37.91985,14.60206,37.91823,15.35910,37.46462,15.35755,37.46625,14.60051",
        )

    def test_get_image_mime_from_request(self):
        self.assertEqual(get_image_mime_from_request("jpeg"), "image/jpeg")
        self.assertEqual(get_image_mime_from_request("jpeg", "image/png"), "image/jpeg")
        self.assertRaises(Http404, get_image_mime_from_request, "json", "image/jpeg")
        self.assertEqual(get_image_mime_from_request(None, "image/webp"), "image/webp")

    def test_simplify_periods(self):
        self.assertEqual(simplify_periods([(0, 2), (5, 7), (1, 3)]), [(0, 3), (5, 7)])

    def test_get_device_name(self):
        self.assertEqual(get_device_name("Queclink"), "Queclink")
        self.assertEqual(get_device_name("Routechoices-ios-tracker/1.3.2"), "iOS")
        self.assertEqual(get_device_name("Dalvik/1.2.3"), "Android")
        self.assertEqual(get_device_name("ConnectMobile/1.0.2"), "Garmin")
        self.assertEqual(get_device_name("Traccar/2.1.3"), "Traccar")
        self.assertEqual(
            get_device_name(
                "Mozilla/5.0 (Linux; Android 15; SM-S931B Build/AP3A.240905.015.A2; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/127.0.6533.103 Mobile Safari/537.36"
            ),
            "Chrome on Android",
        )


class ValidatorsTestCase(TestCase):
    def test_validate_emails(self):
        self.assertRaises(ValidationError, validate_emails, "r a@a.aa")
        validate_emails("r@example.com a@a.aa")

    def test_validate_imei(self):
        validate_imei("012345678901237")
        self.assertRaises(ValidationError, validate_imei, "r a@a.aa")
        self.assertRaises(ValidationError, validate_imei, "0123456789")
        self.assertRaises(ValidationError, validate_imei, "01234567890123456")
        self.assertRaises(ValidationError, validate_imei, "012345678901234")
        self.assertRaises(ValidationError, validate_imei, None)

    def test_validate_esn(self):
        validate_esn("0-1234567")
        self.assertRaises(ValidationError, validate_esn, "r a@a.aa")
        self.assertRaises(ValidationError, validate_esn, "01234567")
        self.assertRaises(ValidationError, validate_esn, "012345678")
        self.assertRaises(ValidationError, validate_esn, "0123456")
        self.assertRaises(ValidationError, validate_esn, None)

    def test_validate_corners_coords(self):
        validate_calibration_string("1,1,1,1,1,1,1,1")
        validate_calibration_string("1,360,1,1,1,1,1,1")
        self.assertRaises(ValidationError, validate_calibration_string, "r a@a.aa")
        self.assertRaises(
            ValidationError, validate_calibration_string, "0,0,0,0,0,0,0,0"
        )
        self.assertRaises(ValidationError, validate_calibration_string, "1,1,1,1,1,1,1")
        self.assertRaises(
            ValidationError, validate_calibration_string, "1,1,1,1,1,1,1,"
        )
        self.assertRaises(
            ValidationError, validate_calibration_string, "1,1,1,1,1,1,1,a"
        )
        self.assertRaises(
            ValidationError, validate_calibration_string, "1,1,1,1,1,1,1,1,1"
        )
        self.assertRaises(
            ValidationError, validate_calibration_string, "100,1,1,1,1,1,1,1"
        )

    def test_validate_domain(self):
        validate_domain_slug("abc01")
        validate_domain_slug("a-b-c-0-1")
        validate_domain_slug("123")
        validate_domain_slug("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertRaises(ValidationError, validate_domain_slug, "r a@a.aa")
        self.assertRaises(ValidationError, validate_domain_slug, "r")
        self.assertRaises(ValidationError, validate_domain_slug, "a.a")
        self.assertRaises(
            ValidationError, validate_domain_slug, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
        self.assertRaises(ValidationError, validate_domain_slug, "-abc")
        self.assertRaises(ValidationError, validate_domain_slug, "a_bc")
        self.assertRaises(ValidationError, validate_domain_slug, "abc-")
        self.assertRaises(ValidationError, validate_domain_slug, "ab--c")
        self.assertRaises(ValidationError, validate_domain_slug, "test")

    def test_validate_nice_slug(self):
        validate_nice_slug("abc01")
        validate_nice_slug("a-b-c-0-1")
        validate_nice_slug("123")
        validate_nice_slug("1_23")
        validate_nice_slug("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertRaises(ValidationError, validate_nice_slug, "r a@a.aa")
        self.assertRaises(ValidationError, validate_nice_slug, "r")
        self.assertRaises(ValidationError, validate_nice_slug, "a.a")
        self.assertRaises(
            ValidationError, validate_nice_slug, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
        self.assertRaises(ValidationError, validate_nice_slug, "-abc")
        self.assertRaises(ValidationError, validate_nice_slug, "_abc")
        self.assertRaises(ValidationError, validate_nice_slug, "abc-")
        self.assertRaises(ValidationError, validate_nice_slug, "abc_")
        self.assertRaises(ValidationError, validate_nice_slug, "ab--c")
        self.assertRaises(ValidationError, validate_nice_slug, "ab_-c")
        self.assertRaises(ValidationError, validate_nice_slug, "ab-_c")
        self.assertRaises(ValidationError, validate_nice_slug, "ab__c")
        self.assertRaises(ValidationError, validate_nice_slug, "test")

    def test_validate_lat(self):
        validate_latitude(12.12456)
        validate_latitude(90)
        validate_latitude(-90)
        self.assertRaises(ValidationError, validate_latitude, "r a@a.aa")
        self.assertRaises(ValidationError, validate_latitude, 90.1)
        self.assertRaises(ValidationError, validate_latitude, 0)
        self.assertRaises(ValidationError, validate_latitude, -90.1)

    def test_validate_lon(self):
        validate_longitude(45.1234)
        validate_longitude(180)
        validate_longitude(-180)
        self.assertRaises(ValidationError, validate_longitude, "r a@a.aa")
        self.assertRaises(ValidationError, validate_longitude, 180.1)
        self.assertRaises(ValidationError, validate_longitude, 0)
        self.assertRaises(ValidationError, validate_longitude, -180.1)
