"""Raw Autotrader UK answers -> clean records. Pure functions, no I/O; every
one tolerates a missing or partial answer.

Fixtures (autotrader_uk/fixtures/, all captured 2026-11-19 with
fetch.py's own calls, postcode SW1A 1AA):

  search_cars_bmw_3_series.json   searchResults, cars, make BMW + model 3 Series, page 1
  search_vans.json                searchResults, vans, make Ford (prices carry a VAT note)
  search_monthly.json             searchResults, cars, make BMW, monthly prices (cards with a finance quote)
  search_dealer_stock.json        searchResults, cars, the stock of dealer 10036877
  search_no_results.json          searchResults for a make the site does not know
  facets_cars.json                every car filter with options and counts
  facets_cars_bmw.json            the same with make BMW (its models)
  facets_vans.json                every van filter
  advert_dealer.json              a dealer's car: the six advert operations
  advert_private.json             a private seller's car (sparse: no dealer, no price rating)
  advert_electric.json            an electric car (battery, condition scores, video)
  advert_van.json                 a van (VAT status, cab type, wheelbase)
  listing_page_dealer.html        the listing page of advert_dealer.json's car
  listing_page_private.html       the listing page of advert_private.json's car
  dealers_search_manchester.json  dealerList, within 10 miles of M1 1AE
  dealers_search_empty.json       dealerList of a name nobody has
  dealer_9994.json                a franchise dealer: Dealer + page profile + review summary
  dealer_10036877.json            an independent dealer (no opening hours, appointment only)
  reviews_10036877.json           a dealer's reviews, page 1
  reviews_10036877_page_2.json    page 2 of the same
  reviews_none.json               a review search that matches nothing
  error_*.json                    the site's error answers, read by fetch.py (test_api.py): an
                                  unknown advert, an unknown dealer, a postcode it does not
                                  know (both schemas), a field the schema does not have

What a missing record looks like is the transport's business (the site
answers an unknown advert or dealer with a NOT_FOUND error, fetch.py raises
on it), so no parser here ever sees a not-found body; a listing page the
site no longer serves arrives as None.
"""
import json
import re

from scraper_utils.dates import iso_datetime, iso_day
from scraper_utils.text import clean, lower_snake, multiline, text_list
from scraper_utils.values import amount, as_dict, as_list, bool_or_none, count, first_float, first_int, g, number
from scraper_utils.values import rating as star_rating
from autotrader_uk import refs

CURRENCY = "GBP"
_HYDRATION_RE = re.compile(r'window\.__staticRouterHydrationData = JSON\.parse\(\s*(?=")')
_PLATE_RE = re.compile(r"\((\w+) reg\)", re.I)
_TOWN_DISTANCE_RE = re.compile(r"^(.*?)\s*\((\d+)\s*miles?\)\s*$", re.I)
_VAT_NOTES = (("inc vat", "inc_vat"), ("no vat", "no_vat"), ("+vat", "plus_vat"), ("plus vat", "plus_vat"),
              ("ex vat", "plus_vat"))
_UNITS = {"mile": "miles", "miles": "miles", "km": "km", "kilometre": "km", "hour": "hours", "hours": "hours"}


# ---- small readers -------------------------------------------------------------------

def money(value):
    """'£5,252' / '£21,222.92' / 5495 -> 5252 / 21222.92 / 5495; None for text without a number."""
    return amount(first_float(value))


def image(link):
    """An image link at full size: the site writes a `{resize}` segment where a size goes."""
    link = clean(link)
    return link.replace("/{resize}/", "/").replace("/%7Bresize%7D/", "/") if link else None


def _lower(value):
    """A site code as a public value: 'PHYSICAL-STOCK' / 'INC_VAT' -> 'physical_stock' / 'inc_vat'."""
    value = clean(value)
    return lower_snake(value) if value else None


def _price_rating(value):
    """GREAT / GOOD / FAIR / LOW / HIGH -> lower case; NOANALYSIS (no rating) -> None."""
    value = _lower(value)
    return None if value in (None, "noanalysis", "no_analysis") else value


def _seller_type(value):
    value = _lower(value)
    return {"trade": "dealer", "dealer": "dealer", "private": "private"}.get(value, value)


def _coordinates(lat_long):
    """'53.02,-2.23' -> {latitude, longitude}; None for anything else."""
    parts = lat_long.split(",") if isinstance(lat_long, str) else []
    latitude, longitude = (number(parts[0]), number(parts[1])) if len(parts) == 2 else (None, None)
    if latitude is None or longitude is None:
        return None
    return {"latitude": latitude, "longitude": longitude}


def _enabled(capability):
    """{enabled: true} -> True; a capability the site leaves null -> None."""
    return bool_or_none(g(capability, "enabled"))


def _measure(node, key):
    """{height: 1441, unit: 'mm'} -> 1441 (the site quotes mm and kg)."""
    return number(g(node, key))


def _quantity(text):
    """The number in a spec text: '57.00 litres' -> 57, '61.40 mpg' -> 61.4; None without one."""
    return number(first_float(text))


def _typed(text):
    """A finance figure as the site prints it -> a number where it is one:
    '£422' -> 422, '10.9%' -> 10.9, '48 months' -> 48, '20,000 miles' -> 20000;
    other text ('HP') stays text."""
    text = clean(text)
    if text is None:
        return None
    if text.startswith("£") or text.endswith("%") or re.fullmatch(r"[\d,.]+\s*(months?|miles?|years?)", text, re.I):
        return amount(first_float(text))
    return text


# ---- search --------------------------------------------------------------------------

def _vat_status(price_text):
    low = (clean(price_text) or "").lower()
    for note, status in _VAT_NOTES:
        if note in low:
            return status
    return None


