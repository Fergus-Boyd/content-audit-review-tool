# Flagging Rules

Content and attachment pages are judged against a set of rules that flag issues and drive a scored recommendation. This page is an overview of how the rules engine works and what the rules are.

## Rules engine

`RuleEngine.py` defines `MultiCategoryRuleEngine`, which takes a dataframe and a YAML config file and runs a scoring pipeline over it, ending in a `recommendation` ("No action" / "Review" / "Remove") and a numeric `adjusted_score` used to rank rows.

Conditions were originally parsed with `eval`; this was replaced with a dedicated parser that only allows a fixed set of expected operations.

#### Condition parsing

`parse_condition` uses regex to split a condition string into a `column operator value` triad and returns a `ParsedCondition`. Supported operators are:

```
== != > >= < <=
IN
IS NULL / IS NOT NULL
```

`value` is parsed by `parse_value`/`parse_literal`: booleans, quoted strings, ints/floats, ISO dates (`YYYY-MM-DD`), `IN` lists (`["a", "b"]`), and `CURRENT_DATE` arithmetic (`CURRENT_DATE - 10 years`, `CURRENT_DATE + 30 days`, etc., via `dateutil.relativedelta`).

`CombinedCondition` ANDs (or ORs) a list of `ParsedCondition`s together. Each rule in `rules.yaml` compiles to one `CombinedCondition` — all of a rule's conditions must be true for a row to pick up that flag.

#### Loading rules

`load_rules_from_config` walks the `rules` section of the YAML into a flat list of `{category, strength, flag, variant, condition}` dicts. A rule's condition list can either be a plain list (one variant) or a dict keyed by variant name (e.g. `html`/`pdf`) when the logic differs by file type — each variant becomes its own entry in `self.rules`.

`self.base_score_dict` is loaded from the top-level `base_score` map in the YAML (see below).

#### Applying rules

`apply_rules` creates two list columns per category (`accessibility`, `functionality`, `freshness`, `negative_user_engagement`, `demand`, `exclusions`, `ranking`):

1. `{category}_flag` — every strength label a row matched, in rule order (e.g. `["high", "critical"]`)
2. `{category}_reasons` — the corresponding human-readable flag names (e.g. `["Content is hard to read...", "Page returns an error..."]`)

Both start as `[]` for every row. For each rule, rows matching its `CombinedCondition` get the rule's `strength` appended to `_flag` and its `flag` name appended to `_reasons`. Flags are **not** reduced to a single strength or boolean here — a row can accumulate several flags per category, and both list columns feed directly into scoring.

#### Scoring (`pipeline`)

`pipeline(config_yaml)` is the main entry point and runs, roughly, in this order:

1. **Ranking populations.** Rows are split into three peer groups so demand/score ceilings are comparable within a group rather than across pages and attachments:
   - pages/parents (`~is_attachment | is_parent`)
   - HTML attachments (`is_attachment & ~is_parent & file_extension == "html"`)
   - non-HTML attachments (everything else attached)

   `demand_rank` is a 0–1 percentile rank of `sessions` computed separately within each group. `engagement_rank` is a percentile rank of `engagement_time_secs` across all rows. `broken_link_ratio` is `n_broken_links / n_links` (0 where undefined).

2. **Load and apply rules** from `rules.yaml` (as above), producing the `*_flag`/`*_reasons` columns.

3. **Demand.** `demand` (the label used downstream, e.g. `"No Demand"`, `"Very High"`) is the first entry of `demand_reasons`, driven by the `demand` rules against `demand_rank`. Separately, `demand_multiplier` is a **continuous** function of `demand_rank` (`demand_rank_score`, piecewise-linear between anchor points in `DEMAND_ANCHORS`), so the score ramps smoothly instead of jumping at band edges. Non-HTML attachments use a different anchor set, `DEMAND_ANCHORS_NON_HTML_ATTACHMENT`, which reaches its top multiplier at a much lower rank (their demand distribution is much flatter). The `demand`/`demand_reasons` flag columns are only used for the human-readable label and the "No Demand" check in step 5 — the multiplier itself doesn't read them.

4. **Category scores.** `accessibility_score`, `functionality_score` and `negative_user_engagement_score` are `_flag_score(...)`: for each row, sum `base_score[strength]` (from the YAML `base_score` map) over every strength in that category's `_flag` list. `freshness_score` is the exception — it is **not** derived from `base_score`/`freshness_flag` at all. It's `freshness_age_score`, a continuous piecewise-linear function of content age in years (from `public_updated_at`, falling back to `pdf_modified`), interpolated between `FRESHNESS_ANCHORS` (`0yrs→0, 3yrs→1, 5yrs→2, 10yrs→3`, capped at 3 beyond 10 years). `freshness_flag`/`freshness_reasons` are still used for the human-readable reason text. Rows flagged by the `exclusions` category (data-quality exclusions, e.g. Publisher/HMRC-manuals-api content) have `freshness_score` forced to 0.

5. **Base score and recommendation.** `base_score` = accessibility + functionality + negative_user_engagement + freshness scores.
   - `base_score == 0` → `"No action"`
   - `demand == "No Demand"` and `freshness_score >= 3` → `"Remove"`
   - otherwise → `"Review"`

   `withdrawn` content is scored on its own route regardless of the above: `base_score` is forced to `1`, `recommendation` is forced to `"Remove"`, and `recommend_transfer_to_tna` is set `True`.

