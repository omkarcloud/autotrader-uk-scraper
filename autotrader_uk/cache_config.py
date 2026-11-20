"""Cache TTL per /autotrader-uk/* endpoint (cache.py, keyed on the validated
params). cars/filters, makes/list and models/list slice ONE cached filter
answer per search (api._facets), so the three cost one upstream call.
"""
from datetime import timedelta

# One row per (filters, sort, page): adverts are added and sold all day, and
# "most_recent" is only useful fresh.
SEARCH_CACHE = timedelta(minutes=15)
# A listing's price and status change a few times in its life; a sold one
# disappears, which an hour of caching hides no longer than the site's own pages.
DETAILS_CACHE = timedelta(hours=1)
# Filter options with live counts (and the spellings a search borrows from
# them): the option lists are stable, the counts drift slowly.
FACET_CACHE = timedelta(hours=6)
# Dealer lists reorder only as dealers join or leave.
DEALER_SEARCH_CACHE = timedelta(hours=12)
# A dealer's profile, contacts and stock counts: stock moves daily, the rest rarely.
DEALER_CACHE = timedelta(hours=6)
# New reviews arrive a few times a week on a busy dealer.
REVIEWS_CACHE = timedelta(hours=6)
