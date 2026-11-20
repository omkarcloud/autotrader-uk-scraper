# 🚀 Autotrader UK Scraper

Autotrader UK Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Autotrader UK data for free.

## ✨ What Can I Get?

- 🚗 **Full details on 470K+ cars for sale** — price, specs, features, history check, finance, delivery, seller phone & every photo
- 🔍 **Search 580K+ cars, vans, bikes, motorhomes & more with 25 filters** — make, model, price, year, mileage, fuel, body type, colour & 8 sort orders
- 🏪 **12K+ dealers with full profiles** — phone, email, website, address, rating, opening hours, stock & every vehicle they sell
- ⭐ **Every dealer review, with the star breakdown** — stars, title, text, reviewer & source, filtered by stars, words or period

## 🚗 Example: A Full Autotrader UK Listing

```json
{
  "id": "202511087749528",
  "link": "https://www.autotrader.co.uk/car-details/202511087749528",
  "title": "Porsche 911 4.0 992 GT3 Coupe 2dr Petrol Manual Euro 6 (510 ps)",
  "make": "Porsche",
  "model": "911",
  "trim": "992 GT3",
  "year": 2022,
  "mileage": 5874,
  "price": { "amount": 174950, "currency": "GBP", "has_no_admin_fees": true },
  "body_type": "Coupe",
  "fuel_type": "Petrol",
  "transmission": "Manual",
  "colour": "Black",
  "engine": { "size_litres": 4, "power": 503, "power_unit": "bhp", "torque_lb_ft": 347, "cylinders": 6, "gearbox": "Manual 6Spd" },
  "performance": { "top_speed_mph": 199, "zero_to_62_mph_seconds": 3.9 },
  "highlights": [
    { "title": "Lower mileage", "badge": "Lowest 25%" },
    { "title": "Faster acceleration", "badge": "Top 25%" }
  ],
  "history_check": { "status": "passed", "summary": "All checks passed", "performed_date": "2026-03-16" },
  "expert_review": { "rating": 5, "link": "https://www.autotrader.co.uk/content/car-reviews/porsche-911-review-coupe-2019" },
  "seller": { "name": "Mayfair Prestige UK Ltd", "phone": "(020) 8022 2494", "email": "info@mayfairprestigeuk.co.uk", "website": "http://www.mayfairprestigeuk.co.uk", "rating": 5.0 },
  "image_count": 67,
  "images": [{ "link": "https://m.atcdn.co.uk/a/media/c6f8d533aabb4482867b59d1d2940302.jpg", "label": "Rear Left", "category": "Exterior" }]
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Autotrader UK Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/autotrader-uk-scraper
cd autotrader-uk-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

Autotrader UK sits behind Cloudflare, but these requests get through on a plain connection — no browser, no cookies, no proxy.

Running thousands of calls from one IP and seeing blocks? Set `AUTOTRADER_UK_PROXY=http://user:pass@host:port` (a datacenter proxy is enough) and keep going.

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/cars/details?car=202511087749528"
```

```json
{
  "id": "202511087749528",
  "link": "https://www.autotrader.co.uk/car-details/202511087749528",
  "title": "Porsche 911 4.0 992 GT3 Coupe 2dr Petrol Manual Euro 6 (510 ps)",
  "make": "Porsche",
  "model": "911",
  "year": 2022,
  "mileage": 5874,
  "price": { "amount": 174950, "currency": "GBP" },
  "body_type": "Coupe",
  "fuel_type": "Petrol",
  "transmission": "Manual",
  "engine": { "size_litres": 4, "power": 503, "power_unit": "bhp", "cylinders": 6 },
  "performance": { "top_speed_mph": 199, "zero_to_62_mph_seconds": 3.9 },
  "history_check": { "status": "passed", "summary": "All checks passed" },
  "seller": { "name": "Mayfair Prestige UK Ltd", "phone": "(020) 8022 2494", "email": "info@mayfairprestigeuk.co.uk" },
  "image_count": 67,
  "images": [{ "link": "https://m.atcdn.co.uk/a/media/c6f8d533aabb4482867b59d1d2940302.jpg", "label": "Rear Left", "category": "Exterior" }]
}
```

All 9 endpoints are now live at `http://localhost:8000`.

## 📚 Endpoints

9 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Car Details | `/cars/details` | Everything about one listing in a single call, every photo included |
| Search Cars | `/cars/search` | 20 vehicles per page with 25 filters and 8 sort orders |
| List Makes / Models | `/makes/list`, `/models/list` | Every make and model with live counts of vehicles for sale |
| Search Filters | `/cars/filters` | Every filter value with live match counts |
| Dealer Details | `/dealers/details` | A dealer's full profile: phone, email, website, hours, rating and stock (website contacts: hosted API only) |
| Search Dealers | `/dealers/search` | 12K+ dealers by place, name or make, 20 per page (website contacts: hosted API only) |
| Dealer Stock | `/dealers/cars` | Every vehicle a dealer sells, with all 25 search filters |
| Dealer Reviews | `/dealers/reviews` | Every review with stars and text, plus the rating breakdown |


## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-autotrader-uk-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-autotrader-uk-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-autotrader-uk-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/cars/details",
    params={"car": "202511087749528"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Autotrader UK Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://www.omkar.cloud/l/autotrader-uk-scraper-github-whatsapp)

[![Ask Us by Email about Autotrader UK Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Autotrader%20UK%20Scraper%20API&body=I%20need%20help%20using%20the%20Autotrader%20UK%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper-api) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Etsy Scraper**](https://www.omkar.cloud/tools/etsy-scraper) — Etsy products: prices, discounts, shops & variations

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/autotrader-uk-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/autotrader-uk-scraper)
