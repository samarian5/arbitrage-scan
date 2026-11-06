# ADR 0003 — How two listings are declared "the same product"

**Status:** accepted. Threshold lives in `ar_settings.match_threshold` (0.72).

## The problem

Amazon search results do not expose UPC/EAN, and eBay only prints item specifics
on the item page, which this crawler does not visit. So exact-identifier matching
is not available on the data we actually hold. That leaves titles, and titles are
full of marketing noise.

The failure mode is asymmetric: a false negative costs one missed opportunity,
but a **false positive invents profit**. An accessory paired with the product it
fits shows a 60–90 % "margin" that cannot be realised.

## Decision — gates first, score second

A pair must survive every gate; no amount of title similarity outvotes one:

| Gate | Rejects |
|---|---|
| accessory language on one side only | "Replacement Battery **for** DEWALT DCD771C2" vs the drill |
| `tool only` on one side only | bare tool vs kit (very different price) |
| quantity mismatch | "2 pack", "lot of 3", "3x" vs single |
| contradicting capacity | 5.0Ah vs 2.0Ah, 128GB vs 256GB, 20V vs 12V |
| conflicting brand | Makita vs DEWALT |
| Amazon model absent from eBay title | different SKU entirely |

Only then:

```
score = 0.75 × jaccard(content tokens)
      + 0.35 × exact model-token match
      + 0.10 × brand match            (capped at 1.0)
```

`method = "model"` when a shared model token exists (e.g. `dcd771c2`), else
`"title"`. Confidence: `high` for model, `medium` for jaccard ≥ 0.85, else `low`.

Matching runs **within one keyword's result pool** — the eBay and Amazon rows for
the same search phrase — which bounds the comparison to products that already
share a query, and keeps cost linear instead of quadratic across the catalog.

## Evidence is stored, not implied

Every match writes `{matched_models, shared_tokens, jaccard, brand, confidence}`
to `ar_matches.evidence`, and the dashboard's drawer renders it. A user looking at
a $9.63 gap can see it came from a shared `dcd1007b` token, not from two similar
sentences.

## Known weaknesses

- A pair with **no model number** is `title`-method and can be wrong; it is
  labelled `low`/`medium` and filterable. Not hidden, not silently trusted.
- Model tokens can collide across variants when the manufacturer reuses a base
  SKU for a kit and a bare tool — partially covered by the tool-only gate, not
  fully.
- eBay titles that omit the model ("Cordless Drill 20V") are rejected against an
  Amazon listing that has one. That is deliberate: it trades recall for precision.
