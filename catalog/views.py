from django.http import JsonResponse, Http404, HttpResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_GET
from django.core.paginator import Paginator
from . import featured, playback
from .providers import LocalProvider, get_provider, normalize_text, ProviderError
COLLECTIONS={"space":{"title":"За пределами Земли","subtitle":"Большое кино о бесконечности","ids":["interstellar","dune-part-two","arrival","blade-runner-2049","inception"]},"feeling":{"title":"Чувствовать больше","subtitle":"Истории, которые остаются внутри","ids":["past-lives","la-la-land","perfect-days","whiplash","the-shawshank-redemption"]},"worlds":{"title":"Миры с характером","subtitle":"Каждый кадр — отдельная вселенная","ids":["grand-budapest-hotel","poor-things","spirited-away","everything-everywhere","the-truman-show"]}}
@require_GET
def home(request):
    movies=LocalProvider().search()
    return render(request, "guest.html", {
        "active": "home", "posters": movies[:20], "featured": movies[:6], "hero_movie": featured.MOVIE,
        "hero_video": playback.source_for(request, featured.ID),
        "open_movies": playback.editorial_films() if playback.published_sources() else [],
    })

def country_names(movie):
    return [name.strip() for name in movie.get("country", "").split(",") if name.strip()]


def results(request, media_type="", ranking=False):
    query = request.GET.get("q", "").strip()[:160]
    collection = request.GET.get("collection", "")
    collection_info = COLLECTIONS.get(collection)
    source_error = False
    if collection_info:
        ids = collection_info["ids"]
        movies = [movie for movie in LocalProvider().search(query) if movie["id"] in ids]
    else:
        try:
            movies = get_provider().search(query)
        except ProviderError:
            movies = LocalProvider().search(query)
            source_error = True

    media_type = media_type or request.GET.get("type", "")
    if media_type not in ("movie", "tv"):
        media_type = ""
    if media_type:
        movies = [movie for movie in movies if movie.get("media_type", "movie") == media_type]

    genres = sorted({genre for movie in movies for genre in movie["genres"]})
    countries = sorted({country for movie in movies for country in country_names(movie)})
    years = sorted({str(movie["year"]) for movie in movies if movie["year"]}, reverse=True)

    def selected(name, options):
        value = request.GET.get(name, "").strip()[:60]
        return next((option for option in options if normalize_text(option) == normalize_text(value)), value)

    genre = selected("genre", genres)
    if genre:
        movies = [movie for movie in movies if any(
            normalize_text(item) == normalize_text(genre) for item in movie["genres"]
        )]
    country = selected("country", countries)
    if country:
        movies = [movie for movie in movies if any(
            normalize_text(item) == normalize_text(country) for item in country_names(movie)
        )]
    year = request.GET.get("year", "").strip()
    if len(year) == 4 and year.isascii() and year.isdecimal():
        movies = [movie for movie in movies if movie["year"] == int(year)]
    else:
        year = ""
    minimum = request.GET.get("rating", "")
    if minimum in ("7", "8", "9"):
        movies = [movie for movie in movies if movie["rating"] >= int(minimum)]
    else:
        minimum = ""

    playable = request.GET.get("watch") == "1"
    if playable:
        sources = playback.published_sources()
        movies = [movie for movie in movies if movie["id"] in sources]

    sort = "rating" if ranking else request.GET.get("sort", "curated")
    if sort in ("rating", "year"):
        movies.sort(key=lambda movie: movie[sort], reverse=True)
    elif sort == "title":
        movies.sort(key=lambda movie: normalize_text(movie["title"]))
    else:
        sort = "curated"
    return movies, {
        "media_type": media_type, "country": country, "minimum": minimum, "ranking": ranking,
        "query": query, "genre": genre, "year": year, "sort": sort, "playable": playable,
        "source_error": source_error, "collection": collection if collection_info else "",
        "collection_info": collection_info, "genres": genres, "years": years, "countries": countries,
    }


