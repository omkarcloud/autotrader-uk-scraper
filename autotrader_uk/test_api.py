"""Offline tests for the transport's reading of the site's answers
(autotrader_uk/fetch.py) and the endpoint logic (autotrader_uk/api.py), with
the network and the cache replaced by fakes (the answers are the fixtures).

    python -m pytest autotrader_uk/test_api.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from autotrader_uk import api, fetch, schemas  # noqa: E402
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def raw(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


def fixture(name):
    return json.loads(raw(name))


# ---- the transport -------------------------------------------------------------------

class Answer:
    def __init__(self, status=200, body="", headers=None):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.content = self.text.encode()
        self.headers = headers or {}


class Wire:
    """A session whose answers are queued; it records what was sent."""

    def __init__(self, monkeypatch, *answers):
        self.answers = list(answers)
        self.sent = []
        self.dropped = 0
        monkeypatch.setattr(fetch, "_session", lambda: self)
        monkeypatch.setattr(fetch, "_drop_session", self._drop)
        monkeypatch.setattr(fetch.retry.time, "sleep", lambda seconds: None)
        monkeypatch.setattr(fetch._local, "hold_until", 0, raising=False)

    def _drop(self):
        self.dropped += 1

    def request(self, method, url, headers=None, data=None, timeout=None, allow_redirects=True):
        self.sent.append({"method": method, "url": url, "headers": headers, "body": data,
                          "allow_redirects": allow_redirects})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _data(**fields):
    return {"data": fields}


def test_error_classes_follow_the_shared_taxonomy():
    assert issubclass(fetch.AutotraderUkBlocked, Blocked) and issubclass(fetch.AutotraderUkBlocked, UpstreamError)
    assert issubclass(fetch.AutotraderUkUpstreamError, UpstreamError)
    assert issubclass(fetch.AutotraderUkBadRequest, BadRequest) and issubclass(fetch.AutotraderUkNotFound, NotFound)
    assert not issubclass(fetch.AutotraderUkNotFound, UpstreamError)


@pytest.mark.parametrize("name, refused, kind, message", [
    ("error_advert_not_found.json", None, fetch.AutotraderUkNotFound, "advert 1 not found"),
    ("error_dealer_not_found.json", None, fetch.AutotraderUkNotFound, "advert 1 not found"),
    ("error_bad_postcode.json", None, fetch.AutotraderUkBadRequest, "postcode: The postcode could not be found"),
    ("error_dealers_bad_postcode.json", "postcode: The postcode could not be found", fetch.AutotraderUkBadRequest,
     "postcode: The postcode could not be found"),
    # the same answer for a call no caller input can break is the site's fault
    ("error_dealers_bad_postcode.json", None, fetch.AutotraderUkUpstreamError, "advert 1: Bad Request"),
    ("error_unknown_field.json", None, fetch.AutotraderUkUpstreamError,
     'advert 1: Cannot query field "nope" on type "Advert".'),
])
def test_the_sites_error_answers(name, refused, kind, message):
    error = fetch._error_of(fixture(name)[0]["errors"], "advert 1", refused)
    assert type(error) is kind and str(error) == message


def test_error_of_junk():
    for junk in (None, [], [None], [{}], "x"):
        assert type(fetch._error_of(junk, "x")) is fetch.AutotraderUkUpstreamError


def test_graphql_sends_one_batch_and_leaves_unset_variables_out(monkeypatch):
    wire = Wire(monkeypatch, Answer(body=[_data(a=1), _data(b=2)]))
    out = fetch.graphql("/at-graphql", [("One", "query One { a }", {"x": 1, "y": None}),
                                        ("Two", "query Two { b }", None)], "thing")
    assert out == {"One": {"a": 1}, "Two": {"b": 2}}
    sent = wire.sent[0]
    assert sent["method"] == "POST" and sent["allow_redirects"] is False
    assert sent["url"] == "https://www.autotrader.co.uk/at-graphql?opname=One&opname=Two"
    assert json.loads(sent["body"]) == [
        {"operationName": "One", "query": "query One { a }", "variables": {"x": 1}},
        {"operationName": "Two", "query": "query Two { b }", "variables": {}}]
    assert sent["headers"]["content-type"] == "application/json" and sent["headers"]["x-sauron-app-name"]
    assert "accept-language" not in sent["headers"]          # the session announces its exit's language


def test_graphql_raises_not_found_at_once(monkeypatch):
    wire = Wire(monkeypatch, Answer(body=fixture("error_advert_not_found.json")))
    with pytest.raises(fetch.AutotraderUkNotFound):
        fetch.graphql("/at-graphql", [("AdvertCore", "q", {})], "advert 1")
    assert len(wire.sent) == 1 and wire.dropped == 0


def test_an_optional_operation_that_fails_is_none(monkeypatch):
    Wire(monkeypatch, Answer(body=[_data(a=1), fixture("error_dealer_not_found.json")[0]]))
    out = fetch.graphql("/at-graphql", [("One", "q", {}), ("Two", "q", {})], "dealer 1", optional=("Two",))
    assert out == {"One": {"a": 1}, "Two": None}


def test_a_blocked_call_is_retried_on_a_fresh_session_and_holds_the_fallback(monkeypatch):
    monkeypatch.setattr(config, "MAX_RETRIES", 3)
    monkeypatch.setattr(config, "AUTOTRADER_UK_EXIT", "direct")
    monkeypatch.setattr(config, "AUTOTRADER_UK_FALLBACK_EXIT", "gateway")
    challenge = "<html><title>Just a moment...</title></html>"
    wire = Wire(monkeypatch, Answer(403, challenge), Answer(429, ""), Answer(body=[_data(a=1)]))
    assert fetch._exit() == "direct"
    assert fetch.graphql("/at-gateway", [("One", "q", {})], "search") == {"One": {"a": 1}}
    assert len(wire.sent) == 3 and wire.dropped == 2
    assert fetch._exit() == "gateway"                 # this thread rides the fallback for the hold
    monkeypatch.setattr(fetch._local, "hold_until", 0)
    assert fetch._exit() == "direct"


def test_transport_failures_run_out_of_retries(monkeypatch):
    monkeypatch.setattr(config, "MAX_RETRIES", 2)
    wire = Wire(monkeypatch, ConnectionError("reset"), Answer(502, "bad gateway"))
    with pytest.raises(fetch.AutotraderUkUpstreamError) as e:
        fetch.graphql("/at-gateway", [("One", "q", {})], "search")
    assert "HTTP 502" in str(e.value) and not isinstance(e.value, Blocked)
    assert len(wire.sent) == 2 and wire.dropped == 2
    assert fetch._exit() == config.AUTOTRADER_UK_EXIT      # only a block moves a thread to the fallback


def test_an_answer_that_is_not_the_batch_is_retryable(monkeypatch):
    monkeypatch.setattr(config, "MAX_RETRIES", 1)
    Wire(monkeypatch, Answer(body="<html>"))
    with pytest.raises(fetch.AutotraderUkUpstreamError):
        fetch.graphql("/at-gateway", [("One", "q", {})], "search")
    Wire(monkeypatch, Answer(body=[_data(a=1)]))
    with pytest.raises(fetch.AutotraderUkUpstreamError):
        fetch.graphql("/at-gateway", [("One", "q", {}), ("Two", "q", {})], "search")


def test_every_advert_operation_is_under_the_sites_complexity_cap():
    """The site refuses an operation above a complexity of 100, roughly its
    number of selected fields. A selection that grows past it must be split."""
    for name, query in fetch.ADVERT_QUERIES.items():
        selection = query.split("{", 3)[3]
        fields = [word for word in selection.replace("{", " ").replace("}", " ").replace("(limit: 200)", " ").split()]
        assert len(fields) <= 95, (name, len(fields))
    text = " ".join(fetch.ADVERT_QUERIES.values())
    for hidden in ("vehicleCheckId", "reportId", "stockItemId", " registration ", "sellerProducts"):
        assert hidden not in text, hidden


def test_fetch_advert_returns_one_part_per_operation(monkeypatch):
    parts = fixture("advert_dealer.json")
    wire = Wire(monkeypatch, Answer(body=[_data(search={"advert": parts[name]}) for name in fetch.ADVERT_QUERIES]))
    assert fetch.fetch_advert("202611137887719", "SW1A 1AA") == parts
    body = json.loads(wire.sent[0]["body"])
    assert [op["operationName"] for op in body] == list(fetch.ADVERT_QUERIES)
    assert body[0]["variables"] == {"advertId": "202611137887719", "searchOptions": {"postcode": "SW1A 1AA"}}


def test_listing_page(monkeypatch):
    wire = Wire(monkeypatch, Answer(200, raw("listing_page_private.html")))
    assert "__staticRouterHydrationData" in fetch.fetch_listing_page("/car-details/1", "SW1A 1AA")
    assert wire.sent[0]["url"] == "https://www.autotrader.co.uk/car-details/1?postcode=SW1A1AA"
    # a sold advert (or the wrong vehicle type) redirects to the site's "expired ad" form
    Wire(monkeypatch, Answer(301, "", {"location": "https://www.autotrader.co.uk/search-form?expired-ad=true"}))
    assert fetch.fetch_listing_page("/car-details/1") is None
    Wire(monkeypatch, Answer(404, "<title>Sorry - the page you're after doesn't exist</title>"))
    assert fetch.fetch_listing_page("/car-details/abc") is None


def test_listing_page_that_is_a_challenge_or_empty(monkeypatch):
    monkeypatch.setattr(config, "MAX_RETRIES", 1)
    Wire(monkeypatch, Answer(200, "<html><title>Just a moment...</title></html>"))
    with pytest.raises(fetch.AutotraderUkBlocked):
        fetch.fetch_listing_page("/car-details/1")
    Wire(monkeypatch, Answer(200, "<html><title>Autotrader</title></html>"))
    with pytest.raises(fetch.AutotraderUkUpstreamError) as e:
        fetch.fetch_listing_page("/car-details/1")
    assert not isinstance(e.value, Blocked)


def test_review_cursor():
    assert fetch.review_cursor(1) is None and fetch.review_cursor(None) is None
    assert fetch.review_cursor(2) == "cGFnZT0y"       # the cursor the site itself hands out after page 1


# ---- the endpoints -------------------------------------------------------------------

class FakeCache:
    """cache.cached_call's contract in memory: keyed on (namespace, params)."""

    def __init__(self):
        self.rows = {}

    def __call__(self, namespace, data, fn, ttl, cache=True, refresh=False):
        key = (namespace, json.dumps(data, sort_keys=True))
        if key not in self.rows:
            self.rows[key] = fn(**data)
        return self.rows[key]