6. **Normalisation.** Each row's `base_score` is normalised against the maximum score achievable by its ranking population (`MAX_BASE_SCORE_HTML_PAR`/`_HTML_ATT`/`_NON_HTML`, derived from the maximum possible per-category scores in `base_score`) to give `normalized_score` (0–100). `adjusted_score = demand_multiplier * normalized_score` — this is the primary sort key used to prioritise the audit.

7. **Convert recommendation.** `recommend_convert` is `True` when a row was flagged for having no accessible alternative (either the pre- or post-2018 accessibility rule) *and* its recommendation isn't already `"Remove"` — i.e. converting to HTML is only suggested when the content is otherwise being kept.

8. **Cleanup and output.** Intermediate columns (`demand_rank`, `engagement_rank`, `exclusions_flag`/`_reasons`, `demand_flag`/`_reasons`, `ranking_flag`) are dropped. The output gains `accessibility_score`, `functionality_score`, `negative_user_engagement_score`, `freshness_score`, `base_score`, `normalized_score`, `score_multiplier` (the demand multiplier), `adjusted_score`, `recommendation`, `recommend_convert`, `recommend_transfer_to_tna`, and `issue_summary` (a sentence built from `recommendation`/`withdrawn`, or the combined freshness/accessibility/engagement/functionality reasons when the recommendation is `"Review"`).

9. **Priority ranking.** Rows are sorted by `adjusted_score` desc, `base_score` desc, whether `ranking_reasons` contains `"Incomplete audit data"` (those come first), recommendation priority (`Review` before `Remove` before `No action`), then oldest freshness date first — and given a `priority_rank` from 1 to the number of rows in the dataset.

There is no separate "interactions" override step any more — everything is folded into the single score/recommendation pipeline above.

## rules.yaml

All rule and scoring configuration lives in `audit_tool_streamlit/rules.yaml`.

#### `base_score`

A flat map from strength label to a numeric score used by `_flag_score`:

```
base_score:
  critical: 5
  high (higher): 4
  high: 3
  high (lower): 2
  moderate: 1
```

These labels are also the "strength" level used in the `rules` hierarchy below — they're not fixed to "weak/moderate/strong" any more and can be extended with new labels as long as `base_score` defines a score for them.

#### `rules`

Rules are represented hierarchically:

- **Category** — top level. Each category becomes a pair of `{category}_flag`/`{category}_reasons` columns. Current categories: `accessibility`, `functionality`, `freshness`, `negative_user_engagement`, `demand`, `exclusions`, `ranking`.
- **Strength** — one of the `base_score` labels for scored categories (`accessibility`, `functionality`, `negative_user_engagement`). `demand`, `exclusions` and `ranking` aren't scored via `base_score` — they use a placeholder strength of `true`, since those categories drive banding/exclusion/audit-completeness logic rather than a numeric score.
- **Flag name** — the human-readable reason, used in `_reasons` and shown to users.
- **Variant (optional)** — when a rule differs between file types, its conditions are a dict keyed by variant name (e.g. `html`/`pdf`) instead of a plain list.

Each condition is one line in a list; all conditions in the list must hold for the flag to apply. Conditions follow:

```
column (== | != | >= | <= | > | < | IN | IS NULL | IS NOT NULL) value
```

`CURRENT_DATE` supports `+`/`-` arithmetic in `days`, `months` or `years`, e.g.:

```
public_updated_at < CURRENT_DATE - 10 years
public_updated_at < CURRENT_DATE - 30 days
```

Current categories:

- **`accessibility`** — no-accessible-alternative PDF rules (split pre/post 2018-09-23, since accessibility regulations changed then), hard-to-read (`flesch_reading_ease_score`) and hard-to-scan (`word_to_heading_ratio`) content.
- **`functionality`** — broken pages/links, banded by count and by `broken_link_ratio`.
- **`freshness`** — age bands (10+ years / 5–10 years / 3–5 years) split by `html` (`public_updated_at`) vs `pdf` (`pdf_modified`) variants. These bands only drive the reason text — the actual `freshness_score` is computed continuously in `RuleEngine.py` (see above), not from `base_score`.
- **`negative_user_engagement`** — currently just "engagement time per session below P10" (`engagement_rank < 0.1`).
- **`demand`** — bands `demand_rank` into `No Demand` / `Low` / `Medium` / `High` / `Very High`. Used for the demand label and the "No Demand" removal check; the continuous demand multiplier is computed separately in code.
- **`exclusions`** — data-quality exclusions, currently content from `publishing_app` `Publisher` or `hmrc-manuals-api`. Rows matching this get their `freshness_score` zeroed.
- **`ranking`** — non-scoring flags used elsewhere in the pipeline: `Incomplete audit data` (missing Screaming Frog data, `public_updated_at`, or `engagement_time_secs`, with different variants for HTML vs non-HTML) and `Withdrawn content`.

There is no `interactions` section any more — the old strength-combination override system was replaced by the single scoring pipeline in `RuleEngine.py`.
