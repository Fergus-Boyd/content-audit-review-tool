"""Scope logic for the audit tool app.

A "scope" is everything that decides which pages are in the user's results:
search, quick settings, the URL list, the filters and the related-pages
additions. This module holds the declarative filter registry, builds SQL
WHERE fragments (named ``:pN`` parameters, the same convention
``data_selection.py`` already uses), and writes the plain-English
descriptions used on the tags.

No Streamlit import at module load (``preview_column_config`` imports it
locally), and no ``dwutils`` import at all, so everything else here is unit
testable without either installed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .params import ParamGenerator

# ---------------------------------------------------------------------------
# Column sets

STANDARD_COLUMNS = [
    "priority_rank",
    "title",
    "public_url",
    "file_extension",
    "document_type",
    "categories",
    "primary_publishing_org",
    "public_updated_at",
    "pdf_modified",
    "publishing_app",
    "withdrawn",
    "is_historic",
    "is_attachment",
    "parent_public_url",
    "sessions",
    "users",
    "file_download_navigated",
    "word_count",
    "word_to_heading_ratio",
    "flesch_reading_ease_score",
    "is_broken",
    "n_broken_links",
    "broken_link_ratio",
    "broken_urls",
    "screaming_frog_available",
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

FLAG_FAMILIES = [
    ("functionality", "Functionality"),
    ("freshness", "Freshness"),
    ("accessibility", "Accessibility and usability"),
    ("negative_user_engagement", "User engagement"),
]

# ---------------------------------------------------------------------------
# Filter registry


@dataclass(frozen=True)
class FilterDef:
    """One filter the user can apply.

    kind decides both the widget and the SQL builder:
      vocab_multi   multiselect over vocab values of `column`
      topic         multiselect over taxonomy, matches the categories list
      date_preset   selectbox of date presets over `column`
      date_range    plain from/to date inputs over `column`
      reasons       multiselect over a *_reasons list column
      vocab_list    the same, over any other list column
      minmax        two text boxes parsed as numbers. Not st.number_input,
                    which always draws plus and minus buttons: see
                    render_minmax
      tristate      Any / Yes / No selectbox over a boolean `column`
    """

    key: str
    label: str
    group: str
    kind: str
    # Labels want to stay short enough not to wrap. A wrapped label is a
    # line taller than its neighbour's, which drops its control out of
    # line with the filter beside it. There is no tooltip to fall back
    # on any more: anything that needs explaining belongs on the
    # Definitions tab, where people can find it without hovering.
    column: str


REGISTRY: list[FilterDef] = [
    FilterDef(
        "organisation",
        "Main publishing organisation",
        "Publishing",
        "vocab_multi",
        "primary_publishing_org",
    ),
    FilterDef(
        "document_type", "Document type", "Publishing", "vocab_multi", "document_type"
    ),
    FilterDef(
        "topic", "Category (topic or taxon)", "Publishing", "topic", "categories"
    ),
    FilterDef(
        "public_updated_at",
        "Date of last editorial update (HTML only)",
        "Publishing",
        "date_preset",
        "public_updated_at",
    ),
    # A basic filter rather than an advanced one: whether something is
    # a PDF is one of the first things anybody narrows by, and it sits
    # with the other questions about what a thing is.
    FilterDef(
        "file_extension",
        "File extension",
        "Publishing",
        "vocab_multi",
        "file_extension",
    ),
    FilterDef(
        "recommendation", "Recommendation", "Scores", "vocab_multi", "recommendation"
    ),
    FilterDef("adjusted_score", "Overall score", "Scores", "minmax", "adjusted_score"),
]
# The exact problems, not how serious they are. Severity used to be the
# filter here and the reasons were tucked away under Advanced. Content
# designers wanted the opposite: "critical" and "high" do not tell you
# what is wrong with the page, and knowing what is wrong is the whole
# job. Severity is gone as a filter; the *_flag columns are still in the
# data and still in the download.
for _fam, _fam_label in FLAG_FAMILIES:
    REGISTRY.append(
        FilterDef(f"why_{_fam}", _fam_label, "Flags", "reasons", f"{_fam}_reasons")
    )
REGISTRY.append(
    FilterDef(
        "recommend_convert",
        "Recommended for conversion to HTML",
        "Flags",
        "tristate",
        "recommend_convert",
    )
)
# The rest of the columns. There is no advanced mode to switch on:
# these sections sit under the "Advanced filters" heading already open
# to one click, and adding a filter is one line here.
_MORE: list[tuple] = [
    # (key/column, label, group, kind)
    # The two date filters share a row on purpose. A date filter is a
    # name above two boxes, which is taller than a filter whose name is
    # its own label, so pairing one with a plain control would leave the
    # row crooked. Two dates side by side match each other exactly.
    (
        "date_published",
        "Date published (HTML only)",
        "Publishing details",
        "date_range",
    ),
    ("pdf_modified", "Date PDF was last changed", "Publishing details", "date_range"),
    ("publishing_app", "Publishing app", "Publishing details", "vocab_multi"),
    ("publishing_orgs", "Publishing organisations", "Publishing details", "vocab_list"),
    # Whether it has any, before how many it has.
    ("is_parent", "Has attachments", "Publishing details", "tristate"),
    ("no_of_attachments", "Number of attachments", "Publishing details", "minmax"),
    # One-box filters together, two-box filters together. Filters are
    # laid out two to a row, and a two-box filter carries its name on a
    # line of its own, so pairing one with a one-box filter leaves the
    # row crooked. Keeping like with like is what holds the rows level.
    (
        "flesch_reading_ease_score",
        "Reading ease score",
        "Content and readability",
        "minmax",
    ),
    ("word_count", "Word count", "Content and readability", "minmax"),
    (
        "word_to_heading_ratio",
        "Words per heading (HTML only)",
        "Content and readability",
        "minmax",
    ),
    ("expected_reading_time_mins", "Reading time", "Content and readability", "minmax"),
    ("pdf_page_count", "PDF page count", "Content and readability", "minmax"),
    # Last, and on its own. File extension used to pair with it; now
    # that it is a basic filter, Readability is the only one-box
    # control left here, and pairing it with a two-box filter would
    # leave the row crooked.
    ("readability", "Readability", "Content and readability", "vocab_multi"),
    ("sessions", "Sessions (HTML and PDFs)", "Traffic and downloads", "minmax"),
    (
        "engaged_sessions",
        "Engaged sessions (HTML and PDFs)",
        "Traffic and downloads",
        "minmax",
    ),
    ("users", "Users (HTML only)", "Traffic and downloads", "minmax"),
    (
        "engagement_time_secs",
        "Engagement time (HTML only)",
        "Traffic and downloads",
        "minmax",
    ),
    (
        "total_file_downloads",
        "File downloads (non-HTML files)",
        "Traffic and downloads",
        "minmax",
    ),
    (
        "is_broken",
        "Broken page or file (404)",
        "Accessibility and broken links",
        "tristate",
    ),
    (
        "is_pdf_with_accessible_version",
        "PDF has accessible version",
        "Accessibility and broken links",
        "tristate",
    ),
    (
        "n_broken_links",
        "Broken links (HTML only)",
        "Accessibility and broken links",
        "minmax",
    ),
]
REGISTRY += [FilterDef(row[0], row[1], row[2], row[3], row[0]) for row in _MORE]


@dataclass(frozen=True)
class FilterGroup:
    """One titled section of the filter panel. The everyday ones start
    open; the rest are one click away but always visible as headings."""

    heading: str
    blurb: str
    open_by_default: bool
    keys: tuple[str, ...]


def _keys_in(group: str) -> tuple[str, ...]:
    return tuple(d.key for d in REGISTRY if d.group == group)


FILTER_GROUPS: list[FilterGroup] = [
    FilterGroup(
        "About the page or file",
        "Who published it and when; what kind of page or file it is.",
        True,
        (
            "organisation",
            "document_type",
            "topic",
            "public_updated_at",
            "file_extension",
        ),
    ),
    FilterGroup(
        "What the audit says",
        "The overall score and what the tool recommends doing.",
        True,
        ("recommendation", "adjusted_score"),
    ),
    FilterGroup(
        "Problems found",
        "The exact problems found on the page or file.",
        True,
        _keys_in("Flags"),
    ),
    FilterGroup(
        "Publishing details",
        "How and when the page or file was published.",
        False,
        _keys_in("Publishing details"),
    ),
    FilterGroup(
        "Content and readability",
        "How long the page or file is and how easy it is to read or scan.",
        False,
        _keys_in("Content and readability"),
    ),
    FilterGroup(
        "Traffic and downloads",
        "How many people came to the page or file and what they downloaded.",
        False,
        _keys_in("Traffic and downloads"),
    ),
    FilterGroup(
        "Accessibility and broken links",
        "Whether the page or file works and whether an accessible version exists.",
        False,
        _keys_in("Accessibility and broken links"),
    ),
]


def registry_by_key() -> dict[str, FilterDef]:
    return {d.key: d for d in REGISTRY}


# Which stats a filter widget needs from the data, keyed by FilterDef.kind.
# "topic" matches against the taxonomy file, not the data, and "tristate"
# is always a fixed Any/Yes/No, so neither needs anything here.
STATS_KIND_FOR_FILTER_KIND = {
    "vocab_multi": "category",
    "reasons": "list",
    "vocab_list": "list",
    "minmax": "numeric",
    "date_preset": "datetime",
    "date_range": "datetime",
}


def columns_needing_stats() -> list[tuple[str, str]]:
    """(column, stats_kind) for every REGISTRY column whose widget needs
    real vocab or bounds from the data - what data_selection.fetch_vocab_stats
    fetches instead of loading the whole table."""
    seen: dict[str, str] = {}
    for d in REGISTRY:
        kind = STATS_KIND_FOR_FILTER_KIND.get(d.kind)
        if kind:
            seen.setdefault(d.column, kind)
    return sorted(seen.items())


# ---------------------------------------------------------------------------
# Rules


@dataclass
class Rule:
    """One active rule in the scope.

    sql and params describe the POSITIVE condition; negated means the
    rule leaves matching pages out. text is a plain-English description
    of the positive condition, like "Document type is pdf".
    kind: "filter", "search", "quick", "url" or "related".
    params is a dict of named parameters (":p_0" style, see params.py),
    matching how dwutils.db.query and data_selection.py already take
    their SQL parameters."""

    key: str
    text: str
    sql: str
    params: dict = field(default_factory=dict)
    negated: bool = False
    kind: str = "filter"


def or_join(values: list[str]) -> str:
    values = [str(v) for v in values]
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + " or " + values[-1]


def _list_membership_sql(
    column: str, values: list[str], pig: ParamGenerator
) -> tuple[str, dict]:
    """SQL for "this list column contains any of these values".

    List columns are stored as stringified Python list text, so membership
    is a LIKE per value, ORed together - not DuckDB's list_has_any."""
    clauses = []
    params: dict = {}
    for v in values:
        p = pig.generate()
        params[p] = f"%{v}%"
        clauses.append(f"({column} LIKE :{p})")
    return "(" + " OR ".join(clauses) + ")", params


