
import re

import pandas as pd
import streamlit as st
import numpy as np
from dwutils import db, s3

class parameter_index_generator:
    def __init__(self, label="p_"):
        self.i = 0
        self.label=label
    def increment(self):
        self.i += 1
    def generate(self):
        output = self.label + str(self.i)
        self.increment()
        return output
    def multi_generate(self, n):
        return (self.generate() for i in range(n))



def create_column_definition_text(table_name):
    d = db.query(f"select column_name, definition from data_dictionaries where table_name = {table_name}")
    # capitalise column name
    d.loc[:, "column_name"] = d["column_name"].apply(lambda row: row.replace("_", " ").title())
    d[['definition', 'source']] = d['definition'].str.split("\n", n=1, expand=True)
    d.loc[:, "source"] = d["source"].replace("", None).fillna("Source: Not Defined").str.strip()
    
    sources = d["source"].unique()
    markdown_text = ""
    for source in sources:
        markdown_text += f"## {source}\n"
        for i, row in d[d["source"] == source].iterrows():
            markdown_text += f"* **{row['column_name']}**: {row['definition']}\n"
    return markdown_text


def print_current_filters(current_filters):
    """
    current filters is a list of tuples. Convert to json for easy reading:
    """
    cf_dict = {}
    for column_selected, column_type, column_filter, column_invert in current_filters:
        cf_dict[column_selected] = {
            "Column Type": column_type,
            "Filter Parameters": column_filter,
            "Invert Selection": column_invert
            
        }

    return cf_dict
    
        

def load_data_with_filters(current_filters=None, method="all", columns="*"):
    

    if isinstance(columns, list):
        columns = ", ".join(columns)


    query = f"select {columns} from *table_name*"

    all_criteria = []
    all_parameters = {}
    if current_filters is not None:
        pig = parameter_index_generator()
        for column_selected, column_type, column_filter, column_invert in current_filters:
            if column_type == "category":
                # use regex to search through the list
                if not isinstance(column_filter, list):
                    column_filter = [column_filter]

                p1 = pig.generate()
                all_parameters.update({p1:tuple(column_filter)})
                criteria = f"({column_selected} IN :{p1})"
        
            elif column_type == "text":
                
                if isinstance(column_filter, str):
                    column_filter = [line.strip() for line in column_filter.splitlines() if line.strip()]
                
                # if the text filter is just "", or " ", or "\n" then we skip it here
                if len(column_filter) == 0:
                    continue

                if column_selected in ["public_url", "content_id", "parent_public_url", "parent_content_id"]:
                    p1 = pig.generate()
                    all_parameters.update({p1: tuple(column_filter)})
                    criteria = f"({column_selected} IN :{p1})"
                else:
                    # use regex match for text and  exact matches for public_url and 
                    list_criteria = []
                    for cf in column_filter:
                        # if the text filter is just "", or " ", or "\n" then we skip it here
                        if len(cf) == 0:
                            continue
                        if cf.startswith("NOT "):
                            p1 = pig.generate()
                            all_parameters.update({p1:cf[4:]})
                            list_criteria.append(f"(NOT {column_selected} ~* :{p1})")
                        else:
                            p1 = pig.generate()
                            all_parameters.update({p1:cf})
                            list_criteria.append(f"({column_selected} ~* :{p1})")
                    criteria = "(" +  " AND ".join(list_criteria) + ")" 

            elif column_type == "numeric":
                c_min, c_max = column_filter
                p1, p2 = pig.multi_generate(2)
                all_parameters.update({p1: c_min, p2: c_max})
                criteria = f"(({column_selected} >= :{p1}) AND ({column_selected} <= :{p2}))"
                
            elif column_type == "boolean":
                criteria = f"({column_selected})"
                
            elif column_type == "list":
                # use regex to search through the list
                if not isinstance(column_filter, list):
                    column_filter = [column_filter]

                list_criteria = []
                for cf in column_filter:
                    p1 = pig.generate()
                    all_parameters.update({p1:f"%{cf}%"})
                    list_criteria.append(f"({column_selected} LIKE :{p1})")
                criteria = "(" +  " OR ".join(list_criteria) + ")"
                
            elif column_type == "datetime":
                c_min, c_max = column_filter
                c_max += pd.offsets.MonthEnd(0)
                p1, p2 = pig.multi_generate(2)
                all_parameters.update({p1: c_min, p2: c_max})
                criteria = f"(({column_selected} >= :{p1}) AND ({column_selected} <= :{p2}))"
                
            else:
                raise ValueError(
                    f"Unexpected column dtype `{column_type}` found in {column_selected}"
                )
            if column_invert:
                criteria = f"(NOT {criteria})"
            all_criteria.append(criteria)

        if len(all_criteria) > 0:
            if method == "all":
                where_conds = " AND ".join(all_criteria)
            else:
                where_conds = " OR ".join(all_criteria)
    
            query = f"{query} WHERE {where_conds}"

    all_data = db.query(query, params=all_parameters)

    date_columns = [
        # "non_html_last_modified",
        "public_updated_at",
        "date_published",
        "pdf_modified",
        "pdf_created",
    ]
    for col in date_columns:
        all_data[col] = pd.to_datetime(all_data[col])

    list_cols = [
        "publishing_orgs",
        "categories",
        "negative_user_engagement_reasons",
        "accessibility_reasons",
        "freshness_reasons",
        "functionality_reasons"
    ]

    for col in list_cols:
        all_data[col] = all_data[col].apply(eval)
        
    all_data.set_index("grouping_id", inplace=True)
    
    return all_data
    


