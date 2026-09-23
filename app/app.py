import audit_tool_streamlit.data_selection as ds
import audit_tool_streamlit.data_visualisation as dv

import pandas as pd
import streamlit as st
import csv
from datetime import datetime

import pandas as pd
import streamlit as st

st.set_page_config(layout="wide")

st.title("Gov.uk Content Audit Tool")
st.markdown("""
To filter the data, choose a column in the dropdown box in the sidebar that you want to filter on. Depending on the data type you will be given a number of options:
* Numeric or Date - a slider will allow you to choose a range that you are interested in
* Boolean - A checkbox will allow you to choose whether you want only True or only False values.
* Text - A text box will allow you to enter text that you want to include. For Text, Title and Abstract columns, this is case-insensitive and allows Regex for experienced users. For content_id, public_url (and parent equivalents) this only allows exact matches.
* Categorical or list - A dropdown box that lets you pick the categories you are interested in.

Each filter you add will have a "Exclude Selection" option. This means that you can search to EXCLUDE certain values, rather than include them. For Example, if you are interested in all document_types except for "html_publication", you could use a filter for "html_publication" and tick the "Exclude Selection" checkbox.

For Text, List, or Categorical columns, you have the option of instead uploading a csv or txt file of exact matches. Particularly useful if you know exactly which URLs or content IDs you want to match on. A csv will look for a column with the same name as the column of interest, otherwise it will use all values in the csv. Txt files are split on commas. Note that this does not support regex, and all matches must be exact.

By default, the filters are applied so that only rows where all filters are true are returned. However, you can change this to return rows where any filters are true. For example, if you are interested in content with "HMRC" as the primary publisher OR content that has "guidance" as document type, you could achieve this with "any".

Once you have chosen your filters, you can apply them with the "Update Search" Button.

""")

if "include_text" not in st.session_state:
    st.session_state.include_text = False
# Initialize session state
if "data" not in st.session_state:
    st.session_state.columns = [

        "grouping_id",
        "content_id",
        "public_url",
        "document_type",
        "categories",
        "publishing_orgs",
        "primary_publishing_org",
        "title",
        "abstract",
        "date_published",
        "public_updated_at",
        "publishing_app",
        "parent_content_id",
        "parent_public_url",
        "is_attachment",
        "is_pdf_with_accessible_version",
        "accessible_url",
        "accessible_url_confidence_score",
        "sessions",
        "users",
        "new_users",
        "returning_users",
        "engagement_time_secs",
        "total_file_downloads",
        "users_file_download",
        "content_clicks",
        "video_views_complete",
        "total_outbound_clicks",
        "total_internal_clicks",
        "entrances",
        "exits",
        "file_downloads",
        "navigation",
        "file_download_navigated",
        "word_count",
        "sentence_count",
        "average_words_per_sentence",
        "flesch_reading_ease_score",
        "readability",
        "expected_reading_time_mins",
        "pdf_modified",
        "pdf_created",
        "pdf_page_count",
        "is_broken",
        "is_redirecting",
        "n_links",
        "n_broken_links",
        "n_redirecting_links",
        "screaming_frog_available",
        "file_extension",
        "no_of_attachments",
        "is_parent",
        "total_headings",
        "word_to_heading_ratio",
        "is_historic",
        'negative_user_engagement_flag', 
        'negative_user_engagement_reasons',
        'accessibility_flag',
        'accessibility_reasons',
        'freshness_flag',
        'freshness_reasons',
        'functionality_flag',
        'functionality_reasons',
        'accessibility_score', 
        'functionality_score',
        'negative_user_engagement_score', 
        'freshness_score', 
        'base_score',
        'normalized_score',
        'score_multiplier', 
        'adjusted_score',
        'issue_summary',
        'recommendation',
        'recommend_convert', 
        'recommend_transfer_to_tna',
        'priority_rank'
    ]
    st.session_state.data = ds.load_data_with_filters(columns=st.session_state.columns)

if "total_rows" not in st.session_state:
    st.session_state.total_rows = st.session_state.data.shape[0]

if "data_vc" not in st.session_state:
    st.session_state.data_vc = st.session_state.data["recommendation"].value_counts()

if "data_deduplicated" not in st.session_state:
    st.session_state.data_deduplicated = st.session_state.data.drop(
        columns=["text", "abstract", "title"], errors="ignore"
    ).drop_duplicates(subset="public_url")

if "all_columns" not in st.session_state:
    st.session_state.all_columns = list(st.session_state.data.columns)
    st.session_state.all_columns.append("text")

if "all_column_types" not in st.session_state:
    all_column_types = ds.get_all_column_types(st.session_state.data)
    all_column_types["text"] = {"column_type": "text", "column_opts": None}
    st.session_state.all_column_types = all_column_types

    st.session_state.categorical_columns = [
        key
        for key, val in all_column_types.items()
        if val.get("column_type") == "category"
    ]
    st.session_state.numeric_columns = [
        key
        for key, val in all_column_types.items()
        if val.get("column_type") == "numeric"
    ]

if "saved_filters" not in st.session_state:
    st.session_state.saved_filters = {}

