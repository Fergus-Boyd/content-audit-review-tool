import pandas as pd
from dwutils import db

TABLE = "example_catalogue_schema.example_content_table"

DATE_COLUMNS = [
    "public_updated_at",
    "date_published",
    "pdf_modified",
    "pdf_created",
]

LIST_COLUMNS = [
    "publishing_orgs",
    "categories",
    "negative_user_engagement_reasons",
    "accessibility_reasons",
    "freshness_reasons",
    "functionality_reasons",
    "broken_urls",
]

# Low-cardinality text columns, stored as pandas "category" dtype instead
# of plain object/string - the same values repeat across every row, so
# this is a large memory saving for close to no cost.
CATEGORY_COLUMNS = [
    "document_type",
    "primary_publishing_org",
    "recommendation",
    "readability",
    "file_extension",
    "publishing_app",
]


def _parse_list_cell(value):
    """A list column's stored value, as a Python list.

    Values are stringified Python list text (see write_to_catalogue's
    json.dumps). A missing value comes back as NaN/None, not a string,
    and eval(NaN) raises - every row with a NULL in a list column would
    otherwise crash the whole fetch."""
    return eval(value) if isinstance(value, str) else []


def _normalise_fetched_data(all_data: pd.DataFrame) -> pd.DataFrame:
    for col in DATE_COLUMNS:
        all_data[col] = pd.to_datetime(all_data[col])

    for col in LIST_COLUMNS:
        all_data[col] = all_data[col].apply(_parse_list_cell)

    for col in CATEGORY_COLUMNS:
        if col in all_data.columns:
            all_data[col] = all_data[col].astype("category")

    all_data.set_index("grouping_id", inplace=True)
    return all_data


def fetch_scope(where_sql: str, params: dict, columns: list[str]) -> pd.DataFrame:
    """Fetch rows matching a ready-built WHERE clause and named params.

    where_sql/params come from scope.assemble_scope (plus the search boxes'
    search.compiler.compile_query)."""
    if isinstance(columns, str):
        columns = [c.strip() for c in columns.split(",")]
    # grouping_id becomes the index, so it must always be fetched
    if "*" not in columns and "grouping_id" not in columns:
        columns = ["grouping_id", *columns]
    columns = ", ".join(columns)

    query = f"select {columns} from {TABLE}"
    if where_sql:
        query = f"{query} WHERE {where_sql}"

    all_data = db.query(query, params=params)
    return _normalise_fetched_data(all_data)


# ---------------------------------------------------------------------------
# Lightweight metadata queries.
#
# The dataset is too large to pull into memory just to work out what a
# filter dropdown should offer or what a date/number range spans. These
# each touch one column (or an aggregate of it), never every row and
# column at once.


def fetch_row_count() -> int:
    df = db.query(f"select count(*) as n from {TABLE}", params={})
    return int(df["n"].iloc[0])


def fetch_distinct_values(column: str) -> list:
    """Every distinct, non-null value a plain (non-list) column holds."""
    df = db.query(
        f"select distinct {column} from {TABLE} where {column} is not null",
        params={},
    )
    return sorted(df[column].dropna().tolist())


def fetch_list_values(column: str) -> list:
    """Every distinct value found inside a list column.

    List columns are stored as stringified Python list text (see
    _normalise_fetched_data's `.apply(eval)`), so distinct raw strings
    are fetched first and then exploded in Python."""
    df = db.query(
        f"select distinct {column} from {TABLE} where {column} is not null",
        params={},
    )
    values: set = set()
    for raw in df[column].dropna():
        values.update(eval(raw))
    return sorted(values)


def fetch_minmax(column: str, as_date: bool = False) -> tuple:
    df = db.query(
        f"select min({column}) as lo, max({column}) as hi from {TABLE}", params={}
    )
    lo, hi = df["lo"].iloc[0], df["hi"].iloc[0]
    if as_date:
        lo = pd.to_datetime(lo) if lo is not None else None
        hi = pd.to_datetime(hi) if hi is not None else None
    return lo, hi


def fetch_known_urls() -> set:
    """Every public_url the dataset holds, for matching an uploaded URL list."""
    df = db.query(f"select distinct public_url from {TABLE}", params={})
    return set(df["public_url"].dropna())


def fetch_vocab_stats(columns_and_kinds: list[tuple[str, str]]) -> dict:
    """{column: {"column_type", "column_opts"}} for exactly the columns a
    filter widget needs, each fetched with its own small query - never a
    full-table load. See scope.columns_needing_stats for how the (column,
    kind) pairs are chosen."""
    output_dict: dict = {}
    for column, kind in columns_and_kinds:
        if kind == "category":
            output_dict[column] = {
                "column_type": "category",
                "column_opts": fetch_distinct_values(column),
            }
        elif kind == "list":
            output_dict[column] = {
                "column_type": "list",
                "column_opts": fetch_list_values(column),
            }
        elif kind == "numeric":
            output_dict[column] = {
                "column_type": "numeric",
                "column_opts": fetch_minmax(column),
            }
        elif kind == "datetime":
            output_dict[column] = {
                "column_type": "datetime",
                "column_opts": fetch_minmax(column, as_date=True),
            }
        else:
            raise ValueError(f"Unexpected stats kind `{kind}` for column `{column}`.")
    return output_dict
