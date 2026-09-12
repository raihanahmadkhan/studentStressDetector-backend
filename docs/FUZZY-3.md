# Current check-in and fuzzy model — fuzzy-3.0.0

This version is an authored, deterministic **routine-based index**, not a clinically validated stress detector. Neither these parameters nor their weights are scientifically validated predictors. Self-reported strain is a separate observed outcome; changing it cannot affect inference.

## Questionnaire

`check-in-2.0.0` requires six routine observations and an explicit strain value or skip:

| API field | UI meaning | Range |
| --- | --- | --- |
| sleep_hours | Sleep ending on the observation date | 0–12 hours, steps of 0.25 |
| academic_load | Total academic demand | Integer 0–10 |
| deadline_pressure | Urgency/difficulty of meeting deadlines | Integer 0–10 |
| screen_hours | Total screen use, overlapping use counted once | 0–16 hours, steps of 0.25 |
| extracurricular_load | Other, nonacademic commitments | Integer 0–10 |
| recovery | Opportunity for restorative rest/relaxation | Integer 0–10 |
| reported_strain | How strained the person felt | Integer 0–10, or explicit null |

Deadline pressure separates urgency from workload. Recovery adds a description of restorative downtime beyond sleep duration. Neither addition establishes predictive value. Other commitments already captures social/personal demand, so there is no extra duplicate field. No focus/energy field was added.

The UI uses native labelled range inputs, visible values, units and endpoints. Arrow/Home/End keyboard behavior is native browser behavior. Sliders initially display an unanswered state rather than silently treating the middle position as an observation; users may move the slider or explicitly accept that displayed value. Changing or accepting a value changes only the draft. Only Save check-in / Save revision writes history. Scenarios require explicit Calculate and never write history. Strain may be skipped explicitly.

## Three components, 27 rules

Each component has just two inputs, a complete 3×3 matrix, and its own output distribution and centroid. There are nine meaningful rules per component, 27 total, rather than 3^6 combinations. The final stage is an explicit weighted blend, not another rule base.

1. **Academic pressure (45%)** combines workload with deadline pressure. A high value in either keeps academic pressure high; related inputs do not get independent additive weights in the final index.
2. **Recovery deficit (40%)** combines sleep with restorative downtime. Its direction is deliberately reversed: a higher deficit means less recovery. Either low input can limit recovery; improvements can plateau if the other input remains limiting.
3. **Contextual pressure (15%)** combines screen time with other commitments. Screen duration alone is ambiguous, so the context matrix and smaller final weight moderate its influence. High screen time with no other commitments produces medium context, not automatically high pressure.

Weights express an inspectable product policy, not estimated effect sizes. Combining these overlapping observations can still double-count aspects of a day. They need future user research and prospective validation before stronger claims.

### Membership functions

For each input, let its maximum be M. The terms low, medium and high are triangular sets `[0,0,M/2]`, `[0,M/2,M]`, `[M/2,M,M]`. Endpoints use shoulder semantics: membership is 1 at 0 for low and 1 at M for high. At every supported value the memberships sum to 1. These labels are relative to the authored input range: for example, medium sleep peaks at 6 hours. They do not mean a clinical recommendation about sleep duration.

Component output terms are equal-area, symmetric triangles:

- low `[0,15,30]`, centroid 15;
- medium `[35,50,65]`, centroid 50;
- high `[70,85,100]`, centroid 85.

The gaps between output sets are intentional. Inputs, not output labels, need complete coverage. Every valid input activates rules and yields positive output mass.

### Full rule tables

Rows are the first input's low/medium/high terms; columns are the second input's low/medium/high terms. Each cell is the consequent. IDs use `component:rowcolumn`, numbered 1–3. For example `academic_pressure:13` means workload low AND deadline high → academic pressure high. All rule weights are 1.

Academic pressure: workload × deadline pressure

| | Low deadline | Medium deadline | High deadline |
| --- | --- | --- | --- |
| Low workload | Low | Medium | High |
| Medium workload | Medium | Medium | High |
| High workload | High | High | High |

Recovery deficit: sleep × recovery

| | Low recovery | Medium recovery | High recovery |
| --- | --- | --- | --- |
| Low sleep | High | High | High |
| Medium sleep | High | Medium | Medium |
| High sleep | High | Medium | Low |

Contextual pressure: screen time × other commitments

| | Low commitments | Medium commitments | High commitments |
| --- | --- | --- | --- |
| Low screen | Low | Low | Medium |
| Medium screen | Low | Medium | High |
| High screen | Medium | High | High |

### Inference: why product-sum here

The previous version was min/max Mamdani. The new version deliberately uses **Larsen-style product-sum fuzzy inference**. Do not describe it as the unchanged min/max Mamdani system in an interview.

For each component:

1. Fuzzify the two observations using the three triangular terms.
2. Rule strength = membership of first antecedent × membership of second antecedent.
3. Scale the consequent fuzzy set pointwise by the rule strength (product implication, not clipping).
4. Sum these scaled sets across the nine rules.
5. Calculate the area centroid using scikit-fuzzy over the 0–100 universe at 0.1 spacing.

