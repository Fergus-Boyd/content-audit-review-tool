"""GOV.UK Content Audit Tool.

Layout follows the client's prototype (see prototype/): three numbered
tabs are the route through the job (Filter and search, Preview,
Download); Help and definitions is unnumbered reference at the end. The
three working tabs each open with the same status block: the count, and
every rule behind it as a chip you can deselect to remove. Filters live
in named topic sections, the everyday ones open and the rest one click
away.

Nothing changes the count until Update results is pressed. What the
widgets hold is staged; what the count was built from is applied, in
ss.applied.

Filter widget keys always come from audit_tool_streamlit.ui_state, never
built by hand: resetting works by changing a key, not by clearing a
value (see that module for why).

Unlike the prototype (DuckDB, several tables), the real data lives in
one table, so a few things are simpler here on purpose:
  - Text search compiles straight into the scope's WHERE clause against
    the html/title columns - there is no separate search-ids step.
  - "Also include attachments/parent pages" is a second fetch keyed on
    public_url / parent_public_url, not a subquery against a separate
    relation table.
"""

from __future__ import annotations

import hashlib
import math

import audit_tool_streamlit.data_selection as ds
import pandas as pd
import streamlit as st
from audit_tool_streamlit import scope, ui_state
from audit_tool_streamlit.definitions import DEFINITIONS, search_help_markdown
from audit_tool_streamlit.params import ParamGenerator
from audit_tool_streamlit.scope import FilterDef, Rule
from audit_tool_streamlit.search.compiler import compile_query
from audit_tool_streamlit.search.parser import QueryError, parse

st.set_page_config(page_title="GOV.UK Content Audit Tool", layout="wide")

PAGE_SIZE = 50

# Every column the dataset has. Used for the one full, unfiltered fetch
# that builds the filter vocab (what a dropdown offers, what a date/number
# range spans) and for the Download tab's "everything" option.
ALL_COLUMNS = [
    "priority_rank",
    "title",
    "public_url",
    "content_id",
    "file_extension",
    "document_type",
    "categories",
    "primary_publishing_org",
    "publishing_orgs",
    "abstract",
    "date_published",
    "public_updated_at",
    "pdf_modified",
    "pdf_created",
    "publishing_app",
    "withdrawn",
    "is_historic",
    "is_attachment",
    "parent_content_id",
    "parent_public_url",
    "no_of_attachments",
    "is_parent",
    "sessions",
    "engaged_sessions",
    "users",
    "engagement_time_secs",
    "total_file_downloads",
    "file_downloads",
    "navigation",
    "file_download_navigated",
    "word_count",
    "sentence_count",
    "pdf_page_count",
    "average_words_per_sentence",
    "total_headings",
    "word_to_heading_ratio",
    "expected_reading_time_mins",
    "flesch_reading_ease_score",
    "readability",
    "is_broken",
    "is_redirecting",
    "n_links",
    "n_broken_links",
    "broken_link_ratio",
    "broken_urls",
    "n_redirecting_links",
    "screaming_frog_available",
    "accessible_url",
    "accessible_url_confidence_score",
    "is_pdf_with_accessible_version",
    "freshness_flag",
    "freshness_reasons",
    "functionality_flag",
    "functionality_reasons",
    "negative_user_engagement_flag",
    "negative_user_engagement_reasons",
    "accessibility_flag",
    "accessibility_reasons",
    "accessibility_score",
    "functionality_score",
    "negative_user_engagement_score",
    "freshness_score",
    "base_score",
    "normalized_score",
    "score_multiplier",
    "adjusted_score",
    "recommendation",
    "recommend_convert",
    "recommend_transfer_to_tna",
    "ranking_reasons",
    "issue_summary",
]

TEXT_WIDTH = 520

# ---------------------------------------------------------------------------
# Cached data access


@st.cache_resource(show_spinner=False)
def cached_taxonomy() -> tuple[dict, dict]:
    """(descendants, paths): descendants for rule_topic's "include topics
    inside this one", paths for the multiselect's labels and hierarchy
    order - see scope.taxon_descendants/taxon_paths."""
    tree = scope.load_taxonomy()
    return scope.taxon_descendants(tree), scope.taxon_paths(tree)


@st.cache_data(show_spinner=False)
def cached_vocab_stats() -> dict:
    """Dropdown vocab and numeric/date bounds, fetched column by column -
    never the whole dataset. See scope.columns_needing_stats."""
    return ds.fetch_vocab_stats(scope.columns_needing_stats())


@st.cache_data(show_spinner=False)
def cached_row_count() -> int:
    return ds.fetch_row_count()


@st.cache_data(show_spinner=False)
def cached_known_urls() -> set:
    return ds.fetch_known_urls()


