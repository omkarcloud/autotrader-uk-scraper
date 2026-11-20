"""Marshmallow request schemas for every /autotrader-uk/* route.

ONE param per input (never a sibling `url` / `id` pair):

    car      an advert id (202611177994981) OR any autotrader.co.uk
             listing link (car-details, van-details, bike-details, ...)
    dealer   a dealer id (10036877) OR any autotrader.co.uk dealer link
    make     a make name ("BMW", "Land Rover"); makes/list returns every one
    model    a model name of that make ("3 Series"); models/list returns
             every one

The site wants a postcode on every search (distances are measured from
it): `postcode` defaults to SW1A 1AA, central London, and with no `radius`
the search is national either way.

The choice params hand api.py the site's own value (refs.py tables). The
text filters (body_type, fuel_type, transmission, colour, drivetrain) take
the names cars/filters lists, in any letter case: api.py looks each up in
the site's own option list, so an unknown one is a 400 that names the
options.
"""
import re

from marshmallow import ValidationError, fields, validate, validates_schema

from schema_fields import (BaseSchema, ChoiceField, Flag, PageField, PositiveInt, QueryField, RefField,
                           StrippedString)
from scraper_utils.schema import max_length
from autotrader_uk import refs
from autotrader_uk.fetch import MAX_DEALER_PAGES, MAX_SEARCH_PAGES

NAME_MAX = 60
MAX_REVIEW_PAGE = 500
# A full UK postcode (outward + inward code): the site refuses an outward code alone.
_POSTCODE_RE = re.compile(r"^([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})$")


class CarRefField(RefField):
    resolver = staticmethod(refs.resolve_car)


class DealerRefField(RefField):
    resolver = staticmethod(refs.resolve_dealer)


def _default_of(field):
    """The field's default, for an empty value: StrippedString hands back
    None for `?param=`, where a missing param gets the default."""
    default = field.load_default
    return default if isinstance(default, str) else None


class DefaultedChoice(ChoiceField):
    """A choice whose empty value means its default, as a missing one does."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        return _default_of(self) if value is None else value


class PostcodeField(StrippedString):
    """A full UK postcode in any spacing or letter case -> 'SW1A 1AA'. An
    empty value means the field's default, as a missing one does."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return _default_of(self)
        match = _POSTCODE_RE.match(value.upper().replace(" ", ""))
        if not match:
            raise ValidationError("Must be a full UK postcode, e.g. SW1A 1AA.")
        return f"{match.group(1)} {match.group(2)}"


def _text(description, maximum=NAME_MAX):
    return StrippedString(load_default=None, validate=max_length(maximum), metadata={"description": description})


def _amount(description, maximum=None):
    """An optional whole number >= 0 (pounds, miles)."""
    return fields.Integer(strict=False, load_default=None, validate=validate.Range(min=0, max=maximum),
                          metadata={"description": description})


def _vehicle_type():
    return DefaultedChoice(list(refs.VEHICLE_TYPES), load_default=refs.DEFAULT_VEHICLE_TYPE, metadata={
        "description": "what to look for: cars (default), vans, bikes, motorhomes, caravans, trucks, farm, plant"})


def _postcode(description, default=refs.DEFAULT_POSTCODE):
    return PostcodeField(load_default=default, metadata={"description": description})


class _SearchFilters(BaseSchema):
    """The filters cars/search, cars/filters and dealers/cars share."""
    vehicle_type = _vehicle_type()
    postcode = _postcode("UK postcode distances are measured from (default SW1A 1AA, central London)")
    radius = PositiveInt(max_value=1500, metadata={
        "description": "only listings within this many miles of the postcode (default: the whole UK)"})
    make = _text("make name, e.g. BMW (see makes/list)")
    model = _text("model name of that make, e.g. 3 Series (see models/list); needs make")
    trim = _text("trim of that model, e.g. M Sport; needs model")
    keywords = _text("words to look for in the listing, e.g. panoramic roof", maximum=100)
    price_type = DefaultedChoice(["total", "monthly"], load_default="total", metadata={
        "description": "total (default) searches cash prices; monthly searches only listings with a finance "
                       "quote, returns the quote on each row, and makes min_price / max_price monthly payments"})
    min_price = _amount("lowest price in GBP")
    max_price = _amount("highest price in GBP")
    min_year = PositiveInt(max_value=2100, metadata={"description": "earliest year of manufacture"})
    max_year = PositiveInt(max_value=2100, metadata={"description": "latest year of manufacture"})
    min_mileage = _amount("lowest mileage")
    max_mileage = _amount("highest mileage")
    body_type = _text("body type as cars/filters lists it, e.g. SUV, Hatchback, Estate")
    fuel_type = _text("fuel type as cars/filters lists it, e.g. Petrol, Diesel, Electric, Hybrid")
    transmission = _text("Automatic or Manual")
    colour = _text("colour as cars/filters lists it, e.g. Black, Blue, Grey")
    drivetrain = _text("Four Wheel Drive, Front Wheel Drive or Rear Wheel Drive")
    doors = fields.Integer(strict=False, load_default=None, validate=validate.Range(min=0, max=9),
                           metadata={"description": "number of doors"})
    seats = PositiveInt(max_value=99, metadata={"description": "number of seats"})
    seller_type = ChoiceField(refs.SELLER_TYPES, metadata={"description": "dealer or private"})
    min_engine_size = fields.Float(load_default=None, validate=validate.Range(min=0, max=20),
                                   metadata={"description": "smallest engine in litres, e.g. 1.6"})
    max_engine_size = fields.Float(load_default=None, validate=validate.Range(min=0, max=20),
                                   metadata={"description": "largest engine in litres, e.g. 2.0"})
    max_insurance_group = ChoiceField(refs.INSURANCE_GROUPS, metadata={
        "description": "highest insurance group: 3, 5, 10, 20, 30, 40 or 50"})
    write_offs = ChoiceField(refs.WRITE_OFFS, metadata={
        "description": "exclude leaves out insurance write-offs, only keeps nothing else (default: both)"})
    manufacturer_approved = Flag(metadata={"description": "true keeps only manufacturer approved vehicles"})

    @validates_schema
    def _dependent_filters(self, data, **kwargs):
        if data.get("model") and not data.get("make"):
            raise ValidationError("model needs a make.", "model")
        if data.get("trim") and not data.get("model"):
            raise ValidationError("trim needs a model.", "trim")
        for name in ("price", "year", "mileage", "engine_size"):
            low, high = data.get(f"min_{name}"), data.get(f"max_{name}")
            if low is not None and high is not None and low > high:
                raise ValidationError(f"min_{name} must not be greater than max_{name}.", f"min_{name}")


