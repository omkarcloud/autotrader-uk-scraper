"""Offline tests for the /autotrader-uk/* request schemas and the id / link
resolvers behind them.

    python -m pytest autotrader_uk/test_schemas.py -q
"""
import os
import sys

import pytest
from marshmallow import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autotrader_uk import refs, schemas  # noqa: E402
from schema_fields import load_query  # noqa: E402


def _errors(schema, params):
    with pytest.raises(ValidationError) as e:
        schema().load(params)
    return e.value.normalized_messages()


# ---- cars/details --------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "202611177994981",
    " 202611177994981 ",
    "https://www.autotrader.co.uk/car-details/202611177994981",
    "https://www.autotrader.co.uk/car-details/202611177994981?sort=relevance&searchId=2ffeccc2&postcode=SW1A1AA",
    "HTTPS://WWW.AUTOTRADER.CO.UK/van-details/202611177994981/",
    "www.autotrader.co.uk/bike-details/202611177994981",
    "autotrader.co.uk/motorhome-details/202611177994981#gallery",
    "https://www.autotrader.co.uk/plant-machinery-details/202611177994981?sort=relevance",
    "https://www.autotrader.co.uk/farm-machinery-details/202611177994981",
    "//www.autotrader.co.uk/classified/advert/202611177994981",
    "https://www.autotrader.co.uk/classified/advert/new/202611177994981",
])
def test_car_accepts_an_id_or_a_link(value):
    assert schemas.CarSchema().load({"car": value}) == {"car": "202611177994981", "postcode": "SW1A 1AA"}


@pytest.mark.parametrize("value, message", [
    ("", "Must not be empty."),
    ("abc", "Must be an advert id (202611177994981) or an autotrader.co.uk listing link."),
    ("12345", "Must be an advert id (202611177994981) or an autotrader.co.uk listing link."),
    ("https://example.com/car-details/202611177994981", "Must be an autotrader.co.uk link."),
    ("https://www.autotrader.com/car-details/202611177994981", "Must be an autotrader.co.uk link."),
    ("https://www.autotrader.co.uk/car-search?make=BMW",
     "Must be an autotrader.co.uk listing link (https://www.autotrader.co.uk/car-details/<id>)."),
])
def test_car_rejects(value, message):
    assert _errors(schemas.CarSchema, {"car": value}) == {"car": [message]}


def test_car_is_required_and_takes_a_postcode():
    assert _errors(schemas.CarSchema, {}) == {"car": ["Missing data for required field."]}
    assert schemas.CarSchema().load({"car": "202611177994981", "postcode": "m11ae"})["postcode"] == "M1 1AE"
    assert _errors(schemas.CarSchema, {"car": "202611177994981", "id": "1"}) == {"id": ["Unknown field."]}


# ---- dealer refs ---------------------------------------------------------------------

@pytest.mark.parametrize("value, dealer", [
    ("10036877", "10036877"),
    ("9994", "9994"),
    ("https://www.autotrader.co.uk/dealers/middlesex/hounslow/bluehawk-automotive-limited-10036877", "10036877"),
    ("https://www.autotrader.co.uk/dealers/middlesex/hounslow/bluehawk-automotive-limited-10036877?channel=cars",
     "10036877"),
    ("https://www.autotrader.co.uk/dealers/lancashire/manchester/williams-landrover-9994/", "9994"),
    ("https://www.autotrader.co.uk/dealers/staffordshire/motors-2000-ltd-10033738", "10033738"),
    ("www.autotrader.co.uk/retailer/10036877", "10036877"),
    ("https://www.autotrader.co.uk/retailer/stock?sort=price-asc&retailerId=10036877", "10036877"),
])
def test_dealer_accepts_an_id_or_a_link(value, dealer):
    assert schemas.DealerSchema().load({"dealer": value}) == {"dealer": dealer}


@pytest.mark.parametrize("value, message", [
    ("", "Must not be empty."),
    ("bluehawk", "Must be a dealer id (10036877) or an autotrader.co.uk dealer link."),
    ("12", "Must be a dealer id (10036877) or an autotrader.co.uk dealer link."),
    ("https://example.com/retailer/10036877", "Must be an autotrader.co.uk link."),
    ("https://www.autotrader.co.uk/cars/dealers/hounslow",
     "Must be an autotrader.co.uk dealer link (https://www.autotrader.co.uk/dealers/<county>/<town>/<name>-<id>)."),
])
def test_dealer_rejects(value, message):
    assert _errors(schemas.DealerSchema, {"dealer": value}) == {"dealer": [message]}


