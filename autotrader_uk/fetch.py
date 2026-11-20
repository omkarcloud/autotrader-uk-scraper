"""Autotrader UK transport: plain curl_cffi on the GraphQL calls the site's
own front end makes, plus the listing page for the few values only it
carries. No browser, no login, no cookies, no token. Egress: one session
per thread on the exit config.AUTOTRADER_UK_EXIT names (egress.exit_session):
in production the server's own IP (pod probe 2026-11-19, table in the
config notes). A thread whose call was refused rides
config.AUTOTRADER_UK_FALLBACK_EXIT (the gateway exit) for
config.AUTOTRADER_UK_BLOCK_HOLD_S. A retryable failure drops the session,
so the retry rides a fresh connection (and, on the gateway exit, a fresh
address).

The site (www.autotrader.co.uk, behind Cloudflare) renders every page from
two GraphQL endpoints, both open to a plain POST:

  * GATEWAY  POST /at-gateway    the current schema
      searchResults(input)       listing cards, total, pages; the same
                                 call with `facets` returns every filter
                                 with its options and live counts
  * LEGACY   POST /at-graphql    the older schema, still what the listing
                                 and dealer pages are rendered from
      search.advert(advertId)    one listing, any vehicle type
      search.dealers(dealerQuery)                  dealer search
      search.dealer(dealerId)                      one dealer
      search.dealer.acceptedDealerReviews(...)     a dealer's reviews
      retailers.retailer(retailerId).profile       the dealer page's text,
                                                   photos, opening hours

  * PAGE     GET /<type>-details/<id>   the listing page; its
      window.__staticRouterHydrationData JSON carries what the page shows
      (the good-price range, highlights, delivery to the caller's postcode)

What the transport depends on (all measured 2026-11-19):
  * a body may be a JSON list of operations (a batch): one answer per
    operation, in order. Each operation is capped at a complexity of 100
    on /at-graphql ("Query blocked, complexity limit exceeded"), so the
    listing is asked for as several small operations in ONE request;
  * introspection is off and a selection is validated strictly: one unknown
    field fails the whole operation with HTTP 400;
  * an unknown advert or dealer answers HTTP 200 with an error whose
    extensions.code is NOT_FOUND; a bad postcode answers "Some input
    filters were invalid: postcode - The postcode could not be found";
  * search pages stop at 100 ("Page input filter is invalid, requested 101,
    max is 100"); the legacy lists (dealers) stop at page 20;
  * the listing page of a sold or unknown advert, or of the wrong vehicle
    type, is a 301 to /search-form?expired-ad=true.

The two `x-sauron-app-*` headers are the ones the site's own pages send.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  AutotraderUkUpstreamError  transport failure / 5xx / unparseable    — retryable
  AutotraderUkBlocked        403 / 429 / a Cloudflare challenge page  — retryable
  AutotraderUkBadRequest     the site refused a filter value          — never retried
  AutotraderUkNotFound       unknown advert / dealer                  — never retried
"""
import base64
import os
import sys
import threading
import time
import uuid

# Allow direct execution (python autotrader_uk/fetch.py): flat imports
# resolve like under the server. Idempotent when imported normally.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import egress
import fastjson
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from scraper_utils import pages, retry, sessions
from scraper_utils.values import g
from autotrader_uk.refs import SITE

IMPERSONATE = "chrome"
# Answers take 0.2-1 s; a gateway exit request gets 29 s in all, so a hung
# request is cut early and retried.
API_TIMEOUT = 20
PAGE_TIMEOUT = 20

GATEWAY_PATH = "/at-gateway"
LEGACY_PATH = "/at-graphql"

SEARCH_PAGE_SIZE = 20       # listings per search page (the site's own)
MAX_SEARCH_PAGES = 100      # "Page input filter is invalid, requested 101, max is 100"
DEALER_PAGE_SIZE = 20       # dealers per page of the legacy list
MAX_DEALER_PAGES = 20       # page 21 of any dealer search is empty (2026-11-19)
REVIEW_PAGE_SIZE = 20       # the review list serves at most 20 per call

