from django.core.management.base import BaseCommand
from catalog.models import Movie
from catalog.providers import editorial_movies
class Command(BaseCommand):
    help="Import the curated catalog without overwriting existing movies"
    def handle(self,*args,**kwargs):
        count=0
        for row in editorial_movies():
            data={k:v for k,v in row.items() if k not in ("id","slug","source")}
            _,created=Movie.objects.get_or_create(slug=row["slug"],defaults=data)
            count+=created
        self.stdout.write(f"Created {count} movies")