# ---------------------------------------------------------------------------
# Taxonomy helpers


def load_taxonomy(path: Path | None = None) -> dict:
    """The topic tree as nested dicts, {level 1 name: {level 2 name: {...}}}.

    taxon_map.json (moved here from notebooks/, not sensitive content
    data) mirrors GOV.UK's taxonomy, where a topic can sit under more
    than one parent - so the same name may appear at more than one
    point in the tree."""
    if path is None:
        path = Path(__file__).parent / "taxon_map.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def taxon_descendants(tree: dict) -> dict[str, list[str]]:
    """{name: every topic inside it, at any depth}, for rule_topic's
    "include topics inside this one". A name met under more than one
    parent collects descendants from every occurrence."""
    descendants: dict[str, set[str]] = {}

    def walk(node: dict) -> set[str]:
        names: set[str] = set()
        for name, children in node.items():
            inside = walk(children)
            descendants.setdefault(name, set()).update(inside)
            names.add(name)
            names.update(inside)
        return names

    walk(tree)
    return {name: sorted(inside) for name, inside in descendants.items()}


def taxon_paths(tree: dict) -> dict[str, str]:
    """{name: "Level 1 > Level 2 > Level 3"}, in the order each name is
    first met walking the tree - parents before their children, so the
    dict order is a hierarchy sort for a topic list. A name met under
    more than one parent keeps the first path found."""
    paths: dict[str, str] = {}

    def walk(node: dict, prefix: list[str]) -> None:
        for name, children in node.items():
            path = prefix + [name]
            paths.setdefault(name, " > ".join(path))
            walk(children, path)

    walk(tree, [])
    return paths