def _card_finance(finance):
    """A search card's finance quote (monthly price searches only)."""
    if not isinstance(finance, dict):
        return None
    example = {}
    for row in as_list(finance.get("representativeValues")):
        key = lower_snake(g(row, "financeKey"))
        if key:
            example[key] = _typed(g(row, "financeValue"))
    return {
        "type": clean(finance.get("quoteSubType")),
        "monthly_payment": money(g(finance, "monthlyPrice", "priceFormattedAndRounded")),
        "deposit": money(g(finance, "rawFinanceFields", "deposit")) if g(finance, "rawFinanceFields", "deposit")
        else money(finance.get("initialPayment")),
        "term_months": first_int(g(finance, "rawFinanceFields", "term") or finance.get("termMonths")),
        "annual_mileage": first_int(g(finance, "rawFinanceFields", "annualMileage")),
        "currency": CURRENCY,
        "provider": _lower(finance.get("financeListingProvider")),
        "representative_example": example or None,
    }


def parse_card(card, vehicle_type=None):
    """One SearchListing -> a result row; None for anything that is not an advert."""
    if not isinstance(card, dict) or not card.get("advertId"):
        return None
    context = as_dict(g(card, "trackingContext", "advertContext"))
    features = as_dict(g(card, "trackingContext", "advertCardFeatures"))
    badges = {clean(g(b, "type")): clean(g(b, "displayText")) for b in as_list(card.get("badges")) if g(b, "type")}
    vehicle_type = refs.vehicle_type_of(context.get("vehicleCategory")) or vehicle_type
    year_text = badges.get("REGISTERED_YEAR") or ""
    plate = _PLATE_RE.search(year_text)
    mileage_text = clean(card.get("mileageText")) or badges.get("MILEAGE")
    unit = (mileage_text or "").split(" ")[-1].lower()
    place = clean(card.get("vehicleLocation")) or ""
    town = _TOWN_DISTANCE_RE.match(place)
    dealer_id = clean(context.get("advertiserId")) or clean(g(card, "trackingContext", "retailerContext", "id"))
    if dealer_id in ("null", "None"):
        dealer_id = None
    seller_type = _seller_type(card.get("sellerType") or context.get("advertiserType"))
    return {
        "id": card["advertId"],
        "link": refs.car_link(card["advertId"], vehicle_type),
        "title": clean(card.get("title")),
        "subtitle": clean(card.get("subTitle")),
        "attention_grabber": clean(card.get("attentionGrabber")),
        "make": clean(context.get("make")),
        "model": clean(context.get("model")),
        "year": number(context.get("year")) or first_int(year_text),
        "plate": plate.group(1) if plate else None,
        "condition": _lower(card.get("condition") or context.get("condition") or features.get("condition")),
        "mileage": first_int(mileage_text),
        "mileage_unit": _UNITS.get(unit) if mileage_text else None,
        "price": {
            "amount": number(context.get("price")) if context.get("price") is not None else money(card.get("price")),
            "currency": CURRENCY,
            "vat_status": _vat_status(card.get("formattedPrice") or card.get("price")),
            "rrp": money(card.get("rrp")),
            "discount": money(card.get("discount")),
            "rating": _price_rating(features.get("priceIndicator")),
        },
        "is_pre_registered": bool_or_none(card.get("preReg")),
        "is_manufacturer_approved": bool_or_none(features.get("isManufacturedApproved")),
        "is_franchise_approved": bool_or_none(features.get("isFranchiseApproved")),
        "has_finance": bool_or_none(features.get("hasFinance")),
        "can_reserve_online": bool_or_none(card.get("hasDigitalRetailing")),
        "image_count": count(card.get("numberOfImages")) if card.get("numberOfImages") is not None else None,
        "images": [link for link in (image(i) for i in as_list(card.get("images"))) if link],
        "location": {
            "town": clean(town.group(1)) if town else (place or None),
            "distance_miles": number(g(card, "trackingContext", "distance", "distance")),
            "type": _lower(card.get("locationType")),
        },
        "seller": {
            "type": seller_type,
            "id": dealer_id if seller_type == "dealer" else None,
            "link": refs.absolute(card.get("dealerLink")),
            "rating": star_rating(card.get("overallReviewRating") or g(card, "dealerReview", "overallReviewRating")),
        },
        "finance": _card_finance(card.get("finance")),
    }


def parse_search(raw, vehicle_type=None):
    """A raw `searchResults` object -> {results, total_count, site_total_pages}."""
    rows, seen = [], set()
    for card in as_list(g(raw, "listings")):
        row = parse_card(card, vehicle_type)
        if row and row["id"] not in seen:
            seen.add(row["id"])
            rows.append(row)
    return {
        "results": rows,
        "total_count": count(g(raw, "page", "results", "count")) if g(raw, "page", "results", "count") is not None
        else None,
        "site_total_pages": number(g(raw, "page", "count")),
    }


# ---- filters -------------------------------------------------------------------------

def _option_value(site_filter, value):
    """A filter option's value, as cars/search takes it."""
    value = clean(str(value)) if value is not None else None
    if site_filter == "insurance_group" and value:
        return value.rstrip("Uu").lstrip("0") or value      # "03U" -> "3", what max_insurance_group takes
    if site_filter == "seller_type" and value == "trade":
        return "dealer"
    return value


def parse_facets(raw):
    """A raw `searchResults` object asked with facets -> {total_count,
    filters: [{name, site_name, options: [{label, value, count}]}]}. `name`
    is the cars/search param that takes the values (the site's own name
    where the search has no such param)."""
    filters = []
    for facet in as_list(g(raw, "facets")):
        for item in as_list(g(facet, "filters")):
            site_name = clean(g(item, "filter"))
            if not site_name or site_name in refs.HIDDEN_FILTERS:
                continue
            options = []
            for option in as_list(g(item, "options")):
                value = _option_value(site_name, g(option, "value"))
                if value is None:
                    continue
                options.append({
                    "label": clean(g(option, "label")) or value,
                    "value": value,
                    "count": count(g(option, "count")) if g(option, "count") is not None else None,
                })
            filters.append({
                "name": refs.PARAM_OF_FILTER.get(site_name, site_name),
                "site_name": site_name,
                "is_search_param": site_name in refs.PARAM_OF_FILTER,
                "options": options,
            })
    return {
        "total_count": count(g(raw, "page", "results", "count")) if g(raw, "page", "results", "count") is not None
        else None,
        "filters": filters,
    }