# ---- cars/search ---------------------------------------------------------------------

def test_search_defaults():
    data = schemas.CarsSearchSchema().load({})
    assert data["vehicle_type"] == "cars" and data["postcode"] == "SW1A 1AA"
    assert data["sort"] == "relevance" and data["page"] == 1 and data["price_type"] == "total"
    for name in ("radius", "make", "model", "trim", "keywords", "min_price", "max_price", "min_year", "max_year",
                 "min_mileage", "max_mileage", "body_type", "fuel_type", "transmission", "colour", "doors",
                 "seats", "drivetrain", "seller_type", "min_engine_size", "max_engine_size",
                 "max_insurance_group", "write_offs", "manufacturer_approved"):
        assert data[name] is None, name


def test_search_maps_the_choices_to_the_sites_values():
    data = schemas.CarsSearchSchema().load({
        "vehicle_type": "VANS", "postcode": "sw1a1aa", "radius": "25", "make": " BMW ", "model": "3 Series",
        "trim": "M Sport", "seller_type": "Dealer", "sort": "price_low_to_high", "page": "3",
        "max_insurance_group": "20", "write_offs": "exclude", "manufacturer_approved": "true",
        "min_engine_size": "1.6", "max_engine_size": "2", "doors": "0", "price_type": "Monthly"})
    assert data["vehicle_type"] == "vans" and data["postcode"] == "SW1A 1AA" and data["radius"] == 25
    assert (data["make"], data["model"], data["trim"]) == ("BMW", "3 Series", "M Sport")
    assert data["seller_type"] == "trade" and data["sort"] == "price_asc" and data["page"] == 3
    assert data["max_insurance_group"] == "20U" and data["write_offs"] == "exclude"
    assert data["manufacturer_approved"] is True and data["price_type"] == "monthly"
    assert (data["min_engine_size"], data["max_engine_size"], data["doors"]) == (1.6, 2.0, 0)


@pytest.mark.parametrize("sort, site", list(refs.SORTS.items()))
def test_every_sort(sort, site):
    assert schemas.CarsSearchSchema().load({"sort": sort})["sort"] == site


@pytest.mark.parametrize("params, field, message", [
    ({"postcode": "M1"}, "postcode", "Must be a full UK postcode, e.g. SW1A 1AA."),
    ({"postcode": "12345"}, "postcode", "Must be a full UK postcode, e.g. SW1A 1AA."),
    ({"vehicle_type": "boats"}, "vehicle_type",
     "Must be one of: cars, vans, bikes, motorhomes, caravans, trucks, farm, plant."),
    ({"sort": "cheapest"}, "sort",
     "Must be one of: relevance, price_low_to_high, price_high_to_low, distance, mileage_low_to_high, "
     "year_new_to_old, year_old_to_new, most_recent."),
    ({"page": "0"}, "page", "Must be greater than or equal to 1 and less than or equal to 100."),
    ({"page": "101"}, "page", "Must be greater than or equal to 1 and less than or equal to 100."),
    ({"model": "3 Series"}, "model", "model needs a make."),
    ({"make": "BMW", "trim": "M Sport"}, "trim", "trim needs a model."),
    ({"min_price": "9000", "max_price": "5000"}, "min_price", "min_price must not be greater than max_price."),
    ({"min_year": "2022", "max_year": "2019"}, "min_year", "min_year must not be greater than max_year."),
    ({"min_price": "-1"}, "min_price", "Must be greater than or equal to 0."),
    ({"radius": "0"}, "radius", "Must be greater than or equal to 1 and less than or equal to 1500."),
    ({"seller_type": "auction"}, "seller_type", "Must be one of: dealer, trade, private."),
    ({"max_insurance_group": "15"}, "max_insurance_group", "Must be one of: 3, 5, 10, 20, 30, 40, 50."),
    ({"write_offs": "maybe"}, "write_offs", "Must be one of: exclude, only."),
    ({"manufacturer_approved": "perhaps"}, "manufacturer_approved", "Must be true or false."),
    ({"price_type": "weekly"}, "price_type", "Must be one of: total, monthly."),
    ({"make": "x" * 61}, "make", "Longer than maximum length 60."),
    ({"url": "https://www.autotrader.co.uk/car-search"}, "url", "Unknown field."),
])
def test_search_rejects(params, field, message):
    assert _errors(schemas.CarsSearchSchema, params) == {field: [message]}