@st.cache_data(show_spinner="Fetching your results...", max_entries=1, ttl=1800)
def cached_scope_fetch(where_sql: str, params: dict) -> pd.DataFrame:
    return ds.fetch_scope(where_sql, params, ALL_COLUMNS)


taxonomy, taxonomy_paths = cached_taxonomy()
column_types = cached_vocab_stats()


def _vocab_options(column: str) -> list:
    return (column_types.get(column) or {}).get("column_opts") or []


def _ranges() -> dict:
    """{column: {"min": ..., "max": ...}} for every numeric/date column,
    the shape scope.date_bounds/number_span_caption expect."""
    out = {}
    for col, info in column_types.items():
        if info.get("column_type") in ("numeric", "datetime"):
            lo, hi = info["column_opts"]
            out[col] = {"min": lo, "max": hi}
    return out


RANGES = _ranges()
ROW_COUNT = cached_row_count()

# ---------------------------------------------------------------------------
# Session state

ss = st.session_state
ss.setdefault("qs_attachments", True)
ss.setdefault("qs_withdrawn", True)
ss.setdefault("qs_historic", True)
ss.setdefault("qs_crawled", False)
ss.setdefault("rel_children", False)
ss.setdefault("rel_parents", False)
ss.setdefault("executed_text", "")
ss.setdefault("executed_title", "")
ss.setdefault("executed_url", "")
ss.setdefault("page_num", 1)
ss.setdefault("applied", scope.empty_applied())
ss.setdefault("has_run_once", False)


def human_size(n_bytes: int) -> str:
    if n_bytes < 1024 * 1024:
        return f"{max(1, round(n_bytes / 1024))} KB"
    return f"{n_bytes / (1024 * 1024):.1f} MB"


# ---------------------------------------------------------------------------
# Filter widgets. Each render function returns an active Rule or None.


def wkey(d: FilterDef, part: str) -> str:
    """Session key for one of a filter's widgets. Always go through
    this: a key built by hand cannot be reset (see ui_state)."""
    return ui_state.widget_key(ss, d.key, part)


def _exclude_control(d: FilterDef, excluding_caption: str, many: bool = False) -> bool:
    excluded = st.checkbox(scope.exclude_label(many), key=wkey(d, "exclude"))
    if excluded:
        st.caption(f":red[{excluding_caption}]")
    return excluded


def _reset_exclude(d: FilterDef) -> None:
    ss[wkey(d, "exclude")] = False


def _date_pair(d: FilterDef, lo, hi):
    c1, c2 = st.columns(2)
    start = c1.date_input(
        "From",
        value=None,
        min_value=lo,
        max_value=hi,
        format=scope.DATE_FORMAT,
        key=wkey(d, "start"),
    )
    end = c2.date_input(
        "To",
        value=None,
        min_value=lo,
        max_value=hi,
        format=scope.DATE_FORMAT,
        key=wkey(d, "end"),
    )
    if start and end and start > end:
        st.caption(":red[The From date should be before the To date.]")
    return start, end


def render_vocab_multi(d: FilterDef, pig: ParamGenerator) -> Rule | None:
    options = _vocab_options(d.column)
    st.markdown(f"**{d.label}**")
    values = st.multiselect(
        d.label,
        options,
        format_func=scope.option_label,
        label_visibility="collapsed",
        key=wkey(d, "values"),
    )
    if not values:
        _reset_exclude(d)
        return None
    rule = scope.rule_vocab_multi(d, values, pig)
    rule.negated = _exclude_control(
        d,
        f"Excluding every {scope.or_join([scope.option_label(v) for v in values])} page. "
        "Everything else stays in.",
        many=len(values) > 1,
    )
    return rule


def render_topic(d: FilterDef, pig: ParamGenerator) -> Rule | None:
    names_by_hierarchy = list(taxonomy_paths)
    st.markdown(f"**{d.label}**")
    chosen = st.multiselect(
        d.label,
        names_by_hierarchy,
        format_func=lambda name: taxonomy_paths[name],
        label_visibility="collapsed",
        key=wkey(d, "values"),
    )
    with_inside = st.checkbox(
        "Include topics inside this one", value=True, key=wkey(d, "descendants")
    )
    if not chosen:
        _reset_exclude(d)
        return None
    names = chosen
    rule = scope.rule_topic(d, taxonomy, names, with_inside, pig)
    rule.negated = _exclude_control(
        d,
        f"Excluding every page tagged {scope.or_join(names)}. "
        "Everything else stays in.",
        many=len(names) > 1,
    )
    return rule