# ---------------------------------------------------------------------------
# SQL builders, one per filter kind. Each returns a Rule for the
# POSITIVE condition; the caller sets negated to exclude instead.


def rule_vocab_multi(d: FilterDef, values: list[str], pig: ParamGenerator) -> Rule:
    p = pig.generate()
    return Rule(
        key=d.key,
        text=f"{d.label}: {or_join([option_label(v) for v in values])}",
        sql=f"{d.column} IN :{p}",
        params={p: tuple(values)},
    )


def rule_topic(
    d: FilterDef,
    taxonomy: dict,
    values: list[str],
    include_descendants: bool,
    pig: ParamGenerator,
) -> Rule:
    expanded: list[str] = []
    for name in values:
        pool = [name, *taxonomy.get(name, [])] if include_descendants else [name]
        for topic in pool:
            if topic not in expanded:
                expanded.append(topic)
    text = f"{d.label}: {or_join(values)}"
    if include_descendants:
        text += " and inside"
    sql, params = _list_membership_sql(d.column, expanded, pig)
    return Rule(key=d.key, text=text, sql=sql, params=params)


DATE_PRESETS = [
    "Any time",
    "Last year",
    "Last 3 years",
    "Older than 3 years",
    "Custom range",
]


def rule_date_preset(
    d: FilterDef, preset: str, start: date | None, end: date | None, pig: ParamGenerator
) -> Rule | None:
    col = d.column
    if preset == "Last year":
        return Rule(
            d.key, f"{d.label}: last year", f"{col} >= CURRENT_DATE - INTERVAL '1 year'"
        )
    if preset == "Last 3 years":
        return Rule(
            d.key,
            f"{d.label}: last 3 years",
            f"{col} >= CURRENT_DATE - INTERVAL '3 years'",
        )
    if preset == "Older than 3 years":
        return Rule(
            d.key,
            f"{d.label}: over 3 years ago",
            f"{col} < CURRENT_DATE - INTERVAL '3 years'",
        )
    if preset == "Custom range":
        # Either end on its own is a real filter. Requiring both was
        # why clearing one box silently dropped the whole rule while
        # the selectbox still said Custom range.
        return rule_date_range(d, start, end, pig)
    return None