if "filter_to_load" not in st.session_state:
    st.session_state.filter_to_load = None

if "column_definitions" not in st.session_state:
    st.session_state.column_definitions = ds.create_column_definition_text()

current_filters = []


def clear_all_filters():
    pass


def save_current_filters(filter_name):
    st.session_state.saved_filters[filter_name] = current_filters


def load_selected_filters(filter_name):
    current_filters = st.session_state.saved_filters[filter_name]
    all_filter_selector = []

    for column_selected, column_type, column_filter, column_invert in current_filters:
        all_filter_selector.append(column_selected)

        filter_key = f"{column_selected}_filter_key"
        invert_key = f"{column_selected}_invert_key"

        st.session_state[invert_key] = column_invert

        if column_type in ["numeric", "datetime"]:
            filter_min, filter_max = column_filter
            key_min = filter_key + "_start"
            key_max = filter_key + "_end"

            st.session_state[key_min] = filter_min
            st.session_state[key_max] = filter_max
        elif column_type == "text":
            # set the csv upload option to False if it is not already
            upload_file_key = f"{column_selected}_upload_file_key"
            st.session_state[upload_file_key] = False
            if isinstance(column_filter, list):
                column_filter = "\n".join(column_filter)
            st.session_state[filter_key] = column_filter
        else:
            st.session_state[filter_key] = column_filter

    st.session_state.all_filter_selector = all_filter_selector


def apply_filters():
    filter_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    columns = st.session_state.columns.copy()
    if st.session_state.include_text:
        columns.append("text")

    st.session_state.data = ds.load_data_with_filters(current_filters, method=filter_method, columns=columns)

    if include_parents or include_children:
        matched_urls = st.session_state.data["public_url"].dropna().to_list()

        expand_filters = [("public_url", "text", matched_urls, False)]
        if include_parents:
            matched_parent_urls = st.session_state.data["parent_public_url"].dropna().to_list()
            expand_filters.append(("public_url", "text", matched_parent_urls, False))
        if include_children:
            expand_filters.append(("parent_public_url", "text", matched_urls, False))

        st.session_state.data = ds.load_data_with_filters(expand_filters, method="or", columns=columns)

    st.session_state.data_deduplicated = st.session_state.data.drop(columns=["text", "abstract", "title"],  errors="ignore").drop_duplicates(subset="public_url")


    n_results = st.session_state.data.shape[0]
    filter_name = f"{filter_time} with {n_results:,} results"
    save_current_filters(filter_name)


def make_title(text):
    return text.replace("_", " ").title()


def prepare_csv():
    st.session_state.csv = (
        st.session_state.data[chosen_cols].to_csv(quoting=csv.QUOTE_ALL).encode("utf-8")
    )
    st.success("CSV File Ready for Download!")


def refresh_all():
    """
    When we update the filters, all visualisations must be remade
    """
    apply_filters()


if st.session_state.filter_to_load is not None:
    load_selected_filters(st.session_state.filter_to_load)
    st.session_state.filter_to_load = None
    st.rerun()

# Display filters in the sidebar
with st.sidebar:
    st.markdown("# Search Bar:")
    st.markdown(
        "Click this button to update your search to the current filters you have applied."
    )
    st.button("Update Search!", key="apply_all_filters", on_click=refresh_all)

    filter_method = st.selectbox(
        label="Do you want ALL filters to be met, or ANY filters to be met?",
        options=["all", "any"],
        format_func=make_title,
        key="filter_method_selector",
    )

    include_parents = st.checkbox("If an attachment matches filters, do you want to also include its parent page?", key="include_parents")
    include_children = st.checkbox("If a page matches filters, do you want to also include its child attachments?", key="include_children")
        
    st.markdown("# Columns:")
    columns_filtered_upon = st.multiselect(
        label="Choose columns to filter upon",
        default=[
            "text",
            "primary_publishing_org",
            "document_type",
            "file_extension",
            "categories",
        ],
        options=st.session_state.all_columns,
        format_func=make_title,
        key="all_filter_selector",
    )

    for column_selected in columns_filtered_upon:
        st.markdown("------------")
        title = f"### {make_title(column_selected)}:"
        filter_key = f"{column_selected}_filter_key"
        invert_key = f"{column_selected}_invert_key"
        st.markdown(title)

        column_dict = st.session_state.all_column_types.get(column_selected)
        column_type = column_dict.get("column_type")
        column_opts = column_dict.get("column_opts")

        if column_type in ["list", "category", "text"]:
            # give the option to upload a csv or txt file for these types
            upload_file_key = f"{column_selected}_upload_file_key"
            upload_file_text = "Upload a csv or text file"
            upload_file = st.checkbox(
                label=upload_file_text, value=False, key=upload_file_key
            )
        else:
            upload_file = False
        if upload_file:
            file = ds.csv_filter(filter_key)
            if file is not None:
                file_type = file.name.split(".")[-1].lower()

                if file_type == "csv":
                    df = pd.read_csv(file)
                    if column_selected in df.columns:
                        column_filter = df[column_selected].to_list()
                    else:
                        # use all values in csv if cannot find a
                        column_filter = df.stack().tolist()

                elif file_type == "txt":
                    content = file.read().decode("utf-8")
                    column_filter = [v.strip() for v in content.split(",")]
            else:
                column_filter = None

        else:
            column_filter = ds.create_filter(column_opts, column_type, filter_key)

        if column_type != "boolean":
            invert_text = "Exclude Selection"
            column_invert = st.checkbox(invert_text, key=invert_key)
        else:
            # if boolean, flip column filter
            column_invert = not column_filter
        if column_filter is not None:
            if column_filter != []:
                current_filters.append(
                    (column_selected, column_type, column_filter, column_invert)
                )
    for i in range(len(st.session_state.all_columns) - len(columns_filtered_upon)):
        st.empty()
