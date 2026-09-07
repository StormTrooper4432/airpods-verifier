# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Astro. Chosen by the project owner over static HTML/CSS, Next.js, and a delegated
recommendation. Deploy target not yet decided; the choice must keep the Gemini API key
server-side rather than shipping it to the browser.

## Users

Secondhand buyers evaluating a used AirPods Pro 2 listing, in two situations treated as
equally primary:

- **At the meet-up, on a phone.** Standing in front of a seller, deciding whether to hand
  over $150–$250. Needs a verdict fast, one-handed, possibly on poor signal, and needs to
  know which photograph to take next while the item is still in front of them.
- **At a desk, reviewing a listing.** Assessing eBay or Reddit photos before contacting a
  seller. Has many images, time to read the full reasoning, and no ability to request more.

## Product Purpose

Turn photographs of an AirPods Pro 2 box and hardware into an itemised, explainable
Evidence Ledger: what is consistent, what is suspicious, what could not be determined, and
exactly which photograph would resolve the open questions.

Success is a buyer who either walks away from a counterfeit or completes a purchase with
justified confidence — and who can check every claim the tool made.

## Positioning

Counterfeit AirPods routinely carry **cloned but valid** serial numbers, so they pass
Apple's own `checkcoverage.apple.com` lookup. That is the check most buyers know about, and
it returns a green light on a fake.

Two things a neighbouring product could not truthfully copy:

- **The LLM never decides authenticity.** It extracts observations into a JSON Schema
  enforced server-side; deterministic Python rules produce the verdict. Every finding is
  reproducible and carries a citation the buyer can check.
- **Serial cross-matching defeats cloning.** Apple prints a serial in four places. A
  counterfeiter copying one genuine serial passes Apple's lookup but must reproduce four
  mutually consistent serials to pass this.

Existing third-party verification sites either cover few models or paywall the result.
This is un-paywalled and itemised.

## Operating Context

- Listings live on eBay, Reddit (r/AreMyAirpodsAuthentic), and Facebook Marketplace.
- Meet-ups are in person, often outdoors, with the box and case physically available.
- Buyers commonly check `checkcoverage.apple.com` and mistakenly treat a pass as proof.
- Apple's "Identify your AirPods" support page is the authority for model numbers.
- The single most valuable photograph is the box underside label; the second is the
  underside of the case lid. Real listing photos usually show neither.

## Capabilities and Constraints

- **Verifies AirPods Pro 2 only** (Lightning and USB-C). Other AirPods products are
  *identified by name* and explicitly declined rather than judged.
- Eight scored rules plus two observation-only ones. Verdicts are `GENUINE`,
  `HIGH_RISK_SUSPECTED`, or `INCONCLUSIVE`; a genuine verdict is capped at 0.95 confidence
  because photographs cannot rule out a cloned serial.
- Every rule carries a cited source, enforced by tests.
- Output includes ranked next-step photo requests derived from which panels were shown.
- Vision provider is pluggable (Gemini default, Claude available). Gemini's free tier is
  rate-limited **and its content is used to improve Google's products** — a disclosure
  obligation once strangers upload photos.
- Font/typeface comparison exists but is **uncalibrated and disabled** (weight zero).
- Only North American packaging is characterised; content rules do not transfer between
  regions.
- Undecided: hosting/deploy target, whether a hosted key or user-supplied key is used, and
  whether uploaded photos are retained at all.

## Brand Commitments

No confirmed product name, logo, domain, or visual identity exists yet. The repository is
`airpods-verifier`. The project owner is a domain expert in AirPods authenticity and is the
authority for ground truth. No naming or identity constraint has been made binding.

## Evidence on Hand

Real, and usable as content:

- **20 real listings, blind-labelled by the owner**, scoring 12/20 agreement with
  **0 counterfeits passed as genuine and 0 genuine units accused**.
- Five genuine reference units photographed by the owner (Pro 2, Pro 1, AirPods 4,
  AirPods 4 ANC) — `knowledge/real_*`, **local only, never transmitted, gitignored**.
- One confirmed counterfeit AirPods Pro 3 — `knowledge/fake`, same handling.
- The owner's catalogue of counterfeit indicators across 25 hand-verified listings.
- Measured records for every threshold in `knowledge/airpods_pro_2.yaml`.

Absences future work must not fabricate: no users, no traffic, no testimonials, no press,
no pricing, no uptime or accuracy claims beyond the 20-post corpus above.

## Product Principles

1. **Absence of evidence is never evidence of authenticity.** An unreadable check forces
   INCONCLUSIVE. Never clear an item because nobody photographed the part that would
   condemn it.
2. **Every claim is traceable.** A finding without a citation is an invented specification.
   The buyer sees the source.
3. **Declining beats guessing.** A missed fake costs $200; an unnecessary re-photograph
   costs nothing. Errors should fall on the side of asking for more.
4. **Say what could not be determined, and what would fix it.** The open questions and the
   next photograph are as much the product as the verdict.
5. **Never overstate certainty.** Photo evidence cannot rule out a cloned serial, and the
   interface must not imply otherwise.

## Accessibility & Inclusion

No formal standard has been set by the owner. Product-specific needs that follow from the
operating context: usable one-handed on a phone outdoors, legible in bright daylight,
tolerant of poor connectivity, and never conveying a verdict by colour alone.
