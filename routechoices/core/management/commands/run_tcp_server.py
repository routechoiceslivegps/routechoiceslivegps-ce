import signal
import sys

from django.core.management.base import BaseCommand
from tornado.ioloop import IOLoop


def sigterm_handler(_signo, _stack_frame):
    sys.exit(0)


INTERNAL_PORT_RANGE_START = 5000
SUPPORTED_PORT = (
    ("codec8", "Codec8 (Teltonilka)", (5027, 2000)),
    ("gt06", "GT06", (5023, 2005)),
    ("h02", "H02", (5013)),
    ("mictrack", "MicTrack", (5191, 2001)),
    ("queclink", "Queclink", (5004, 2002)),
    ("xexun", "Xexun", (5006, 2004)),
    ("xexun2", "Xexun2", (5233, 2006)),
)


class Command(BaseCommand):
    help = "Run a TCP server for GPS trackers."

    def add_arguments(self, parser):
        for slug, name, port in SUPPORTED_PORT:
            parser.add_argument(
                f"--{slug}_port", nargs="?", type=int, help=f"{name} Port"
            )

    def handle(self, *args, **options):
        servers = set()
        signal.signal(signal.SIGTERM, sigterm_handler)
        for slug, name, port in SUPPORTED_PORT:
            if port := options.get(f"{slug}_port"):
                protocol_lib = __import__(
                    f"routechoices.lib.tcp_protocols.{slug}", fromlist=[slug]
                )
                server = protocol_lib.TCPServer()
                server.listen(options.get("{name}_port"), reuse_port=True)
                servers.add((slug, server))
                print(f"Listening protocol {name} on port {port}", flush=True)
        try:
            print("Start listening TCP data...", flush=True)
            IOLoop.current().start()
        except (KeyboardInterrupt, SystemExit):
            for slug, server in servers:
                if options.get("{slug}_port"):
                    server.stop()
            IOLoop.current().stop()
        finally:
            print("Stopped listening TCP data...", flush=True)