API_HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "origin": SITE,
    "referer": SITE + "/",
    "x-sauron-app-name": "sauron-search-results-app",
    "x-sauron-app-version": "1",
}
PAGE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "upgrade-insecure-requests": "1",
}


class AutotraderUkUpstreamError(UpstreamError):
    """Transport failure, 5xx, or an answer that is not the expected JSON — retryable."""


class AutotraderUkBlocked(AutotraderUkUpstreamError, Blocked):
    """403 / 429 or a Cloudflare challenge page — retried."""


class AutotraderUkBadRequest(BadRequest):
    """The site refused a filter value (an unknown postcode) — never retried."""


class AutotraderUkNotFound(NotFound):
    """No such advert / dealer — never retried."""


# ---- the queries ---------------------------------------------------------------------
# Every field the site's schema offers for a record is named: a GraphQL
# selection returns nothing it was not asked for. Left out on purpose: the
# vehicle check id and report (the page hides the number plate and VIN that
# report carries), the masked plate, a private seller's postcode and
# coordinates (the page shows the town only), and the site's internals (stock
# item id, product codes and programme flags, advertising locations, the
# advert's version number).

_CARD = """
type advertId title subTitle attentionGrabber price formattedPrice rrp discount condition mileageText
vehicleLocation locationType images numberOfImages sellerType dealerLink overallReviewRating
dealerReview { overallReviewRating }
hasDigitalRetailing preReg position
badges { type displayText }
finance {
  monthlyPrice { priceFormattedAndRounded }
  initialPayment termMonths quoteSubType financeListingProvider
  representativeValues { financeKey financeValue }
  rawFinanceFields { deposit term annualMileage }
}
trackingContext {
  retailerContext { id }
  advertContext { id advertiserId advertiserType make model vehicleCategory year condition price }
  advertCardFeatures { condition numImages hasFinance priceIndicator isManufacturedApproved isFranchiseApproved }
  distance { distance distance_unit }
}
"""

SEARCH_QUERY = """
query SearchResultsListingsGridQuery($filters: [FilterInput!]!, $channel: Channel!, $page: Int,
    $sortBy: SearchResultsSort, $listingType: [ListingType!], $searchId: String!) {
  searchResults(input: {facets: [], filters: $filters, channel: $channel, page: $page, sortBy: $sortBy,
                        listingType: $listingType, searchId: $searchId}) {
    listings { ... on SearchListing { %s } }
    page { number count results { count } }
  }
}
""" % _CARD

FACETS_QUERY = """
query SearchResultsFacetsWithGroupsQuery($facets: [FacetName!]!, $filters: [FilterInput!]!, $channel: Channel!,
    $sortBy: SearchResultsSort) {
  searchResults(input: {facets: $facets, filters: $filters, channel: $channel, sortBy: $sortBy}) {
    sortBy { selected options { name value } }
    facets { facet filters { parent filter selected options { label value count description } } }
    page { results { count } }
  }
}
"""
# Any facet name makes the site answer with every facet it has for the channel.
FACET_NAMES = ["make", "model"]