def render_date_preset(d: FilterDef, pig: ParamGenerator) -> Rule | None:
    lo, hi = scope.date_bounds(RANGES, d.column)
    st.markdown(f"**{d.label}**")
    preset = st.selectbox(
        d.label, scope.DATE_PRESETS, label_visibility="collapsed", key=wkey(d, "preset")
    )
    span = scope.date_span_caption(lo, hi)
    if span:
        st.caption(span)
    start = end = None
    if preset == "Custom range":
        start, end = _date_pair(d, lo, hi)
    rule = scope.rule_date_preset(d, preset, start, end, pig)
    if rule is None:
        _reset_exclude(d)
        return None
    rule.negated = _exclude_control(
        d, "Excluding pages in this period. Everything else stays in."
    )
    return rule


def render_date_range(d: FilterDef, pig: ParamGenerator) -> Rule | None:
    lo, hi = scope.date_bounds(RANGES, d.column)
    st.markdown(f"**{d.label}**")
    start, end = _date_pair(d, lo, hi)
    span = scope.date_span_caption(lo, hi)
    if span:
        st.caption(span)
    rule = scope.rule_date_range(d, start, end, pig)
    if rule is None:
        _reset_exclude(d)
        return None
    rule.negated = _exclude_control(
        d, "Excluding pages in this period. Everything else stays in."
    )
    return rule


def render_reasons(d: FilterDef, pig: ParamGenerator) -> Rule | None:
    """The *_reasons columns, and publishing_orgs (kind "vocab_list"):
    a list of values per page."""
    options = _vocab_options(d.column)
    st.markdown(f"**{d.label}**")
    values = st.multiselect(
        d.label,
        options,
        format_func=scope.option_label,
        label_visibility="collapsed",
        key=wkey(d, "values"),
    )
    if not values:
        _reset_exclude(d)
        return None
    rule = scope.rule_reasons(d, values, pig)
    rule.negated = _exclude_control(
        d,
        "Excluding every page with these problems. Everything else stays in.",
        many=len(values) > 1,
    )
    return rule


def render_minmax(d: FilterDef, pig: ParamGenerator) -> Rule | None:
    st.markdown(f"**{d.label}**")
    c1, c2 = st.columns(2)
    raw_low = c1.text_input(
        f"{d.label}, at least",
        value="",
        placeholder="At least",
        label_visibility="collapsed",
        key=wkey(d, "min"),
    )
    raw_high = c2.text_input(
        f"{d.label}, at most",
        value="",
        placeholder="At most",
        label_visibility="collapsed",
        key=wkey(d, "max"),
    )
    span = scope.number_span_caption(RANGES, d.column)
    if span:
        st.caption(span)

    low, low_ok = scope.parse_limit(raw_low)
    high, high_ok = scope.parse_limit(raw_high)
    if not (low_ok and high_ok):
        st.caption(":red[Use numbers only, like 500.]")
    if low is not None and high is not None and low > high:
        st.caption(":red[The first number should be smaller than the second.]")
    if low is None and high is None:
        _reset_exclude(d)
        return None
    rule = scope.rule_minmax(d, low, high, pig)
    rule.negated = _exclude_control(
        d, "Excluding every page in this range. Everything else stays in."
    )
    return rule


def render_tristate(d: FilterDef, pig: ParamGenerator) -> Rule | None:
    st.markdown(f"**{d.label}**")
    value = st.selectbox(
        d.label,
        ["Any", "Yes", "No"],
        label_visibility="collapsed",
        key=wkey(d, "value"),
    )
    if value == "Any":
        _reset_exclude(d)
        return None
    rule = scope.rule_tristate(d, value)
    rule.negated = _exclude_control(
        d, "Excluding these pages. Everything else stays in."
    )
    return rule


RENDERERS = {
    "vocab_multi": render_vocab_multi,
    "topic": render_topic,
    "date_preset": render_date_preset,
    "date_range": render_date_range,
    "reasons": render_reasons,
    "minmax": render_minmax,
    "vocab_list": render_reasons,
    "tristate": render_tristate,
}


def render_filter(d: FilterDef, pig: ParamGenerator) -> Rule | None:
    return RENDERERS[d.kind](d, pig)


def filter_in_use(d: FilterDef) -> bool:
    if d.kind in ("vocab_multi", "reasons", "vocab_list", "topic"):
        return bool(ss.get(wkey(d, "values")))
    if d.kind == "minmax":
        low, low_ok = scope.parse_limit(ss.get(wkey(d, "min")))
        high, high_ok = scope.parse_limit(ss.get(wkey(d, "max")))
        return (low is not None) or (high is not None)
    if d.kind == "tristate":
        return ss.get(wkey(d, "value"), "Any") != "Any"
    if d.kind == "date_preset":
        preset = ss.get(wkey(d, "preset"), "Any time")
        if preset == "Custom range":
            return bool(ss.get(wkey(d, "start")) or ss.get(wkey(d, "end")))
        return preset != "Any time"
    if d.kind == "date_range":
        return bool(ss.get(wkey(d, "start")) or ss.get(wkey(d, "end")))
    return False