def facet_values(raw, site_filter):
    """The raw option values of one filter (the site's own spelling), in the site's order."""
    for facet in as_list(g(raw, "facets")):
        for item in as_list(g(facet, "filters")):
            if g(item, "filter") == site_filter:
                return [str(g(o, "value")) for o in as_list(g(item, "options")) if g(o, "value") is not None]
    return []


def named_counts(facets, name):
    """parse_facets()'s options of one filter -> [{name, count}] (makes, models)."""
    for item in as_list(g(facets, "filters")):
        if g(item, "site_name") == name:
            return [{"name": o["value"], "count": o["count"]} for o in as_list(item.get("options"))]
    return []


# ---- the listing page ----------------------------------------------------------------

def parse_listing_page(html):
    """The listing page -> the record its hydration JSON holds for the
    listing route ({advert, vehicleData, financeData, ...}); None when the
    page carries none (no page, or not a listing page)."""
    html = html or ""
    match = _HYDRATION_RE.search(html)
    if not match:
        return None
    try:
        # The argument is one JSON string, read to its own closing quote: a
        # seller's text may hold `");` itself, which is not the end of it.
        text, _ = json.JSONDecoder().raw_decode(html, match.end())
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    for record in as_dict(g(data, "loaderData")).values():
        if isinstance(record, dict) and isinstance(record.get("advert"), dict):
            return record
    return None


_APR_RE = re.compile(r"Representative APR\s*([\d.]+)\s*%", re.I)
_PAYABLE_RE = re.compile(r"Total amount payable\s*(£[\d,.]+)", re.I)


def _page_quotes(page):
    """The finance quotes the listing page shows (one per finance type), with
    the representative example as the site words it."""
    quotes = []
    for quote in as_list(g(page, "financeData", "financeData", "quotes")):
        if not isinstance(quote, dict):
            continue
        example = clean(quote.get("representativeExampleMessage")) or ""
        apr, payable = _APR_RE.search(example), _PAYABLE_RE.search(example)
        quotes.append({
            "type": clean(quote.get("quoteType")),
            "monthly_payment": money(quote.get("monthlyPrice")),
            "deposit": money(quote.get("cashDeposit")),
            "term_months": first_int(quote.get("term")),
            "annual_mileage": first_int(quote.get("annualMileage")),
            "apr": first_float(apr.group(1)) if apr else None,
            "vehicle_price": money(quote.get("vehiclePrice")),
            "total_credit": money(quote.get("totalCredit")),
            "total_amount_payable": money(payable.group(1)) if payable else None,
            "provider": clean(quote.get("financeProvider")),
            "representative_example": example or None,
        })
    return quotes


def _page_highlights(page):
    out = []
    for item in as_list(g(page, "vehicleData", "highlights")):
        title = clean(g(item, "title"))
        if not title:
            continue
        out.append({
            "title": title,
            "badge": clean(g(item, "badge")),
            "details": [text for text in (multiline(d) for d in as_list(g(item, "descriptionList"))) if text],
        })
    return out


def _page_hours(page):
    out = []
    for row in as_list(g(page, "advert", "seller", "location", "openingHours")):
        day = clean(g(row, "dayOfWeek"))
        if day:
            out.append({"day": day, "hours": clean(g(row, "hours"))})
    return out


def _page_image_tags(page):
    """{image link: (label, category)} from the page's gallery."""
    tags = {}
    for item in as_list(g(page, "advert", "gallery", "images")):
        link = image(g(item, "url"))
        tag = g(item, "classificationTags", 0)
        if link and isinstance(tag, dict):
            tags[link] = (clean(tag.get("label")), clean(tag.get("category")))
    return tags


def _page_check_labels(page):
    """{check key: the sentence the page prints for it}."""
    labels = {}
    for group in ("basicChecks", "detailChecks"):
        for item in as_list(g(page, "advert", "history", "vehicleCheck", group)):
            key, label = clean(g(item, "id")), clean(g(item, "label"))
            if key and label:
                labels[key] = label
    return labels


# ---- one listing ---------------------------------------------------------------------

def _features(items):
    return text_list([g(item, "description") for item in as_list(items)])


def _history_check(summary, status, labels):
    """The history check the page shows (which checks passed). The report
    itself, with the full number plate and VIN, is never asked for."""
    if not isinstance(summary, dict) and not status:
        return None
    summary = as_dict(summary)
    checks = []
    for item in as_list(summary.get("checks")):
        key = clean(g(item, "key"))
        if not key:
            continue
        failed, warning = bool_or_none(g(item, "failed")), bool_or_none(g(item, "warning"))
        checks.append({
            "check": key.lower(),
            "label": labels.get(key) or labels.get(key.replace("_CATEGORY", "")) or clean(g(item, "name")),
            "is_failed": failed,
            "is_warning": warning,
        })
    return {
        "status": _lower(status),
        "type": _lower(summary.get("type")),
        "summary": clean(summary.get("title")),
        "performed_date": iso_day(summary.get("performed")),
        "passed_count": number(summary.get("passes")),
        "failed_count": number(summary.get("fails")),
        "warning_count": number(summary.get("warnings")),
        "write_off_category": clean(summary.get("writeOffCategory")),
        "checks": checks,
    }


def _address(location):
    location = as_dict(location)
    return {
        "line_1": clean(location.get("addressOne")),
        "line_2": clean(location.get("addressTwo")),
        "town": clean(location.get("town")),
        "county": clean(location.get("county")),
        "region": clean(location.get("region")),
        "postcode": clean(location.get("postcode")),
        "country": clean(location.get("country")),
    }


def _awards(items):
    return [{"year": first_int(g(a, "year")), "category": _lower(g(a, "category"))}
            for a in as_list(items) if g(a, "category")]