def rule_date_range(
    d: FilterDef, start: date | None, end: date | None, pig: ParamGenerator
) -> Rule | None:
    """A plain from/to range, with either end optional."""
    col = d.column
    if start and end:
        p1, p2 = pig.multi_generate(2)
        return Rule(
            d.key,
            f"{d.label}: {fmt_date(start)} to {fmt_date(end)}",
            f"{col} BETWEEN :{p1} AND :{p2}",
            {p1: start, p2: end},
        )
    if start:
        p1 = pig.generate()
        return Rule(
            d.key,
            f"{d.label}: {fmt_date(start)} onwards",
            f"{col} >= :{p1}",
            {p1: start},
        )
    if end:
        p1 = pig.generate()
        return Rule(
            d.key, f"{d.label}: up to {fmt_date(end)}", f"{col} <= :{p1}", {p1: end}
        )
    return None


def rule_reasons(d: FilterDef, values: list[str], pig: ParamGenerator) -> Rule:
    """A column holding a list per row: the problem families, and the
    publishing organisations (kind "vocab_list" uses this too - the SQL is
    the same "matches any of the chosen values" query). Matches a row that
    has any of the chosen values, which is what somebody means by picking
    two."""
    sql, params = _list_membership_sql(d.column, values, pig)
    return Rule(
        key=d.key,
        text=f"{d.label}: {or_join([option_label(v) for v in values])}",
        sql=sql,
        params=params,
    )


# "vocab_list" filters (publishing_orgs) match the same way as "reasons"
# filters: any of the chosen values present in the list column.
rule_vocab_list = rule_reasons


def rule_minmax(d: FilterDef, low, high, pig: ParamGenerator) -> Rule:
    parts: list[str] = []
    params: dict = {}
    bits: list[str] = []
    if low is not None:
        p = pig.generate()
        parts.append(f"{d.column} >= :{p}")
        params[p] = low
        bits.append(f"{low:,} or more")
    if high is not None:
        p = pig.generate()
        parts.append(f"{d.column} <= :{p}")
        params[p] = high
        bits.append(f"{high:,} or fewer")
    return Rule(
        key=d.key,
        text=f"{d.label}: {', '.join(bits)}",
        sql=" AND ".join(parts),
        params=params,
    )


