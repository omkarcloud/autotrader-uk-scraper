"""Offline tests for autotrader_uk/parsers.py: every parser against every
fixture (a full answer, a sparse one, and what a missing page or an empty
answer becomes).

    python -m pytest autotrader_uk/test_parsers.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autotrader_uk import parsers  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def raw(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


def fixture(name):
    return json.loads(raw(name))


def keys_of(value, prefix=""):
    """Every key path of a nested record (lists by their first item)."""
    out = set()
    if isinstance(value, dict):
        for key, item in value.items():
            out.add(prefix + key)
            out |= keys_of(item, prefix + key + ".")
    elif isinstance(value, list) and value:
        out |= keys_of(value[0], prefix)
    return out


# ---- small readers -------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("£5,252", 5252), ("£21,222.92", 21222.92), (5495, 5495), ("1000", 1000), ("Free", None), (None, None),
])
def test_money(value, expected):
    assert parsers.money(value) == expected


def test_image_links_are_full_size():
    assert parsers.image("https://m.atcdn.co.uk/a/media/{resize}/abc.jpg") == "https://m.atcdn.co.uk/a/media/abc.jpg"
    assert parsers.image("https://m.atcdn.co.uk/a/media/abc.jpg") == "https://m.atcdn.co.uk/a/media/abc.jpg"
    assert parsers.image("") is None and parsers.image(None) is None


# ---- search --------------------------------------------------------------------------

def test_search_page_of_cars():
    page = parsers.parse_search(fixture("search_cars_bmw_3_series.json"), "cars")
    assert page["total_count"] == 7347 and page["site_total_pages"] == 368
    rows = page["results"]
    assert len(rows) == 20 and len({row["id"] for row in rows}) == 20
    first = rows[0]
    assert first["id"] == "202611137887719"
    assert first["link"] == "https://www.autotrader.co.uk/car-details/202611137887719"
    assert (first["title"], first["subtitle"]) == ("BMW 3 Series", "2.0 320d M Sport Euro 5 (s/s) 4dr")
    assert (first["make"], first["model"], first["year"], first["plate"]) == ("BMW", "3 Series", 2013, "63")
    assert (first["mileage"], first["mileage_unit"], first["condition"]) == (116911, "miles", "used")
    assert first["price"] == {"amount": 5495, "currency": "GBP", "vat_status": None, "rrp": None,
                              "discount": None, "rating": "good"}
    assert first["images"][0] == "https://m.atcdn.co.uk/a/media/436dd9a4e21a4c6dac697ecd5d48a289.jpg"
    assert first["image_count"] == 21 and len(first["images"]) == 4
    assert first["location"] == {"town": "Newcastle Under Lyme", "distance_miles": 138, "type": "standard"}
    assert first["seller"] == {
        "type": "dealer", "id": "10033738", "rating": 4.7,
        "link": "https://www.autotrader.co.uk/dealers/staffordshire/newcastle-under-lyme/sre-motors-ltd-10033738"}
    assert first["finance"] is None and first["can_reserve_online"] is True and first["has_finance"] is True


def test_every_search_row_has_the_same_keys():
    for name, kind in (("search_cars_bmw_3_series.json", "cars"), ("search_vans.json", "vans"),
                       ("search_monthly.json", "cars"), ("search_dealer_stock.json", "cars")):
        rows = parsers.parse_search(fixture(name), kind)["results"]
        assert len(rows) == 20, name
        assert len({tuple(row) for row in rows}) == 1, name
        assert all(row["id"] and row["link"] and row["price"]["amount"] is not None for row in rows), name


def test_private_seller_row_has_no_dealer():
    rows = parsers.parse_search(fixture("search_cars_bmw_3_series.json"), "cars")["results"]
    private = [row for row in rows if row["seller"]["type"] == "private"]
    assert private
    assert all(row["seller"] == {"type": "private", "id": None, "link": None, "rating": None} for row in private)
    assert all(row["price"]["rating"] is None for row in private)      # the site rates dealer prices only


def test_van_rows_link_to_the_van_page_and_carry_the_vat_note():
    rows = parsers.parse_search(fixture("search_vans.json"), "vans")["results"]
    assert rows[0]["link"].startswith("https://www.autotrader.co.uk/van-details/")
    assert rows[0]["price"]["amount"] == 1000 and rows[0]["price"]["vat_status"] == "inc_vat"
    assert {row["price"]["vat_status"] for row in rows} <= {"inc_vat", "no_vat", "plus_vat", None}


def test_monthly_search_rows_carry_the_finance_quote():
    rows = parsers.parse_search(fixture("search_monthly.json"), "cars")["results"]
    quote = rows[0]["finance"]
    assert quote["type"] == "HP" and quote["monthly_payment"] == 422 and quote["currency"] == "GBP"
    assert (quote["deposit"], quote["term_months"], quote["annual_mileage"]) == (1000, 48, 20000)
    example = quote["representative_example"]
    assert example["total_amount_payable"] == 21222.92 and example["representative_apr"] == 10.9
    assert example["finance_type"] == "HP" and example["term"] == 48
    assert all(row["finance"] for row in rows)


def test_dealer_stock_rows_are_the_dealers():
    rows = parsers.parse_search(fixture("search_dealer_stock.json"), "cars")["results"]
    assert {row["seller"]["id"] for row in rows} == {"10036877"}


def test_search_without_results_and_junk():
    assert parsers.parse_search(fixture("search_no_results.json"), "cars") == {
        "results": [], "total_count": 0, "site_total_pages": 1}
    for junk in (None, {}, [], {"listings": [None, {"type": "GPT_LISTING"}, "x"]}):
        assert parsers.parse_search(junk)["results"] == []
    assert parsers.parse_card({"advertId": "1"})["price"]["amount"] is None      # a bare card never raises


# ---- filters -------------------------------------------------------------------------

def test_car_filters():
    facets = parsers.parse_facets(fixture("facets_cars.json"))
    assert facets["total_count"] == 481420
    by_name = {item["name"]: item for item in facets["filters"]}
    for name in ("make", "model", "trim", "body_type", "fuel_type", "transmission", "colour", "doors", "seats",
                 "drivetrain", "seller_type", "radius", "min_price", "max_price", "min_year", "max_year",
                 "min_mileage", "max_mileage", "min_engine_size", "max_engine_size", "max_insurance_group",
                 "write_offs", "manufacturer_approved"):
        assert by_name[name]["is_search_param"] is True, name
    assert "postcode" not in by_name and "price_search_type" not in by_name and "keywords" not in by_name
    assert by_name["acceleration_values"]["is_search_param"] is False       # listed by the site, no search param
    assert by_name["make"]["options"][0] == {"label": "Abarth", "value": "Abarth", "count": 673}
    assert len(by_name["make"]["options"]) == 145
    assert [o["value"] for o in by_name["transmission"]["options"]] == ["Automatic", "Manual"]
    # values come back the way cars/search takes them
    assert [o["value"] for o in by_name["max_insurance_group"]["options"]] == ["3", "5", "10", "20", "30", "40", "50"]
    assert [o["value"] for o in by_name["seller_type"]["options"]] == ["private", "dealer"]
    assert by_name["model"]["options"] == []          # models need a make


def test_filters_narrowed_by_make_list_its_models():
    facets = parsers.parse_facets(fixture("facets_cars_bmw.json"))
    models = parsers.named_counts(facets, "model")
    assert models[0] == {"name": "1 Series", "count": 7025} and len(models) == 65
    assert parsers.named_counts(facets, "make")[0]["name"] == "Abarth"
    assert parsers.named_counts(facets, "nope") == []


def test_van_filters_have_van_body_types():
    raw_facets = fixture("facets_vans.json")
    assert "Panel Van" in parsers.facet_values(raw_facets, "body_type")
    assert parsers.facet_values(raw_facets, "nope") == []
    assert parsers.parse_facets(raw_facets)["total_count"] > 0


def test_filters_of_junk():
    for junk in (None, {}, {"facets": [None, {"filters": [None, {"filter": None}]}]}):
        assert parsers.parse_facets(junk) == {"total_count": None, "filters": []}


# ---- the listing page ----------------------------------------------------------------

def test_listing_page_record():
    page = parsers.parse_listing_page(raw("listing_page_dealer.html"))
    assert page["advertData"]["id"] == "202611137887719"
    assert page["advert"]["details"]["pricing"]["marketPriceRating"]["goodPriceLowerBound"] == "£5,252"


def test_listing_page_without_data():
    assert parsers.parse_listing_page(None) is None
    assert parsers.parse_listing_page("") is None
    assert parsers.parse_listing_page("<html><title>Sorry - the page you're after doesn't exist</title></html>") is None
    broken = 'window.__staticRouterHydrationData = JSON.parse("{not json");'
    assert parsers.parse_listing_page(broken) is None
    other = 'window.__staticRouterHydrationData = JSON.parse("{\\"loaderData\\":{}}");'
    assert parsers.parse_listing_page(other) is None            # a page of the app that is not a listing


def test_listing_page_whose_text_holds_the_closing_characters():
    """A seller's text with `");` in it is not the end of the page's JSON."""
    import json
    record = {"advert": {"description": 'Ask for "Dave"); viewing by appointment'}}
    inner = json.dumps({"loaderData": {"routes/listing": record}})
    html = f"<script>window.__staticRouterHydrationData = JSON.parse({json.dumps(inner)});</script>"
    assert parsers.parse_listing_page(html) == record


def test_seller_listed_features_are_not_repeated_by_the_page():
    """The page's extras repeat the listing's own features, at times with stray spaces."""
    parts = {"AdvertCore": {"id": "202611177994981", "combinedFeatures": ["Heated  Seats ", "Tow Bar"]}}
    page = {"advert": {"details": {"advertiserProvidedExtras": ["Heated Seats", "Roof Rails"]}}}
    assert parsers.parse_advert(parts, page)["features"]["seller_listed"] == ["Heated Seats", "Tow Bar", "Roof Rails"]


# ---- one listing ---------------------------------------------------------------------

def _dealer_car():
    return parsers.parse_advert(fixture("advert_dealer.json"),
                                parsers.parse_listing_page(raw("listing_page_dealer.html")), "SW1A 1AA")


def test_dealer_listing():
    car = _dealer_car()
    assert car["id"] == "202611137887719" and car["vehicle_type"] == "cars"
    assert car["link"] == "https://www.autotrader.co.uk/car-details/202611137887719"
    assert (car["make"], car["model"], car["trim"]) == ("BMW", "3 Series", "M Sport")
    assert car["derivative"] == "2.0 320d M Sport Euro 5 (s/s) 4dr"
    assert car["generation"] == {"id": "6e1cf47f7c03b2048932dc44c079ab4b", "name": "Saloon (2011 - 2015)"}
    assert (car["body_type"], car["fuel_type"], car["transmission"], car["drivetrain"]) == (
        "Saloon", "Diesel", "Manual", "Rear Wheel Drive")
    assert (car["year"], car["plate"], car["registration_date"]) == (2013, "63", "2013-11-25")
    assert (car["mileage"], car["mileage_unit"], car["mileage_vs_average"]) == (116911, "miles", 13719)
    assert (car["owners"], car["keys"], car["mot_expiry_date"]) == (5, 1, "2027-02-14")
    assert (car["condition"], car["stock_type"], car["status"]) == ("used", "physical_stock", "forecourt")
    assert car["stock_label"] == "Available to reserve"
    assert car["engine"] == {"size_litres": 2, "size_cc": 1995, "badge_size_litres": 2, "power": 181,
                             "power_unit": "bhp", "torque_lb_ft": 280, "cylinders": 4, "valves": 16,
                             "gears": None, "gearbox": "Manual 6Spd"}
    assert car["performance"] == {"top_speed_mph": 146, "zero_to_62_mph_seconds": 7.5,
                                  "zero_to_60_mph_seconds": None}
    assert car["running_costs"] == {
        "co2_emissions_g_km": 120, "emission_class": "Euro 5", "annual_tax": 35, "insurance_group": "32D",
        "fuel_consumption_mpg": {"combined": 61.4, "urban": 48.7, "extra_urban": 74.3}, "fuel_tank_litres": 57}
    assert car["dimensions"]["length_mm"] == 4624 and car["dimensions"]["boot_litres"] == 480
    assert car["electric"] is None and car["condition_scores"] is None
    assert len(car["features"]["standard"]) == 114 and len(car["features"]["optional"]) == 10
    assert car["expert_review"]["rating"] == 4.6
    assert car["expert_review"]["link"].startswith("https://www.autotrader.co.uk/content/car-reviews/")


def test_dealer_listing_price_takes_the_range_from_the_page():
    price = _dealer_car()["price"]
    assert price["amount"] == 5495 and price["currency"] == "GBP"
    assert (price["rrp"], price["saving"], price["has_no_admin_fees"]) == (30630, 25135, True)
    assert (price["rating"], price["rating_label"], price["vs_market_average"]) == ("good", "Good price", 136)
    assert price["market_comparison"] == "Close to market average"
    assert price["good_price_range"] == {"low": 5252, "high": 5520}


def test_dealer_listing_seller():
    seller = _dealer_car()["seller"]
    assert (seller["type"], seller["id"], seller["name"]) == ("dealer", "10033738", "SRE Motors Ltd")
    assert seller["link"] == \
        "https://www.autotrader.co.uk/dealers/staffordshire/newcastle-under-lyme/sre-motors-ltd-10033738"
    assert seller["website"] == "https://www.sremotors.com" and seller["phone"] == "(07822) 001591"
    assert seller["email"] and seller["accepts_email"] is True and seller["is_phone_protected"] is False
    assert seller["address"]["postcode"] == "ST5 9JJ" and seller["address"]["town"] == "Newcastle-Under-Lyme"
    assert seller["coordinates"] == {"latitude": 53.0210597, "longitude": -2.2313701}
    assert (seller["rating"], seller["review_count"], seller["distance_miles"]) == (4.7, 39, 138)
    assert seller["segment"] == "independent" and seller["fca_number"] == "1024055"
    assert seller["opening_hours"][0] == {"day": "Monday", "hours": "09:00 - 17:00"}
    assert (seller["has_home_delivery"], seller["has_click_and_collect"], seller["has_safe_selling"]) == (
        True, False, False)


def test_dealer_listing_images_history_finance_delivery():
    car = _dealer_car()
    assert car["image_count"] == 21 and len(car["images"]) == 21
    assert car["images"][0] == {"link": "https://m.atcdn.co.uk/a/media/436dd9a4e21a4c6dac697ecd5d48a289.jpg",
                                "label": "Front Right", "category": "Exterior"}
    check = car["history_check"]
    assert (check["status"], check["type"], check["performed_date"]) == ("passed", "full", "2026-11-18")
    assert (check["passed_count"], check["failed_count"], check["warning_count"]) == (10, 0, 0)
    assert check["checks"][0] == {"check": "stolen", "label": "Not recorded as stolen", "is_failed": False,
                                  "is_warning": False}
    assert [h["title"] for h in car["highlights"]] == ["3 rare features", "Better fuel economy", "Bigger boot"]
    finance = car["finance"]
    assert finance["is_available"] is True and finance["provider"] == "dealer"
    assert (finance["default_deposit"], finance["default_term_months"], finance["default_annual_mileage"]) == (
        1000, 48, 10000)
    quote = finance["quotes"][0]
    assert (quote["type"], quote["monthly_payment"], quote["deposit"], quote["term_months"]) == ("HP", 131.76, 550, 48)
    assert (quote["apr"], quote["total_amount_payable"], quote["total_credit"]) == (13.4, 6875.48, 4945)
    delivery = car["delivery"]
    assert delivery["is_home_delivery_available"] is True and delivery["delivers_to_postcode"] is True
    assert delivery["cost"] == "£1.75 per mile" and delivery["postcode"] == "SW1A 1AA"
    assert "SW" in delivery["region_codes"] and delivery["has_click_and_collect"] is True
    assert car["reservation"] == {"can_reserve_online": True, "has_seller_promise": None,
                                  "eligibility": "eligible", "status": None}


def test_the_hidden_report_never_leaves():
    """The page hides the number plate and VIN of the history report; the
    record carries neither, nor the id that would fetch them."""
    text = json.dumps(_dealer_car()).lower()
    for word in ("vehiclecheckid", "vehicle_check_id", "report_id", "reportid", "vin", "stock_item_id"):
        assert f'"{word}"' not in text
    assert "registration" not in [key.split(".")[-1] for key in keys_of(_dealer_car())]


def test_private_seller_listing_is_sparse_and_keeps_its_shape():
    page = parsers.parse_listing_page(raw("listing_page_private.html"))
    car = parsers.parse_advert(fixture("advert_private.json"), page, "SW1A 1AA")
    seller = car["seller"]
    assert (seller["type"], seller["id"], seller["name"], seller["link"]) == ("private", None, None, None)
    assert seller["website"] is None and seller["email"] is None and seller["coordinates"] is None
    assert seller["phone"] == "(020) 80337009" and seller["is_phone_protected"] is True
    assert seller["address"]["town"] == "STEPNEY" and seller["address"]["postcode"] is None
    assert car["price"]["amount"] == 1200 and car["price"]["rating"] is None
    assert car["price"]["good_price_range"] == {"low": None, "high": None}
    assert car["history_check"]["type"] == "basic" and car["highlights"] == []
    assert car["finance"]["quotes"] == [] and car["delivery"]["is_home_delivery_available"] is None
    assert keys_of(car) >= {k for k in keys_of(_dealer_car()) if not k.startswith(("highlights.", "finance.quotes.",
                                                                                 "seller.opening_hours.",
                                                                                 "delivery.", "seller.coordinates.",
                                                                                 "seller.awards."))}


def test_listing_without_its_page_keeps_every_key():
    with_page = _dealer_car()
    alone = parsers.parse_advert(fixture("advert_dealer.json"))
    assert set(alone) == set(with_page)
    assert alone["price"]["good_price_range"] == {"low": None, "high": None}
    assert alone["highlights"] == [] and alone["finance"]["quotes"] == [] and alone["stock_label"] is None
    assert alone["images"][0]["label"] is None and len(alone["images"]) == 21
    assert alone["delivery"]["is_home_delivery_available"] is True       # the site still lists delivery regions


def test_electric_listing():
    car = parsers.parse_advert(fixture("advert_electric.json"))
    assert car["fuel_type"] == "Electric"
    assert car["electric"] == {"battery_capacity_kwh": 81.5, "usable_battery_capacity_kwh": 77.5,
                               "battery_range_miles": 305, "total_range_miles": None,
                               "charge_time": "12h / 0 - 100%", "quick_charge_time": "1h20m / 0 - 80%"}
    assert car["condition_scores"] == {"body": 95, "interior": 95, "tyres": 95}
    assert car["video_link"].startswith("https://youtube.com/embed/")
    assert car["engine"]["size_cc"] is None and car["engine"]["cylinders"] is None
    assert car["service_history"] == "Full service history"


def test_van_listing():
    van = parsers.parse_advert(fixture("advert_van.json"))
    assert van["vehicle_type"] == "vans" and van["link"].startswith("https://www.autotrader.co.uk/van-details/")
    assert van["body_type"] == "Panel Van" and van["price"]["vat_status"] == "inc_vat"
    assert van["specification"]["cab_type"] == "Single Cab" and van["specification"]["wheelbase"] == "SWB"


def test_listing_of_nothing_never_raises():
    for junk in (None, {}, {"AdvertCore": None, "AdvertSpec": {"specification": None}}, {"AdvertCore": []}):
        car = parsers.parse_advert(junk)
        assert car["id"] is None and car["link"] is None and car["images"] == []
        assert set(car) == set(_dealer_car())


# ---- dealers -------------------------------------------------------------------------

def test_dealer_search_rows():
    page = parsers.parse_dealers(fixture("dealers_search_manchester.json"))
    assert page["total_count"] == 560 and len(page["results"]) == 20
    assert len({tuple(row) for row in page["results"]}) == 1
    first = page["results"][0]
    assert (first["id"], first["name"], first["legal_name"]) == ("10023544", "Bespoke20", "Bespoke20 Limited")
    assert first["link"] == "https://www.autotrader.co.uk/dealers/lancashire/manchester/bespoke20-10023544"
    assert first["website"] == "https://www.bespoke20.co.uk/" and first["email"] == "info@bespoke20.co.uk"
    assert first["phone"] == "(0161) 506 5732" and first["accepts_email"] is True
    assert (first["rating"], first["review_count"], first["distance_miles"]) == (5.0, 77, 1)
    assert first["awards"] == [{"year": 2026, "category": "highly_rated"}]
    assert first["forecourt_stock"]["cars"] == 13 and first["advertised_stock"]["cars"] == 52
    assert first["matching_stock_count"] == 13 and first["fca_number"] == "973957"
    # the contact fields end together: the website's own contacts go right after them
    keys = list(first)
    assert keys[keys.index("website"):keys.index("address")] == [
        "website", "email", "accepts_email", "phone", "phone_2", "is_phone_protected"]


def test_dealer_search_without_results_and_junk():
    assert parsers.parse_dealers(fixture("dealers_search_empty.json")) == {"results": [], "total_count": 0}
    assert parsers.parse_dealers(None) == {"results": [], "total_count": None}
    assert parsers.parse_dealers({"dealers": [None, {}, {"name": "no id"}]})["results"] == []


def test_franchise_dealer():
    dealer = parsers.parse_dealer(fixture("dealer_9994.json"))
    assert (dealer["id"], dealer["name"]) == ("9994", "Williams Landrover")
    assert dealer["link"] == "https://www.autotrader.co.uk/dealers/lancashire/manchester/williams-landrover-9994"
    assert dealer["legal_name"] == "WILLIAMS MOTOR CO. LIMITED T/A Williams Landrover"
    assert dealer["description"].startswith("The Williams Group is a family owned business")
    assert dealer["website"] == "https://www.williamsgroup.co.uk/landrover/contact-us/manchester/"
    assert dealer["email"] == "jenny.brayton@williamsgroup.co.uk" and dealer["phone"] == "(0161) 937 6765"
    assert dealer["address"] == {"line_1": "24 BARTON DOCK ROAD", "line_2": None, "town": "Manchester",
                                 "county": "Lancashire", "region": "North West", "postcode": "M41 7GF",
                                 "country": "GB"}
    assert dealer["coordinates"] == {"latitude": 53.4647775, "longitude": -2.3388703}
    assert (dealer["rating"], dealer["review_count"]) == (1.0, 10)
    assert dealer["rating_breakdown"] == {"5": 8, "4": 0, "3": 0, "2": 0, "1": 2}
    assert [s["source"] for s in dealer["review_sources"]] == ["autotrader", "feefo_partnership"]
    assert dealer["franchises"] == ["LAND ROVER"] and dealer["new_car_makes"] == ["LAND ROVER"]
    assert dealer["forecourt_stock"]["cars"] == 137 and dealer["advertised_stock"]["vans"] == 11
    assert dealer["opening_hours"][0] == {"day": "Monday", "opens": "08:00", "closes": "18:00",
                                          "valid_from": None, "valid_through": None}
    assert [row["day"] for row in dealer["opening_hours"]] == [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    assert "Home delivery" in dealer["business_services"] and "MOT" in dealer["on_site_services"]
    assert dealer["highlights"][0] == {"category": "Family owned business",
                                      "title": "Family Business for 110 years", "description": None}
    assert dealer["manufacturers"][0]["name"] == "LAND ROVER" and dealer["has_live_chat"] is True
    assert dealer["photos"] and all("{resize}" not in link for link in dealer["photos"])
    assert dealer["faqs"][0]["question"] == "Where are Williams Landrover based?"
    assert "distance_miles" not in dealer and "matching_stock_count" not in dealer
    keys = list(dealer)
    assert keys[keys.index("website"):keys.index("address")] == [
        "website", "email", "accepts_email", "phone", "phone_2", "is_phone_protected", "assigned_phone"]


def test_independent_dealer_is_sparse_and_keeps_its_shape():
    dealer = parsers.parse_dealer(fixture("dealer_10036877.json"))
    assert dealer["opening_hours"] == [] and dealer["is_appointment_only"] is True
    assert dealer["franchises"] == [] and dealer["manufacturers"] == []
    assert [p["name"] for p in dealer["partners"]][0] == "MotoNovo Finance"
    assert dealer["can_reserve_online"] is True and dealer["has_live_chat"] is None
    assert set(dealer) == set(parsers.parse_dealer(fixture("dealer_9994.json")))


def test_dealer_without_profile_or_rating():
    raw_dealer = fixture("dealer_9994.json")
    bare = parsers.parse_dealer({"dealer": raw_dealer["dealer"], "profile": None, "rating": None})
    assert set(bare) == set(parsers.parse_dealer(raw_dealer))
    assert bare["opening_hours"] == [] and bare["rating_breakdown"] is None and bare["review_sources"] == []
    assert (bare["rating"], bare["review_count"]) == (1.0, 10)      # the dealer record's own figures
    assert parsers.parse_dealer({}) is None and parsers.parse_dealer(None) is None


# ---- reviews -------------------------------------------------------------------------

def test_reviews_page():
    page = parsers.parse_reviews(fixture("reviews_10036877.json"))
    assert page["dealer"] == {
        "id": "10036877", "name": "BLUEHAWK AUTOMOTIVE LIMITED",
        "link": "https://www.autotrader.co.uk/dealers/middlesex/hounslow/bluehawk-automotive-limited-10036877"}
    assert (page["rating"], page["review_count"], page["matching_count"]) == (4.2, 169, 169)
    assert page["rating_breakdown"] == {"5": 148, "4": 2, "3": 1, "2": 0, "1": 18}
    assert page["sources"] == [{"source": "feefo_partnership", "rating": 4.2, "review_count": 169, "link": None}]
    assert len(page["reviews"]) == 20
    first = page["reviews"][0]
    assert first["title"] == "Honest and genuine dealer." and first["rating"] == 5
    assert first["reviewer_name"] == "Anonymous" and first["source"] == "feefo_partnership"
    assert (first["created_at"], first["updated_at"]) == ("2026-11-12T21:32:19Z", "2026-11-12T21:35:37Z")
    assert first["is_verified"] is False and first["reply"] is None
    assert len({tuple(review) for review in page["reviews"]}) == 1


def test_second_reviews_page_differs():
    first = parsers.parse_reviews(fixture("reviews_10036877.json"))["reviews"]
    second = parsers.parse_reviews(fixture("reviews_10036877_page_2.json"))["reviews"]
    assert len(second) == 20
    assert not {(r["created_at"], r["title"]) for r in first} & {(r["created_at"], r["title"]) for r in second}


def test_review_reply():
    review = parsers.parse_review({"title": "t", "text": "a\n\nb", "overallRating": 1, "created": "2026-01-02T03:04:05.678Z",
                                   "reply": {"replyText": " Sorry to hear that ", "updated": "2026-01-03T00:00:00.000Z"}})
    assert review["text"] == "a\n\nb"
    assert review["reply"] == {"text": "Sorry to hear that", "updated_at": "2026-01-03T00:00:00Z"}
    assert parsers.parse_review(None) is None


def test_reviews_that_match_nothing_and_junk():
    page = parsers.parse_reviews(fixture("reviews_none.json"))
    assert page["reviews"] == [] and page["matching_count"] == 0 and page["dealer"]["id"] == "10036877"
    empty = parsers.parse_reviews(None)
    assert empty["reviews"] == [] and empty["dealer"] == {"id": None, "name": None, "link": None}