def test_empty_filter_values_mean_not_set():
    data = schemas.CarsSearchSchema().load({"make": "", "body_type": " ", "keywords": ""})
    assert data["make"] is None and data["body_type"] is None and data["keywords"] is None


def test_an_empty_value_of_a_param_with_a_default_means_the_default():
    """`?vehicle_type=` is the same as no vehicle_type: the default, never None
    (None reached api.py as a KeyError, and the site refuses a search without
    a postcode)."""
    data = schemas.CarsSearchSchema().load({"vehicle_type": "", "postcode": " ", "sort": "", "price_type": ""})
    assert (data["vehicle_type"], data["postcode"], data["sort"], data["price_type"]) == \
        ("cars", "SW1A 1AA", "relevance", "total")
    assert schemas.CarSchema().load({"car": "202611177994981", "postcode": ""})["postcode"] == "SW1A 1AA"
    assert schemas.MakesListSchema().load({"vehicle_type": ""}) == {"vehicle_type": "cars"}
    reviews = schemas.DealerReviewsSchema().load({"dealer": "9994", "period": "", "sort": ""})
    assert (reviews["period"], reviews["sort"]) == ("forever", "newest-first")
    # a param with no default stays unset
    dealers = schemas.DealersSearchSchema().load({"vehicle_type": "", "postcode": "", "sort": ""})
    assert (dealers["vehicle_type"], dealers["postcode"], dealers["sort"]) == ("cars", None, None)


def test_the_route_layer_names_the_field_in_a_400():
    data, error = load_query(schemas.CarsSearchSchema, {"page": "abc"})
    assert data is None
    assert error["error"] == "Invalid parameters: page: Not a valid integer."
    assert error["errors"] == {"page": ["Not a valid integer."]}


# ---- cars/filters, makes/list, models/list -------------------------------------------

def test_filters_take_the_search_filters_and_one_filter_name():
    data = schemas.CarsFiltersSchema().load({"make": "BMW", "filter": "model", "vehicle_type": "cars"})
    assert (data["make"], data["filter"], data["postcode"]) == ("BMW", "model", "SW1A 1AA")
    assert schemas.CarsFiltersSchema().load({})["filter"] is None
    assert _errors(schemas.CarsFiltersSchema, {"page": "2"}) == {"page": ["Unknown field."]}
    assert _errors(schemas.CarsFiltersSchema, {"sort": "distance"}) == {"sort": ["Unknown field."]}


def test_makes_and_models():
    assert schemas.MakesListSchema().load({}) == {"vehicle_type": "cars"}
    assert schemas.MakesListSchema().load({"vehicle_type": "bikes"}) == {"vehicle_type": "bikes"}
    assert schemas.ModelsListSchema().load({"make": " Land  Rover "}) == {"make": "Land Rover", "vehicle_type": "cars"}
    assert _errors(schemas.ModelsListSchema, {}) == {"make": ["Missing data for required field."]}
    assert _errors(schemas.ModelsListSchema, {"make": ""}) == {"make": ["Must not be empty."]}


# ---- dealers/search ------------------------------------------------------------------

def test_dealers_search_defaults_and_forms():
    assert schemas.DealersSearchSchema().load({}) == {
        "postcode": None, "radius": None, "name": None, "make": None, "vehicle_type": "cars", "sort": None,
        "page": 1}
    data = schemas.DealersSearchSchema().load({"postcode": "m1 1ae", "radius": "10", "make": "bmw",
                                               "sort": "Reviews", "page": "20", "vehicle_type": "vans"})
    assert data == {"postcode": "M1 1AE", "radius": 10, "name": None, "make": "bmw", "vehicle_type": "vans",
                    "sort": "WITH_RETAILER_REVIEWS", "page": 20}
    assert schemas.DealersSearchSchema().load({"name": "Arnold Clark", "sort": "name"})["sort"] == "NAME"


@pytest.mark.parametrize("params, field, message", [
    ({"radius": "10"}, "radius", "radius needs a postcode."),
    ({"sort": "distance"}, "sort", "sort=distance needs a postcode."),
    ({"sort": "rating"}, "sort", "Must be one of: distance, name, reviews."),
    ({"page": "21"}, "page", "Must be greater than or equal to 1 and less than or equal to 20."),
    ({"postcode": "Manchester"}, "postcode", "Must be a full UK postcode, e.g. SW1A 1AA."),
    ({"dealer": "9994"}, "dealer", "Unknown field."),
])
def test_dealers_search_rejects(params, field, message):
    assert _errors(schemas.DealersSearchSchema, params) == {field: [message]}


