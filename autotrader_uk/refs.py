"""Reference resolvers, link builders and the filter tables for
/autotrader-uk/*.

ONE param per input: `car` accepts a bare advert id OR any autotrader.co.uk
listing link, `dealer` a bare dealer id OR any autotrader.co.uk dealer link.
Resolvers raise ValueError with a user-facing message (RefField turns it
into a 400).

Identifier shapes:
  * advert   202611177994981   15 digits; the listing of any vehicle type
             https://www.autotrader.co.uk/car-details/<id>   (van-details,
             bike-details, motorhome-details, caravan-details,
             truck-details, farm-machinery-details, plant-machinery-details
             for the others; any other first segment is a 404)
  * dealer   10036877 / 9994   3 to 9 digits
             https://www.autotrader.co.uk/dealers/<county>/<town>/<name>-<id>
             https://www.autotrader.co.uk/retailer/<id>
"""
import re
from urllib.parse import parse_qs, urlparse

SITE = "https://www.autotrader.co.uk"
DEFAULT_POSTCODE = "SW1A 1AA"       # the site wants a postcode on every search; central London when the caller gives none

ADVERT_ID_RE = re.compile(r"^[0-9]{12,17}$")
DEALER_ID_RE = re.compile(r"^[0-9]{3,9}$")
_ADVERT_LINK_RE = re.compile(
    r"/(?:[a-z-]+-details|car-ssr|(?:spa/)?classified/advert(?:/new)?)/([0-9]{12,17})(?:[/?#]|$)", re.I)
_DEALER_LINK_RE = re.compile(r"/(?:dealers|retailer)/(?:[^?#]*?[-/])?([0-9]{3,9})/?$", re.I)

# Public vehicle type -> what the site calls it in each place.
#   channel      the search channel
#   details      the first segment of a listing link
#   category     Advert.specification.vehicleCategory
#   advertising  the advertising location a listing lookup names
#   dealers      the dealer search's retailer types (used, then new where the site sells new ones)
VEHICLE_TYPES = {
    "cars":       {"channel": "cars", "details": "car-details", "category": "Car",
                   "advertising": "at_cars", "dealers": ["used_car", "new_car"]},
    "vans":       {"channel": "vans", "details": "van-details", "category": "Van",
                   "advertising": "at_vans", "dealers": ["used_van"]},
    "bikes":      {"channel": "bikes", "details": "bike-details", "category": "Bike",
                   "advertising": "at_bikes", "dealers": ["used_bike", "new_bike"]},
    "motorhomes": {"channel": "motorhomes", "details": "motorhome-details", "category": "Motorhome",
                   "advertising": "at_motorhomes", "dealers": ["used_motorhome"]},
    "caravans":   {"channel": "caravans", "details": "caravan-details", "category": "Caravan",
                   "advertising": "at_caravans", "dealers": ["used_caravan"]},
    "trucks":     {"channel": "trucks", "details": "truck-details", "category": "Truck",
                   "advertising": "at_trucks", "dealers": ["used_truck"]},
    "farm":       {"channel": "farm", "details": "farm-machinery-details", "category": "Agricultural",
                   "advertising": "at_farm", "dealers": ["used_farm"]},
    "plant":      {"channel": "plant", "details": "plant-machinery-details", "category": "Plant",
                   "advertising": "at_plants", "dealers": ["used_plant"]},
}
DEFAULT_VEHICLE_TYPE = "cars"
_TYPE_OF_CATEGORY = {row["category"].lower(): name for name, row in VEHICLE_TYPES.items()}
_TYPE_OF_DETAILS = {row["details"]: name for name, row in VEHICLE_TYPES.items()}

# Public sort -> SearchResultsSort.
SORTS = {
    "relevance": "relevance",
    "price_low_to_high": "price_asc",
    "price_high_to_low": "price_desc",
    "distance": "distance",
    "mileage_low_to_high": "mileage",
    "year_new_to_old": "year_dsc",
    "year_old_to_new": "year_asc",
    "most_recent": "most_recent",
}
# Public sort -> DealerSort.
DEALER_SORTS = {"distance": "DISTANCE", "name": "NAME", "reviews": "WITH_RETAILER_REVIEWS"}
# Public sort -> the review list's sortOrder.
REVIEW_SORTS = {"newest": "newest-first", "oldest": "oldest-first", "invited_first": "verified-first"}
# Public period -> the review list's returnReviewsFrom.
REVIEW_PERIODS = {"all": "forever", "last_12_months": "last-year", "last_6_months": "last-6-months"}

SELLER_TYPES = {"dealer": "trade", "trade": "trade", "private": "private"}
WRITE_OFFS = {"exclude": "exclude", "only": "only"}
INSURANCE_GROUPS = {"3": "03U", "5": "05U", "10": "10U", "20": "20U", "30": "30U", "40": "40U", "50": "50U"}

