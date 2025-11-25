from django.core.management.base import BaseCommand

from routechoices.core.bg_tasks import (
    import_single_event_from_gps_seuranta,
    import_single_event_from_livelox,
    import_single_event_from_loggator,
    import_single_event_from_sportrec,
    import_single_event_from_tractrac,
    import_single_event_from_virekunnas,
)
from routechoices.core.models import Club
from routechoices.lib.other_gps_services.commons import EventImportError


class Command(BaseCommand):
    help = "Import event from 3rd party"

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(
            title="sub-commands",
            required=True,
        )
        gpsseuranta_parser = subparsers.add_parser(
            "gpsseuranta",
            help="Import from gpsseuranta",
        )
        gpsseuranta_parser.set_defaults(method=self.gpsseuranta)
        gpsseuranta_parser.add_argument(dest="event_ids", nargs="+", type=str)

        virekunnas_parser = subparsers.add_parser(
            "virekunnas",
            help="Import from virekunnas",
        )
        virekunnas_parser.set_defaults(method=self.virekunnas)
        virekunnas_parser.add_argument(dest="event_ids", nargs="+", type=str)

        livelox_parser = subparsers.add_parser(
            "livelox",
            help="Import from livelox",
        )
        livelox_parser.set_defaults(method=self.livelox)
        livelox_parser.add_argument(dest="event_ids", nargs="+", type=str)

        loggator_parser = subparsers.add_parser(
            "loggator",
            help="Import from loggator",
        )
        loggator_parser.set_defaults(method=self.loggator)
        loggator_parser.add_argument(dest="event_ids", nargs="+", type=str)

        sportrec_parser = subparsers.add_parser(
            "sportrec",
            help="Import from sportrec",
        )
        sportrec_parser.set_defaults(method=self.sportrec)
        sportrec_parser.add_argument(dest="event_ids", nargs="+", type=str)

        tractrac_parser = subparsers.add_parser(
            "tractrac",
            help="Import from tractrac",
        )
        tractrac_parser.set_defaults(method=self.tractrac)
        tractrac_parser.add_argument(dest="event_ids", nargs="+", type=str)

        parser.add_argument("-t", "--task", action="store_true", default=False)
        parser.add_argument("-c", "--club", type=str)

    def handle(self, *args, method, **options):
        club = Club.objects.filter(slug=options["club"], is_personal_page=False).first()
        if options["club"] and not club:
            self.stderr.write(f"Could not find club {options['club']}")
            return
        method(*args, **options)

    def gpsseuranta(self, *args, **options):
        for event_id in options["event_ids"]:
            try:
                self.stdout.write(f"Importing event {event_id}")
                if options["task"]:
                    import_single_event_from_gps_seuranta(
                        event_id, club=options["club"]
                    )
                else:
                    import_single_event_from_gps_seuranta.now(
                        event_id, club=options["club"]
                    )
            except EventImportError:
                self.stderr.write(f"Could not import event {event_id}")
                continue

    def virekunnas(self, *args, **options):
        for event_id in options["event_ids"]:
            try:
                self.stdout.write(f"Importing event {event_id}")
                if options["task"]:
                    import_single_event_from_virekunnas(event_id, club=options["club"])
                else:
                    import_single_event_from_virekunnas.now(
                        event_id, club=options["club"]
                    )
            except EventImportError:
                self.stderr.write(f"Could not import event {event_id}")
                continue

    def livelox(self, *args, **options):
        for event_id in options["event_ids"]:
            try:
                self.stdout.write(f"Importing event {event_id}")
                if options["task"]:
                    import_single_event_from_livelox(event_id, club=options["club"])
                else:
                    import_single_event_from_livelox.now(event_id, club=options["club"])
            except EventImportError:
                self.stderr.write(f"Could not import event {event_id}")
                continue

    def loggator(self, *args, **options):
        for event_id in options["event_ids"]:
            try:
                self.stdout.write(f"Importing event {event_id}")
                if options["task"]:
                    import_single_event_from_loggator(event_id, club=options["club"])
                else:
                    import_single_event_from_loggator.now(
                        event_id, club=options["club"]
                    )
            except EventImportError:
                self.stderr.write(f"Could not import event {event_id}")
                continue

    def sportrec(self, *args, **options):
        for event_id in options["event_ids"]:
            try:
                self.stdout.write(f"Importing event {event_id}")
                if options["task"]:
                    import_single_event_from_sportrec(event_id, club=options["club"])
                else:
                    import_single_event_from_sportrec.now(
                        event_id, club=options["club"]
                    )
            except EventImportError as e:
                self.stderr.write(f"Could not import event {event_id}: {str(e)}")
                continue

    def tractrac(self, *args, **options):
        for event_id in options["event_ids"]:
            try:
                self.stdout.write(f"Importing event {event_id}")
                if options["task"]:
                    import_single_event_from_tractrac(event_id, club=options["club"])
                else:
                    import_single_event_from_tractrac.now(
                        event_id, club=options["club"]
                    )
            except EventImportError:
                self.stderr.write(f"Could not import event {event_id}")
                continue
