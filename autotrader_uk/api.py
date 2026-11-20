"""The /autotrader-uk/* endpoint functions (route_glue's route_table calls
them with the schema's loaded kwargs). Every endpoint caches through
cache.cached_call keyed on the validated params.

One filter answer serves three endpoints: cars/filters, makes/list and
models/list all slice the cached `_facets` record of a search, and the
searches borrow the site's own spellings from it ("Petrol" matches,
"petrol" finds nothing), so a body type, fuel, colour, model or trim is
accepted in any letter case.

The site serves 100 pages (2,000 listings) per search and 20 pages (400)
per dealer search and no more, whatever the total: `total_pages` is capped
there and `is_truncated` says when the total is larger than what the pages
reach.
"""
from cache import cached_call
from autotrader_uk import cache_config as ttl
from autotrader_uk import parsers, refs
from autotrader_uk.fetch import (DEALER_PAGE_SIZE, MAX_DEALER_PAGES, MAX_SEARCH_PAGES, REVIEW_PAGE_SIZE,
                                 SEARCH_PAGE_SIZE, AutotraderUkBadRequest, AutotraderUkNotFound, fetch_advert,
                                 fetch_dealer, fetch_dealer_name, fetch_dealers, fetch_facets, fetch_listing_page,
                                 fetch_reviews, fetch_search)

_FILTER_PARAMS = tuple(refs.SEARCH_FILTERS)


