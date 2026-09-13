# Stress engine review — fuzzy-3.1.0

## Decision

Retain the three-component, 27-rule product-sum fuzzy design. Change two contextual rules and make halfway rounding mathematically stable. This remains an authored stress-estimation heuristic, not a validated measure or predictive model. Questionnaire ranges, input meanings, weights, category thresholds and self-reported strain separation are unchanged.

## Findings and changes

1. **Coverage is complete.** Every input has a continuous low/medium/high partition summing to one. Every component has a complete 3×3 rule table. Product strengths sum to one, so a positive-area output always exists for valid inputs. No catch-all rules are needed.
2. **No directional contradictions.** Academic/context tables are nondecreasing in both inputs; recovery deficit is nonincreasing. Product partition interpolation preserves this order, as does the positive weighted blend. Competing fuzzy labels are interpolation, not contradictory diagnoses.
3. **Two weak contextual rules.** In 3.0.0, low screen + medium commitments produced low pressure; low screen + high commitments produced medium pressure. Low screen duration is not evidence that rated nonacademic demands are absent. The new outputs are medium and high respectively. The other seven context rules are unchanged. High screen use alone still produces only medium context because screen purpose is unknown.
4. **Rounding defect.** Numerical centroid integration could produce 34.24999999999999 for a mathematical 34.25, making documented HALF_UP rounding return 34.2 instead of 34.3. The engine still constructs the real product-scaled, summed output distribution and calculates its numerical centroid. It also evaluates the equal-area centroid identity using decimal input partitions at precision 50 and checks agreement within 1e-9. Final rounding uses this decimal calculation. A nonfinite or inconsistent centroid returns an error with no score.
5. **Input hardening.** Reject nonfinite Decimal values, booleans, strings and oversized numeric inputs cleanly. Missing legacy inputs remain unsupported, not imputed. API validation additionally enforces quarter hours and integer ratings; internal evaluation supports continuous values for continuity testing.

## Why these components and rules remain

Academic pressure (45%) groups the related workload and deadline measures. High workload can remain demanding without an immediate deadline; urgent deadlines can remain demanding even for a small workload. Thus a high term in either dominates. Medium + medium remains medium; no extra stress is invented just to amplify the score.

Recovery deficit (40%) groups sleep and relaxation with reversed direction. Either very low recovery input can remain limiting even if the other improves. The resulting plateaus are an explicit conservative policy, not a guarantee that sleep or relaxation changes will have no real-world benefit. The model has no sleep-quality input and treats longer sleep monotonically; no new clinical sleep threshold was introduced.

Contextual pressure (15%) now ensures commitments stand on their own while limiting the influence of screen time, which includes both useful and unwanted use. Its smaller weight caps the contribution to 12.75 points; it cannot independently force a high overall score.

Rows are low/medium/high for the first input; columns are low/medium/high for the second. Entries are output labels. IDs remain `component:rowcolumn` within a recorded model version.

| Component | Low first input | Medium first input | High first input |
| --- | --- | --- | --- |
| Academic: workload × deadline | low, medium, high | medium, medium, high | high, high, high |
| Recovery deficit: sleep × relaxation | high, high, high | high, medium, medium | high, medium, low |
| Context: screens × commitments | low, **medium**, **high** | low, medium, high | medium, high, high |

Changed rule IDs: `contextual_pressure:12`, `contextual_pressure:13`. No new rules were added. The context-rule change can increase the unrounded final index by at most 5.25 points. Academic and recovery scores are unchanged except for removal of numerical drift.

## Calculation and interpretation

Triangular inputs peak at zero, midpoint and maximum. Equal-area output triangles have centroids 15, 50 and 85. Each rule multiplies its two memberships, scales its consequent distribution, and sums into the component distribution. Because the areas are equal, centroid = sum(rule strength × consequent center). This identity is verified against numerical integration; it is not a replacement with an unrelated scoring formula.

Final score = 0.45 × academic centroid + 0.40 × recovery deficit centroid + 0.15 × contextual centroid, rounded once to one decimal HALF_UP. Bands remain <25 Very low, <45 Low, <65 Moderate, <85 High, otherwise Very high.

The reachable range is **15-85 on the displayed /100 scale**, not a percentage or probability. The Very high band starts only when the raw score rounds to 85 (84.95 or above). We retained these labels for continuity instead of presenting arbitrary new thresholds as better calibrated. Components can compensate in the weighted blend: a moderate overall score can coexist with high academic pressure. Always interpret the displayed component explanations alongside the score and the independent strain report.

Weights, memberships and thresholds have no empirical calibration. Self-report noise, correlated inputs, unmeasured sleep quality and screen purpose limit meaningfulness. Default sliders are starting values, not measured observations; accepting all defaults produces 50.0. Test coverage proves mathematical properties, not accuracy at measuring an individual's stress.

## Validation

- All 1,375 supported component pairs checked against an independent rational-arithmetic centroid oracle; monotonicity verified in both dimensions across each grid. Together with the separable architecture and positive weights, this covers direction/coverage for the complete six-input product space without claiming to have enumerated every full check-in.
- Existing checks activate all 27 rules, cover all 64 input corners, validate input boundaries, isolate components and keep reported strain independent.
- Seeded input combinations checked against rational HALF_UP rounding; midpoint continuity, category boundaries, nonfinite failures and concurrent determinism tested.
- A saved 3.0.0 record remains byte-equivalent as assessment JSON after a current-model scenario. Explicit edits create 3.1.0 revisions; exports retain both versions. No history is recalculated in place.

`app/fuzzy_v3.py` preserves 3.0.0 for regression/reproduction. New assessments use 3.1.0 and a new specification hash through the existing engine registry. No database migration is needed for this engine update. See `FUZZY-3.md` for the historical 3.0.0 design; this review supersedes its contextual table and numerical rounding description.

Final validation: 207 backend tests and 33 frontend tests passed; frontend production build passed. An additional exhaustive rounding assertion across all 1,375 component pairs passed. Local 200-evaluation performance sample: p50 6.438 ms, p95 10.564 ms, max 11.345 ms (sequential evaluation, includes explanation payload construction; not a production latency SLA). Only dependency deprecation and pytest cache-permission warnings were reported.