Since each input partition sums to 1, the nine rule strengths sum to 1. The aggregate is a convex combination of fuzzy sets and cannot exceed membership 1. Every output triangle has the same area and is symmetric, so its centroid is also the weighted mean of the consequent centers under those strengths. The runtime computes the actual aggregate and centroid; the numerical equivalence provides an independent check.

This choice avoids counterintuitive dips found in the initial min/max prototype. Equal-area output sets plus product partition weights give piecewise bilinear interpolation of each ordered rule matrix. Each row and column is ordered: demand cannot reduce pressure, and more sleep/recovery cannot increase deficit. Flat regions are intentional. Output symmetry alone was insufficient with max aggregation, so both the implication and aggregation were changed together.

### Final index and example

`raw_score = 0.45 × academic_centroid + 0.40 × recovery_deficit_centroid + 0.15 × contextual_centroid`.

Only the final result is rounded, to one decimal with decimal HALF_UP. Existing category cutoffs are applied to that rounded score: <25 Very low, <45 Low, <65 Moderate, <85 High, otherwise Very high. These are heuristic index bands, not diagnoses, probabilities or calibrated stress levels. Components and the final index are bounded by 15–85; 0/100 are not reachable, and Very high occurs only near the upper rounded limit.

For sleep 6, workload 5, deadline 5, screen 8, commitments 5 and recovery 5, each component centroid is 50. Contributions are 22.5 + 20 + 7.5 = 50.0 (Moderate). Strain may be 0, 10 or skipped with exactly the same calculation.

For workload 2 and deadline 7, four academic rules fire at 0.36, 0.24, 0.24 and 0.16. Their consequents are medium/high/medium/high, giving academic centroid 64 and contribution 28.8. This explains an actual component calculation without interpreting firing strength as confidence or causation.

## Contracts, traces and history

`/api/fuzzy-model` exposes the model specification and SHA-256 hash. The runtime uses private copied definitions so a caller cannot alter evaluation by mutating the public specification. Each saved revision records questionnaire version, engine version, specification hash and complete assessment.

The new assessment includes all input memberships, all 27 rule traces, three components (label, rules, aggregate, raw centroid, weight, contribution, backend-authored explanation), `raw_score` and the rounded score/category. Top-level `raw_centroid` is null and the top-level aggregate is empty: the final weighted blend is not itself a fuzzy centroid. The frontend plots component distributions and renders server explanations; it does not generate reasoning. Legacy assessments retain their single-centroid presentation.

LLM fact bundles include the actual component explanations and component IDs, alongside observed strain, the stored index and actual activated rules. The existing evidence-selection schema, safeguards, consent, deadlines, quotas and fallback architecture are unchanged. The LLM never computes a score or supplies new facts.

Migration `0003_checkin_inputs` adds nullable columns for old rows and conditional database constraints for the new questionnaire. Existing revisions and results are not recomputed. An explicit edit creates a new revision under the new questionnaire/model; missing new values must be supplied deliberately. Legacy dates/timezones remain immutable. Reads, exports and historical revision views retain nulls and original version provenance.

New write requests outside the stated ranges, fractional rating values, booleans, nonfinite numbers, missing routine inputs or extra fields return validation errors. Internal evaluation of an incomplete legacy reference returns unsupported/missing_current_model_inputs with no score. Impossible empty coverage returns an error with no fabricated score. The API's existing unexpected-inference-failure handling still preserves an explicitly submitted observation with an unavailable assessment. Old hour values outside the new range remain readable; editing requires an explicit in-range observation rather than silent clipping.

Scenarios run only current-model calculations and never write observations, revisions, receipts or registry entries. An incomplete legacy reference cannot receive a same-model comparison; its original stored result is untouched. Hypothetical strain is null, never predicted.

Personal analytics policy `personal-patterns-2.0.0` handles deadline/recovery independently using the existing calendar windows, counts, medians and missingness rules (minimum deviation two points). Older missing inputs do not become zero. Fuzzy index trends are still excluded to avoid mixing versions. Exports include all revisions, new fields, actual assessments and referenced model specifications.

Future ML preparation policy `next-day-strain-2.0.0` includes the six routine values and current observed strain, with next-day independent strain still the target. It excludes incomplete legacy feature rows instead of imputing. Prepared pairs carry source questionnaire/engine provenance outside the feature vector. The existing leakage-safe evaluation and disabled serving policy remain unchanged; no predictive feature or infrastructure was added.

## Verification and limits

Tests cover each field's bounds and types, all 64 six-input corners, every supported one-dimensional value, activation of all 27 rules, exact product strengths, an independent centroid example, deterministic results, self-report independence, component isolation and direction consistency. API regressions cover new validation, legacy reads, versioned edits, exports and scenario isolation; frontend tests cover explicit slider submission, empty drafts, range contracts, visible values and backend explanation rendering.

This is a compact authored baseline, not an evidence-based health assessment. Sleep quality, screen purpose and the contexts behind ratings are unmeasured. Self-report noise, correlated inputs, missing days, range caps and authored thresholds limit interpretation. New fields increase recording burden. Live LLM quality and predictive usefulness are not established by these implementation tests.
