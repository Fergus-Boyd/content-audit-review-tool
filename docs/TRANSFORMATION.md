# Data Transformation

This readme file explains how content, analytics, and crawl data are cleaned, standardised and joined together.
This notebook DOES NOT cover data collection, which is handled through various different pipelines.
All functions needed for this process can be found in the `data_creation` module:

 ```
 import data_creation as dcr
 ```
 
## Google analytics
Google analytics data is provided to us in three separate xlsx sheets:
1. Html content
2. Html attachments
3. Non-html attachments

Which are saved in a shared folder.
There is minimal duplication of URLs in these tables, however, non-html attachments will sometimes appear multiple times when they have different parent_urls. To deduplicate these, we group by public_url and take the mode (most common) value. Otherwise, the pipeline is essentially:
1. Read the xlsx sheets as dataframes
2. concatenate the dataframes
3. keep only the columns of interest
4. deduplicate on public_url using the mode.

The final table as saved as a SQL table in our shared folder. To create it, run this code, setting `write=True` to save it to s3: 
```
ga_data = ga_data_pipeline(write=False)
```
## Screaming Frog
Screaming frog data is provided in 6 csvs that are saved in a shared folder.

Slightly different to the GA data, as the three categories of csv are:
1. Html internal
1. Html external
1. PDF

Each of these categories has two csvs, which I refer to as a depth_df and a data_df.
#### Depth df
This dataframe contains information about the traversal screaming frog did to collect the data. The only columns I am interested in here are the From and To columns, which are used to join to the data df in order to determine the parent URLs of attachment pages
#### Data df
This dataframe includes all the data that SF collects about each page.The processing of screaming frog data is more complicated than the google analytics data, mainly because SF only gives data at the page level, however we want to aggregate the data at the parent level.The following steps are performed separately for each pair of tables in each category:

### Data processing
The first step is to deduplicate the data, as each page is traversed multiple times. The `depth_df` is deduplicated on the subset of the `From` and `To` columns, as we are only interested in unique parent/attachment relationships. The `data_df` is deduplicated by grouping by the url, and taking the mode or most common value for each column.

The depth_df and data_df are then merged on the `To` and `Address` columns. This associates each row in `data_df` with the `From` column in `depth_df` so that we know the parent url of each atttachment. this is a many-to-one relationship, as each attachment page may have more than one parent url (different content pages can have the same attachment).

This merged dataset is then merged with the content data on the `From` and `public_url` columns, and `public_url` is renamed to `parent_public_url`, and `Address` is renamed to `public_url`. This is done in order to make sure that the only parent urls we are intested in are the ones that are in the content data.

At this point, additional metrics are derived from the SF data. we define is_broken as any page that returned a status code of `404`, and is_redirecting as any page that has a Redirect Type using the following code:
```
sf_df["is_broken"] = sf_df["Status Code"] == 404
sf_df["is_redirecting"] = ~sf_df["Redirect Type"].isna()
```

Next we create a broken_redirect_summary_df. We group by the parent_public_urls, and create a summary of the number of attachments, the number of broken links and the number of redirecting links:
```
broken_redirect_summary_df = sf_df.groupby("parent_public_url").agg(
    n_links=('parent_public_url', 'size'),
    n_broken_links=('is_broken', 'sum'),
    n_redirecting_links=('is_redirecting', 'sum')
    ).reset_index()
```

This will later be used to join back to the main data, however we cannot do that until three datasets are concatenated, as the same parent url may have html and pdf attachments, therefore we need save this for later.

At this point we can drop the parent_public_url column as we are done with it.

### Combining data
Once this is done for all three data sources, we need to concatenate our data together.

For the `data_df`s this is fairly straightforward, they are concatenated together with no issues.

The `broken_redirect_summary_df`s are concatenated together, however they are then grouped together by the `parent_public_url` column, and each other row is summed together:
```
link_summary_df =  pd.concat(link_summary_dfs).groupby("parent_public_url").agg({
    'n_links': sum, 
    'n_broken_links': sum,  
    'n_redirecting_links': sum
    }).reset_index().rename(columns={"parent_public_url": "public_url"})
```
This is done to get the total number of links for each parent page, considering all three data tables. Once this is done, this is merged to the data_df on the `public_url`.