def get_all_column_types(df: pd.DataFrame, category_max: int = 500) -> dict:
    """
    We want to have filters that behave differently depending on what type of data they are filtering.
    For example, we use checkboxes for boolean data, and sliders for dates and ranges.

    args:
        df: the main dataframe.
        category_max: The number of unique values a text column can have to be considered categorical data.
    returns:
        a dictionary of columns and types.
    """
    all_dtypes = df.dtypes
    output_dict = {}

    for column, dtype in all_dtypes.items():
        column_data = df[column]
        if dtype == "O":
            if all([isinstance(x, list) for x in column_data] | column_data.isna()):
                column_type = "list"
                column_opts = column_data.explode().dropna().unique().tolist()
                column_opts.sort()
                
                output_dict[column] = {
                    "column_type": column_type,
                    "column_opts": column_opts
                }
            else:
                if column_data.nunique() <= category_max:
                    column_type = "category"
                    column_opts =  column_data.unique().tolist()
                    column_opts.sort()
                    
                    output_dict[column] = {
                        "column_type": column_type,
                        "column_opts": column_opts
                    }
                else:
                    column_type = "text"
                    column_opts = None
                    output_dict[column] = {
                        "column_type": column_type,
                        "column_opts": column_opts
                    }
        # numeric
        elif dtype in [int, float]:
            column_type = "numeric"
            column_opts =  (column_data.min().item(), column_data.max().item())
            output_dict[column] = {
                "column_type": column_type,
                "column_opts": column_opts
            }
        # boolean
        elif dtype == "bool":
            column_type = "boolean"
            column_opts = None
            output_dict[column] = {
                "column_type": column_type,
                "column_opts": column_opts
            }
        # date
        elif dtype in ["<M8[ns]", "datetime64[ns]"]:
            column_type = "datetime"
            date_start, date_end = (column_data.min(), column_data.max())
            date_range = pd.date_range(start=date_start, end=date_end, freq="MS")
            column_opts = (date_range[0], date_range[-1])
            output_dict[column] = {
                "column_type": column_type,
                "column_opts": column_opts
            }
        else:
            raise ValueError(f"Unexpected column dtype `{dtype}` found.")

    return output_dict