def rule_tristate(d: FilterDef, value: str) -> Rule:
    if value == "Yes":
        return Rule(d.key, f"{d.label}: yes", f"{d.column} IS TRUE")
    return Rule(d.key, f"{d.label}: no", f"{d.column} IS DISTINCT FROM TRUE")


# ---------------------------------------------------------------------------
# Staged and applied
#
# What the widgets hold is staged. What the count was built from is
# applied. They part company as soon as someone changes a filter and
# come back together when Update results is pressed. Comparing the two
# signatures is how the panel knows to say something is waiting.


def empty_applied() -> dict:
    """The scope the tool opens with: everything, nothing excluded."""
    # The fourth is crawl data, which starts off rather than on,
    # because everything is in scope until somebody narrows it.
    return {
        "rules": [],
        "include": (True, True, True, False),
        "related": (False, False),
        "url_fp": None,
        "url_ids": None,
    }


def rule_signature(rule: Rule) -> tuple:
    return (rule.key, rule.sql, repr(rule.params), bool(rule.negated))


def scope_signature(rules, text, title, url, include, related, url_fp) -> tuple:
    """Everything that decides the count, as one comparable value.

    A tuple rather than a hash: it compares by equality and it can be
    read in a traceback. Rules are sorted so the order they were
    rendered in cannot register as a change, and the search strings are
    stripped so a trailing newline is not a change either."""
    return (
        tuple(sorted(rule_signature(r) for r in rules)),
        (text or "").strip(),
        (title or "").strip(),
        (url or "").strip(),
        tuple(bool(b) for b in include),
        tuple(bool(b) for b in related),
        repr(url_fp),
    )


# ---------------------------------------------------------------------------
# Assembly


def clause_sql(rule: Rule) -> str:
    """The rule's SQL with its Exclude negation applied.

    The negation has to be wrapped in COALESCE. In SQL, NOT of an
    unknown value is itself unknown, so a plain NOT drops every row
    where the column is empty along with the rows that matched.

    Empty means the row does not have that value, so excluding that
    value leaves it in."""
    sql = f"({rule.sql})"
    if rule.negated:
        sql = f"(NOT COALESCE({sql}, FALSE))"
    return sql


def combine_filters(rules: list[Rule]) -> tuple[str, dict]:
    """Join the filter rules with AND: every filter must match.

    There used to be a choice between every filter and any filter.
    It was unused, so it is gone and
    filters always narrow. It also removed a trap: under "any filter" a
    Exclude rule stopped removing pages and started adding them, which
    needed a standing warning on screen to explain."""
    if not rules:
        return "TRUE", {}
    params: dict = {}
    for r in rules:
        params.update(r.params)
    return "(" + " AND ".join(clause_sql(r) for r in rules) + ")", params


def assemble_scope(
    filter_rules: list[Rule],
    always_rules: list[Rule],
) -> tuple[str, dict]:
    """The core scope: every filter AND every always-on rule (quick
    settings, search, URL list)."""
    sql, params = combine_filters(filter_rules)
    parts = [sql]
    for r in always_rules:
        parts.append(clause_sql(r))
        params.update(r.params)
    return " AND ".join(parts), params


# ---------------------------------------------------------------------------
# Quick settings


def quick_rules(
    include_attachments: bool,
    include_withdrawn: bool,
    include_historic: bool,
    crawled_only: bool = False,
) -> list[Rule]:
    """The page-type settings, as rules.

    The first three are ticked to begin with and make a rule when they
    are unticked. Crawl data is the other way round: it is unticked to
    begin with, because everything starts in scope, and makes a rule
    when it is ticked."""
    rules: list[Rule] = []
    if crawled_only:
        rules.append(
            Rule(
                "quick_crawled",
                "Only items the third-party crawler reached",
                "screaming_frog_available IS TRUE",
                kind="quick",
            )
        )
    if not include_attachments:
        rules.append(
            Rule(
                "quick_attachments",
                "Attachments",
                "is_attachment IS DISTINCT FROM TRUE",
                kind="quick",
            )
        )
    if not include_withdrawn:
        rules.append(
            Rule(
                "quick_withdrawn",
                "Withdrawn pages",
                "withdrawn IS DISTINCT FROM TRUE",
                kind="quick",
            )
        )
    if not include_historic:
        rules.append(
            Rule(
                "quick_historic",
                "Historic pages",
                "is_historic IS DISTINCT FROM TRUE",
                kind="quick",
            )
        )
    return rules