def _advert_seller(seller, page):
    seller = as_dict(seller)
    dealer = seller.get("dealer") if isinstance(seller.get("dealer"), dict) else None
    private = as_dict(seller.get("privateAdvertiser"))
    contact = as_dict(seller.get("sellerContact"))
    seller_type = _seller_type(seller.get("sellerType"))
    dealer_id = clean(g(dealer, "dealerId")) or clean(seller.get("retailerId"))
    location = as_dict(g(dealer, "location"))
    if dealer:
        address = _address(location)
    else:
        # A private seller: the page names the town only, and so does this.
        place = as_dict(private.get("location"))
        address = _address({"town": place.get("town") or seller.get("sellerLocation"),
                            "county": place.get("county") or seller.get("sellerLocationCounty"),
                            "region": place.get("region"), "country": place.get("country")})
    protected = contact.get("protectedNumber")
    if protected is None:
        protected = g(dealer, "media", "protectedNumber") if dealer else g(private, "contact", "protectedNumber")
    return {
        "type": seller_type,
        "id": dealer_id if dealer or seller_type == "dealer" else None,
        "name": clean(g(dealer, "name")) or clean(seller.get("sellerName")),
        "link": refs.dealer_link(dealer_id, g(dealer, "name"), location.get("town"), location.get("county"))
        if dealer_id and seller_type == "dealer" else None,
        "legal_name": clean(g(dealer, "legalName")),
        "segment": _lower(seller.get("advertiserSegment")),
        "strapline": clean(g(dealer, "marketing", "strapline")),
        "description": multiline(g(dealer, "description")) or multiline(g(page, "advert", "seller", "branding",
                                                                           "description")),
        "website": clean(g(dealer, "media", "dealerWebsite", "href")),
        "email": clean(g(dealer, "media", "emailAddress")),
        "accepts_email": bool_or_none(contact.get("byEmail")),
        "phone": clean(contact.get("phoneNumberOne")) or clean(g(dealer, "media", "phoneNumber1")),
        "phone_2": clean(contact.get("phoneNumberTwo")) or clean(g(dealer, "media", "phoneNumber2")),
        "is_phone_protected": bool_or_none(protected),
        "assigned_phone": clean(g(dealer, "assignedNumber", "number")),
        "address": address,
        "coordinates": _coordinates(location.get("latLong")),
        "distance_miles": number(g(seller, "sellerLocationDistance", "value")),
        "rating": star_rating(g(seller, "sellerRating", "rating") or g(dealer, "reviews", "overallReviewRating")),
        "review_count": first_int(g(seller, "sellerRating", "reviewCount") or g(dealer, "reviews", "numberOfReviews")),
        "awards": _awards(g(dealer, "atAwards")),
        "fca_number": clean(g(dealer, "fca")),
        "fca_permission": clean(g(dealer, "fcaPermission")),
        "franchises": text_list(as_list(g(dealer, "servicesOffered", "franchises"))),
        "has_profile_page": bool_or_none(g(dealer, "servicesOffered", "hasDealerProfilePage")),
        "has_video_walk_around": bool_or_none(g(dealer, "servicesOffered", "hasVideoWalkAround")),
        "has_home_delivery": bool_or_none(g(dealer, "servicesOffered", "hasHomeDelivery")),
        "has_click_and_collect": bool_or_none(g(dealer, "servicesOffered", "hasClickAndCollect")),
        "has_safe_selling": bool_or_none(g(dealer, "servicesOffered", "hasSafeSelling")),
        "area": ({"code": clean(g(seller, "locationArea", "code")), "region": clean(g(seller, "locationArea", "region"))}
                 if isinstance(seller.get("locationArea"), dict) else None),
        "opening_hours": _page_hours(page),
    }


def _electric(spec):
    battery = g(spec, "battery")
    total_range = number(g(spec, "range", "totalRange"))
    if not isinstance(battery, dict) and total_range is None:
        return None
    return {
        "battery_capacity_kwh": number(g(battery, "capacity", "capacity")),
        "usable_battery_capacity_kwh": number(g(battery, "usableCapacity", "capacity")),
        "battery_range_miles": number(g(battery, "range", "range")),
        "total_range_miles": total_range,
        "charge_time": clean(g(battery, "charging", "chargeTime")),
        "quick_charge_time": clean(g(battery, "charging", "quickChargeTime")),
    }


def _none_if_unlisted(value):
    value = clean(value)
    return None if value in (None, "Unlisted") else value


def _positive(value):
    """A count the site stores as 0 when it does not apply (berths of a car)."""
    value = number(value)
    return value if value else None