def render_group_body(keys, by_key, pig: ParamGenerator) -> list[Rule]:
    """Filters two to a row, so a section stays readable."""
    found: list[Rule] = []
    for row_start in range(0, len(keys), 2):
        cols = st.columns(2)
        for col, key in zip(cols, keys[row_start : row_start + 2]):
            with col:
                rule = render_filter(by_key[key], pig)
                if rule:
                    found.append(rule)
    return found


# ---------------------------------------------------------------------------
# Page skeleton

st.title("GOV.UK Content Audit Tool")
st.caption(
    "Find GOV.UK guidance that needs attention. Filter and search, "
    "preview your data, then download a CSV with recommendations for "
    "action - most urgent first.",
    width=TEXT_WIDTH,
)

(tab_define, tab_preview, tab_download, tab_help) = st.tabs(
    ["1. Filter and search", "2. Preview", "3. Download", "Help and definitions"]
)

# ---------------------------------------------------------------------------
# Help and definitions: reference, so no status block and no numbering.

with tab_help:
    help_col, _gutter = st.columns([3, 1], gap="large")

with help_col:
    st.header("About the tool")
    st.markdown(
        "This tool audits all GOV.UK pages and files that may contain "
        "guidance, including 44 document types and their attachments."
    )
    st.markdown(
        "It helps you identify content that may need to be reviewed or removed."
    )
    st.markdown("Each page and file is assessed against four factors:")
    st.markdown(
        "- **Freshness** – whether the content may be out of date\n"
        "- **Functionality** – whether there are technical issues, such as "
        "broken links or inaccessible files\n"
        "- **Accessibility** – whether the content may present "
        "accessibility or usability challenges\n"
        "- **User engagement** – whether users appear to be finding the "
        "content useful"
    )
    st.markdown(
        "These factors are combined into an overall priority score, which "
        "is used to rank content. Higher-priority items appear nearer the "
        "top of the results and may need attention sooner."
    )
    st.markdown(
        "Each item in the audit also includes a recommended action and a "
        "summary of any issues identified."
    )
    st.markdown(
        "Use the **Filter and search** tab to choose which content to "
        "include in your audit. Use the **Preview** tab to review the "
        "results. Use the **Download** tab to export the same results as "
        "a CSV file for further analysis."
    )

with tab_help:
    st.divider()
    st.header("About the data")
    st.markdown("Search the table to find definitions of all the data in this tool.")
    st.markdown("**Find data**")
    lookup = st.text_input(
        "Find data",
        label_visibility="collapsed",
        placeholder="Type part of a name or meaning",
        key="defs_lookup",
    )
    defs_df = pd.DataFrame(
        [
            (scope.column_title(field), scope.download_name(field), meaning)
            for field, meaning in DEFINITIONS
        ],
        columns=["Label", "Name in the CSV", "What it means"],
    )
    if lookup.strip():
        needle = lookup.strip().lower()
        mask = False
        for col in defs_df.columns:
            mask = mask | defs_df[col].str.lower().str.contains(needle, regex=False)
        defs_df = defs_df[mask]
        if defs_df.empty:
            st.caption(f'Nothing matches "{lookup.strip()}".')
    st.dataframe(defs_df, hide_index=True, width="stretch")


# ---------------------------------------------------------------------------
# Tab 1: Filter and search

filter_rules: list[Rule] = []
search_error: str | None = None
pig = ParamGenerator()


def _clear_search() -> None:
    ss.text_query_box = ""
    ss.title_query_box = ""
    ss.url_query_box = ""
    ss.executed_text = ""
    ss.executed_title = ""
    ss.executed_url = ""


def _request_apply() -> None:
    ss.apply_requested = True


def _clear_everything() -> None:
    _clear_search()
    ui_state.clear_filters(ss, [d.key for d in scope.REGISTRY])
    ui_state.bump(ss, "urls")
    for key in ("url_fp", "url_matched", "url_total", "url_removed"):
        ss.pop(key, None)
    ss.qs_attachments = True
    ss.qs_withdrawn = True
    ss.qs_historic = True
    ss.qs_crawled = False
    ss.rel_children = False
    ss.rel_parents = False
    ss.page_num = 1
    ss.prepared = None
    ss.applied = scope.empty_applied()
    ss.has_run_once = False
    ss.pop("apply_requested", None)