class Site:
    """The site's surfaces, answering from the fixtures and recording the calls."""

    def __init__(self, monkeypatch):
        self.calls = []
        self.search = fixture("search_cars_bmw_3_series.json")
        self.known_dealers = {"10036877": "BLUEHAWK AUTOMOTIVE LIMITED"}
        self.page_html = raw("listing_page_dealer.html")
        self.reviews = fixture("reviews_10036877.json")
        for name in ("fetch_search", "fetch_facets", "fetch_advert", "fetch_listing_page", "fetch_dealers",
                     "fetch_dealer", "fetch_dealer_name", "fetch_reviews"):
            monkeypatch.setattr(api, name, getattr(self, name))
        self.cache = FakeCache()
        monkeypatch.setattr(api, "cached_call", self.cache)

    def named(self, name):
        return [call for call in self.calls if call[0] == name]

    def fetch_search(self, channel, filters, page=1, sort="relevance"):
        self.calls.append(("search", channel, dict(filters), page, sort))
        return self.search

    def fetch_facets(self, channel, filters):
        self.calls.append(("facets", channel, dict(filters)))
        if channel == "vans":
            return fixture("facets_vans.json")
        if filters.get("make", "").lower() == "bmw":
            answer = fixture("facets_cars_bmw.json")
            if filters.get("model"):
                for facet in answer["facets"]:
                    for item in facet["filters"]:
                        if item["filter"] == "aggregated_trim":
                            item["options"] = [{"label": "M Sport", "value": "M Sport", "count": 4332}]
            return answer
        return fixture("facets_cars.json")

    def fetch_advert(self, advert_id, postcode, advertising_location=None):
        self.calls.append(("advert", advert_id, postcode))
        if advert_id != "202611137887719":
            raise fetch.AutotraderUkNotFound(f"advert {advert_id} not found")
        return fixture("advert_dealer.json")

    def fetch_listing_page(self, path, postcode=None):
        self.calls.append(("page", path, postcode))
        return self.page_html

    def fetch_dealers(self, query, sort=None, page=1):
        self.calls.append(("dealers", dict(query), sort, page))
        return fixture("dealers_search_manchester.json")

    def fetch_dealer(self, dealer_id):
        self.calls.append(("dealer", dealer_id))
        if dealer_id != "9994":
            raise fetch.AutotraderUkNotFound(f"dealer {dealer_id} not found")
        return fixture("dealer_9994.json")

    def fetch_dealer_name(self, dealer_id):
        self.calls.append(("dealer_name", dealer_id))
        if dealer_id not in self.known_dealers:
            raise fetch.AutotraderUkNotFound(f"dealer {dealer_id} not found")
        return self.known_dealers[dealer_id]

    def fetch_reviews(self, dealer_id, page=1, sort="newest-first", rating=None, text=None, period="forever"):
        self.calls.append(("reviews", dealer_id, page, sort, rating, text, period))
        return self.reviews