def parse_advert(parts, page=None, postcode=None):
    """The advert operations of one listing ({operation: raw advert}), with
    what the listing page adds (parse_listing_page's record, or None) -> the
    public record."""
    parts = as_dict(parts)
    core, seller = as_dict(parts.get("AdvertCore")), as_dict(parts.get("AdvertSeller"))
    media = as_dict(parts.get("AdvertMedia"))
    spec = as_dict(g(parts, "AdvertSpec", "specification"))
    size = as_dict(g(parts, "AdvertSize", "specification"))
    tech = as_dict(size.get("techData"))
    extras = as_dict(g(parts, "AdvertFeatures", "specification"))
    page = as_dict(page)
    advert_id = clean(core.get("id")) or clean(seller.get("id")) or clean(media.get("id"))
    vehicle_type = refs.vehicle_type_of(spec.get("vehicleCategory"))
    market = as_dict(g(page, "advert", "details", "pricing", "marketPriceRating"))
    tags = _page_image_tags(page)
    images = []
    for item in as_list(g(media, "imageList", "images")):
        link = image(g(item, "url"))
        if link:
            label, category = tags.get(link, (None, None))
            images.append({"link": link, "label": label or clean(g(item, "angle")), "category": category})
    delivery = as_dict(g(page, "advert", "deliveryAndCollection", "homeDelivery"))
    regions = text_list(as_list(core.get("homeDeliveryRegionCodes")))
    seller_features = text_list(as_list(core.get("combinedFeatures")))
    collection = [{
        "id": clean(g(place, "id")),
        "dealer_id": clean(g(place, "dealerId")),
        "name": clean(g(place, "name")),
        "distance_miles": number(g(place, "distance")),
        "coordinates": ({"latitude": number(g(place, "geoCoordinate", "latitude")),
                         "longitude": number(g(place, "geoCoordinate", "longitude"))}
                        if g(place, "geoCoordinate", "latitude") is not None else None),
    } for place in as_list(g(media, "collectionLocations", "locations")) if isinstance(place, dict)]
    quote = as_dict(media.get("finance"))
    review = as_dict(g(media, "generation", "review", "expertReviewSummary"))
    condition_scores = as_dict(core.get("vehicleCondition"))
    return {
        "id": advert_id,
        "link": refs.car_link(advert_id, vehicle_type),
        "vehicle_type": vehicle_type,
        "title": clean(core.get("title")),
        "subtitle": clean(g(core, "heading", "subtitle")),
        "summary": clean(core.get("summary")),
        "attention_grabber": clean(core.get("attentionGrabber")),
        "description": multiline(core.get("description")),
        "seller_note": multiline(core.get("advertText")),
        "condition": _lower(core.get("condition")),
        "stock_type": _lower(core.get("stockType")),
        "status": _lower(core.get("tradeLifecycleStatus")),
        "stock_label": clean(g(page, "advert", "details", "stockLabel")),
        "make": clean(spec.get("make")) or clean(g(media, "generation", "make")),
        "model": clean(spec.get("model")) or clean(g(media, "generation", "model")),
        "trim": clean(spec.get("trim")),
        "derivative": clean(spec.get("derivative")),
        "generation": {
            "id": clean(g(media, "generation", "generationId")) or clean(spec.get("generationId")),
            "name": clean(g(media, "generation", "name")),
        },
        "body_type": clean(spec.get("bodyType")) or clean(spec.get("rawBodyType"))
        or clean(g(media, "generation", "bodyType")) or clean(tech.get("bodyStyle")),
        "fuel_type": clean(spec.get("fuel")) or clean(tech.get("fuelType")),
        "transmission": clean(spec.get("transmission")),
        "drivetrain": clean(spec.get("driveTrain")) or clean(spec.get("oemDrivetrain")),
        "colour": clean(core.get("colour")),
        "doors": _positive(spec.get("doors")) or _positive(first_int(tech.get("numberOfDoors"))),
        "seats": _positive(spec.get("seats")) or _positive(first_int(tech.get("numberOfSeats"))),
        "year": number(core.get("year")),
        "plate": clean(core.get("plate")),
        "registration_date": iso_day(core.get("dateOfRegistration")),
        "mileage": number(g(core, "mileage", "mileage")),
        "mileage_unit": _UNITS.get((clean(g(core, "mileage", "unit")) or "").lower()),
        "mileage_vs_average": number(core.get("mileageDeviation")),
        "hours_used": number(core.get("hoursUsed")),
        "owners": number(core.get("owners")),
        "keys": number(core.get("keys")),
        "service_history": clean(core.get("serviceHistory")),
        "last_service": {
            "date": iso_day(core.get("lastServiceDate")),
            "mileage": number(core.get("lastServiceOdometerReadingMiles")),
        },
        "mot_expiry_date": iso_day(core.get("motExpiry")),
        "has_12_months_mot_included": bool_or_none(core.get("twelveMonthsMotIncluded")),
        "mot_insurance": clean(core.get("motInsurance")) if not isinstance(core.get("motInsurance"), bool)
        else core.get("motInsurance"),
        "warranty_months": number(core.get("warrantyMonthsOnPurchase")),
        "price": {
            "amount": number(core.get("price")),
            "currency": CURRENCY,
            "excluding_fees": number(core.get("priceExcludingFees")),
            "vat_status": _lower(core.get("vatStatus")),
            "is_plus_vat": bool_or_none(core.get("plusVatIndicated")),
            "is_on_application": bool_or_none(core.get("priceOnApplication")),
            "rrp": number(core.get("rrp")),
            "saving": number(core.get("saving")),
            "admin_fee": number(core.get("adminFee")),
            "has_no_admin_fees": bool_or_none(core.get("noAdminFees")),
            "admin_fee_note": clean(core.get("adminFeeInfoDescription")),
            "due_at_seller": number(core.get("dueAtSeller")),
            "rating": _price_rating(core.get("priceIndicatorRating")),
            "rating_label": clean(core.get("priceIndicatorRatingLabel"))
            if _price_rating(core.get("priceIndicatorRating")) else None,
            "vs_market_average": number(core.get("priceDeviation")),
            "market_comparison": clean(market.get("comparisonDescription")),
            "good_price_range": {
                "low": money(market.get("goodPriceLowerBound")),
                "high": money(market.get("goodPriceUpperBound")),
            },
            "value_rating": clean(core.get("valueRating")),
        },
        "is_pre_registered": bool_or_none(core.get("preReg")),
        "is_auction": bool_or_none(core.get("isAuction")),
        "is_premium_listing": bool_or_none(core.get("isPremium")),
        "is_manufacturer_approved": bool_or_none(core.get("manufacturerApproved")),
        "is_ulez_compliant": bool_or_none(spec.get("ulezCompliant")),
        "is_part_exchange_available": bool_or_none(core.get("isPartExAvailable")),
        "are_previous_owners_hidden": bool_or_none(core.get("excludePreviousOwners")),
        "write_off_category": clean(core.get("insuranceWriteOffCategory")),
        "engine": {
            "size_litres": number(g(spec, "engine", "sizeLitres")),
            "size_cc": number(g(spec, "engine", "sizeCC")),
            "badge_size_litres": number(g(spec, "engine", "manufacturerEngineSize")),
            "power": number(g(spec, "engine", "power", "enginePower")),
            "power_unit": _lower(g(spec, "engine", "power", "unit")),
            "torque_lb_ft": _quantity(tech.get("engineTorque")),
            "cylinders": _positive(first_int(tech.get("cylinders"))),
            "valves": _positive(first_int(tech.get("valves"))),
            "gears": number(spec.get("numberOfGears")),
            "gearbox": clean(tech.get("transmission")),
        },
        "performance": {
            "top_speed_mph": number(g(spec, "topSpeed", "topSpeed")) or _quantity(tech.get("topSpeed")),
            "zero_to_62_mph_seconds": _quantity(tech.get("zeroToSixtyTwoMph")),
            "zero_to_60_mph_seconds": _quantity(tech.get("zeroToSixtyMph")),
        },
        "running_costs": {
            "co2_emissions_g_km": number(g(spec, "co2Emissions", "co2Emission")),
            "emission_class": clean(spec.get("emissionClass")),
            "annual_tax": number(g(spec, "annualTax", "standardRate")),
            "insurance_group": clean(tech.get("insuranceGroup")),
            "fuel_consumption_mpg": {
                "combined": _quantity(tech.get("fuelConsumptionCombined")),
                "urban": _quantity(tech.get("fuelConsumptionUrban")),
                "extra_urban": _quantity(tech.get("fuelConsumptionExtraUrban")),
            },
            "fuel_tank_litres": _quantity(tech.get("fuelTankCapacity")),
        },
        "electric": _electric(spec),
        "dimensions": {
            "length_mm": _measure(size.get("exteriorLength"), "length") or _quantity(tech.get("vehicleLength")),
            "width_mm": _measure(size.get("exteriorWidth"), "width") or _quantity(tech.get("vehicleWidth")),
            "width_with_mirrors_mm": _quantity(tech.get("vehicleWidthInclMirrors")),
            "height_mm": _measure(size.get("exteriorHeight"), "height") or _quantity(tech.get("vehicleHeight")),
            "wheelbase_mm": _quantity(tech.get("wheelbase")),
            "kerb_weight_kg": _measure(size.get("minimumKerbWeight"), "weight")
            or _quantity(tech.get("minimumKerbWeight")),
            "gross_weight_kg": _measure(size.get("grossVehicleWeight"), "weight")
            or _quantity(tech.get("grossVehicleWeight")),
            "max_load_kg": _quantity(tech.get("maxLoadingWeight")),
            "boot_litres": _quantity(tech.get("bootspaceSeatsUp")),
            "boot_seats_down_litres": _quantity(tech.get("luggageCapacitySeatsDown")),
            "load_length_mm": _measure(size.get("capacityLength"), "length"),
            "load_width_mm": _measure(size.get("capacityWidth"), "width"),
            "load_height_mm": _measure(size.get("capacityHeight"), "height"),
            "load_weight_kg": _measure(size.get("capacityWeight"), "weight"),
            "lifting_capacity_kg": _measure(size.get("liftingCapacity"), "weight"),
            "max_reach_mm": _measure(size.get("maxReach"), "length"),
            "operating_width_mm": _measure(size.get("operatingWidth"), "width"),
        },
        "specification": {
            "style": clean(spec.get("style")),
            "sub_style": clean(spec.get("subStyle")),
            "wheelbase": clean(spec.get("wheelbase")),
            "cab_type": _none_if_unlisted(spec.get("cabType")),
            "driver_position": _none_if_unlisted(spec.get("driverPosition")),
            "axle_configuration": clean(spec.get("axleConfig")),
            "trailer_axles": number(spec.get("trailerAxleNumber")),
            "berths": _positive(spec.get("berth")),
            "bedrooms": _positive(spec.get("bedrooms")),
            "bedroom_layout": clean(spec.get("bedroomLayout")),
            "end_layout": clean(spec.get("endLayout")),
            "bike_licence_type": clean(spec.get("bikeLicenceType")),
            "brake_type": clean(spec.get("brakeType")),
            "frame_size_cm": number(spec.get("frameSizeCM")),
            "frame_style": clean(spec.get("frameStyle")),
            "motor_make": clean(spec.get("motorMake")),
            "operating_type": clean(spec.get("operatingType")),
            "interior": clean(spec.get("interiorDescription")),
            "is_crossover": bool_or_none(spec.get("isCrossover")),
        },
        "features": {
            "standard": _features(extras.get("standardFeatures")),
            "optional": _features(extras.get("optionalFeatures")),
            "seller_listed": seller_features
            + [x for x in text_list(as_list(g(page, "advert", "details", "advertiserProvidedExtras")))
               if x not in seller_features],
        },
        "highlights": _page_highlights(page),
        "condition_scores": ({
            "body": number(condition_scores.get("bodyCondition")),
            "interior": number(condition_scores.get("interiorCondition")),
            "tyres": number(condition_scores.get("tyreCondition")),
        } if condition_scores else None),
        "history_check": _history_check(media.get("vehicleCheckSummary"), core.get("vehicleCheckStatus"),
                                        _page_check_labels(page)),
        "expert_review": {
            "rating": number(review.get("rating")),
            "link": clean(review.get("reviewUrl")),
        },
        "image_count": number(g(media, "imageList", "size")) if g(media, "imageList", "size") is not None
        else len(images),
        "images": images,
        "video_link": clean(g(media, "video", "url")),
        "spin_link": clean(g(media, "spin", "url")),
        "seller": _advert_seller(seller, page),
        "delivery": {
            "postcode": clean(delivery.get("consumerPostcode")) or postcode,
            "is_home_delivery_available": bool_or_none(delivery.get("hasHomeDelivery")) if delivery
            else (bool(regions) if regions else bool_or_none(g(seller, "dealer", "servicesOffered", "hasHomeDelivery"))),
            "delivers_to_postcode": bool_or_none(delivery.get("deliversToMyPostcode")),
            "cost": clean(delivery.get("deliveryCostText")),
            "notes": text_list(as_list(g(delivery, "info", "overview")) + as_list(g(delivery, "info", "details"))),
            "region_codes": regions,
            "has_click_and_collect": bool_or_none(core.get("hasClickAndCollect")),
            "collection_locations": collection,
        },
        "finance": {
            "is_available": bool_or_none(core.get("isFinanceAvailable")),
            "has_finance_information": bool_or_none(core.get("hasFinanceInformation")),
            "has_full_application": bool_or_none(core.get("isFinanceFullApplicationAvailable")),
            "provider": _lower(core.get("financeProvider")),
            "currency": CURRENCY,
            "default_deposit": money(g(core, "financeDefaults", "depositAmount")),
            "default_term_months": first_int(g(core, "financeDefaults", "term")),
            "default_annual_mileage": first_int(g(core, "financeDefaults", "mileage")),
            "headline_quote": ({
                "type": clean(quote.get("quoteSubtype")) or clean(quote.get("quoteType")),
                "monthly_payment": number(quote.get("monthlyPayment")),
                "apr": number(quote.get("representativeApr")),
                "term_months": number(quote.get("duration")),
                "final_payment": number(quote.get("finalPayment")),
                "total_amount_payable": number(quote.get("totalAmountPayable")),
                "total_credit": number(quote.get("totalCredit")),
                "on_road_price": number(quote.get("onRoadPrice")),
            } if quote else None),
            "quotes": _page_quotes(page),
        },
        "reservation": {
            "can_reserve_online": _enabled(g(media, "capabilities", "digitalRetailing")),
            "has_seller_promise": _enabled(g(media, "capabilities", "sellerPromise")),
            "eligibility": _lower(g(media, "reservation", "eligibility")),
            "status": _lower(g(media, "reservation", "status")),
        },
    }