with tab_define:
    status_define = st.container()
    left, right = st.columns([2, 3], gap="large")

    with left:
        st.header("Search")
        search_help = search_help_markdown()
        st.markdown("**Search page and PDF text**", help=search_help)
        st.text_area(
            "Search page and PDF text",
            label_visibility="collapsed",
            key="text_query_box",
            height=140,
            placeholder=("waste\nroad safety | food standards\nNOT closed review"),
        )
        st.markdown("**Search page and PDF titles**", help=search_help)
        st.text_area(
            "Search page and PDF titles",
            label_visibility="collapsed",
            key="title_query_box",
            height=100,
            placeholder="Same rules as for text search",
        )
        st.markdown("**Search by URL**", help=search_help)
        st.text_area(
            "Search by URL",
            label_visibility="collapsed",
            key="url_query_box",
            height=100,
            placeholder="Same rules as for text search",
        )
        st.caption(
            "Select Update results to run your search and your filters together."
        )
        box_text = ss.get("text_query_box", "") or ""
        box_title = ss.get("title_query_box", "") or ""
        box_url = ss.get("url_query_box", "") or ""
        st.button(
            "Clear search",
            key="clear_search_button",
            on_click=_clear_search,
            disabled=not (
                box_text
                or box_title
                or box_url
                or ss.executed_text
                or ss.executed_title
                or ss.executed_url
            ),
            help="Empties all three search boxes. Your filters stay as they are.",
        )
        search_error_slot = st.container()

        st.header("Upload your own list of URLs")
        with st.expander("Add URLs"):
            st.markdown("**Upload a list of GOV.UK URLs**")
            uploaded = st.file_uploader(
                "Upload a list of GOV.UK URLs",
                label_visibility="collapsed",
                type=["csv", "txt"],
                key=ui_state.widget_key(ss, "urls", "uploader"),
            )
            if uploaded is not None:
                file_fp = (uploaded.name, uploaded.size)
                if ss.get("url_fp") != file_fp:
                    raw = uploaded.getvalue().decode("utf-8", errors="ignore")
                    tokens = [
                        t.strip().strip('"').strip("'")
                        for chunk in raw.split("\n")
                        for t in chunk.split(",")
                    ]
                    urls = [t for t in tokens if "/" in t]
                    known_urls = cached_known_urls()
                    matched = [u for u in urls if u in known_urls]
                    ss.url_fp = file_fp
                    ss.url_matched = matched
                    ss.url_total = len(urls)
                matched = ss.get("url_matched", [])
                total_urls = ss.get("url_total", len(matched))
                st.caption(
                    f"{len(matched):,} of {total_urls:,} URLs matched a "
                    "content item in our dataset. Select Update results "
                    "to get audit data for them."
                )
                if st.button("Remove this list", key="url_remove"):
                    ui_state.bump(ss, "urls")
                    ss.pop("url_fp", None)
                    ss.pop("url_matched", None)
                    ss.pop("url_total", None)
                    st.rerun()

    with right:
        st.header("Basic filters")

        st.subheader("Which content items to include")
        qs_a, qs_b, qs_c = st.columns(3)
        with qs_a:
            stage_attachments = st.checkbox("Attachments", key="qs_attachments")
        with qs_b:
            stage_withdrawn = st.checkbox("Withdrawn pages", key="qs_withdrawn")
        with qs_c:
            stage_historic = st.checkbox("Historic pages", key="qs_historic")

        rel_a, rel_b, rel_c = st.columns(3)
        with rel_a:
            stage_children = st.checkbox(
                "Also include attachments when only the parent page matches",
                key="rel_children",
            )
        with rel_b:
            stage_parents = st.checkbox(
                "Also include the parent page when only the attachment matches",
                key="rel_parents",
            )
        with rel_c:
            stage_crawled = st.checkbox(
                "Only include items reached by the third-party web crawler",
                key="qs_crawled",
            )

        by_key = scope.registry_by_key()
        seen_advanced = False
        for grp in scope.FILTER_GROUPS:
            keys = [k for k in grp.keys if k in by_key]
            if not keys:
                continue
            if not grp.open_by_default and not seen_advanced:
                seen_advanced = True
                st.header("Advanced filters")
            if grp.open_by_default:
                st.divider()
                st.subheader(grp.heading)
                st.caption(grp.blurb)
                filter_rules.extend(render_group_body(keys, by_key, pig))
            else:
                n_used = sum(1 for k in keys if filter_in_use(by_key[k]))
                label = grp.heading
                if n_used:
                    label += f"  -  {n_used} in use"
                exp_key = f"sec_{grp.heading}"
                with st.expander(label, expanded=ss.get(exp_key, False)):
                    st.caption(grp.blurb)
                    filter_rules.extend(render_group_body(keys, by_key, pig))

        st.divider()
        st.button(
            "Update results",
            key="apply_filters",
            type="primary",
            on_click=_request_apply,
        )