# ---------------------------------------------------------------------------
# Plain-English search echo


def search_summary(q: str) -> str:
    """One-line summary of a query, for the tag that describes it."""
    lines = [ln.strip() for ln in (q or "").split("\n") if ln.strip()]
    return "; ".join(lines)


# ---------------------------------------------------------------------------
# Removable tags


def option_label(value) -> str:
    """How a stored value is shown in a dropdown.

    Some columns hold machine-shaped values: document_type is
    snake_case, publishing_app is kebab-case, and the recommendation
    and readability bands are lowercase. Every option reads as a
    sentence instead, which is display only. The stored value is what
    the filter matches on and what the CSV carries.

    File extensions are the exception. "Csv" reads as a mistake where
    "CSV" is how everyone writes it."""
    text = str(value)
    if text.isalnum() and len(text) <= 5 and text.islower():
        return text.upper()  # csv, docx, html, pdf
    if " " not in text:
        # Machine-shaped: case_study, legacy-editor, archive. A value
        # that already has a space in it is prose, and its own hyphens
        # are meant, as in "Pre-2018 PDF has no accessible version".
        text = text.replace("_", " ").replace("-", " ")
    return text[:1].upper() + text[1:]


def _badge_safe(text: str) -> str:
    """A tag label is markdown, where square brackets start a link, so a
    value containing one would cut the label in half. Round brackets
    read the same."""
    return text.replace("[", "(").replace("]", ")")


def chip_id(rule: Rule) -> str:
    """A stable name for one removable rule, used both as the pill's
    value and to work out what to clear when it is removed."""
    if rule.kind in ("search", "url"):
        return rule.kind
    if rule.kind in ("related", "quick"):
        return rule.key
    return f"filter:{rule.key}"


def chip_label(rule: Rule, max_len: int = 52) -> str:
    """What the pill says. The colour coding is gone, so the leading
    word and the icon have to carry what kind of rule this is.

    Every label ends in a cross. Testers did not work out that clicking
    a chip removed it, and a caption saying so was not enough. The
    cross is part of the label rather than a target of its own, so a
    click anywhere on the chip still removes it, but the chip now looks
    like something you can dismiss."""
    icon, text = {
        "search": ("search", rule.text),
        "url": ("list", rule.text),
        "related": ("add", rule.text),
        # The page-type settings all narrow by leaving something out,
        # except the crawl one, which narrows by keeping something in.
        # "Exclude - Only items with crawl data" would say the opposite
        # of what ticking it did.
        "quick": (
            "block",
            rule.text if rule.key == "quick_crawled" else f"Exclude - {rule.text}",
        ),
    }.get(rule.kind, (None, None))
    if icon is None:
        icon = "block" if rule.negated else "filter_alt"
        text = f"Exclude - {rule.text}" if rule.negated else rule.text
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "..."
    return f":material/{icon}: {_badge_safe(text)} :material/close:"


# ---------------------------------------------------------------------------
# Preview table