def _sort():
    return DefaultedChoice(refs.SORTS, load_default=refs.SORTS["relevance"], metadata={
        "description": "result order: relevance (default), price_low_to_high, price_high_to_low, distance, "
                       "mileage_low_to_high, year_new_to_old, year_old_to_new, most_recent"})


def _page():
    return PageField(max_page=MAX_SEARCH_PAGES, metadata={
        "description": f"page number, 20 listings each; Autotrader serves at most {MAX_SEARCH_PAGES} pages "
                       "per search"})


class CarsSearchSchema(_SearchFilters):
    sort = _sort()
    page = _page()


class CarSchema(BaseSchema):
    car = CarRefField(metadata={
        "description": "advert id (e.g. 202611177994981), or an autotrader.co.uk listing link"})
    postcode = _postcode("UK postcode the seller's distance and delivery are worked out for "
                         "(default SW1A 1AA, central London)")


class CarsFiltersSchema(_SearchFilters):
    filter = _text("return only this filter, e.g. body_type (default: every filter)")


class MakesListSchema(BaseSchema):
    vehicle_type = _vehicle_type()


class ModelsListSchema(BaseSchema):
    make = QueryField(max_length=NAME_MAX, metadata={"description": "make name, e.g. BMW (see makes/list)"})
    vehicle_type = _vehicle_type()


class DealersSearchSchema(BaseSchema):
    postcode = _postcode("UK postcode to look around; without one the search is national", default=None)
    radius = PositiveInt(max_value=1500, metadata={
        "description": "only dealers within this many miles of the postcode; needs postcode"})
    name = _text("dealer name or part of it, e.g. Arnold Clark")
    make = _text("only dealers that sell this make, e.g. BMW")
    vehicle_type = _vehicle_type()
    sort = ChoiceField(refs.DEALER_SORTS, metadata={
        "description": "distance (default with a postcode; needs postcode), name or reviews"})
    page = PageField(max_page=MAX_DEALER_PAGES, metadata={
        "description": f"page number, 20 dealers each; Autotrader serves at most {MAX_DEALER_PAGES} pages "
                       "per search"})

    @validates_schema
    def _needs_postcode(self, data, **kwargs):
        if data.get("radius") and not data.get("postcode"):
            raise ValidationError("radius needs a postcode.", "radius")
        if data.get("sort") == refs.DEALER_SORTS["distance"] and not data.get("postcode"):
            raise ValidationError("sort=distance needs a postcode.", "sort")


def _dealer():
    return DealerRefField(metadata={
        "description": "dealer id (e.g. 10036877), or an autotrader.co.uk dealer link"})


class DealerSchema(BaseSchema):
    dealer = _dealer()


class DealerReviewsSchema(BaseSchema):
    dealer = _dealer()
    rating = PositiveInt(max_value=5, metadata={"description": "only reviews with this many stars, 1 to 5"})
    query = _text("only reviews that mention these words", maximum=100)
    period = DefaultedChoice(refs.REVIEW_PERIODS, load_default=refs.REVIEW_PERIODS["all"], metadata={
        "description": "all (default), last_12_months or last_6_months"})
    sort = DefaultedChoice(refs.REVIEW_SORTS, load_default=refs.REVIEW_SORTS["newest"], metadata={
        "description": "newest (default), oldest or invited_first (reviews from invited customers first)"})
    page = PageField(max_page=MAX_REVIEW_PAGE, metadata={"description": "page number, 20 reviews each"})


class DealerCarsSchema(_SearchFilters):
    dealer = _dealer()
    sort = _sort()
    page = _page()
