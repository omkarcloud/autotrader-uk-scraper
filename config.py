"""Configuration for the Autotrader UK Scraper. Everything can be set with an environment
variable; the defaults work out of the box.

    PORT                  port the API listens on (default 8000)
    AUTOTRADER_UK_PROXY   proxy URL for every request, e.g. http://user:pass@host:port
                          (default: none, requests go out from your own connection).
                          www.autotrader.co.uk sits behind Cloudflare, which lets these
                          requests through from a plain connection, so you very likely
                          don't need this. Set a datacenter proxy only if you start
                          seeing blocks (HTTP 403 / 429) at high volume.

Everything else below is a plain constant with a working default.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

AUTOTRADER_UK_PROXY = os.environ.get("AUTOTRADER_UK_PROXY") or None


# ---- what the autotrader_uk module reads: defaults for a direct run ----
AUTOTRADER_UK_BLOCK_HOLD_S = 600
AUTOTRADER_UK_EXIT = AUTOTRADER_UK_PROXY      # the exit every session leaves through: your proxy, or direct
AUTOTRADER_UK_FALLBACK_EXIT = None
MAX_RETRIES = 3
RETRY_BACKOFF = 2