@require_GET
def catalog(request, media_type="", ranking=False):
    movies, ctx = results(request, media_type, ranking)
    page = Paginator(movies, 12).get_page(request.GET.get("page"))
    for movie in page.object_list:
        movie["video"] = playback.source_for(request, movie["id"])
    params = request.GET.copy()
    params.pop("page", None)
    filters = {
        "type": {"movie": "Фильмы", "tv": "Сериалы"}.get(ctx["media_type"]) if not media_type else "",
        "genre": ctx["genre"], "year": ctx["year"], "country": ctx["country"],
        "rating": f"Рейтинг от {ctx['minimum']}" if ctx["minimum"] else "",
        "watch": "Доступно для просмотра" if ctx["playable"] else "",
    }
    active_filters = []
    for key, label in filters.items():
        if label:
            remaining = params.copy()
            remaining.pop(key, None)
            active_filters.append({"label": label, "url": request.path + (
                "?" + remaining.urlencode() if remaining else ""
            )})
    reset = params.copy()
    for key in set(reset) - {"q", "collection"}:
        reset.pop(key)
    ctx.update(
        movies=page.object_list, page=page, total=len(movies),
        active="top" if ranking else "series" if media_type == "tv" else "catalog",
        active_filters=active_filters, pagination_query=params.urlencode(),
        reset_url=request.path + ("?" + reset.urlencode() if reset else ""),
    )
    return render(request, "catalog.html", ctx)


@require_GET
def detail(request, movie_id):
    try:
        movie = get_provider().get(movie_id) or LocalProvider().get(movie_id)
    except ProviderError:
        movie = LocalProvider().get(movie_id)
        if not movie:
            return render(request, "error.html", {
                "message": "Источник фильмов временно недоступен. Попробуйте ещё раз через минуту.",
            }, status=503)
    if not movie:
        raise Http404
    genres = set(movie["genres"])
    related = [other for other in LocalProvider().search()
               if other["id"] != movie_id and genres.intersection(other["genres"])]
    related.sort(key=lambda other: (other.get("media_type") != movie.get("media_type"),
                                     -len(genres.intersection(other["genres"]))))
    return render(request, "detail.html", {
        "movie": movie, "movies": related[:6], "active": "catalog",
        "video": playback.source_for(request, movie_id),
    })


@require_GET
def search_api(request):
    movies,ctx=results(request);page=Paginator(movies,24).get_page(request.GET.get("page"))
    response=JsonResponse({"results":list(page.object_list),"count":len(movies),"page":page.number,"pages":page.paginator.num_pages,"fallback":ctx["source_error"]},json_dumps_params={"ensure_ascii":False})
    response["Cache-Control"]="public, max-age=60"
    return response
@require_GET
def watchlist(request):
    if request.cloud_user:return redirect('/profile/?tab=saved')
    return render(request,"watchlist.html",{"active":"watchlist"})
@require_GET
def subscription(request):
    from .billing import member
    return render(request,"subscription.html",{"active":"subscription","membership":member(request.cloud_user['id']) if request.cloud_user else None})
@require_GET
def collections(request):return render(request,"collections.html",{"active":"collections"})
@require_GET
def about(request):return render(request,"about.html",{"active":"about"})
@require_GET
def health(request):return JsonResponse({"status":"ok","service":"cinescope"})
def not_found(request,exception):return render(request,"error.html",{"message":"Страница не найдена.","code":"404"},status=404)
def server_error(request):return render(request,"error.html",{"message":"Не удалось загрузить страницу. Попробуйте немного позже.","code":"500"},status=500)

@require_GET
def trailers(request):
    movies=[m for m in LocalProvider().search() if m.get("trailer_id")]
    return render(request,"trailers.html",{"movies":movies,"active":"trailers","total":len(movies)})

@require_GET
def devices(request):
    from .devices import DEVICES, device_guide
    from django.conf import settings
    key=request.GET.get('device','')
    selected=device_guide(key) if key else None
    if key and not selected: raise Http404
    return render(request,'devices.html',{'active':'devices','devices':DEVICES,'selected':selected,'site_url':settings.SITE_URL,'preview_movies':LocalProvider().search()[:3]})

@require_GET
def store(request):
    movies=LocalProvider().search()
    selected=[m for m in movies if m['id'] in (featured.ID,'dune-part-two','interstellar','spider-man-across-the-spider-verse','inception')][:4]
    return render(request,'store.html',{'active':'store','slides':selected,'movies':movies[:6],'new_movies':sorted(movies,key=lambda m:m['year'],reverse=True)[:6],'series':[m for m in movies if m.get('media_type')=='tv'][:6], 'feature_id':featured.ID, 'feature_video':playback.source_for(request, featured.ID)})
