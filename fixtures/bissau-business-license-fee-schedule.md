# Fixture B — Bissau City Council (CMB) business licence fee schedule

Source: `Business Registry and Business License Requirement Gathering Questionnaire FV_edh_.pdf`
— an illustrative sample found online by the product team, **not confirmed as the actual
client-supplied policy document** for the live Guinea-Bissau pilot. Useful as a fixture for its
*format* (fee logic buried in 14 mostly-irrelevant pages), not because its exact numbers are
guaranteed to match the real document. The real document is expected to add rebate/adjustment
rules this fixture doesn't have — see `DESIGN.md`'s "Coverage gap in the current worked examples."
This is only the fee-relevant span the "locate relevant spans" step should retain, plus the
narrative line that confirms area is the sole rate-driving attribute in *this* sample.

## Narrative confirmations (scattered across the document, must be cross-referenced)
- Q18: "The registration fee is determined according to the price list in accordance with the
  area of occupation of the commercial establishment officially approved and published by the
  City Council (CMB) – 06/04/2011."
- Q19: "In size, by the roof area of the commercial establishment building."
- Q17 (second questionnaire): "There is no classification system for commercial establishments."
- Methodology question (second questionnaire, Q15): fee is "Based on the City Council Pricelist
  table, calculated by the coverage area of the commercial establishment" — attributes/trade
  type/no. of workers all explicitly *not* selected as fee drivers.

## Fee tables (page 7)

### Commercial establishments located IN the market area (locker, kiosks, containers)
| Area range (m²) | Rate (XOF/m²) |
|---|---|
| 1–5 | 1,000 |
| 6–10 | 850 |
| 11–20 | 800 |
| 21–30 | 790 |
| >30 | 750 |

### Commercial establishments located OUTSIDE the market area (locker, kiosks, containers)
| Area range (m²) | Rate (XOF/m²) |
|---|---|
| 1–5 | 1,050 |
| 6–10 | 900 |
| 11–20 | 850 |
| 21–30 | 825 |
| >30 | 800 |

### Other establishment types (shops, warehouses, workshops, pharmacies, bars, restaurants,
hotels, taverns, apartments, etc.) — inside and outside the market area
| Area range (m²) | Rate (XOF/m²) |
|---|---|
| 15–25 | 10,000 |
| 26–35 | 12,500 |
| 36–45 | 15,000 |
| 46–55 | 17,500 |
| >55 | 20,000 |

Note: rate is per m², multiplied by total area — a `PER_UNIT` band, not a `SLAB` (the whole area
is charged at the one rate for the band it falls in, not marginal tiers). See
`reference/calculation-rule-vocabulary.md`.