# ---- dealers -------------------------------------------------------------------------

_STOCK_KEYS = (("cars", "car"), ("vans", "van"), ("bikes", "bike"), ("motorhomes", "motorhome"),
               ("caravans", "caravan"), ("trucks", "truck"), ("plant", "plant"), ("farm", "agricultural"))


def _stock(counts, prefix=""):
    counts = as_dict(counts)
    return {name: number(counts.get(prefix + key)) for name, key in _STOCK_KEYS}


def parse_dealer_row(dealer):
    """One raw Dealer -> the fields every dealer answer shares; None for a row without an id."""
    if not isinstance(dealer, dict) or not dealer.get("dealerId"):
        return None
    location = as_dict(dealer.get("location"))
    services = as_dict(dealer.get("servicesOffered"))
    counts = g(dealer, "stockLevels", "atStockCounts")
    dealer_id = clean(str(dealer["dealerId"]))
    return {
        "id": dealer_id,
        "link": refs.dealer_link(dealer_id, dealer.get("name"), location.get("town"), location.get("county")),
        "name": clean(dealer.get("name")),
        "legal_name": clean(dealer.get("legalName")),
        "strapline": clean(g(dealer, "marketing", "strapline")),
        "description": multiline(dealer.get("description")) or multiline(g(dealer, "marketing", "profile")),
        "logo": clean(g(dealer, "marketing", "productPageBrandingImage")),
        "distance_miles": number(dealer.get("distance")),
        "website": clean(g(dealer, "media", "dealerWebsite", "href")),
        "email": clean(g(dealer, "media", "emailAddress")),
        "accepts_email": {"yes": True, "no": False}.get((clean(g(dealer, "media", "email")) or "").lower()),
        "phone": clean(g(dealer, "media", "phoneNumber1")),
        "phone_2": clean(g(dealer, "media", "phoneNumber2")),
        "is_phone_protected": bool_or_none(g(dealer, "media", "protectedNumber")),
        "address": _address(location),
        "coordinates": _coordinates(location.get("latLong")),
        "rating": star_rating(g(dealer, "reviews", "overallReviewRating")),
        "review_count": first_int(g(dealer, "reviews", "numberOfReviews")),
        "awards": _awards(dealer.get("atAwards")),
        "fca_number": clean(dealer.get("fca")),
        "fca_permission": clean(dealer.get("fcaPermission")),
        "charges_admin_fee": bool_or_none(dealer.get("chargesAdminFee")),
        "franchises": text_list(as_list(services.get("franchises"))),
        "has_profile_page": bool_or_none(services.get("hasDealerProfilePage")),
        "has_home_delivery": bool_or_none(services.get("hasHomeDelivery")),
        "has_click_and_collect": bool_or_none(services.get("hasClickAndCollect")),
        "has_video_walk_around": bool_or_none(services.get("hasVideoWalkAround")),
        "matching_stock_count": first_int(g(dealer, "stockLevels", "matchingStockCount")),
        "forecourt_stock": _stock(counts),
        "advertised_stock": _stock(counts, "stock_"),
    }