_ADVERT_OPERATIONS = {
    "AdvertCore": """
id title summary advertText attentionGrabber description
heading { title subtitle }
condition stockType isAuction preReg isPremium year plate dateOfRegistration colour
owners keys excludePreviousOwners serviceHistory lastServiceDate lastServiceOdometerReadingMiles
motExpiry motInsurance twelveMonthsMotIncluded warrantyMonthsOnPurchase hoursUsed dueAtSeller
mileage { mileage unit } mileageDeviation
price priceCurrency priceExcludingFees priceOnApplication plusVatIndicated vatStatus rrp saving
adminFee noAdminFees adminFeeInfoDescription
priceIndicatorRating priceIndicatorRatingLabel priceDeviation valueRating
manufacturerApproved insuranceWriteOffCategory vehicleCheckStatus tradeLifecycleStatus
hasClickAndCollect isPartExAvailable
isFinanceAvailable isFinanceFullApplicationAvailable hasFinanceInformation financeProvider
financeDefaults { term mileage depositAmount }
homeDeliveryRegionCodes combinedFeatures
vehicleCondition { tyreCondition interiorCondition bodyCondition }
""",
    "AdvertSeller": """
id sellerName sellerType sellerLocation sellerLocationCounty retailerId advertiserSegment
sellerLocationDistance { value unit }
sellerContact { phoneNumberOne phoneNumberTwo protectedNumber byEmail }
sellerRating { rating reviewCount }
locationArea { code region }
privateAdvertiser { contact { protectedNumber } location { town county region country } }
dealer {
  dealerId name legalName description distance fca fcaPermission
  location { addressOne addressTwo town county region country postcode latLong }
  media { email emailAddress phoneNumber1 phoneNumber2 protectedNumber dealerWebsite { href } }
  reviews { numberOfReviews overallReviewRating }
  marketing { strapline profile }
  servicesOffered { hasHomeDelivery hasClickAndCollect hasVideoWalkAround hasSafeSelling hasDealerProfilePage franchises }
  atAwards { category year }
  assignedNumber { number }
}
""",
    "AdvertMedia": """
id
imageList(limit: 200) { size images { url angle paint } }
video { url preview }
spin { url preview }
generation { generationId name bodyType make model review { expertReviewSummary { rating reviewUrl } } }
capabilities { digitalRetailing { enabled } sellerPromise { enabled } }
reservation { eligibility status }
vehicleCheckSummary {
  type title performed passes fails warnings writeOffCategory
  checks { key name description failed warning info }
}
finance {
  monthlyPayment quoteType quoteSubtype representativeApr duration finalPayment
  totalAmountPayable totalCredit onRoadPrice
}
collectionLocations { locations { id dealerId name distance geoCoordinate { latitude longitude } } }
""",
    "AdvertSpec": """
id
specification {
  make model trim derivative derivativeId generationId vehicleCategory bodyType rawBodyType style subStyle
  fuel transmission driveTrain oemDrivetrain driverPosition doors seats numberOfGears emissionClass
  ulezCompliant isCrossover operatingType interiorDescription wheelbase cabType axleConfig
  berth bedrooms bedroomLayout endLayout trailerAxleNumber
  bikeLicenceType brakeType frameSizeCM frameStyle motorMake
  engine { sizeLitres sizeCC manufacturerEngineSize power { enginePower unit } }
  co2Emissions { co2Emission unit }
  annualTax { standardRate }
  topSpeed { topSpeed }
  range { totalRange unit }
  battery {
    capacity { capacity unit } usableCapacity { capacity unit } range { range unit }
    charging { chargeTime quickChargeTime }
  }
}
""",
    "AdvertSize": """
id
specification {
  exteriorHeight { height unit } exteriorLength { length unit } exteriorWidth { width unit }
  capacityHeight { height unit } capacityLength { length unit } capacityWidth { width unit }
  capacityWeight { weight unit } grossVehicleWeight { weight unit } minimumKerbWeight { weight unit }
  liftingCapacity { weight unit } maxReach { length unit } operatingWidth { width unit }
  techData {
    bodyStyle fuelType transmission numberOfDoors numberOfSeats cylinders valves
    enginePower engineTorque topSpeed zeroToSixtyMph zeroToSixtyTwoMph
    co2Emissions insuranceGroup fuelConsumptionCombined fuelConsumptionExtraUrban fuelConsumptionUrban
    fuelTankCapacity bootspaceSeatsUp luggageCapacitySeatsDown
    vehicleHeight vehicleLength vehicleWidth vehicleWidthInclMirrors wheelbase
    minimumKerbWeight grossVehicleWeight maxLoadingWeight
  }
}
""",
    "AdvertFeatures": """
id
specification {
  standardFeatures { description category type }
  optionalFeatures { description category type }
}
""",
}

ADVERT_QUERIES = {
    name: "query %s($advertId: String!, $searchOptions: SearchOptions) { search { "
          "advert(advertId: $advertId, searchOptions: $searchOptions) { %s } } }" % (name, selection)
    for name, selection in _ADVERT_OPERATIONS.items()
}