# ---- dealers/reviews, dealers/cars ---------------------------------------------------

def test_reviews_defaults_and_forms():
    assert schemas.DealerReviewsSchema().load({"dealer": "9994"}) == {
        "dealer": "9994", "rating": None, "query": None, "period": "forever", "sort": "newest-first", "page": 1}
    data = schemas.DealerReviewsSchema().load({
        "dealer": "https://www.autotrader.co.uk/retailer/9994", "rating": "5", "query": " finance ",
        "period": "last_12_months", "sort": "invited_first", "page": "4"})
    assert data == {"dealer": "9994", "rating": 5, "query": "finance", "period": "last-year",
                    "sort": "verified-first", "page": 4}


@pytest.mark.parametrize("params, field, message", [
    ({}, "dealer", "Missing data for required field."),
    ({"dealer": "9994", "rating": "6"}, "rating", "Must be greater than or equal to 1 and less than or equal to 5."),
    ({"dealer": "9994", "sort": "best"}, "sort", "Must be one of: newest, oldest, invited_first."),
    ({"dealer": "9994", "period": "today"}, "period", "Must be one of: all, last_12_months, last_6_months."),
    ({"dealer": "9994", "page": "0"}, "page", "Must be greater than or equal to 1 and less than or equal to 500."),
])
def test_reviews_rejects(params, field, message):
    assert _errors(schemas.DealerReviewsSchema, params) == {field: [message]}


def test_dealer_cars_is_a_search_with_a_dealer():
    data = schemas.DealerCarsSchema().load({"dealer": "10036877", "max_price": "10000", "sort": "most_recent"})
    assert data["dealer"] == "10036877" and data["max_price"] == 10000 and data["sort"] == "most_recent"
    assert data["postcode"] == "SW1A 1AA" and data["page"] == 1 and data["vehicle_type"] == "cars"
    assert _errors(schemas.DealerCarsSchema, {"max_price": "10000"}) == {"dealer": ["Missing data for required field."]}
    assert _errors(schemas.DealerCarsSchema, {"dealer": "10036877", "model": "Golf"}) == {
        "model": ["model needs a make."]}


# ---- refs ----------------------------------------------------------------------------

def test_links_and_vehicle_types():
    assert refs.car_link("1" * 15, "vans") == "https://www.autotrader.co.uk/van-details/" + "1" * 15
    assert refs.car_link("1" * 15, None) == "https://www.autotrader.co.uk/car-details/" + "1" * 15
    assert refs.car_link(None, "cars") is None and refs.car_path("1" * 15, "boats") is None
    assert refs.car_path("1" * 15, "plant") == "/plant-machinery-details/" + "1" * 15
    assert refs.car_path("1" * 15, "farm") == "/farm-machinery-details/" + "1" * 15
    assert refs.vehicle_type_of(None, "https://www.autotrader.co.uk/farm-machinery-details/1") == "farm"
    assert refs.vehicle_type_of("Agricultural") == "farm" and refs.vehicle_type_of("car") == "cars"
    assert refs.vehicle_type_of(None, "https://www.autotrader.co.uk/bike-details/1") == "bikes"
    assert refs.vehicle_type_of("Hovercraft") is None and refs.vehicle_type_of(None) is None
    assert refs.dealer_link("9994", "Williams Landrover", "Manchester", "Lancashire") == \
        "https://www.autotrader.co.uk/dealers/lancashire/manchester/williams-landrover-9994"
    assert refs.dealer_link("9994") == "https://www.autotrader.co.uk/retailer/9994"
    assert refs.dealer_link(None, "x", "y", "z") is None
    assert refs.absolute("/dealers/a/b/c-1?channel=cars") == "https://www.autotrader.co.uk/dealers/a/b/c-1"
    assert refs.absolute(None) is None
    # a dealer link built here resolves back to the same dealer
    assert refs.resolve_dealer(refs.dealer_link("9994", "Williams 4x4 2000", "Manchester", "Lancashire")) == "9994"


def test_every_search_filter_has_a_schema_field():
    declared = schemas.CarsSearchSchema().fields
    assert set(refs.SEARCH_FILTERS) <= set(declared)
    assert set(refs.SPELLED) <= set(refs.SEARCH_FILTERS)
