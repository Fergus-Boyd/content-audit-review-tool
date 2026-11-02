from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import pandas as pd


def find_accessible_version_of_pdf(data, threshold=0.9, train_on="pdf"):
    """
    We consider a PDF document to have an HTML equivalent if:

    ·    It shares its parent_public_url with an HTML attachment

    ·    It has >= 0.9 semantic similarity with that HTML attachment

    To derive semantic similarity scores I fit a tf-idf model on the complete corpus
    of pdf and html text. I chose to use tf-idf over more modern and sophisticated
    sentence embedders because we are interested in documents with almost identical wording
    and uninterested in exploring more subtle relationships.

    Then I split the pdf and html documents and transformed each of them using the trained tf-idf model.
    This generated two sparse matrices of sentence embeddings.

    I used cosine similarity to find the similarity between the embeddings of the pdf documents
    and the embeddings of the html documents. This gave me a matrix where
    each row represented a PDF document, and each column represented an HTML document,
    and each value represented the similarity between that pair of PDF and HTML
    documents. This matrix allows us to find the most similar HTML document to each PDF document.

    For each PDF attachment, I took its parent_public_url and filtered for HTML attachments with
    the same public URL. I cross referenced these with the similarity
    matrix to find the closest HTML document to each PDF.
    """

    def create_parent_in_common_lookup(data):
        pdf_data = data.loc[
            data["file_extension"] == "pdf", ["parent_public_url", "public_url"]
        ].rename(columns={"public_url": "pdf_public_url"})
        html_data = data.loc[
            data["file_extension"] == "html", ["parent_public_url", "public_url"]
        ].rename(columns={"public_url": "html_public_url"})

        return pdf_data.merge(html_data, on="parent_public_url")

    parent_lookup = create_parent_in_common_lookup(data)

    # only look at urls of PDFs and HTML pages with the same parent
    included_urls = (
        parent_lookup[["pdf_public_url", "html_public_url"]]
        .stack()
        .drop_duplicates()
        .to_list()
    )
    subset = data[data["public_url"].isin(included_urls)].drop_duplicates("public_url")

    docs = subset["text"].to_list()
    ids = list(subset.index)
    is_html = subset["file_extension"] == "html"

    docs_html = subset.loc[is_html, "text"].to_list()
    ids_html = list(subset.loc[is_html, "public_url"])

    docs_pdf = subset.loc[~is_html, "text"].to_list()
    ids_pdf = list(subset.loc[~is_html, "public_url"])

    vectorizer = TfidfVectorizer(stop_words="english")

    if train_on == "pdf":
        vectorizer.fit(docs_pdf)
    elif train_on == "html":
        vectorizer.fit(docs_html)
    elif train_on == "both":
        vectorizer.fit(docs)
    else:
        raise ValueError(
            f"`train_on` must be one of `pdf`, `html`, or `both`. `{train_on}` not allowed."
        )

    tfidf_matrix_html = vectorizer.transform(docs_html)
    tfidf_matrix_pdf = vectorizer.transform(docs_pdf)

    similarity_matrix = cosine_similarity(tfidf_matrix_pdf, tfidf_matrix_html)

    similarity_df = pd.DataFrame(similarity_matrix, index=ids_pdf, columns=ids_html)

    def get_highest_cosine_similarity_with_same_parent(similarity_df, parent_lookup):
        all_pdf_with_parent = (
            parent_lookup[["parent_public_url", "pdf_public_url"]]
            .copy()
            .drop_duplicates()
        )

        ouput_parent_public_url = []
        ouput_pdf_url = []
        ouput_html_url = []
        ouput_similarity = []

        for i, row in all_pdf_with_parent.iterrows():
            pdf_url = row["pdf_public_url"]
            parent_public_url = row["parent_public_url"]
            matching_html = parent_lookup.loc[
                parent_lookup["parent_public_url"] == parent_public_url,
                "html_public_url",
            ].drop_duplicates()
            similarity = similarity_df.loc[pdf_url, matching_html]

            ouput_parent_public_url.append(parent_public_url)
            ouput_pdf_url.append(pdf_url)
            ouput_html_url.append(similarity.idxmax())
            ouput_similarity.append(round(similarity.max().item(), 2))

        return pd.DataFrame(
            {
                "parent_public_url": ouput_parent_public_url,
                "public_url": ouput_pdf_url,
                "accessible_url": ouput_html_url,
                "accessible_url_confidence_score": ouput_similarity,
            }
        )

    result = get_highest_cosine_similarity_with_same_parent(
        similarity_df, parent_lookup
    )

    return result[result["accessible_url_confidence_score"].ge(threshold)]