_DEALER_FIELDS = """
dealerId name legalName description distance fca fcaPermission chargesAdminFee
location { addressOne addressTwo town county region country postcode latLong }
marketing { profile productPageBrandingImage strapline }
media { email emailAddress phoneNumber1 phoneNumber2 protectedNumber dealerWebsite { href } }
reviews { overallReviewRating numberOfReviews }
atAwards { year category }
"""
_STOCK_COUNTS = """
car bike van motorhome caravan truck plant agricultural
stock_car stock_bike stock_van stock_caravan stock_motorhome stock_truck stock_plant stock_agricultural
"""

DEALERS_QUERY = """
query RetailerSearchQuery($dealerQuery: DealerQuery!, $sort: DealerSort, $page: Int) {
  search { dealers(dealerQuery: $dealerQuery, sort: $sort) { dealerList(page: $page) {
    size totalElements
    dealers {
      %s
      servicesOffered { hasDealerProfilePage hasHomeDelivery hasClickAndCollect hasVideoWalkAround franchises }
      stockLevels { matchingStockCount atStockCounts { %s } }
    }
  } } }
}
""" % (_DEALER_FIELDS, _STOCK_COUNTS)

DEALER_QUERY = """
query DealerDataQuery($dealerId: String!) {
  search { dealer(dealerId: $dealerId) {
    %s
    branding { brands { name imageUrl } accreditations { name imageUrl } }
    capabilities {
      digitalRetailing { enabled } instantMessagingChat { enabled provider }
      instantMessagingText { enabled provider }
    }
    servicesOffered {
      services newCarMakes franchises hasDealerProfilePage hasHomeDelivery hasClickAndCollect
      hasVideoWalkAround hasSafeSelling servicePartners { name imageUrl }
    }
    assignedNumber { number }
    stockLevels { atStockCounts { %s } }
  } }
}
""" % (_DEALER_FIELDS, _STOCK_COUNTS)

DEALER_NAME_QUERY = """
query DealerInfoQuery($dealerId: String!) { search { dealer(dealerId: $dealerId) { dealerId name } } }
"""

DEALER_PROFILE_QUERY = """
query RetailerQuery($dealerId: String!) {
  retailers { retailer(retailerId: $dealerId) { profile {
    faqs { question answer }
    statement description
    openingHoursRules { dayOfWeek opens closes validFrom validThrough }
    media { type url videoPreviewUrl }
    noneATAwards appointmentOnlyVisits businessServices onSiteServices
    keyHighlights { category title description }
    whyBuy { category title description }
    safeSelling { bulletPoints paragraphs }
    metropolitan { name uriValue }
    manufacturers { id name types retailerTitle approvedUsedParagraphs retailerParagraphs approvedUsedBulletPoints }
  } } }
}
"""

_REVIEW_SUMMARY = """
totalLiveReviews averageLiveReviewRating
sourceSummaries { source averageLiveReviewRating totalLiveReviews sourceUrl }
starSummaries { starRating totalReviews }
"""

DEALER_RATING_QUERY = """
query DealerReviewsSummaryQuery($dealerId: String!) {
  search { dealer(dealerId: $dealerId) { acceptedDealerReviews(limit: 1) { %s } } }
}
""" % _REVIEW_SUMMARY

REVIEWS_QUERY = """
query DealerReviewsQuery($dealerId: String!, $limit: Int, $sortOrder: String, $cursor: String,
    $starRatings: String, $returnReviewsFrom: String, $text: String) {
  search { dealer(dealerId: $dealerId) {
    dealerId name
    location { town county }
    acceptedDealerReviews(limit: $limit, cursor: $cursor, sortOrder: $sortOrder, starRating: $starRatings,
                          summaryRequired: true, returnReviewsFrom: $returnReviewsFrom, text: $text) {
      %s
      dealerReviewList {
        size nextCursor
        reviews { title text reviewerName overallRating source verified created updated reply { replyText updated } }
      }
    }
  } }
}
""" % _REVIEW_SUMMARY


# ---- sessions ------------------------------------------------------------------------

# One curl session per cheroot worker thread (a curl handle must not be
# shared across threads). Nothing else lives here: the site needs no token.
_local = threading.local()