def parse_dealers(raw):
    """A raw `dealerList` -> {results, total_count}."""
    rows = [row for row in (parse_dealer_row(d) for d in as_list(g(raw, "dealers"))) if row]
    total = g(raw, "totalElements")
    return {"results": rows, "total_count": count(total) if total is not None else None}


def _titled(items):
    return [{"category": clean(g(i, "category")), "title": clean(g(i, "title")),
             "description": multiline(g(i, "description"))}
            for i in as_list(items) if g(i, "title") or g(i, "description")]


def _opening_hours(rules):
    """[{dayOfWeek: [Monday, ...], opens, closes}] -> one row per day, Monday first."""
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    rows = []
    for rule in as_list(rules):
        days = rule.get("dayOfWeek") if isinstance(rule, dict) else None
        for day in (days if isinstance(days, list) else [days]):
            day = clean(day)
            if day:
                rows.append({"day": day, "opens": clean(g(rule, "opens")), "closes": clean(g(rule, "closes")),
                             "valid_from": iso_day(g(rule, "validFrom")), "valid_through": iso_day(g(rule, "validThrough"))})
    rows.sort(key=lambda row: order.index(row["day"]) if row["day"] in order else len(order))
    return rows


def parse_rating(summary):
    """A raw acceptedDealerReviews summary -> {rating, review_count, rating_breakdown, sources}."""
    summary = as_dict(summary)
    stars = {str(n): 0 for n in (5, 4, 3, 2, 1)}
    for item in as_list(summary.get("starSummaries")):
        key = str(first_int(g(item, "starRating")))
        if key in stars:
            stars[key] = count(g(item, "totalReviews")) or 0
    total = summary.get("totalLiveReviews")
    return {
        "rating": star_rating(summary.get("averageLiveReviewRating")),
        "review_count": count(total) if total is not None else None,
        "rating_breakdown": stars if summary.get("starSummaries") is not None else None,
        "sources": [{
            "source": _lower(g(item, "source")),
            "rating": star_rating(g(item, "averageLiveReviewRating")),
            "review_count": count(g(item, "totalLiveReviews")),
            "link": clean(g(item, "sourceUrl")),
        } for item in as_list(summary.get("sourceSummaries")) if g(item, "source")],
    }