def create_filter(col_opts, col_type, key=""):
    # strings
    if col_type == "category":
        return category_filter(col_opts, key)
    elif col_type == "list":
        return list_filter(col_opts, key)
    elif col_type == "text":
        return text_filter(col_opts, key)
    # numbers
    elif col_type == "numeric":
        return numeric_filter(col_opts, key)
    # boolean
    elif col_type == "boolean":
        return boolean_filter(col_opts, key)
    # date
    elif col_type == "datetime":
        return datetime_filter(col_opts, key)
    else:
        raise ValueError(f"Unexpected column dtype `{col_type}` found.")


def list_filter(column_options, key):
    input_text = "Choose categories:"

    output = st.multiselect(label=input_text, options=column_options, key=key)
    return output


def category_filter(column_options, key):
    input_text = "Choose categories:"

    output = st.multiselect(label=input_text, options=column_options, key=key)
    return output

def csv_filter(key):
    input_text = "Upload a csv or text file"

    output = st.file_uploader(label=input_text, 
                              type=["csv", "txt"],
                             key = key + "_file_upload")
    return output



def text_filter(column_options, key):

    input_text = "Input text or phrases (use line breaks to create multiple separate searches):"
    return st.text_area(label=input_text, key=key)


def numeric_filter(column_options, key):
    c_min, c_max = column_options

    output_start = st.number_input(
        label="Minimum",
        value=c_min,
        min_value=c_min, 
        max_value=c_max,
        key=key + "_start"
    )
    output_end = st.number_input(
        label="Maximum",
        value=c_max,
        min_value=c_min, 
        max_value=c_max,
        key=key + "_end"
    )
    return output_start, output_end


def boolean_filter(column_options, key):
    input_text = "Check if you want this flag to be True"
    output = st.checkbox(input_text, key=key)
    return output


def datetime_filter(column_options, key):
    date_start, date_end = column_options


    output_start = st.date_input(
        label="Earliest Date",
        value=date_start,
        min_value=date_start, 
        max_value=date_end,
        key=key + "_start"
    )
    output_end = st.date_input(
        label="Latest Date",
        value=date_end,
        min_value=date_start, 
        max_value=date_end,
        key=key + "_end"
    )
    return output_start, output_end


def filter_by_current_filters(data, current_filters, method="all"):
    d = data.copy()
    all_criteria = []
    for column_selected, column_type, column_filter, column_invert in current_filters:
        if column_type == "category":
            if isinstance(column_filter, str):
                column_filter = [column_filter]
            criteria = d[column_selected].isin(column_filter)
        elif column_type == "text":
            criteria = d[column_selected].str.contains(
                column_filter, na=False, case=False
            )
        elif column_type == "numeric":
            c_min, c_max = column_filter
            criteria = (d[column_selected] >= c_min) & (d[column_selected] <= c_max)
        elif column_type == "boolean":
            criteria = d[column_selected] if column_filter else ~d[column_selected]
        elif column_type == "list":
            # make sure the column filter is in the form of a list
            if isinstance(column_filter, str):
                column_filter = [column_filter]
            criteria = d[column_selected].apply(
                lambda row: any(val in row for val in column_filter)
            )
        elif column_type == "datetime":
            c_min, c_max = column_filter
            c_max += pd.offsets.MonthEnd(0)
            criteria = (d[column_selected] >= c_min) & (d[column_selected] <= c_max)
        else:
            raise ValueError(
                f"Unexpected column dtype `{column_type}` found in {column_selected}"
            )
        if column_invert:
            criteria = ~criteria
        all_criteria.append(criteria)

    all_criteria_df = pd.concat(all_criteria, axis=1)
    if method == "all":
        final_criteria = all_criteria_df.all(axis=1)
    elif method == "any":
        final_criteria = all_criteria_df.any(axis=1)

    return d[final_criteria]
