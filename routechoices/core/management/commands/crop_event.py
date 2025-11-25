from django.core.management.base import BaseCommand

from routechoices.core.models import Event
from routechoices.lib.helpers import distance_between_locations


class Command(BaseCommand):
    help = "Crop all points of the competitors of an event so that their distances are within a given distance range (in km)"

    def add_arguments(self, parser):
        parser.add_argument("--event_url", type=str)
        parser.add_argument("--min_dist", type=float, default=0)
        parser.add_argument("--max_dist", type=float)
        parser.add_argument("--force", action="store_true", default=False)

    def handle(self, *args, **options):
        force = options["force"]
        min_dist = options["min_dist"]
        max_dist = options["max_dist"]
        event = Event.get_by_url(options["event_url"])
        if not event:
            self.stderr.write("Event not found")
            return
        if max_dist and max_dist <= min_dist:
            self.stderr.write("max_dist must be higher than min_dist")
            return

        for competitor, start, end in event.iterate_competitors():
            if not competitor.device:
                continue
            prev_point = None
            total_distance = 0
            all_locations = competitor.device.locations
            crop_starttime = None
            for point in all_locations:
                if start.timestamp() > point[0]:
                    continue
                if point[0] > end.timestamp():
                    break
                if prev_point:
                    total_distance += (
                        distance_between_locations(point, prev_point) / 1000
                    )
                if max_dist and total_distance > max_dist:
                    crop_starttime = point[0]
                    break
                prev_point = point
            if total_distance < min_dist:
                if force:
                    competitor.device = None
                    competitor.save()
                self.stdout.write(f"Removing all points for {competitor}")
                continue
            if crop_starttime:
                cropped_locations = [
                    point
                    for point in all_locations
                    if (point[0] < crop_starttime or point[0] > end.timestamp())
                ]
                nb_points_cropped = len(all_locations) - len(cropped_locations)
                if force:
                    device = competitor.device
                    device.add_locations(cropped_locations, reset=True)
                self.stdout.write(
                    f"Cropping {nb_points_cropped} locations for {competitor}"
                )
        if not force:
            self.stderr.write("This was a dry run....")