# The plain-English label for every column, as set out in the column
# table. These win over a filter's own label: see column_title.
COLUMN_TITLES = {
    "priority_rank": "Rank",
    "title": "Title",
    "public_url": "URL",
    "content_id": "Content ID (HTML only)",
    "file_extension": "File extension",
    "document_type": "Document type (HTML only)",
    "categories": "Category (topic or taxon)",
    "primary_publishing_org": "Main publishing organisation",
    "publishing_orgs": "All publishing organisations",
    "abstract": "Abstract (HTML only)",
    "date_published": "Date published (HTML only)",
    "public_updated_at": "Date of last editorial update (HTML only)",
    "pdf_modified": "Date PDF was last changed (PDF only)",
    "pdf_created": "Date PDF was created (PDF only)",
    "publishing_app": "Publishing app",
    "withdrawn": "Is withdrawn",
    "is_historic": "Is historic",
    "is_attachment": "Is attachment",
    "parent_content_id": "Parent content ID",
    "parent_public_url": "Parent URL",
    "no_of_attachments": "Number of attachments",
    "is_parent": "Has attachments",
    "sessions": "Sessions",
    "engaged_sessions": "Engaged sessions (HTML only)",
    "users": "Users",
    "engagement_time_secs": "Engagement time (HTML only)",
    "total_file_downloads": "Downloads of all files from a parent page (HTML only)",
    "file_downloads": "File downloads - when attachment (non-HTML files only)",
    "navigation": "File downloads - via navigation (non-HTML files only)",
    "file_download_navigated": "Total file downloads (non-HTML files only)",
    "word_count": "Word count (HTML and PDF only)",
    "sentence_count": "Sentence count (HTML and PDF only)",
    "pdf_page_count": "PDF page count (PDF only)",
    "average_words_per_sentence": "Average words per sentence (HTML and PDF only)",
    "total_headings": "Total headings (HTML only)",
    "word_to_heading_ratio": "Words per heading (HTML only)",
    "expected_reading_time_mins": "Reading time (HTML and PDF only)",
    "flesch_reading_ease_score": "Reading ease score (HTML and PDF only)",
    "readability": "Readability label (HTML and PDF only)",
    "is_broken": "Broken page or file (404)",
    "is_redirecting": "Is redirecting",
    "n_links": "Number of links (HTML only)",
    "n_broken_links": "Number of broken links (HTML only)",
    "broken_link_ratio": "Broken link ratio (HTML only)",
    "broken_urls": "Broken link text (HTML only)",
    "n_redirecting_links": "Number of redirecting links (HTML only)",
    "screaming_frog_available": "Web crawl data available",
    "accessible_url": "Accessible URL (HTML and PDF only)",
    "accessible_url_confidence_score": "Accessible URL confidence score (HTML and PDF only)",
    "is_pdf_with_accessible_version": "Is PDF with an accessible version",
    "freshness_flag": "Freshness flag",
    "freshness_reasons": "Freshness issue",
    "functionality_flag": "Functionality flag",
    "functionality_reasons": "Functionality issue",
    "negative_user_engagement_flag": "User engagement flag",
    "negative_user_engagement_reasons": "User engagement issue",
    "accessibility_flag": "Accessibility flag",
    "accessibility_reasons": "Accessibility and usability issue",
    "accessibility_score": "Accessibility score",
    "functionality_score": "Functionality score",
    "negative_user_engagement_score": "User engagement score",
    "freshness_score": "Freshness score",
    "base_score": "Base score",
    "normalized_score": "Normalised score",
    "score_multiplier": "Score multiplier",
    "adjusted_score": "Overall score",
    "recommendation": "Recommendation",
    "recommend_convert": "Recommend converting to HTML",
    "recommend_transfer_to_tna": "Recommend transfer to TNA",
    "ranking_reasons": "Ranking reasons",
    "issue_summary": "Issue summary",
}


# Column headers in the downloaded CSV where they differ from the name in
# the table. Applied at download time only: the table, the filters and the
# rules all keep the underlying names. Renamed in one pass, so the swap of
# total_file_downloads and file_download_navigated is safe.
DOWNLOAD_NAMES = {
    "total_file_downloads": "all_downloads_from_parent",
    "file_downloads": "all_downloads_of_attachment",
    "navigation": "all_downloads_via_navigation",
    "file_download_navigated": "total_file_downloads",
    "readability": "readability_label",
    "broken_urls": "broken_link_text",
    "normalized_score": "normalised_score",
}


def download_name(name: str) -> str:
    """The column's header in the downloaded CSV."""
    return DOWNLOAD_NAMES.get(name, name)


def column_title(name: str) -> str:
    """The one plain-English name for a column, used for the preview
    heading and the definitions tab. COLUMN_TITLES covers every column
    in the dataset; a column missing from it falls back to the label of
    its filter, then to a tidied-up version of its name.

    The raw name is not lost. The definitions tab lists the CSV header
    beside this one."""
    if name in COLUMN_TITLES:
        return COLUMN_TITLES[name]
    for d in REGISTRY:
        if d.column == name:
            return d.label
    return name.replace("_", " ").capitalize()