Finally, we have our joined dataset. The column names are cleaned (converted to lowercase, punctuation and spaces replace with underscores).The output is saved to a SQL table in the shared folder. 

```
sf_data = sf_data_pipeline(write=False)  
```
## Attachments
Attachments are not a separate dataset from the content data, however they are treated differently and need to be identified from the content data. 

The `identify_child_content` function is used to explode the `attachments` column and the `linked_content_raw` columns and concatenate everything it finds into a single dataframe. Attachments have some metadata that is different to the parent, however, category inherits from the parent when it is missing using the following code:
```
for parent_col in parent_cols:
        col = parent_col.replace("_parent", "")
        child_content[col] = child_content[col].fillna(child_content[parent_col])
```
The data is cleaned and saved to a SQL table:
```
 attachments_data = attachments_data_pipeline(write=False)  
 ```

## Master Dataset
 The master dataset is created by joining the content data to each of the datasets that we have created. We need two different SQL queries in order to handle html content and attachments slightly differently. These datasets are concatenated together and cleaned.
 
 The `parent_url` is used to derive metrics on the number of attachments
 ```
     # label all parents
    no_of_attachments_data = (
        all_data["parent_public_url"]
        .value_counts()
        .reset_index()
        .rename(
            columns={"parent_public_url": "public_url", "count": "no_of_attachments"}
        )
    )

    all_data = all_data.merge(no_of_attachments_data, on="public_url", how="left")
    all_data["no_of_attachments"] = all_data["no_of_attachments"].fillna(0)
    all_data["is_parent"] = all_data["no_of_attachments"].ge(1)
```
And a grouping ID is created (this is the parent_id for attachments and otherwise content_id)    
```
    # set id
    grouping_id = all_data.apply(
        lambda row: row.parent_content_id
        if (row.is_attachment and not row.is_parent)
        else row.content_id,
        axis=1,
    )
    all_data["grouping_id"] = grouping_id
    all_data = all_data.sort_values(
        by=["grouping_id", "is_parent"], ascending=[True, False]
    )
```
Pdf text is joined to the dataset using  `get_pdf_text_lookup()`, and the max text length is capped at `10,000` (longer texts are truncated).

Some fields are derived like `heading_count` (calculated using regex to find header tags in the raw html for each content page) and `word_to_heading_ratio` (simply the word count from screaming frod divided by the number of headers or divided by 1 if the number of headings is 0)

We have a simple model designed to identify when pdf text has an HTML equivalent and is therefore accessible. This works by training a tf-idf model on the text and finding the closest HTML text to each PDF text given that they share a parent URL. This function finds the closest HTML url and gives a similarity score. We consider anything with a similarity above 0.9 to have an accessible version, but anything above 0.8 the closest URL is included.
```
pdf_lookup = dc.find_accessible_version_of_pdf(all_data, threshold=0.8)
   
all_data = all_data.merge(pdf_lookup, how="left", on=["parent_public_url", "public_url"], validate="1:1")
all_data["accessible_url"] = all_data.apply(lambda row: row["public_url"] if row["file_extension"] == "html" else row["accessible_url"], axis=1)
all_data["has_accessible_version"] = all_data.apply(lambda row: (row["file_extension"] == "html") or (row["accessible_url_confidence_score"] >= 0.9), axis=1)
```
Finally the rules engine is used to assign flags and recommendations to the data:    
```
engine = MultiCategoryRuleEngine(all_data)
    engine.load_config_from_yaml("../audit_tool_streamlit/rules.yaml")
    engine.load_rules_from_config()
    engine.load_interactions_from_config()
    
    engine.apply_rules()
    engine.apply_interactions()

    all_data = engine.df
```
This is the final output, therefore we must write it to the catalogue. 

```
data = create_full_data()
write_to_catalogue(data, "dbt", "gov_uk_content__regulation_xd",
                    convert_cols=["publishing_orgs", "categories", 'freshness_reasons', 'user_relevance_reasons', 'usability_reasons'])
```

the `convert_cols` argument is uses on columns that are lists or dictionaries in the dataframe. Since writing this to a SQL table doesn't allow this, we first convert those columns to JSON.