# ---------------------------------------------------------------------------
# Apply what was staged, if asked. Every widget has rendered by now, so
# filter_rules holds what the user can see on screen.

staged_include = (stage_attachments, stage_withdrawn, stage_historic, stage_crawled)
staged_related = (stage_children, stage_parents)
staged_url_fp = ss.get("url_fp")

if ss.pop("apply_requested", False):
    try:
        parse(box_text)
        parse(box_title)
        parse(box_url)
    except QueryError as e:
        search_error = str(e)
    else:
        ss.executed_text = box_text
        ss.executed_title = box_title
        ss.executed_url = box_url
        ss.applied = {
            "rules": list(filter_rules),
            "include": staged_include,
            "related": staged_related,
            "url_fp": staged_url_fp,
            "url_ids": ss.get("url_matched"),
        }
        ss.has_run_once = True
if search_error:
    search_error_slot.error(search_error)

applied = ss.applied
stale = scope.scope_signature(
    filter_rules,
    box_text,
    box_title,
    box_url,
    staged_include,
    staged_related,
    staged_url_fp,
) != scope.scope_signature(
    applied["rules"],
    ss.executed_text,
    ss.executed_title,
    ss.executed_url,
    applied["include"],
    applied["related"],
    applied["url_fp"],
)
(include_attachments, include_withdrawn, include_historic, crawled_only) = applied[
    "include"
]
add_children, add_parents = applied["related"]

# ---------------------------------------------------------------------------
# Assemble the scope, from what was applied and never from the widgets

apply_pig = ParamGenerator(label="a_")
always_rules: list[Rule] = scope.quick_rules(
    include_attachments, include_withdrawn, include_historic, crawled_only
)

# One chip covers all three search boxes (see _clear_search): the parser
# already validated ss.executed_* when Update results was pressed, so
# compile_query cannot raise here.
search_rule: Rule | None = None
_search_parts: list[str] = []
_search_params: dict = {}
_search_bits: list[str] = []
for column, box, label in (
    ("html", ss.executed_text, "page text"),
    ("title", ss.executed_title, "title"),
    ("public_url", ss.executed_url, "URL"),
):
    if not box.strip():
        continue
    sql, params = compile_query(column, box, apply_pig)
    if not sql:
        continue
    _search_parts.append(sql)
    _search_params.update(params)
    _search_bits.append(f"{label}: {scope.search_summary(box)}")
if _search_parts:
    search_rule = Rule(
        "search",
        "Search - " + "; ".join(_search_bits),
        " AND ".join(_search_parts),
        _search_params,
        kind="search",
    )
    always_rules.append(search_rule)

url_rule: Rule | None = None
url_matched_ids = applied["url_ids"] if applied["url_fp"] else None
if url_matched_ids:
    p = apply_pig.generate()
    url_rule = Rule(
        "url",
        f"URL list: {len(url_matched_ids):,} pages",
        f"public_url IN :{p}",
        {p: tuple(url_matched_ids)},
        kind="url",
    )
    always_rules.append(url_rule)

scope_sql, scope_params = scope.assemble_scope(applied["rules"], always_rules)

# Nothing is fetched until Update results has been pressed at least once -
# the whole point of staying out of memory until the user asks for rows.
if ss.has_run_once:
    base_df = cached_scope_fetch(scope_sql, scope_params)

    related_df = base_df
    if add_children or add_parents:
        urls = base_df["public_url"].dropna().unique().tolist()
        parent_urls = base_df["parent_public_url"].dropna().unique().tolist()
        rel_pig = ParamGenerator(label="rel_")
        parts = [f"({scope_sql})"]
        rel_params = dict(scope_params)
        if add_children and urls:
            p = rel_pig.generate()
            parts.append(f"(parent_public_url IN :{p})")
            rel_params[p] = tuple(urls)
        if add_parents and parent_urls:
            p = rel_pig.generate()
            parts.append(f"(public_url IN :{p})")
            rel_params[p] = tuple(parent_urls)
        if len(parts) > 1:
            related_df = cached_scope_fetch(" OR ".join(parts), rel_params)

    count = len(related_df)
else:
    base_df = None
    related_df = None
    count = 0
ss.match_count = count

if url_matched_ids:
    denominator = len(url_matched_ids)
else:
    denominator = ROW_COUNT
whole_table = denominator == ROW_COUNT

scope_fp = hashlib.md5(
    repr((scope_sql, sorted(scope_params.items(), key=str))).encode()
).hexdigest()
if ss.get("scope_fp") != scope_fp:
    ss.scope_fp = scope_fp
    ss.page_num = 1

