import re

import arrow

from routechoices.lib import luhn
from routechoices.lib.helpers import random_key, safe64encode
from routechoices.lib.tcp_protocols.commons import (
    GenericConnection,
    GenericTCPServer,
    add_locations,
    save_device,
)

BINARY_PROTOCOL = "$"
TEXT_PROTOCOL = "*"
# Regex based on Traccar's pattern to handle various message formats
# It captures key groups for different message types (V1, NBR, HTBT etc.)
H02_STR_PATTERN = re.compile(
    r"^\*..,"  # Manufacturer
    r"(\d+?)?,"  # IMEI (Group 1)
    r"([^,]+?)"  # Command (Group 2)
    r"(,[^#]*)?"  # Rest of the data (Group 3)
    r"#?$"  # Optional terminator
)
LATLON_FMT1_RE = re.compile(r"-(\d+)-(\d+\.\d+)")
LATLON_FMT2_RE = re.compile(r"(\d+)(\d\d\.\d+)")
LATLON_FMT3_RE = re.compile(r"(\d+)(\d\d)(\d{4})")


def parse_h02_latlon(lat_raw, lon_raw, ns, ew):
    lat = None
    if match := LATLON_FMT1_RE.match(lat_raw):
        lat_deg, lat_min = match.groups()
        lat_deg = int(lat_deg)
        lat_min = float(lat_min)
        lat = lat_deg + (lat_min / 60)
    elif match := LATLON_FMT2_RE.match(lat_raw):
        lat_deg, lat_min = match.groups()
        lat_deg = int(lat_deg)
        lat_min = float(lat_min)
        lat = lat_deg + (lat_min / 60)
    elif match := LATLON_FMT3_RE.match(lat_raw):
        lat_deg, lat_min, lat_min_frac = match.groups()
        lat_deg = int(lat_deg)
        lat_min = float(f"{lat_min}.{lat_min_frac}")
        lat = lat_deg + (lat_min / 60)

    lon = None
    if match := LATLON_FMT1_RE.match(lon_raw):
        lon_deg, lon_min = match.groups()
        lon_deg = int(lon_deg)
        lon_min = float(lon_min)
        lon = lon_deg + (lon_min / 60)
    elif match := LATLON_FMT2_RE.match(lon_raw):
        lon_deg, lon_min = match.groups()
        lon_deg = int(lon_deg)
        lon_min = float(lon_min)
        lon = lon_deg + (lon_min / 60)
    elif match := LATLON_FMT3_RE.match(lon_raw):
        lon_deg, lon_min, lon_min_frac = match.groups()
        lon_deg = int(lon_deg)
        lon_min = float(f"{lon_min}.{lon_min_frac}")
        lon = lon_deg + (lon_min / 60)

    if not lat or not lon:
        raise Exception("Coul not parse value")

    if ns == "S":
        lat *= -1
    if ew == "W":
        lon *= -1

    return lat, lon


def bcd_integer(buff, digits):
    offset = 0
    result = 0
    while offset < digits // 2:
        b = buff[offset]
        result *= 10
        result += b >> 4
        result *= 10
        result += b & 0x0F
        offset += 1

    if digits % 2 != 0:
        b = buff[offset]
        result *= 10
        result += b >> 4

    return result


def read_binary_coordinate(buff, lon):
    deg = bcd_integer(buff, 3 if lon else 2)

    result = 0
    offset = 1
    ll = 6
    if lon:
        result = buff[1] & 0x0F
        offset = 2
        ll = 5
    return (result * 10 + bcd_integer(buff[offset:], ll) * 0.0001) / 60 + deg


def decode_battery(value):
    if value == 0:
        return None
    elif value <= 3:
        return (value - 1) * 10
    elif value <= 6:
        return (value - 1) * 20
    elif value <= 100:
        return value
    elif value >= 0xF1 and value <= 0xF6:
        return value - 0xF0
    else:
        return None