@pytest.fixture
def site(monkeypatch):
    return Site(monkeypatch)


def _search(**params):
    return api.cars_search(**schemas.CarsSearchSchema().load(params))


def test_search_answer_and_the_filters_it_sends(site):
    answer = _search(make="BMW", min_price="1000", max_price="9000", radius="50", seller_type="dealer",
                     max_insurance_group="20", write_offs="exclude", manufacturer_approved="true",
                     min_engine_size="2", doors="4", keywords="panoramic roof", sort="price_low_to_high", page="2")
    assert list(answer) == ["vehicle_type", "postcode", "results", "is_truncated", "pagination"]
    assert answer["vehicle_type"] == "cars" and answer["postcode"] == "SW1A 1AA" and len(answer["results"]) == 20
    assert answer["pagination"] == {"page": 2, "items_per_page": 20, "total_pages": 100, "total_count": 7347}
    assert answer["is_truncated"] is True                    # 7,347 listings, 2,000 reachable
    (_, channel, filters, page, sort), = site.named("search")
    assert (channel, page, sort) == ("cars", 2, "price_asc")
    assert filters == {
        "postcode": "SW1A 1AA", "price_search_type": "total", "distance": "50", "make": "BMW",
        "keywords": "panoramic roof", "min_price": "1000", "max_price": "9000", "doors_values": "4",
        "seller_type": "trade", "min_engine_size": "2.0", "insurance_group": "20U", "is_writeoff": "exclude",
        "is_manufacturer_approved": "true"}
    assert site.named("facets") == []                        # nothing here needs the site's spellings


