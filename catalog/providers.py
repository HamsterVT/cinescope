"""Provider boundary: views consume normalized movie dictionaries only."""
import re
import hashlib
import json
import logging
from functools import lru_cache
from typing import Protocol
import httpx
from django.conf import settings
from django.core.cache import cache
from . import featured
logger = logging.getLogger(__name__)
class ProviderError(Exception): pass
class MovieProvider(Protocol):
    def search(self, query: str = "") -> list[dict]: ...
    def get(self, movie_id: str) -> dict | None: ...
@lru_cache(maxsize=1)
def editorial_movies():
    from .playback import editorial_films
    return [featured.MOVIE, *json.loads((settings.BASE_DIR / "catalog/data/movies.json").read_text(encoding="utf-8")), *editorial_films()]
def normalize_text(value): return value.casefold().replace("ё", "е")
class LocalProvider:
    def search(self, query=""):
        q=normalize_text(query)
        return [dict(m) for m in editorial_movies() if q in normalize_text(" ".join([m["title"],m["original_title"],m["director"]," ".join(m.get("cast",[]))]))]
    def get(self, movie_id):
        return next((dict(m) for m in editorial_movies() if m["id"]==movie_id),None)
class DatabaseProvider:
    def search(self, query=""):
        from .models import Movie
        from django.db.models import Q
        rows=Movie.objects.all().order_by("-rating","title")
        if query: rows=rows.filter(Q(title__icontains=query)|Q(original_title__icontains=query)|Q(director__icontains=query))
        return [self.normalize(m) for m in rows[:1000]]
    def normalize(self,m):
        d={f.name:getattr(m,f.name) for f in m._meta.fields if f.name not in ("updated_at","id")}
        d.update(id=m.slug,rating=float(m.rating),source="database")
        return d
    def get(self,movie_id):
        from .models import Movie
        m=Movie.objects.filter(slug=movie_id).first()
        return self.normalize(m) if m else None
GENRES={28:"Боевик",12:"Приключения",16:"Анимация",35:"Комедия",80:"Криминал",99:"Документальный",18:"Драма",10751:"Семейный",14:"Фэнтези",36:"История",27:"Ужасы",10402:"Музыка",9648:"Детектив",10749:"Мелодрама",878:"Фантастика",53:"Триллер",10752:"Военный",37:"Вестерн"}
class TMDbProvider:
    def request(self,path,**params):
        if not settings.TMDB_READ_TOKEN: raise ProviderError("TMDb credentials missing")
        key="tmdb:"+hashlib.sha256((path+json.dumps(params,sort_keys=True)).encode()).hexdigest()
        result=cache.get(key)
        if result is not None:return result
        try:
            response=httpx.get("https://api.themoviedb.org/3/"+path,params={"language":"ru-RU","include_adult":"false",**params},headers={"Authorization":"Bearer "+settings.TMDB_READ_TOKEN},timeout=httpx.Timeout(5,connect=2))
            if response.status_code==404:return None
            response.raise_for_status();result=response.json()
            if not isinstance(result,dict):raise ValueError("Expected object")
            cache.set(key,result,300);return result
        except (httpx.HTTPError,ValueError) as exc:
            logger.warning("TMDb request failed: %s",type(exc).__name__)
            raise ProviderError("Movie source unavailable") from exc
    def normalize(self,m):
        mid=m.get("id",0);credits=m.get("credits",{})
        trailers=[v for v in m.get("videos",{}).get("results",[]) if v.get("site")=="YouTube" and v.get("type")=="Trailer" and re.fullmatch(r"[A-Za-z0-9_-]{11}",str(v.get("key","")))]
        trailers.sort(key=lambda v:(not v.get("official",False),v.get("iso_639_1")!="ru"))
        trailer=trailers[0] if trailers else {}
        return {"trailer_id":trailer.get("key",""),"trailer_title":trailer.get("name",""),"trailer_channel":"","media_type":"movie","id":f"tmdb-{mid}","slug":f"tmdb-{mid}","tmdb_id":mid,"title":m.get("title") or m.get("original_title") or "Без названия","original_title":m.get("original_title", ""),"year":int((m.get("release_date") or "0")[:4]),"rating":round(m.get("vote_average") or 0,1),"genres":[g["name"] for g in m.get("genres",[])] or [GENRES.get(g,"Другое") for g in m.get("genre_ids",[])],"overview":m.get("overview") or "Описание пока не добавлено.","poster":"https://image.tmdb.org/t/p/w500"+m["poster_path"] if m.get("poster_path") else "", "backdrop":"https://image.tmdb.org/t/p/original"+m["backdrop_path"] if m.get("backdrop_path") else "", "runtime":m.get("runtime") or 0,"director":", ".join(x["name"] for x in credits.get("crew",[]) if x.get("job")=="Director"),"cast":[x["name"] for x in credits.get("cast",[])[:8]],"country":", ".join(x["name"] for x in m.get("production_countries",[])),"source":"tmdb"}
    def search(self,query=""):
        data=self.request("search/movie" if query else "movie/popular",**({"query":query} if query else {})) or {}
        return [self.normalize(m) for m in data.get("results",[]) if not m.get("adult")]
    def get(self,movie_id):
        if not movie_id.startswith("tmdb-") or not movie_id[5:].isdigit():return LocalProvider().get(movie_id)
        m=self.request("movie/"+movie_id[5:],append_to_response="credits,videos",include_video_language="ru,en")
        return self.normalize(m) if m and not m.get("adult") else None
def get_provider():
    return {"local":LocalProvider,"tmdb":TMDbProvider,"database":DatabaseProvider}[settings.MOVIE_PROVIDER]()