class H02Connection(GenericConnection):
    """
    Handles a single device connection using the H02 protocol.

    Each instance of this class manages the lifecycle of a TCP connection
    from a GPS device, including reading data, parsing packets, and
    updating the database.
    """

    protocol_name = "H02"

    def __init__(self, stream, address, logger):
        """
        Initialize the H02 connection handler.
        """
        print(f"H02 - New connection from {address}")
        self.aid = random_key()
        self.imei = None
        self.address = address
        self.stream = stream
        self.stream.set_close_callback(self._on_close)
        self.db_device = None
        self.logger = logger

    async def decode_binary_packet(self, data_bin):
        if not self.imei:
            raise Exception(f"Data from unknown device ({self.address})")

        if battery_level := decode_battery(data_bin[10]):
            self.db_device.battery_level = battery_level
            await save_device(self.db_device)

        is_valid = (data_bin[15] & 0x02) != 0
        if not is_valid:
            print(
                f"H02 - {self.imei} sent data with invalid GPS fix. Location not saved.",
                flush=True,
            )
            return

        hh = bcd_integer(data_bin[0:], 2)
        mm = bcd_integer(data_bin[1:], 2)
        ss = bcd_integer(data_bin[2:], 2)
        DD = bcd_integer(data_bin[3:], 2)
        MM = bcd_integer(data_bin[4:], 2)
        YY = bcd_integer(data_bin[5:], 2)

        timestamp = arrow.get(
            f"20{YY:0>2}-{MM:0>2}-{DD:0>2}T{hh:0>2}:{mm:0>2}:{ss:0>2}Z"
        ).timestamp()

        lat = read_binary_coordinate(data_bin[6:], False)
        lon = read_binary_coordinate(data_bin[11:], True)
        if (data_bin[15] & 0x04) == 0:
            lat = -lat
        if (data_bin[15] & 0x08) == 0:
            lon = -lon

        await add_locations(self.db_device, [(timestamp, lat, lon)])
        print(f"H02 - {self.imei} wrote 1 locations to DB", flush=True)

    async def send_response(self, response_type):
        response = ""
        imei = self.imei
        if self.fake_imei:
            imei = imei[:10]
        if response_type == "R12":
            time = arrow.get().format("HHmmss")
            response = f"*HQ,{imei},{response_type},{time}#".encode()
        else:
            time = arrow.get().format("YYYYMMDDHHmmss")
            response = f"*HQ,{imei},V4,{response_type},{time}#".encode()
        await self.stream.write(response)

    async def parse_default_text_packet(self, imei, data, command):
        if not self.imei:
            raise Exception(f"Data from unknown device ({self.address})")
        parts = data.split(",")
        # Standard location packets have a specific length
        if len(parts) < 10:
            print("too short", len(parts), flush=True)
            return

        if command == "V4":
            parts = parts[1:]

        if command == "V1":
            await self.send_response("V1")

        gps_status = parts[1]
        if gps_status != "A" and not gps_status.isdigit():
            print(
                f"H02 - {self.imei} sent data with invalid GPS fix. Location not saved.",
                flush=True,
            )
            return

        time_raw = parts[0]
        date_raw = parts[8]
        timestamp = arrow.get(
            f"20{date_raw[4:6]}-{date_raw[2:4]}-{date_raw[0:2]}T{time_raw[0:2]}:{time_raw[2:4]}:{time_raw[4:6]}Z"
        ).timestamp()

        lat_raw, ns = parts[2], parts[3]
        lon_raw, ew = parts[4], parts[5]
        lat, lon = parse_h02_latlon(lat_raw, lon_raw, ns, ew)
        await add_locations(self.db_device, [(timestamp, lat, lon)])
        print(f"H02 - {self.imei} wrote 1 locations to DB", flush=True)

    async def parse_vp1_packet(self, imei, data):
        if not self.imei:
            raise Exception(f"Data from unknown device ({self.address})")
        parts = data.split(",")
        # Standard location packets have a specific length
        if len(parts) < 5:
            return

        gps_status = parts[0]
        if gps_status != "A":
            print(
                f"H02 - {self.imei} sent data with invalid GPS fix. Location not saved.",
                flush=True,
            )
            return
        timestamp = arrow.get().timestamp()

        lat_raw, ns = parts[1], parts[2]
        lon_raw, ew = parts[3], parts[4]
        lat, lon = parse_h02_latlon(lat_raw, lon_raw, ns, ew)
        await add_locations(self.db_device, [(timestamp, lat, lon)])
        print(f"H02 - {self.imei} wrote 1 locations to DB", flush=True)

    async def parse_heartbeat_packet(self, imei, data):
        if not self.imei:
            raise Exception(f"Data from unknown device ({self.address})")
        print(f"H02 - {imei} Heartbeat")
        if data:
            parts = data.split(",")
            try:
                battery_level = min(max(0, int(parts[0])), 100)
                self.db_device.battery_level = battery_level
                print(f"H02 - Battery: {battery_level}%")
            except (ValueError, IndexError):
                print("H02 - Could not read battery value")
        await save_device(self.db_device)

    async def start_listening(self):
        """
        Start the main loop to listen for and process incoming data.
        """
        print(f"H02 - Listening from {self.address}")

        while True:
            try:
                data_bin = await self.stream.read_bytes(1)
                protocol = data_bin.decode("ascii", errors="ignore").strip()
                if protocol == BINARY_PROTOCOL:
                    data = bytearray(b"\x00" * 64)
                    data_len = await self.stream.read_into(data, partial=True)
                    data_bin += data[:data_len]
                elif protocol == TEXT_PROTOCOL:
                    data_bin += await self.stream.read_until(b"#")
                else:
                    raise Exception("Invalid protocol")
            except Exception:
                self.stream.close()
                return

            if not data_bin:
                continue

            if protocol == TEXT_PROTOCOL:
                # Text Protocol
                try:
                    data_str = data_bin.decode("ascii", errors="ignore").strip()
                except UnicodeDecodeError:
                    print(
                        f"H02 - Could not decode data from {self.address}", flush=True
                    )
                    continue
                match = H02_STR_PATTERN.match(data_str)
                if not match:
                    print(f"H02 - Unrecognized packet format: {data_str}")
                    continue

                imei, command, data = match.groups()
                if data:
                    if data[0] == ",":
                        data = data[1:]
                    else:
                        print(f"H02 - Unrecognized packet format: {data_str}")
                        continue

                if not imei and self.imei:
                    imei = self.imei
                try:
                    self.fake_imei = False
                    if len(imei) == 10:
                        self.fake_imei = True
                        imei = luhn.append(f"{imei:0<14}")
                    await self.process_identification(imei)
                except Exception:
                    print(
                        f"H02 - Error parsing identification data ({self.address})",
                        flush=True,
                    )
                    self.stream.close()
                    return

                self.logger.info(
                    f"H02 CONN, {self.aid}, {self.address}: {safe64encode(data_bin)}"
                )

                try:
                    if command == "NBR":
                        self.send_response("NBR")
                    elif command in ("LINK", "V3", "SMS"):
                        # Not handled commands, does not require an answer
                        continue
                    elif command == "VP1":
                        await self.parse_vp1_packet(imei, data)
                    elif command in ("V0", "HTBT"):
                        await self.parse_heartbeat_packet(imei, data)
                        search = f",{command}"
                        response = f"{data_str[0:(data_str.find(search) + len(search))]}#".encode()
                        await self.stream.write(response)
                    elif command.startswith("V"):
                        await self.parse_default_text_packet(imei, data, command)
                    else:
                        continue
                except Exception as e:
                    print(f"H02 - Error parsing data ({self.address}): {e}", flush=True)
            else:
                # Binary Protocol
                offset = 9
                try:
                    if len(data_bin) == 42:
                        imei = data_bin[1:9].hex()[:15]
                    else:
                        offset = 6
                        imei = data_bin[1:6].hex()[:10]
                        imei = luhn.append(f"{imei:0<14}")
                    await self.process_identification(imei)
                except Exception:
                    print(
                        f"H02 - Error parsing identification data ({self.address}) ({imei})",
                        flush=True,
                    )
                    self.stream.close()
                    return

                self.logger.info(
                    f"H02 CONN, {self.aid}, {self.address}: {safe64encode(data_bin)}"
                )

                try:
                    await self.decode_binary_packet(data_bin[offset:])
                except Exception as e:
                    print(f"H02 - Error parsing data ({self.address}): {e}", flush=True)


class TCPServer(GenericTCPServer):
    """
    A TCP server for handling H02 protocol connections.
    """

    connection_class = H02Connection