def test_search_borrows_the_sites_spellings(site):
    _search(make="bmw", model="3 series", trim="m sport", body_type="suv", fuel_type="petrol plug-in hybrid",
            transmission="AUTOMATIC", colour="black", drivetrain="four wheel drive")
    filters = site.named("search")[0][2]
    assert (filters["model"], filters["aggregated_trim"]) == ("3 Series", "M Sport")
    assert (filters["body_type"], filters["fuel_type"]) == ("SUV", "Petrol Plug-in Hybrid")
    assert (filters["transmission"], filters["colour"], filters["drivetrain"]) == (
        "Automatic", "Black", "Four Wheel Drive")
    # one filter answer per narrowing, however many values were looked up in it:
    # the unfiltered one (five spellings), the make's (its models), the model's (its trims)
    narrowings = sorted((call[2].get("make", ""), call[2].get("model", "")) for call in site.named("facets"))
    assert narrowings == [("", ""), ("bmw", ""), ("bmw", "3 Series")]


def test_an_unknown_text_filter_is_a_400_that_names_the_options(site):
    with pytest.raises(BadRequest) as e:
        _search(body_type="spaceship")
    assert str(e.value).startswith("body_type: 'spaceship' is not one of: Convertible, Coupe, Estate, Hatchback")
    assert site.named("search") == []