# manually remove stale keys
for column_selected in st.session_state.all_columns:
    if column_selected not in columns_filtered_upon:
        filter_key = f"{column_selected}_filter_key"
        invert_key = f"{column_selected}_invert_key"
        st.session_state.pop(filter_key, None)
        st.session_state.pop(invert_key, None)

main_tab, filter_tab, definition_tab = st.tabs(
    ["Main tab", "History tab", "Column Definitions"]
)

with main_tab:
    if len(st.session_state.data) > 0:
        # Convert to CSV
        # ignore_cols = ["text", "html", "attachments", "linked_content", "linked_content_raw"]
        # csv_data = d.drop(ignore_cols, axis=1).to_csv().encode("utf-8")
        with st.expander("Data Viewer"):
            st.markdown(
                f"Under current filters there are **{len(st.session_state.data)}** rows in the data, compared to **{st.session_state.total_rows}** in the full data"
            )
            st.markdown(
                f"This corresponds to **{st.session_state.data.public_url.nunique()}** unique URLs"
            )
            default_cols = [
                "content_id",
                "public_url",
                "is_parent",
                "is_attachment",
                "document_type",
                "file_extension",
                "categories",
                "primary_publishing_org",
                "publishing_orgs",
                "title",
                "abstract",
                "date_published",
                "public_updated_at",
                "publishing_app",
                "is_pdf_with_accessible_version",
                "accessible_url",
                "accessible_url_confidence_score",
                "word_count",
                "sentence_count",
                "average_words_per_sentence",
                "flesch_reading_ease_score",
                "expected_reading_time_mins",
                "sessions",
                "users",
                "returning_users",
                "engagement_time_secs",
                "file_download_navigated",
                "pdf_created",
                "pdf_modified",
                "pdf_page_count",
                "n_broken_links",
                "n_redirecting_links",
                "total_headings",
                "word_to_heading_ratio",
                "is_historic",
                'negative_user_engagement_flag', 
                'negative_user_engagement_reasons',
                'accessibility_flag',
                'accessibility_reasons',
                'freshness_flag',
                'freshness_reasons',
                'functionality_flag',
                'functionality_reasons',
                'accessibility_score', 
                'functionality_score',
                'negative_user_engagement_score', 
                'freshness_score', 
                'base_score',
                'normalized_score',
                'score_multiplier', 
                'adjusted_score',
                'issue_summary',
                'recommendation',
                'recommend_convert', 
                'recommend_transfer_to_tna',
                'priority_rank'
            ]
            chosen_cols = st.multiselect(
                "Choose the columns you want to include in your subset",
                options=st.session_state.all_columns,
                default=default_cols,
                format_func=make_title,
            )

            if "text" in chosen_cols:
                st.session_state.include_text = True
            else:
                st.session_state.include_text = False

            if st.session_state.include_text and "text" not in st.session_state.data.columns:
                # add text if not present
                # TODO: work on data read function so that it doesn't fail if key columns
                # are not requested, so we can request single columns
                apply_filters()

            st.dataframe(st.session_state.data[chosen_cols].head(50))
            st.caption(
                "DataFrame showing the first 50 elements in the table under the current filters."
            )

            st.button("Prepare CSV", key="prepare_csv", on_click=prepare_csv)
            if "csv" in st.session_state:
                st.download_button(
                    label="Download CSV",
                    data=st.session_state.csv,
                    file_name="gov_audit_data.csv",
                    mime="text/csv",
                )

    else:
        st.markdown("# No data under current filters")
        for i in range(10):
            st.empty()

with filter_tab:
    st.markdown(
        "Whenever you run a query, this tab saves the search parameters. Use this tab to return to previous search parameters"
    )

    if len(st.session_state.saved_filters) >= 1:
        load_filter_col1, load_filter_col2 = st.columns(2)
        with load_filter_col1:
            filter_options = list(st.session_state.saved_filters.keys())
            chosen_filter_set = st.selectbox(
                label="Choose the saved filter set you want to restore",
                options=filter_options,
                index=None,
            )
        with load_filter_col2:
            load_filters = st.button("Load selected filters")
            if load_filters:
                st.session_state.filter_to_load = chosen_filter_set
                st.rerun()

        if chosen_filter_set is not None:
            st.json(
                ds.print_current_filters(
                    st.session_state.saved_filters[chosen_filter_set]
                )
            )

with definition_tab:
    st.markdown(st.session_state.column_definitions)
