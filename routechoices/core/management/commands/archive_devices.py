from django.core.management.base import BaseCommand
from django.utils.timezone import now

from routechoices.core.models import Competitor


class Command(BaseCommand):
    help = "Release competitors trapped in a freezed event"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", default=False)

    def handle(self, *args, **options):
        force = options["force"]

        chilling_competitor = Competitor.objects.filter(
            event__freezed_at__lt=now(),
            device__virtual=False,
        )
        competitor_released = 0
        for competitor in chilling_competitor:
            archive = competitor.archive_device(force)
            if archive:
                competitor_released += 1
                self.stdout.write(f"Competitor {competitor} is trapped, releasing him")
        if not competitor_released:
            self.stdout.write(self.style.SUCCESS("No competitor trapped"))
        elif force:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully released {competitor_released} competitors"
                )
            )
        else:
            self.stdout.write(f"Would released {competitor_released} competitors")