def parse_dealer(raw):
    """fetch_dealer()'s {dealer, profile, rating} -> the full dealer record."""
    dealer = as_dict(g(raw, "dealer"))
    profile = as_dict(g(raw, "profile"))
    row = parse_dealer_row(dealer)
    if row is None:
        return None
    services = as_dict(dealer.get("servicesOffered"))
    capabilities = as_dict(dealer.get("capabilities"))
    media = [m for m in as_list(profile.get("media")) if isinstance(m, dict)]
    reviews = parse_rating(g(raw, "rating")) if g(raw, "rating") is not None else None
    row.pop("matching_stock_count", None)
    row.pop("distance_miles", None)
    position = list(row).index("address")
    head = dict(list(row.items())[:position])
    tail = dict(list(row.items())[position:])
    head["description"] = multiline(profile.get("description")) or head.get("description")
    head["assigned_phone"] = clean(g(dealer, "assignedNumber", "number"))
    tail.update({
        "rating_breakdown": g(reviews, "rating_breakdown"),
        "review_sources": g(reviews, "sources", default=[]) or [],
        "area": clean(g(profile, "metropolitan", "name")),
        "statement": multiline(profile.get("statement")),
        "is_appointment_only": bool_or_none(profile.get("appointmentOnlyVisits")),
        "opening_hours": _opening_hours(profile.get("openingHoursRules")),
        "highlights": _titled(profile.get("keyHighlights")),
        "why_buy": _titled(profile.get("whyBuy")),
        "business_services": text_list(as_list(profile.get("businessServices"))),
        "on_site_services": text_list(as_list(profile.get("onSiteServices"))),
        "services": text_list(as_list(services.get("services"))),
        "new_car_makes": text_list(as_list(services.get("newCarMakes"))),
        "has_safe_selling": bool_or_none(services.get("hasSafeSelling")),
        "safe_selling": {
            "points": text_list(as_list(g(profile, "safeSelling", "bulletPoints"))),
            "paragraphs": text_list(as_list(g(profile, "safeSelling", "paragraphs"))),
        } if profile.get("safeSelling") else None,
        "can_reserve_online": _enabled(capabilities.get("digitalRetailing")),
        "has_live_chat": _enabled(capabilities.get("instantMessagingChat")),
        "has_text_chat": _enabled(capabilities.get("instantMessagingText")),
        "partners": [{"name": clean(g(p, "name")), "logo": clean(g(p, "imageUrl"))}
                     for p in (as_list(g(dealer, "branding", "brands")) or as_list(services.get("servicePartners")))
                     if g(p, "name")],
        "accreditations": [{"name": clean(g(p, "name")), "logo": clean(g(p, "imageUrl"))}
                           for p in as_list(g(dealer, "branding", "accreditations")) if g(p, "name")],
        "other_awards": text_list(as_list(profile.get("noneATAwards"))),
        "manufacturers": [{
            "name": clean(g(m, "name")),
            "types": text_list(as_list(g(m, "types"))),
            "title": clean(g(m, "retailerTitle")),
            "about": text_list(as_list(g(m, "retailerParagraphs"))),
            "approved_used": text_list(as_list(g(m, "approvedUsedParagraphs"))
                                       + as_list(g(m, "approvedUsedBulletPoints"))),
        } for m in as_list(profile.get("manufacturers")) if g(m, "name")],
        "photos": [link for link in (image(m.get("url")) for m in media if m.get("type") != "Video") if link],
        "videos": [link for link in (clean(m.get("url")) for m in media if m.get("type") == "Video") if link],
        "faqs": [{"question": clean(g(f, "question")), "answer": multiline(g(f, "answer"))}
                 for f in as_list(profile.get("faqs")) if g(f, "question")],
    })
    if reviews:
        # The review service's live figures: the dealer record rounds its own.
        tail["rating"] = reviews["rating"] if reviews["rating"] is not None else tail.get("rating")
        tail["review_count"] = reviews["review_count"] if reviews["review_count"] is not None \
            else tail.get("review_count")
    return {**head, **tail}


# ---- reviews -------------------------------------------------------------------------

def parse_review(review):
    if not isinstance(review, dict):
        return None
    reply = review.get("reply") if isinstance(review.get("reply"), dict) else None
    return {
        "title": clean(review.get("title")),
        "text": multiline(review.get("text")),
        "rating": number(review.get("overallRating")),
        "reviewer_name": clean(review.get("reviewerName")),
        "source": _lower(review.get("source")),
        "is_verified": bool_or_none(review.get("verified")),
        "created_at": iso_datetime(review.get("created")),
        "updated_at": iso_datetime(review.get("updated")),
        "reply": {"text": multiline(reply.get("replyText")), "updated_at": iso_datetime(reply.get("updated"))}
        if reply and reply.get("replyText") else None,
    }


def parse_reviews(raw):
    """fetch_reviews()'s raw Dealer -> {dealer, rating summary, reviews,
    matching_count}. `matching_count` is the number of reviews the filters
    match (the list's own size), which is what the pages count."""
    accepted = as_dict(g(raw, "acceptedDealerReviews"))
    dealer_id = clean(str(g(raw, "dealerId"))) if g(raw, "dealerId") else None
    summary = parse_rating(accepted)
    size = g(accepted, "dealerReviewList", "size")
    return {
        "dealer": {
            "id": dealer_id,
            "name": clean(g(raw, "name")),
            "link": refs.dealer_link(dealer_id, g(raw, "name"), g(raw, "location", "town"),
                                     g(raw, "location", "county")),
        },
        "rating": summary["rating"],
        "review_count": summary["review_count"],
        "rating_breakdown": summary["rating_breakdown"],
        "sources": summary["sources"],
        "reviews": [r for r in (parse_review(x) for x in as_list(g(accepted, "dealerReviewList", "reviews"))) if r],
        "matching_count": count(size) if size is not None else None,
    }
