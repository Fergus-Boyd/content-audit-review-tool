# App

This document explains how the dashboard works. The app is a single Streamlit script, `app/app.py`, backed by helper modules in `audit_tool_streamlit/`.

## Running the app

```
streamlit run app/app.py --server.port 9000
```

`app/start.sh` does the same on port 8888.

## Layout

The page has four tabs:

1. **Filter and search** - choose which content items you want.
2. **Preview** - a paged table of the matching items.
3. **Download** - export the matching items as a CSV.
4. **Help and definitions** - what the tool is for, plus a searchable table of every column's meaning (from `definitions.py`).

Tabs 1-3 each start with the same status block: the number of matching items, every active rule as a chip, and the **Update results** and **Clear everything** buttons.

## How Streamlit state is used

Streamlit re-runs the whole of `app.py` on every interaction, so anything that must persist lives in `st.session_state` (`ss` in the code).

The key idea is **staged vs applied**:

- *Staged* is whatever the widgets currently show.
- *Applied* (`ss.applied`, plus `ss.executed_text/title/url`) is what the current results were built from.

Nothing changes the count until **Update results** is pressed. That button sets `ss.apply_requested`; later in the script the staged values are copied to `ss.applied`. The scope is always built from the applied values, never from the widgets. `scope.scope_signature` compares staged and applied, and if they differ (`stale`) the app shows a warning and disables **Prepare CSV**.

Nothing is fetched from the database until Update results has been pressed once (`ss.has_run_once`).

### Widget keys and resetting

Widget keys must come from `ui_state.widget_key`, never be built by hand. Streamlit's browser copy of a widget's value can be re-sent after the key is deleted, so a "reset" silently undoes itself. Instead, each filter has a generation number included in its key; `ui_state.bump` / `clear_filters` change it, which creates brand-new widgets. See the docstring in `ui_state.py`.

## Tab 1: Filter and search

### Search

Three text boxes: page and PDF text (`html` column), titles (`title`), and URLs (`public_url`). The language is:

- One rule per line; every line must match.
- `a | b` means either.
- `NOT` at the start of a line excludes matches.
- Brackets group an either/or, e.g. `import (licence | permit)`.
- Phrases are typed without quotes and matching happens inside words.

`search/parser.py` parses the text into a tree (and raises `QueryError` for bad input, which is shown as an error), and `search/compiler.py` compiles it to a parameterised SQL condition. The rules are listed in `definitions.SEARCH_RULES` and shown as a help tooltip on each box.

### Upload a list of URLs

An expander accepts a CSV or TXT of GOV.UK URLs. They are matched against `fetch_known_urls()` and the matches become a `public_url IN (...)` rule. The number matched out of the number uploaded is shown.

### Basic filters

- Checkboxes for which items to include: attachments, withdrawn pages, historic pages (all on by default).
- *Only include items reached by the third-party web crawler* (off by default).
- *Also include attachments when only the parent page matches* / *Also include the parent page when only the attachment matches* - a second fetch keyed on `parent_public_url` / `public_url`. In the Preview these rows get a "Why it's here" column.

### Filters

Filters are declared as `FilterDef`s in the `scope.REGISTRY` and arranged into `scope.FILTER_GROUPS`. The first three groups are open by default; the rest sit under **Advanced filters** in expanders that show how many filters in them are in use.

| Group | Open by default |
|---|---|
| About the page or file | yes |
| What the audit says | yes |
| Problems found | yes |
| Publishing details | no |
| Content and readability | no |
| Traffic and downloads | no |
| Accessibility and broken links | no |

Each `FilterDef` has a `kind` which picks the widget (`render_*` in `app.py`) and the rule builder (`rule_*` in `scope.py`):

- `vocab_multi` - multiselect of the values in the data.
- `topic` - multiselect over the taxonomy in `taxon_map.json`, with an option to include topics inside the chosen one.
- `date_preset` / `date_range` - quick presets or a from/to date pair.
- `reasons` - multiselect over the "reasons" list columns.
- `minmax` - lower and upper limits for a numeric column.
- `tristate` - Any / Yes / No.

Most filters have an "exclude" option to invert them. Dropdown options and numeric/date bounds come from `ds.fetch_vocab_stats`, which queries column by column rather than loading the dataset (`scope.columns_needing_stats` says which are needed).

Each active filter becomes a `scope.Rule` holding a SQL fragment and its parameters. Parameter names come from `ParamGenerator` (`params.py`) so they are unique across the query.

## Building the query and loading data

1. The applied rules, the quick (include/exclude) rules, the search rule and the URL-list rule are combined by `scope.assemble_scope` into one `WHERE` clause plus a parameter dict.
2. `cached_scope_fetch` (`st.cache_data`, one entry, 30 minute TTL) calls `ds.fetch_scope`, which queries the table with `dwutils.db` and returns every column in `ALL_COLUMNS`.
3. `data_selection._normalise_fetched_data` does light cleaning: list columns are parsed into lists, date columns become datetimes and low-cardinality text columns become `category`.
4. If either "Also include" box is ticked, a second fetch ORs in the related rows.

The count shown is the number of rows returned, out of the whole table (or out of the uploaded URLs that matched, if a list is in use).

## Status block and chips

Each rule - search, URL list, each filter, each include/exclude option and each "also include" option - is shown as a chip (`scope.chip_id` / `chip_label`). Deselecting a chip removes that rule immediately (`_remove_chips`) and re-applies. **Clear everything** resets the search, URL list, every filter and the checkboxes. If there are no matches the block says so.

## Tab 2: Preview

Shows 50 rows per page (`PAGE_SIZE`) with Previous / Next buttons, in priority rank order (1 is most urgent). Columns and formatting come from `scope.preview_columns` and `scope.preview_column_config`; the `html` column is never shown. The page number resets to 1 whenever the scope changes.

## Tab 3: Download

Choose between the **Standard set** (`scope.STANDARD_COLUMNS`) and **Everything** (all of `ALL_COLUMNS`). Streamlit download buttons need the data already converted, so a **Prepare CSV** button builds the file on demand and then **Download CSV** appears with the row count and file size. Prepared files are discarded if the scope or column choice changes. The file is `content-audit-tool-export.csv`, with every field quoted.

## Where things live

| File | Purpose |
|---|---|
| `app/app.py` | The page: widgets, applying, tabs |
| `audit_tool_streamlit/scope.py` | Filter registry and groups, rules, SQL assembly, chips, preview formatting |
| `audit_tool_streamlit/search/` | Search parser and SQL compiler |
| `audit_tool_streamlit/data_selection.py` | Database access and cleaning |
| `audit_tool_streamlit/definitions.py` | Column definitions and search help text |
| `audit_tool_streamlit/ui_state.py` | Resettable widget keys |
| `audit_tool_streamlit/params.py` | Unique SQL parameter names |
| `audit_tool_streamlit/taxon_map.json` | Topic taxonomy for the topic filter |
