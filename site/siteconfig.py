"""
Website configuration.

CHANGE THE DOMAIN HERE, once, after you buy it. Everything else -- canonical
URLs, the sitemap, Open Graph tags -- is derived from this single value, so
there is no second place to forget.
"""

# --------------------------------------------------------------------------
# Set this to your domain once you own it, with no trailing slash.
# Until then the placeholder is harmless: the site works fine locally and on
# any host, only the absolute URLs in the sitemap and social cards are wrong.
# --------------------------------------------------------------------------
DOMAIN = "https://marginco.co.uk"

SITE_NAME = "Margin & Co."
AUTHOR = "Adam"
TAGLINE = "Testing whether popular trading indicators survive realistic trading costs."

DESCRIPTION = (
    "An independent research project testing whether the trading indicators "
    "retail traders rely on \u2014 EMA, VWAP, MACD and RSI \u2014 still make "
    "money once out-of-sample validation and realistic trading costs are "
    "applied. Published weekly, with a live paper portfolio."
)

REPO_URL = "https://github.com/offlineb52-ctrl/margin-and-co"

# If you deploy to a subdirectory rather than a domain root, set this to
# e.g. "/margin-and-co/". For a custom domain, leave it as "/".
BASE_PATH = "/"
