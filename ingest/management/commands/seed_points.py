from django.core.management.base import BaseCommand
from ingest.models import WeatherPoint

# 14 Czech krajů capitals + 8 Slovak krajů capitals = 22 points.
# macro_region: CZ split into Čechy/Morava (no separate Slezsko — Ostrava
# would be its only point, so it belongs to Morava by explicit user choice;
# Jihlava sits on the historic border, assigned to Morava), SK into the
# standard západné/stredné/východné thirds.
POINTS = [
    # Czech Republic — one point per kraj (administrative region)
    {"name": "Praha",               "region": "Hlavní město Praha",    "country": "CZ", "macro_region": "cechy",    "latitude": "50.0755", "longitude": "14.4378"},
    {"name": "Středočeský",         "region": "Středočeský kraj",      "country": "CZ", "macro_region": "cechy",    "latitude": "49.9393", "longitude": "14.6811"},
    {"name": "České Budějovice",    "region": "Jihočeský kraj",        "country": "CZ", "macro_region": "cechy",    "latitude": "48.9745", "longitude": "14.4745"},
    {"name": "Plzeň",               "region": "Plzeňský kraj",         "country": "CZ", "macro_region": "cechy",    "latitude": "49.7384", "longitude": "13.3736"},
    {"name": "Karlovy Vary",        "region": "Karlovarský kraj",      "country": "CZ", "macro_region": "cechy",    "latitude": "50.2304", "longitude": "12.8712"},
    {"name": "Ústí nad Labem",      "region": "Ústecký kraj",          "country": "CZ", "macro_region": "cechy",    "latitude": "50.6607", "longitude": "14.0323"},
    {"name": "Liberec",             "region": "Liberecký kraj",        "country": "CZ", "macro_region": "cechy",    "latitude": "50.7671", "longitude": "15.0563"},
    {"name": "Hradec Králové",      "region": "Královéhradecký kraj",  "country": "CZ", "macro_region": "cechy",    "latitude": "50.2092", "longitude": "15.8328"},
    {"name": "Pardubice",           "region": "Pardubický kraj",       "country": "CZ", "macro_region": "cechy",    "latitude": "50.0343", "longitude": "15.7812"},
    {"name": "Jihlava",             "region": "Kraj Vysočina",         "country": "CZ", "macro_region": "morava",   "latitude": "49.3961", "longitude": "15.5912"},
    {"name": "Brno",                "region": "Jihomoravský kraj",     "country": "CZ", "macro_region": "morava",   "latitude": "49.1951", "longitude": "16.6068"},
    {"name": "Olomouc",             "region": "Olomoucký kraj",        "country": "CZ", "macro_region": "morava",   "latitude": "49.5938", "longitude": "17.2509"},
    {"name": "Zlín",                "region": "Zlínský kraj",          "country": "CZ", "macro_region": "morava",   "latitude": "49.2237", "longitude": "17.6630"},
    {"name": "Ostrava",             "region": "Moravskoslezský kraj",  "country": "CZ", "macro_region": "morava",   "latitude": "49.8209", "longitude": "18.2625"},
    # Slovakia — one point per kraj
    {"name": "Bratislava",          "region": "Bratislavský kraj",     "country": "SK", "macro_region": "zapadne",  "latitude": "48.1486", "longitude": "17.1077"},
    {"name": "Trnava",              "region": "Trnavský kraj",         "country": "SK", "macro_region": "zapadne",  "latitude": "48.3774", "longitude": "17.5879"},
    {"name": "Trenčín",             "region": "Trenčínský kraj",       "country": "SK", "macro_region": "zapadne",  "latitude": "48.8973", "longitude": "18.0440"},
    {"name": "Nitra",               "region": "Nitrianský kraj",       "country": "SK", "macro_region": "zapadne",  "latitude": "48.3069", "longitude": "18.0875"},
    {"name": "Žilina",              "region": "Žilinský kraj",         "country": "SK", "macro_region": "stredne",  "latitude": "49.2231", "longitude": "18.7394"},
    {"name": "Banská Bystrica",     "region": "Banskobystrický kraj",  "country": "SK", "macro_region": "stredne",  "latitude": "48.7395", "longitude": "19.1530"},
    {"name": "Prešov",              "region": "Prešovský kraj",        "country": "SK", "macro_region": "vychodne", "latitude": "49.0018", "longitude": "21.2393"},
    {"name": "Košice",              "region": "Košický kraj",          "country": "SK", "macro_region": "vychodne", "latitude": "48.7163", "longitude": "21.2611"},
]


class Command(BaseCommand):
    help = "Seed the 22 CZ/SK weather points. Safe to run multiple times."

    def handle(self, *args, **options):
        created_count = 0
        for data in POINTS:
            _, created = WeatherPoint.objects.update_or_create(
                name=data["name"],
                country=data["country"],
                defaults={
                    "region": data["region"],
                    "macro_region": data["macro_region"],
                    "latitude": data["latitude"],
                    "longitude": data["longitude"],
                },
            )
            if created:
                created_count += 1
                self.stdout.write(f"  Created: {data['name']} ({data['country']})")

        total = WeatherPoint.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f"Done. {created_count} new point(s) created. {total} total in database."
        ))