def test_an_unknown_model_or_trim_is_searched_as_given(site):
    _search(make="BMW", model="Model Nobody Makes")
    assert site.named("search")[0][2]["model"] == "Model Nobody Makes"


def test_van_spellings_come_from_the_van_filters(site):
    _search(vehicle_type="vans", body_type="panel van")
    assert site.named("search")[0][1] == "vans" and site.named("search")[0][2]["body_type"] == "Panel Van"


def test_monthly_prices_filter_the_monthly_payment(site):
    _search(price_type="monthly", min_price="100", max_price="300")
    filters = site.named("search")[0][2]
    assert filters["price_search_type"] == "monthly"
    assert (filters["min_monthly_price"], filters["max_monthly_price"]) == ("100", "300")
    assert "min_price" not in filters and "max_price" not in filters


def test_a_small_search_counts_its_own_pages(site):
    site.search = {**site.search, "page": {"number": 1, "count": 3, "results": {"count": 41}}}
    answer = _search(make="BMW")
    assert answer["pagination"]["total_pages"] == 3 and answer["is_truncated"] is False
    site.search = fixture("search_no_results.json")
    empty = _search(make="Zzzz")
    assert empty["results"] == [] and empty["pagination"]["total_pages"] == 0
    assert empty["pagination"]["total_count"] == 0


def test_dealer_cars_adds_the_dealer_filter(site):
    site.search = fixture("search_dealer_stock.json")
    data = schemas.DealerCarsSchema().load({"dealer": "10036877", "max_price": "10000"})
    answer = api.dealer_cars(**data)
    assert answer["dealer"] == {"id": "10036877", "link": "https://www.autotrader.co.uk/retailer/10036877"}
    assert len(answer["results"]) == 20 and answer["pagination"]["page"] == 1
    assert site.named("search")[0][2]["retailer_id"] == "10036877"
    assert site.named("dealer_name") == []                   # rows came back: the dealer exists


def test_dealer_cars_tells_no_stock_from_no_dealer(site):
    site.search = fixture("search_no_results.json")
    answer = api.dealer_cars(**schemas.DealerCarsSchema().load({"dealer": "10036877", "max_price": "100"}))
    assert answer["results"] == [] and answer["pagination"]["total_count"] == 0
    with pytest.raises(NotFound) as e:
        api.dealer_cars(**schemas.DealerCarsSchema().load({"dealer": "999999999"}))
    assert str(e.value) == "dealer 999999999 not found"


def test_car_details_merges_the_page(site):
    car = api.car_details(**schemas.CarSchema().load({"car": "202611137887719", "postcode": "m1 1ae"}))
    assert car["id"] == "202611137887719" and car["price"]["good_price_range"] == {"low": 5252, "high": 5520}
    assert site.named("advert") == [("advert", "202611137887719", "M1 1AE")]
    assert site.named("page") == [("page", "/car-details/202611137887719", "M1 1AE")]
    api.car_details(**schemas.CarSchema().load({"car": "202611137887719", "postcode": "m1 1ae"}))
    assert len(site.named("advert")) == 1                    # served from the cache


def test_car_details_without_a_page(site):
    site.page_html = None                                    # the site has the advert but serves no page for it
    car = api.car_details(car="202611137887719", postcode="SW1A 1AA")
    assert car["id"] == "202611137887719" and car["price"]["amount"] == 5495
    assert car["price"]["good_price_range"] == {"low": None, "high": None} and car["highlights"] == []


def test_car_details_of_an_unknown_advert(site):
    with pytest.raises(NotFound) as e:
        api.car_details(car="202306299089853", postcode="SW1A 1AA")
    assert str(e.value) == "advert 202306299089853 not found"
    assert site.named("page") == []