def _pages(total, page_size, cap=None):
    pages = -(-total // page_size) if total else 0
    return min(pages, cap) if cap else pages


# ---- the cached filter answer --------------------------------------------------------

def _facets_record(vehicle_type, filters):
    channel = refs.VEHICLE_TYPES[vehicle_type]["channel"]
    return parsers.parse_facets(fetch_facets(channel, filters))


def _facets(vehicle_type, filters=None):
    """Every filter of a search with its options and counts (parsers.parse_facets),
    cached. `filters` is {the site's filter name: value}; postcode and price
    type are filled in."""
    filters = {"postcode": refs.DEFAULT_POSTCODE, "price_search_type": "total", **(filters or {})}
    return cached_call("autotrader_uk.facets", {"vehicle_type": vehicle_type, "filters": filters},
                       _facets_record, ttl.FACET_CACHE)


def _options(facets, site_filter):
    for item in facets.get("filters") or []:
        if item.get("site_name") == site_filter:
            return [option["value"] for option in item.get("options") or []]
    return []


def _spelled(value, options):
    """`value` the way the site spells it, or None when it is not an option."""
    by_lower = {str(option).lower(): option for option in options}
    return by_lower.get(str(value).lower())


def _site_filters(data):
    """The validated search params -> {the site's filter name: value}."""
    vehicle_type = data["vehicle_type"]
    monthly = data.get("price_type") == "monthly"
    filters = {"postcode": data["postcode"], "price_search_type": "monthly" if monthly else "total"}
    for param in _FILTER_PARAMS:
        value = data.get(param)
        if value is None or value is False:
            continue
        site = refs.SEARCH_FILTERS[param]
        if param in refs.SPELLED:
            options = _options(_facets(vehicle_type), site)
            spelled = _spelled(value, options)
            if spelled is None:
                raise AutotraderUkBadRequest(f"{param}: {value!r} is not one of: {', '.join(options)}")
            value = spelled
        elif param == "model":
            value = _spelled(value, _options(_facets(vehicle_type, {"make": data["make"]}), "model")) or value
        elif param == "trim":
            model = filters.get("model") or data["model"]
            narrowed = _facets(vehicle_type, {"make": data["make"], "model": model})
            value = _spelled(value, _options(narrowed, "aggregated_trim")) or value
        elif param in ("min_price", "max_price") and monthly:
            site = site.replace("price", "monthly_price")
        elif param in ("min_engine_size", "max_engine_size"):
            value = f"{value:.1f}"
        elif value is True:
            value = "true"
        filters[site] = str(value)
    return filters


# ---- cars/search, dealers/cars -------------------------------------------------------

def _search_page(data, filters):
    vehicle_type, page = data["vehicle_type"], data["page"]
    channel = refs.VEHICLE_TYPES[vehicle_type]["channel"]
    parsed = parsers.parse_search(fetch_search(channel, filters, page, data["sort"]), vehicle_type)
    total = parsed["total_count"] or 0
    return {
        "vehicle_type": vehicle_type,
        "postcode": data["postcode"],
        "results": parsed["results"],
        "is_truncated": total > SEARCH_PAGE_SIZE * MAX_SEARCH_PAGES,
        "pagination": {
            "page": page,
            "items_per_page": SEARCH_PAGE_SIZE,
            "total_pages": _pages(total, SEARCH_PAGE_SIZE, MAX_SEARCH_PAGES),
            "total_count": total,
        },
    }


def _cars_search(**data):
    return _search_page(data, _site_filters(data))


def cars_search(**data):
    """Vehicles for sale that match the filters, 20 per page, without the
    promoted copies the site mixes in."""
    return cached_call("autotrader_uk.cars_search", data, _cars_search, ttl.SEARCH_CACHE)


def _dealer_cars(**data):
    dealer = data["dealer"]
    result = _search_page(data, {**_site_filters(data), "retailer_id": dealer})
    if not result["results"] and data["page"] == 1:
        fetch_dealer_name(dealer)        # no stock, or no such dealer? An unknown id raises NotFound
    return {"dealer": {"id": dealer, "link": refs.dealer_link(dealer)}, **result}


def dealer_cars(**data):
    """A dealer's stock as search rows (the same filters as cars/search)."""
    return cached_call("autotrader_uk.dealer_cars", data, _dealer_cars, ttl.SEARCH_CACHE)


# ---- cars/details --------------------------------------------------------------------

def _car_details(car, postcode):
    parts = fetch_advert(car, postcode)
    vehicle_type = refs.vehicle_type_of(((parts.get("AdvertSpec") or {}).get("specification") or {})
                                        .get("vehicleCategory"))
    path = refs.car_path(car, vehicle_type)
    # The page adds what only it shows (the good-price range, highlights,
    # finance quotes, delivery to the postcode). A listing the site still has
    # but serves no page for keeps those values null.
    html = fetch_listing_page(path, postcode) if path else None
    return parsers.parse_advert(parts, parsers.parse_listing_page(html) if html else None, postcode)


def car_details(**data):
    """Everything Autotrader shows for one listing, of any vehicle type."""
    return cached_call("autotrader_uk.car_details", data, _car_details, ttl.DETAILS_CACHE)


# ---- cars/filters, makes/list, models/list -------------------------------------------

def _public(item):
    return {"name": item["name"], "is_search_param": item["is_search_param"], "options": item["options"]}


def cars_filters(**data):
    """The filters of a search with their options and live counts. The
    other params narrow the counts the way they narrow the search."""
    wanted = data.pop("filter", None)
    filters = _site_filters(data)
    facets = _facets(data["vehicle_type"], filters)
    items = [_public(item) for item in facets.get("filters") or []]
    if wanted:
        names = [item["name"] for item in items]
        items = [item for item in items if item["name"] == wanted.lower()]
        if not items:
            raise AutotraderUkBadRequest(f"filter: {wanted!r} is not one of: {', '.join(names)}")
    return {
        "vehicle_type": data["vehicle_type"],
        "postcode": data["postcode"],
        "total_count": facets.get("total_count"),
        "filters": items,
    }


def makes_list(vehicle_type):
    """Every make with listings, with its live count."""
    makes = parsers.named_counts(_facets(vehicle_type), "make")
    return {"vehicle_type": vehicle_type, "count": len(makes), "results": makes}


def models_list(make, vehicle_type):
    """Every model of a make with listings, with its live count."""
    spelled = _spelled(make, _options(_facets(vehicle_type), "make"))
    if spelled is None:
        raise AutotraderUkNotFound(f"make {make!r} not found on Autotrader (see makes/list)")
    models = parsers.named_counts(_facets(vehicle_type, {"make": spelled}), "model")
    return {"vehicle_type": vehicle_type, "make": spelled, "count": len(models), "results": models}


# ---- dealers -------------------------------------------------------------------------

def _dealers_search(postcode, radius, name, make, vehicle_type, sort, page):
    query = {"retailerTypes": refs.VEHICLE_TYPES[vehicle_type]["dealers"]}
    if postcode:
        query["postcode"] = postcode
        sort = sort or refs.DEALER_SORTS["distance"]
    if radius:
        query["distance"] = radius
    if name:
        query["name"] = name
    if make:
        query["make"] = [make.upper()]
    parsed = parsers.parse_dealers(fetch_dealers(query, sort, page))
    total = parsed["total_count"] or 0
    return {
        "vehicle_type": vehicle_type,
        "postcode": postcode,
        "results": parsed["results"],
        "is_truncated": total > DEALER_PAGE_SIZE * MAX_DEALER_PAGES,
        "pagination": {
            "page": page,
            "items_per_page": DEALER_PAGE_SIZE,
            "total_pages": _pages(total, DEALER_PAGE_SIZE, MAX_DEALER_PAGES),
            "total_count": total,
        },
    }


def dealers_search(**data):
    """Dealers by place, name or make, 20 per page."""
    return cached_call("autotrader_uk.dealers_search", data, _dealers_search, ttl.DEALER_SEARCH_CACHE)


def _dealer_details(dealer):
    record = parsers.parse_dealer(fetch_dealer(dealer))
    if record is None:
        raise AutotraderUkNotFound(f"dealer {dealer} not found")
    return record


def dealer_details(**data):
    """A dealer's full profile: contacts, address, rating, stock, services, opening hours."""
    return cached_call("autotrader_uk.dealer_details", data, _dealer_details, ttl.DEALER_CACHE)


def _dealer_reviews(dealer, rating, query, period, sort, page):
    parsed = parsers.parse_reviews(fetch_reviews(dealer, page, sort, rating, query, period))
    matching = parsed.pop("matching_count") or 0
    total_pages = _pages(matching, REVIEW_PAGE_SIZE)
    if page > total_pages:
        parsed["reviews"] = []           # the site answers a page past the end with its last page again
    return {
        **parsed,
        "pagination": {
            "page": page,
            "items_per_page": REVIEW_PAGE_SIZE,
            "total_pages": total_pages,
            "total_count": matching,
        },
    }


def dealer_reviews(**data):
    """A dealer's customer reviews, 20 per page, with the rating summary."""
    return cached_call("autotrader_uk.dealer_reviews", data, _dealer_reviews, ttl.REVIEWS_CACHE)