def preview_column_config(columns, score_max: float, date_columns=()) -> dict:
    """Streamlit column_config for the preview table. URLs are kept
    narrow: they are long, they are not read, and left wide they push
    the audit columns off the screen."""
    import streamlit as st  # local: keeps the rest of this module pure

    config: dict = {}
    for name in columns:
        title = column_title(name)
        if name == "broken_urls":
            config[name] = st.column_config.ListColumn(title, width="medium")
        elif name in (
            "public_url",
            "parent_public_url",
            "accessible_url",
        ):
            config[name] = st.column_config.LinkColumn(
                title, width="small", display_text="Open"
            )
        elif name == "adjusted_score":
            config[name] = st.column_config.ProgressColumn(
                title, min_value=0.0, max_value=score_max, format="%.2f"
            )
        elif name == "title":
            config[name] = st.column_config.TextColumn(title, width="large")
        elif name in date_columns:
            # Without this they render as "2020-05-06 00:00:00", which
            # is both the wrong order and a time nobody asked for.
            config[name] = st.column_config.DateColumn(title, format=DATE_FORMAT)
        else:
            config[name] = st.column_config.Column(title)
    return config


# ---------------------------------------------------------------------------
# Dates


DATE_FORMAT = "DD/MM/YYYY"
# The same shape for Python. Rule text is what the chips show, and a
# chip reading 2023-09-09 next to a box reading 09/09/2023 is the kind
# of mismatch that makes people distrust the whole panel. The CSV is
# the exception and stays ISO: spreadsheets parse that without being
# asked.
DATE_STRFTIME = "%d/%m/%Y"


def fmt_date(value: date) -> str:
    return value.strftime(DATE_STRFTIME)


def _as_date(value) -> date | None:
    """Anything the vocab file might hold for a date, as a date."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def date_bounds(ranges: dict, column: str) -> tuple[date | None, date | None]:
    """Earliest and latest value the data holds for a date column.

    Returns (None, None) when the data does not say, which happens for a
    column that is entirely empty. Callers must cope: passing None to a
    date picker means "no bound", which is the right behaviour."""
    entry = (ranges or {}).get(column) or {}
    return _as_date(entry.get("min")), _as_date(entry.get("max"))


def date_span_caption(low: date | None, high: date | None) -> str:
    """Say in words when the data starts and stops, because nobody can
    guess it and one tester could not work out where to begin a range."""
    if not low or not high:
        return ""
    return (
        f"This data runs from {low.strftime('%d %B %Y')} "
        f"to {high.strftime('%d %B %Y')}."
    )


# ---------------------------------------------------------------------------
# Numbers typed into a filter


def number_span_caption(ranges: dict, column: str) -> str:
    """Say what the numbers in a column actually run between.

    The same reason the date filters say it: nobody can guess whether a
    score tops out at 1 or 100, or whether a page of 900 words is long,
    so an empty box is a guess about a range you cannot see."""
    r = (ranges or {}).get(column) or {}
    lo, hi = r.get("min"), r.get("max")
    if lo is None or hi is None:
        return ""

    def fmt(v):
        # Whole numbers read as whole numbers: 900, not 900.0.
        v = round(float(v), 2)
        return f"{int(v):,}" if v == int(v) else f"{v:,}"

    return f"Between {fmt(lo)} and {fmt(hi)}."


def parse_limit(text) -> tuple[float | int | None, bool]:
    """Turn what someone typed into a number, or say it was not one.

    Blank means no limit, which is not an error. Commas and spaces are
    ignored so "1,000" works. Whole numbers come back as int so the
    tag reads "1,000 or more" rather than "1000.0 or more"."""
    raw = (text or "").strip().replace(",", "").replace(" ", "")
    if not raw:
        return None, True
    try:
        value = float(raw)
    except ValueError:
        return None, False
    # "nan" and "inf" parse as floats and would poison the SQL parameter.
    if value != value or value in (float("inf"), float("-inf")):
        return None, False
    return (int(value) if value.is_integer() else value), True


def exclude_label(many: bool) -> str:
    """One verb across the whole tool: things are excluded, never left
    out, so the checkbox matches the tags and the captions.

    "Leave this value out" was tested and not understood. It reads as
    an instruction the user is being given rather than a choice they
    are making."""
    return (
        "Exclude these values from results"
        if many
        else "Exclude this value from results"
    )
