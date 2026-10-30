"""Plain-English definitions of the main columns and flag families,
shown in the Help and definitions tab lookup."""

DEFINITIONS: list[tuple[str, str]] = [
    (
        "priority_rank",
        "Position of page or file in the priority list, based on overall score (lower rank numbers indicate content that may need attention sooner)",
    ),
    ("title", "Name of the page or file"),
    ("public_url", "Web address of the page or file"),
    ("content_id", "Unique GOV.UK identifier for a page"),
    ("file_extension", "File format, such as HTML, PDF or DOCX"),
    ("document_type", "GOV.UK content type, such as guidance or policy paper"),
    ("categories", "Topics the content is tagged to on GOV.UK"),
    ("primary_publishing_org", "Organisation primarily responsible for the content"),
    ("publishing_orgs", "All organisations associated with publishing the content"),
    ("abstract", "Short summary of what a page is about"),
    ("date_published", "Date a page was first published"),
    ("public_updated_at", "Date a page was last significantly updated"),
    ("pdf_modified", "Date a PDF file was last modified"),
    ("pdf_created", "Date a PDF file was originally created"),
    ("publishing_app", "System used to publish the content"),
    ("withdrawn", "Whether the page or file has been officially withdrawn"),
    ("is_historic", "Whether the page or file relates to a previous government"),
    (
        "is_attachment",
        "Whether the page or file is a child or attachment on another page",
    ),
    ("parent_content_id", "Content ID of the parent page for a child or attachment"),
    ("parent_public_url", "URL of the parent page for a child or attachment"),
    ("no_of_attachments", "How many files are attached to a page"),
    ("is_parent", "Whether a page is a parent page with one or more attachments"),
    ("sessions", "Number of visits over a 12-month period"),
    (
        "engaged_sessions",
        "Number of visits where users actively engaged with a page over a 12-month period",
    ),
    ("users", "Number of unique users over a 12-month period"),
    (
        "engagement_time_secs",
        "Total time users actively spent on the page over a 12-month period",
    ),
    (
        "total_file_downloads",
        "Total downloads of all attachments on a parent page over a 12-month period",
    ),
    (
        "file_downloads",
        "Number of times a specific file was downloaded over a 12-month period",
    ),
    (
        "navigation",
        "Number of times a file was opened through navigation interactions over a 12-month period",
    ),
    (
        "file_download_navigated",
        "Combined total of downloads and navigation opens for a specific file over a 12-month period",
    ),
    ("word_count", "Total number of words"),
    ("sentence_count", "Total number of sentences"),
    ("pdf_page_count", "Number of pages in a PDF"),
    ("average_words_per_sentence", "Average sentence length"),
    ("total_headings", "Number of headings in the content"),
    ("word_to_heading_ratio", "Average number of words between headings on a page"),
    ("expected_reading_time_mins", "Estimated time needed to read the content"),
    ("flesch_reading_ease_score", "Score showing how easy the content is to read"),
    ("readability", "Plain language readability rating, such as Easy or Difficult"),
    (
        "is_broken",
        "Whether the page or file cannot be accessed because it returns a 404 error",
    ),
    ("is_redirecting", "Whether the URL automatically sends users to another URL"),
    ("n_links", "Total number of links on a page"),
    ("n_broken_links", "Number of links on a page that do not work"),
    ("broken_link_ratio", "Percentage of links on a page that are broken"),
    ("broken_urls", "List of broken links found on a page"),
    ("n_redirecting_links", "Number of links on a page that redirect elsewhere"),
    (
        "screaming_frog_available",
        "Whether web crawl data is available for the page or file (web crawl data is used for things like link checking, readability and PDF text analysis)",
    ),
    (
        "accessible_url",
        "URL of an accessible version of the content, where one has been identified",
    ),
    (
        "accessible_url_confidence_score",
        "How confident the system is that the accessible version match is correct",
    ),
    ("is_pdf_with_accessible_version", "Whether an accessible version of a PDF exists"),
    ("freshness_flag", "Whether the content appears out of date and how severely"),
    ("freshness_reasons", "Reason the content appears out of date"),
    (
        "functionality_flag",
        "Whether the content has technical issues and how severe they are",
    ),
    ("functionality_reasons", "Details of any technical issues found"),
    (
        "negative_user_engagement_flag",
        "Whether user engagement appears low and the severity of the issue",
    ),
    (
        "negative_user_engagement_reasons",
        "Reason the content was flagged for low engagement",
    ),
    (
        "accessibility_flag",
        "Whether accessibility or usability issues were found and how severe they are",
    ),
    ("accessibility_reasons", "Details of accessibility or usability issues found"),
    (
        "accessibility_score",
        "Score indicating the severity of a page or file's accessibility and usability issues",
    ),
    (
        "functionality_score",
        "Score indicating the severity of a page or file's functionality issues",
    ),
    (
        "negative_user_engagement_score",
        "Score indicating the severity of a page or file's user engagement issues",
    ),
    (
        "freshness_score",
        "Score indicating the severity of a page or file's freshness issues",
    ),
    ("base_score", "Combined score before adjustments are applied"),
    (
        "normalized_score",
        "Adjusted score that allows pages and files to be compared on a consistent scale",
    ),
    (
        "score_multiplier",
        "Adjustment factor based on user demand that affects prioritisation",
    ),
    ("adjusted_score", "Final score used to prioritise content"),
    ("recommendation", "Suggested editorial action for each page or file"),
    ("recommend_convert", "Whether a PDF would be better published as HTML"),
    (
        "recommend_transfer_to_tna",
        "Whether the content may be suitable for removal from GOV.UK and transfer to The National Archives",
    ),
    (
        "ranking_reasons",
        "Indication of where the page or file's rank has been affected by a lack of data",
    ),
    ("issue_summary", "Summary of the main issues identified"),
]


# The search rules, in one place, shown as help beside both search
# boxes so they are readable at the moment somebody is typing a query.
SEARCH_RULES = [
    ("One rule per line", "Every line must match. More lines means fewer results."),
    ("a | b", "Either counts. A page or file matches if it contains a or b."),
    (
        "NOT at the start of a line",
        "Leave those out. NOT a | b leaves out anything with a and anything with b.",
    ),
    (
        "Phrases",
        "Type the phrase as it is, with no quote marks. It must appear exactly.",
    ),
    ("Matching happens inside words", "licen finds licence and licensing."),
    (
        "Brackets group an either/or",
        "import (licence | permit) finds import licence or import permit. "
        "Without brackets the | splits the whole line.",
    ),
]


def search_help_markdown() -> str:
    """The same rules as a tooltip, for the ? beside each search box."""
    lines = ["**How to search**", ""]
    lines += [f"- **{rule}** - {what}" for rule, what in SEARCH_RULES]
    return "\n".join(lines)