def _exit():
    """The exit this thread's next session rides: the fallback while its hold runs."""
    fallback = getattr(config, "AUTOTRADER_UK_FALLBACK_EXIT", None)
    if fallback and getattr(_local, "hold_until", 0) > time.monotonic():
        return fallback
    return config.AUTOTRADER_UK_EXIT


def _hold():
    """This thread's call was refused: its next sessions ride the fallback exit."""
    if getattr(config, "AUTOTRADER_UK_FALLBACK_EXIT", None):
        _local.hold_until = time.monotonic() + config.AUTOTRADER_UK_BLOCK_HOLD_S


def _session():
    sess = getattr(_local, "session", None)
    exit = _exit()
    if sess is not None and getattr(_local, "session_exit", None) != exit:
        _drop_session()                  # a hold began or ended: change exits
        sess = None
    if sess is None:
        sess = _local.session = egress.exit_session(exit, impersonate=IMPERSONATE)
        _local.session_exit = exit
    if getattr(sess, "via_exit_gateway", False):
        # Every gateway exit request leaves from a new address: a Cloudflare
        # cookie earned by the last one would arrive from the wrong address.
        # No call needs a cookie, so each goes out without the ones the site set.
        sess.cookies.clear()
    return sess


def _drop_session():
    """Close the thread's session so the retry rides a fresh connection / exit."""
    sessions.drop(_local)


def _on_failure(error):
    if isinstance(error, AutotraderUkBlocked):
        _hold()
    _drop_session()
    print(f"autotrader-uk: attempt failed: {error}")


def _retrying(fn):
    """fn() under the shared retry policy: AutotraderUkUpstreamError only
    (Blocked included), each retry on a fresh session; BadRequest and
    NotFound surface at once."""
    return retry.run(fn, config, AutotraderUkUpstreamError, _on_failure)


def _send(method, url, label, headers, body=None, timeout=API_TIMEOUT):
    """One request -> the response. A transport failure is retryable; a
    challenge page (whatever its status), a 403 and a 429 are Blocked."""
    try:
        resp = _session().request(method, url, headers=headers, data=body, timeout=timeout, allow_redirects=False)
    except Exception as e:
        raise AutotraderUkUpstreamError(f"{label}: request failed: {type(e).__name__}: {e}")
    status = resp.status_code
    if status in (403, 429) or pages.is_challenged(status, resp.headers, resp.text if status >= 400 else ""):
        raise AutotraderUkBlocked(f"HTTP {status} for {label}")
    if status >= 500:
        raise AutotraderUkUpstreamError(f"HTTP {status} for {label}")
    return resp


# ---- GraphQL -------------------------------------------------------------------------

_FILTER_ERROR = "Some input filters were invalid:"


def _error_of(errors, label, refused=None):
    """The site's `errors` list of one operation -> the exception to raise.
    `refused` is what to tell the caller when the legacy schema's own
    service answered 400 (it says only "Bad Request"): the one input of that
    call a caller can get wrong."""
    first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
    message = str(first.get("message") or "")
    extensions = first.get("extensions") if isinstance(first.get("extensions"), dict) else {}
    code = extensions.get("code")
    if code == "NOT_FOUND" or extensions.get("statusCode") == 404:
        return AutotraderUkNotFound(f"{label} not found")
    if message.startswith(_FILTER_ERROR):
        # "Some input filters were invalid: postcode - The postcode could not be found"
        return AutotraderUkBadRequest(message[len(_FILTER_ERROR):].strip().replace(" - ", ": ", 1))
    if message.startswith("Page input filter is invalid"):
        return AutotraderUkBadRequest(message)
    if code == "BAD_USER_INPUT":
        return AutotraderUkBadRequest(message)
    if refused and g(extensions, "exception", "response", "statusCode") == 400:
        return AutotraderUkBadRequest(refused)
    return AutotraderUkUpstreamError(f"{label}: {message[:200] or 'GraphQL error'}")


