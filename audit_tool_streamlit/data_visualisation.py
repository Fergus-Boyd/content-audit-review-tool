from itertools import chain

import matplotlib.pyplot as plt
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import upsetplot as up


def plotly_bar_chart(df, xcol, y_col, title):
    # Create horizontal bar chart
    fig = px.bar(df, x=xcol, y=y_col, orientation="h", title=title)

    # Layout tweaks
    fig.update_traces(textposition="outside")
    fig.update_layout(yaxis=dict(categoryorder="total ascending"))

    return fig
    # Display in Streamlit
    st.plotly_chart(fig, width="stretch")


def plotly_pie_plot(s, title=None):
    # Count unique values
    value_counts = s.value_counts()

    # Create Plotly donut chart
    fig = go.Figure(
        data=[
            go.Pie(
                labels=value_counts.index,
                values=value_counts.values,
                hole=0.4,  # donut hole
                hoverinfo="label+value+percent",
                textinfo="none",  # hide static labels
            )
        ]
    )

    fig.update_layout(title_text=title, showlegend=False)

    return fig


def get_dataset_counts_by_subset(df, cols_2_plot, subset, optional_subset=None):
    counts_dict = {}

    if subset:
        counts_dict["This subset"] = df[df[subset]][cols_2_plot].sum()
        if optional_subset:
            counts_dict[f"This subset - {optional_subset} only"] = df[
                (df[subset] & df[optional_subset])
            ][cols_2_plot].sum()
            counts_dict[f"This subset - non-{optional_subset} only"] = df[
                (df[subset] & ~df[optional_subset])
            ][cols_2_plot].sum()
    elif optional_subset:
        counts_dict[f"Full Dataset - {optional_subset} only"] = df[
            (df[subset] & df[optional_subset])
        ][cols_2_plot].sum()
        counts_dict[f"Full Dataset - non-{optional_subset} only"] = df[
            (df[subset] & ~df[optional_subset])
        ][cols_2_plot].sum()

    counts_dict["Full Dataset"] = df[cols_2_plot].sum()

    df = pd.DataFrame(counts_dict).transpose()
    return df


def combine_data(full_data_vc, subset, col, rank_order):
    counts_dict = {}
    counts_dict["Full Dataset"] = full_data_vc
    counts_dict["This Subset"] = subset[col].value_counts()
    df = pd.DataFrame(counts_dict).transpose()
    for r in rank_order:
        if r not in df.columns:
            df[r] = 0
    return df[rank_order]


def group_by_and_get_value_counts(df, recommend_col, grouping_col, rank_order):
    grouped_df = df.groupby(grouping_col).value_counts([recommend_col])
    value_df = grouped_df.unstack().fillna(0)
    for r in rank_order:
        if r not in value_df.columns:
            value_df[r] = 0
    return value_df[rank_order]


def plotly_stacked_bar_chart(df, title=None, colours_dict=None):
    """
    Plots a stacked bar chart of the cols_2_plot in df.

    Will include up to four plots
    """
    if colours_dict is None:
        colours_dict = {}

    # Normalize for proportions
    df_norm = df.div(df.sum(axis=1), axis=0)

    # Create traces for each group
    fig = go.Figure()

    for group in df.columns:
        fig.add_trace(
            go.Bar(
                y=df.index,
                x=df_norm[group],
                orientation="h",
                name=group,
                marker_color=colours_dict.get(group),
                text=[group.replace("_", " ").title()]
                * len(df),  # raw values as labels
                insidetextanchor="middle",
                textangle=0,
                hovertemplate=[
                    f"%{{text}}<br>%{{y}}<br>Value: {val}<br>Proportion: %{{x:.1%}}<extra></extra>"
                    for val in df[group]
                ],
            )
        )

    # Layout adjustments
    fig.update_layout(
        barmode="stack", title=title, yaxis=dict(title="Category"), height=400
    )

    return fig


def plotly_histogram(df, column):
    title = column.replace("_", " ").title()
    # Create histogram
    fig = px.histogram(
        df,
        x=column,
        title=f"Distribution of {title}",
        labels={"Values": "Value"},
        opacity=0.75,
    )

    return fig


def get_count_of_reasons(reasons_col, grouping_col=None):
    output_dict = {}
    for group in grouping_col.unique():
        sub_series = reasons_col[grouping_col == group]
        output_dict[group] = pd.Series(chain(*sub_series.dropna())).value_counts()
    output_df = pd.DataFrame.from_dict(output_dict, orient="index").fillna(0)
    return output_df


def get_all_reasons(data, grouping_col_name):
    grouping_col = data[grouping_col_name]
    freshness = get_count_of_reasons(data["freshness_reasons"], grouping_col)
    usability = get_count_of_reasons(data["usability_reasons"], grouping_col)
    relevance = get_count_of_reasons(data["user_relevance_reasons"], grouping_col)

    return pd.concat([freshness, usability, relevance], axis=1).fillna(0)


def simple_count_reasons(data):
    lists = [
        pd.Series(chain(*data["freshness_reasons"].dropna())).value_counts(),
        pd.Series(chain(*data["usability_reasons"].dropna())).value_counts(),
        pd.Series(chain(*data["user_relevance_reasons"].dropna())).value_counts(),
    ]
    return pd.concat(lists).reset_index()


def plotly_heatmap(df, title):
    # Create heatmap
    fig = px.imshow(
        df,
        color_continuous_scale="Reds",  # darker red = higher value
        labels=dict(color="Value"),
        text_auto=True,
        aspect="auto",
    )

    fig.update_layout(title=title, coloraxis_showscale=False)

    return fig


def plotly_upset_plot(df, title=None):
    flag_cols = ["freshness_flag", "user_relevance_flag", "usability_flag"]

    # checks that at least two flags are present (nothing to plot otherwise)
    if (df[flag_cols].sum() > 0).sum() <= 1:
        return None

    data = up.from_indicators(flag_cols, df)
    up.plot(data, sort_by="cardinality", min_degree=1, show_counts=True)
    fig = plt.gcf()
    plt.title(title)
    return fig