def test_cars_filters(site):
    answer = api.cars_filters(**schemas.CarsFiltersSchema().load({}))
    assert list(answer) == ["vehicle_type", "postcode", "total_count", "filters"]
    assert answer["total_count"] == 481420
    assert all(list(item) == ["name", "is_search_param", "options"] for item in answer["filters"])
    one = api.cars_filters(**schemas.CarsFiltersSchema().load({"make": "BMW", "filter": "Model"}))
    assert [item["name"] for item in one["filters"]] == ["model"] and len(one["filters"][0]["options"]) == 65
    with pytest.raises(BadRequest) as e:
        api.cars_filters(**schemas.CarsFiltersSchema().load({"filter": "zzz"}))
    assert str(e.value).startswith("filter: 'zzz' is not one of: acceleration_values, ")


def test_makes_and_models_slice_the_cached_filters(site):
    makes = api.makes_list(**schemas.MakesListSchema().load({}))
    assert makes["count"] == 145 and makes["results"][0] == {"name": "Abarth", "count": 673}
    models = api.models_list(**schemas.ModelsListSchema().load({"make": "bmw"}))
    assert models["make"] == "BMW" and models["count"] == 65
    assert models["results"][0] == {"name": "1 Series", "count": 7025}
    api.cars_filters(**schemas.CarsFiltersSchema().load({}))
    assert len(site.named("facets")) == 2                    # one unfiltered answer, one for BMW
    with pytest.raises(NotFound) as e:
        api.models_list(make="Zzzz", vehicle_type="cars")
    assert str(e.value) == "make 'Zzzz' not found on Autotrader (see makes/list)"


def test_dealers_search(site):
    data = schemas.DealersSearchSchema().load({"postcode": "m1 1ae", "radius": "10", "make": "land rover"})
    answer = api.dealers_search(**data)
    assert list(answer) == ["vehicle_type", "postcode", "results", "is_truncated", "pagination"]
    assert answer["pagination"] == {"page": 1, "items_per_page": 20, "total_pages": 20, "total_count": 560}
    assert answer["is_truncated"] is True and len(answer["results"]) == 20
    assert site.named("dealers") == [("dealers", {"retailerTypes": ["used_car", "new_car"], "postcode": "M1 1AE",
                                                  "distance": 10, "make": ["LAND ROVER"]}, "DISTANCE", 1)]


def test_dealers_search_without_a_postcode_is_national(site):
    api.dealers_search(**schemas.DealersSearchSchema().load({"name": "Arnold Clark", "vehicle_type": "vans"}))
    assert site.named("dealers") == [("dealers", {"retailerTypes": ["used_van"], "name": "Arnold Clark"}, None, 1)]


def test_dealer_details(site):
    dealer = api.dealer_details(**schemas.DealerSchema().load({"dealer": "9994"}))
    assert dealer["id"] == "9994" and dealer["website"]
    with pytest.raises(NotFound):
        api.dealer_details(dealer="999999999")


def test_dealer_reviews_pages(site):
    data = schemas.DealerReviewsSchema().load({"dealer": "10036877", "rating": "5", "query": "finance", "page": "2"})
    answer = api.dealer_reviews(**data)
    assert list(answer) == ["dealer", "rating", "review_count", "rating_breakdown", "sources", "reviews",
                            "pagination"]
    assert answer["pagination"] == {"page": 2, "items_per_page": 20, "total_pages": 9, "total_count": 169}
    assert len(answer["reviews"]) == 20
    assert site.named("reviews") == [("reviews", "10036877", 2, "newest-first", 5, "finance", "forever")]


def test_a_reviews_page_past_the_end_is_empty(site):
    """The site answers a page past the end with its last page again."""
    answer = api.dealer_reviews(**schemas.DealerReviewsSchema().load({"dealer": "10036877", "page": "10"}))
    assert answer["reviews"] == [] and answer["pagination"]["total_pages"] == 9
    assert answer["review_count"] == 169
