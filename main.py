"""Use the scraper straight from Python, no server needed.

    python main.py

Each call validates its params with the same schema the API uses (so the
defaults apply) and returns the same JSON the API does, except that a list
keeps its `pagination` block instead of next / previous links. The results
are written to output/*.json.
"""
import json
import os

from autotrader_uk import api, schemas
from schema_fields import load_query

os.makedirs("output", exist_ok=True)


def run(schema_cls, impl, **params):
    data, error = load_query(schema_cls, {k: str(v) for k, v in params.items()})
    if error:
        raise ValueError(error)
    return impl(**data)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # an advert id or any autotrader.co.uk listing link: price, specs, features, history check, seller, every photo
    save("cars_details.json", run(schemas.CarSchema, api.car_details, car='202511087749528'))

    # 20 vehicles per page with the search's total; filter by make, model, price, year, mileage, fuel and more
    save("cars_search.json", run(schemas.CarsSearchSchema, api.cars_search))

    # every make with its live number of vehicles for sale
    save("makes_list.json", run(schemas.MakesListSchema, api.makes_list))