# Search param -> the site's filter name (FilterName). The value goes as it
# is, except where api.py maps it (the choice tables above) or spells it the
# way the site does (SPELLED: those values are case-sensitive on the site).
SEARCH_FILTERS = {
    "radius": "distance",
    "make": "make",
    "model": "model",
    "trim": "aggregated_trim",
    "keywords": "keywords",
    "min_price": "min_price",
    "max_price": "max_price",
    "min_year": "min_year_manufactured",
    "max_year": "max_year_manufactured",
    "min_mileage": "min_mileage",
    "max_mileage": "max_mileage",
    "body_type": "body_type",
    "fuel_type": "fuel_type",
    "transmission": "transmission",
    "colour": "colour",
    "doors": "doors_values",
    "seats": "seats_values",
    "drivetrain": "drivetrain",
    "seller_type": "seller_type",
    "min_engine_size": "min_engine_size",
    "max_engine_size": "max_engine_size",
    "max_insurance_group": "insurance_group",
    "write_offs": "is_writeoff",
    "manufacturer_approved": "is_manufacturer_approved",
}
# "Petrol" matches, "petrol" finds nothing: these are looked up in the
# site's own option list (cars/filters) and sent the way it spells them.
SPELLED = ("body_type", "fuel_type", "transmission", "colour", "drivetrain")
# The site's filter name -> the search param that takes its values (what
# cars/filters calls a filter). A filter the search has no param for keeps
# the site's name.
PARAM_OF_FILTER = {site: param for param, site in SEARCH_FILTERS.items()}
# Filters the site lists that are not value lists a caller can pick from.
HIDDEN_FILTERS = ("postcode", "price_search_type", "keywords")


def _as_url(value):
    """A pasted autotrader.co.uk link as an absolute URL, or None when the
    value is not a link. A link to another site is a ValueError."""
    low = value.lower()         # a pasted link may be upper-cased
    if not low.startswith(("http://", "https://", "//", "www.", "autotrader.co.uk")):
        return None
    url = value
    if low.startswith("//"):
        url = "https:" + url
    elif not low.startswith(("http://", "https://")):
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    if not (host == "autotrader.co.uk" or host.endswith(".autotrader.co.uk")):
        raise ValueError("Must be an autotrader.co.uk link.")
    return url


def resolve_car(value):
    """Bare advert id or an autotrader.co.uk listing link -> the advert id."""
    value = (value or "").strip()
    if ADVERT_ID_RE.match(value):
        return value
    url = _as_url(value)
    if url:
        match = _ADVERT_LINK_RE.search(urlparse(url).path)
        if match:
            return match.group(1)
        raise ValueError("Must be an autotrader.co.uk listing link "
                         "(https://www.autotrader.co.uk/car-details/<id>).")
    raise ValueError("Must be an advert id (202611177994981) or an autotrader.co.uk listing link.")


def resolve_dealer(value):
    """Bare dealer id or an autotrader.co.uk dealer link -> the dealer id."""
    value = (value or "").strip()
    if DEALER_ID_RE.match(value):
        return value
    url = _as_url(value)
    if url:
        parsed = urlparse(url)
        match = _DEALER_LINK_RE.search(parsed.path)
        if match:
            return match.group(1)
        for name in ("retailerId", "retailer_id", "dealer"):      # a dealer's stock page names it in the query
            for candidate in parse_qs(parsed.query).get(name, []):
                if DEALER_ID_RE.match(candidate):
                    return candidate
        raise ValueError("Must be an autotrader.co.uk dealer link "
                         "(https://www.autotrader.co.uk/dealers/<county>/<town>/<name>-<id>).")
    raise ValueError("Must be a dealer id (10036877) or an autotrader.co.uk dealer link.")


def vehicle_type_of(category=None, link=None):
    """The public vehicle type of an advert: from the site's category
    ("Car", "Agricultural") or from a listing link's first segment. None
    when neither names a known one."""
    if isinstance(category, str) and category.strip().lower() in _TYPE_OF_CATEGORY:
        return _TYPE_OF_CATEGORY[category.strip().lower()]
    if isinstance(link, str):
        segment = urlparse(link).path.strip("/").split("/")[0].lower()
        return _TYPE_OF_DETAILS.get(segment)
    return None


def car_path(advert_id, vehicle_type):
    """The path of a listing page, or None for an unknown vehicle type."""
    row = VEHICLE_TYPES.get(vehicle_type)
    return f"/{row['details']}/{advert_id}" if row and advert_id else None


def car_link(advert_id, vehicle_type):
    path = car_path(advert_id, vehicle_type or DEFAULT_VEHICLE_TYPE)
    return SITE + path if path else None


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def dealer_link(dealer_id, name=None, town=None, county=None):
    """The dealer's page: the site's own /dealers/<county>/<town>/<name>-<id>
    when the three names are known, else the short /retailer/<id> (the site
    reads the id off the end of either)."""
    if not dealer_id:
        return None
    parts = [_slug(county), _slug(town), _slug(name)]
    if all(parts):
        return f"{SITE}/dealers/{parts[0]}/{parts[1]}/{parts[2]}-{dealer_id}"
    return f"{SITE}/retailer/{dealer_id}"


def absolute(link):
    """A site-relative link -> absolute, without its query; anything else unchanged."""
    if isinstance(link, str) and link.startswith("/"):
        return SITE + link.split("?")[0]
    return link or None