def graphql(path, operations, label, optional=(), refused=None):
    """POST a batch of operations [(name, query, variables)] in one request
    -> {name: the operation's `data`}. An operation named in `optional`
    yields None when the site answers it with an error; any other error
    raises (NOT_FOUND -> AutotraderUkNotFound, a refused filter ->
    AutotraderUkBadRequest, the rest retryable)."""
    # A variable that is not set is left out: the site passes a null on to
    # its services as the text "null" (the review list then answers 400).
    body = fastjson.dumps([{"operationName": name, "query": query,
                            "variables": {key: value for key, value in (variables or {}).items() if value is not None}}
                           for name, query, variables in operations])
    url = f"{SITE}{path}?" + "&".join(f"opname={name}" for name, _, _ in operations)

    def once():
        resp = _send("POST", url, label, API_HEADERS, body)
        try:
            answers = fastjson.loads(resp.content)
        except ValueError:
            raise AutotraderUkUpstreamError(f"{label}: HTTP {resp.status_code}, not JSON: {resp.text[:120]!r}")
        if isinstance(answers, dict):
            answers = [answers]
        if not isinstance(answers, list) or len(answers) != len(operations):
            raise AutotraderUkUpstreamError(f"{label}: {len(operations)} operations asked, "
                                            f"{len(answers) if isinstance(answers, list) else 0} answered")
        out = {}
        for (name, _, _), answer in zip(operations, answers):
            answer = answer if isinstance(answer, dict) else {}
            data = answer.get("data")
            if answer.get("errors") or not isinstance(data, dict):
                if name in optional:
                    out[name] = None
                    continue
                raise _error_of(answer.get("errors"), label, refused)
            out[name] = data
        return out
    return _retrying(once)


def _filter_inputs(filters):
    return [{"filter": name, "selected": value if isinstance(value, list) else [str(value)]}
            for name, value in filters.items() if value is not None]


def fetch_search(channel, filters, page=1, sort="relevance"):
    """One search page -> the raw `searchResults` object. `filters` is {the
    site's filter name: value}; postcode and price_search_type must be in it.
    Only the natural listings are asked for, so a page is 20 adverts with no
    promoted copies or ad slots between them (a dealer's stock, the
    retailer_id filter, comes back under the same listing type)."""
    variables = {"filters": _filter_inputs(filters), "channel": channel, "page": page, "sortBy": sort,
                 "listingType": ["NATURAL_LISTING"], "searchId": str(uuid.uuid4())}
    name = "SearchResultsListingsGridQuery"
    data = graphql(GATEWAY_PATH, [(name, SEARCH_QUERY, variables)], f"{channel} search page {page}")
    return data[name].get("searchResults") or {}


def fetch_facets(channel, filters):
    """Every filter of a search with its options and live counts -> the raw
    `searchResults` object (facets, sortBy, page.results.count)."""
    variables = {"facets": FACET_NAMES, "filters": _filter_inputs(filters), "channel": channel,
                 "sortBy": "relevance"}
    name = "SearchResultsFacetsWithGroupsQuery"
    data = graphql(GATEWAY_PATH, [(name, FACETS_QUERY, variables)], f"{channel} filters")
    return data[name].get("searchResults") or {}


def fetch_advert(advert_id, postcode, advertising_location=None):
    """One listing -> {operation name: the raw `advert` object of that
    operation} (the listing is asked for in parts, see the complexity cap).
    Raises AutotraderUkNotFound for an advert the site no longer has."""
    options = {"postcode": postcode}
    if advertising_location:
        options["advertisingLocations"] = [advertising_location]
    variables = {"advertId": advert_id, "searchOptions": options}
    operations = [(name, query, variables) for name, query in ADVERT_QUERIES.items()]
    data = graphql(LEGACY_PATH, operations, f"advert {advert_id}")
    return {name: (part.get("search") or {}).get("advert") or {} for name, part in data.items()}


