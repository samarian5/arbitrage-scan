# ADR 0002 — Stance on automated access and resale data

**Status:** accepted, with a caveat the operator must read before scaling.

## What this system does

It fetches **public search-results HTML** from eBay and Amazon the way a browser
does, at a deliberately low rate (2 pages per keyword per day, one global page
budget), for **personal price research**, and stores prices it observed.

## What it deliberately does not do

- **No login, no account, no checkout.** No seller account is touched, no cart or
  order is created, nothing is bought.
- **No captcha solving service, no proxy rotation, no fingerprint spoofing beyond
  the TLS profile curl_cffi ships with.** If a lane is blocked, the run records
  `blocked` and stops; it does not escalate. That is a hard design rule, not a
  tuning parameter: `fetch_ebay.search_page()` raises after one browser attempt,
  and `fetch_amazon.search_page()` walks three fixed lanes then raises.
- **No resale or redistribution of the data.** The board is a private research
  aid; the queue and DB are not a data product.
- **No rate that could degrade service.** The daily budget (`ar_settings`),
  per-slice pacing (~3–5 s between pages) and the 24 h per-keyword cadence are
  enforced in code, not left to the operator's restraint.

## The honest caveats

1. **Amazon's Conditions of Use prohibit "data mining, robots, or similar data
   gathering and extraction tools."** eBay's `robots.txt` disallows `/sch/`.
   Reading search pages with a script is therefore contrary to the letter of both
   sites' terms, even at low volume. This is the operator's risk decision.
2. **Amazon sells the legitimate version of this**: the Product Advertising API
   (free with an approved Associates account) and third-party price APIs (Keepa).
   If this ever moves from personal research to a commercial product, switch the
   Amazon lane to one of those — the `AmazonFetcher` interface is the only place
   that would change.
3. **Prices are point observations**, not a price history service, and eBay
   sold/completed data is behind a login wall, so no sold-price claims are made
   anywhere in the UI.

## Consequences

- Runs that hit a wall fail loudly (`stopped_reason: amazon_blocked`) instead of
  silently degrading, so the operator sees the site's posture changing.
- The system stays free and dependency-free, at the cost of throughput being
  bounded by politeness rather than by infrastructure.