display_rules: list[Rule] = []
if search_rule:
    display_rules.append(search_rule)
if url_rule:
    display_rules.append(url_rule)
display_rules.extend(applied["rules"])
display_rules.extend(r for r in always_rules if r.kind == "quick")
if add_children:
    display_rules.append(
        Rule("rel_children", "Also including their attachments", "", kind="related")
    )
if add_parents:
    display_rules.append(
        Rule(
            "rel_parents",
            "Also including the pages they are attached to",
            "",
            kind="related",
        )
    )

# ---------------------------------------------------------------------------
# Tab 2: Preview

with tab_preview:
    status_preview = st.container()
    if not ss.has_run_once:
        st.info(
            "Choose your filters and search, then select Update results to "
            "see your results."
        )
    else:
        st.caption("Default order is by priority rank. 1 is most urgent.")
        total_pages = max(1, math.ceil(count / PAGE_SIZE))
        ss.page_num = min(max(1, ss.page_num), total_pages)

        def _page_back() -> None:
            ss.page_num = max(1, ss.page_num - 1)

        def _page_next() -> None:
            ss.page_num = ss.page_num + 1

        view_cols = [c for c in ALL_COLUMNS if c in related_df.columns]
        start = (ss.page_num - 1) * PAGE_SIZE
        page_df = related_df[view_cols].iloc[start : start + PAGE_SIZE].copy()

        if add_children or add_parents:
            base_ids = set(base_df.index)
            base_urls = set(base_df["public_url"].dropna())
            base_parent_urls = set(base_df["parent_public_url"].dropna())

            def why(row) -> str:
                if row.name in base_ids:
                    return "Matches your rules"
                if row.get("parent_public_url") in base_urls:
                    return "Added as a child or attachment of a match"
                if row.get("public_url") in base_parent_urls:
                    return "Added as the parent of a match"
                return "Matches your rules"

            page_df.insert(1, "Why it's here", page_df.apply(why, axis=1))

        if "broken_urls" in page_df.columns:
            # Show every broken URL as a list (a link cell holds only one).
            def all_broken(value):
                if value is None or isinstance(value, float):
                    return []
                if isinstance(value, str):
                    value = [u.strip(" []'\"") for u in value.split(",")]
                return [u for u in value if u]

            page_df["broken_urls"] = page_df["broken_urls"].map(all_broken)

        score_max = float((RANGES.get("adjusted_score") or {}).get("max") or 1.0)
        st.dataframe(
            page_df,
            hide_index=True,
            width="stretch",
            column_config=scope.preview_column_config(
                page_df.columns,
                score_max,
                date_columns=[
                    c
                    for c in page_df.columns
                    if pd.api.types.is_datetime64_any_dtype(page_df[c])
                ],
            ),
        )
        first_row = start + 1
        last_row = min(start + PAGE_SIZE, count)
        if count:
            st.caption(
                f"Showing {first_row:,} to {last_row:,} of {count:,} - "
                f"page {ss.page_num} of {total_pages:,}"
            )
        pg_back, pg_next, _spare = st.columns([1, 1, 6])
        pg_back.button(
            "Previous",
            key="pv_back",
            on_click=_page_back,
            disabled=ss.page_num <= 1,
            width="stretch",
        )
        pg_next.button(
            "Next",
            key="pv_next",
            on_click=_page_next,
            disabled=ss.page_num >= total_pages,
            width="stretch",
        )

# ---------------------------------------------------------------------------
# Tab 3: Download

with tab_download:
    status_download = st.container()
    if not ss.has_run_once:
        st.info(
            "Choose your filters and search, then select Update results "
            "before you can download anything."
        )
    else:
        standard_label = f"Standard set - {len(scope.STANDARD_COLUMNS)} columns"
        everything_label = f"Everything - all {len(ALL_COLUMNS)} columns"
        ss.setdefault("dl_cols", standard_label)
        st.markdown("**Columns to download**")
        col_choice = st.segmented_control(
            "Columns to download",
            [standard_label, everything_label],
            label_visibility="collapsed",
            key="dl_cols",
        )
        export_cols = (
            ALL_COLUMNS
            if col_choice == everything_label
            else [c for c in scope.STANDARD_COLUMNS if c in related_df.columns]
        )
        # headers are renamed here only: the table and the filters keep
        # the underlying names

        export_fp = hashlib.md5(
            repr((scope_fp, tuple(export_cols))).encode()
        ).hexdigest()
        if ss.get("prepared") and ss.prepared.get("fp") != export_fp:
            ss.prepared = None

        if st.button("Prepare CSV", key="dl_prepare", disabled=stale):
            with st.spinner("Preparing your CSV"):
                import csv as _csv

                blob = (
                    related_df[export_cols]
                    .rename(columns=scope.DOWNLOAD_NAMES)
                    .to_csv(quoting=_csv.QUOTE_ALL)
                    .encode("utf-8")
                )
                ss.prepared = {"fp": export_fp, "bytes": blob, "count": count}
        if stale:
            st.caption(
                "Select Update results on the Filter and search tab first. "
                "What is on screen is not what this file would contain."
            )

        if ss.get("prepared"):
            blob = ss.prepared["bytes"]
            st.download_button(
                f"Download CSV - {ss.prepared['count']:,} rows "
                f"({human_size(len(blob))})",
                data=blob,
                file_name="content-audit-tool-export.csv",
                mime="text/csv",
                key="dl_csv",
            )