def fetch_listing_page(path, postcode=None):
    """The listing page's HTML, or None when the site has no page there (a
    301 to its "expired ad" search form, or a 404)."""
    url = SITE + path + (f"?postcode={postcode.replace(' ', '')}" if postcode else "")
    label = f"listing page {path}"

    def once():
        resp = _send("GET", url, label, PAGE_HEADERS, timeout=PAGE_TIMEOUT)
        if resp.status_code in (301, 302, 303, 307, 308, 404, 410):
            return None
        if resp.status_code != 200:
            raise AutotraderUkUpstreamError(f"HTTP {resp.status_code} for {label}")
        text = resp.text
        if "__staticRouterHydrationData" not in text:
            if pages.is_challenged(403, resp.headers, text):
                raise AutotraderUkBlocked(f"challenge page for {label}")
            raise AutotraderUkUpstreamError(f"{label}: the page carries no data")
        return text
    return _retrying(once)


def fetch_dealers(dealer_query, sort=None, page=1):
    """One page of a dealer search -> the raw `dealerList` object."""
    variables = {"dealerQuery": dealer_query, "sort": sort, "page": page}
    name = "RetailerSearchQuery"
    data = graphql(LEGACY_PATH, [(name, DEALERS_QUERY, variables)], f"dealer search page {page}",
                   refused="postcode: The postcode could not be found" if dealer_query.get("postcode") else None)
    return (((data[name].get("search") or {}).get("dealers") or {}).get("dealerList")) or {}


def fetch_dealer(dealer_id):
    """One dealer -> {"dealer": the raw Dealer, "profile": the raw page
    profile or None, "rating": the raw review summary or None}. Raises
    AutotraderUkNotFound for an unknown dealer id."""
    variables = {"dealerId": dealer_id}
    operations = [("DealerDataQuery", DEALER_QUERY, variables),
                  ("RetailerQuery", DEALER_PROFILE_QUERY, variables),
                  ("DealerReviewsSummaryQuery", DEALER_RATING_QUERY, variables)]
    data = graphql(LEGACY_PATH, operations, f"dealer {dealer_id}",
                   optional=("RetailerQuery", "DealerReviewsSummaryQuery"))
    dealer = (data["DealerDataQuery"].get("search") or {}).get("dealer")
    if not isinstance(dealer, dict) or not dealer.get("dealerId"):
        raise AutotraderUkNotFound(f"dealer {dealer_id} not found")
    profile = ((((data.get("RetailerQuery") or {}).get("retailers") or {}).get("retailer")) or {}).get("profile")
    rating = ((((data.get("DealerReviewsSummaryQuery") or {}).get("search") or {}).get("dealer")) or {}).get(
        "acceptedDealerReviews")
    return {"dealer": dealer, "profile": profile, "rating": rating}


def fetch_dealer_name(dealer_id):
    """The dealer's name, the cheapest proof that a dealer id exists. Raises
    AutotraderUkNotFound for an unknown one."""
    name = "DealerInfoQuery"
    data = graphql(LEGACY_PATH, [(name, DEALER_NAME_QUERY, {"dealerId": dealer_id})], f"dealer {dealer_id}")
    dealer = (data[name].get("search") or {}).get("dealer")
    if not isinstance(dealer, dict):
        raise AutotraderUkNotFound(f"dealer {dealer_id} not found")
    return dealer.get("name")


def review_cursor(page):
    """The cursor the review list hands out for page N (base64 of "page=N");
    None for the first page."""
    return base64.b64encode(f"page={page}".encode()).decode() if page and page > 1 else None


def fetch_reviews(dealer_id, page=1, sort="newest-first", rating=None, text=None, period="forever"):
    """One page of a dealer's reviews -> the raw Dealer object (dealerId,
    name, location, acceptedDealerReviews). Raises AutotraderUkNotFound for
    an unknown dealer id."""
    variables = {"dealerId": dealer_id, "limit": REVIEW_PAGE_SIZE, "sortOrder": sort, "cursor": review_cursor(page),
                 "starRatings": str(rating) if rating else None, "returnReviewsFrom": period, "text": text}
    name = "DealerReviewsQuery"
    data = graphql(LEGACY_PATH, [(name, REVIEWS_QUERY, variables)], f"dealer {dealer_id}")
    dealer = (data[name].get("search") or {}).get("dealer")
    if not isinstance(dealer, dict):
        raise AutotraderUkNotFound(f"dealer {dealer_id} not found")
    return dealer