# ---------------------------------------------------------------------------
# The always-visible status strip, filled last so it reflects this run.

active_rules = [r for r in display_rules if r.kind != "quick"]
chip_rules = list(display_rules)
chip_ids = [scope.chip_id(r) for r in chip_rules]
chip_labels = {scope.chip_id(r): scope.chip_label(r) for r in chip_rules}
chips_fp = hashlib.md5("|".join(chip_ids).encode()).hexdigest()[:8]

_QUICK_BACK_ON = {
    "quick_attachments": ("qs_attachments", True),
    "quick_withdrawn": ("qs_withdrawn", True),
    "quick_historic": ("qs_historic", True),
    "quick_crawled": ("qs_crawled", False),
}
_QUICK_KEYS = (
    "quick_attachments",
    "quick_withdrawn",
    "quick_historic",
    "quick_crawled",
)
_RELATED_KEYS = ("rel_children", "rel_parents")


def _remove_chips(ids: tuple, pills_key: str) -> None:
    kept = set(ss.get(pills_key) or [])
    applied = dict(ss.get("applied") or scope.empty_applied())
    include = list(applied["include"])
    related = list(applied["related"])
    for cid in ids:
        if cid in kept:
            continue
        if cid == "search":
            _clear_search()
        elif cid == "url":
            ui_state.bump(ss, "urls")
            for k in ("url_fp", "url_matched", "url_total", "url_removed"):
                ss.pop(k, None)
            applied["url_fp"] = None
            applied["url_ids"] = None
        elif cid.startswith("filter:"):
            name = cid.split(":", 1)[1]
            ui_state.clear_filter(ss, name)
            applied["rules"] = [r for r in applied["rules"] if r.key != name]
        elif cid in _RELATED_KEYS:
            ss[cid] = False
            related[_RELATED_KEYS.index(cid)] = False
        elif cid in _QUICK_BACK_ON:
            key, opens_as = _QUICK_BACK_ON[cid]
            ss[key] = opens_as
            include[_QUICK_KEYS.index(cid)] = opens_as
    applied["include"] = tuple(include)
    applied["related"] = tuple(related)
    ss.applied = applied


def render_status(container, key: str) -> None:
    with container:
        with st.container(border=True):
            if not ss.has_run_once:
                st.markdown(
                    "### Choose your filters and search, then select Update results"
                )
            elif count == denominator and whole_table:
                st.markdown(f"### {count:,} content items in full dataset")
            else:
                st.markdown(f"### {count:,} of {denominator:,} content items match")
            if chip_ids:
                pills_key = f"chips_{key}_{chips_fp}"
                st.pills(
                    "Your rules. Selecting one removes it.",
                    chip_ids,
                    selection_mode="multi",
                    default=chip_ids,
                    format_func=lambda cid: chip_labels[cid],
                    key=pills_key,
                    on_change=_remove_chips,
                    args=(tuple(chip_ids), pills_key),
                    label_visibility="collapsed",
                )
            if stale and ss.has_run_once:
                st.warning(
                    "You have changed your filters or search. Select "
                    "Update results to see the new count."
                )
            if search_error:
                st.error(search_error)
            if ss.has_run_once and count == 0 and active_rules:
                st.markdown("")
                st.info(
                    "Nothing matches your criteria.  \n"
                    "Remove a search / filter or clear everything."
                )
            b_apply, b_clear, _spare = st.columns([1, 1, 3])
            b_apply.button(
                "Update results",
                key=f"apply_{key}",
                type="primary",
                on_click=_request_apply,
                width="stretch",
            )
            b_clear.button(
                "Clear everything",
                key=f"reset_{key}",
                on_click=_clear_everything,
                width="stretch",
                help="Clears your search, your URL list and every filter.",
            )
        st.write("")


render_status(status_define, "define")
render_status(status_preview, "preview")
render_status(status_download, "download")
