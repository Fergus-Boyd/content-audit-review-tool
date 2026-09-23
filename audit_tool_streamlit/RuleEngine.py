import re

import numpy as np
import pandas as pd
import yaml
from dateutil.relativedelta import relativedelta


class Condition:
    def __call__(self, df):
        raise NotImplementedError


class ParsedCondition(Condition):
    def __init__(self, column, op, value):
        self.column = column
        self.op = op
        self.value = value

    def __call__(self, df):
        return OP_MAP[self.op](df[self.column], self.value)


class CombinedCondition(Condition):
    def __init__(self, conditions, join="AND"):
        self.conditions = conditions
        self.join = join.upper()

    def __call__(self, df):
        mask = self.conditions[0](df)
        for cond in self.conditions[1:]:
            if self.join == "AND":
                mask = mask & cond(df)
            else:
                mask = mask | cond(df)
        return mask


OP_MAP = {
    "==": lambda s, v: s == v,
    "!=": lambda s, v: s != v,
    ">": lambda s, v: s > v,
    ">=": lambda s, v: s >= v,
    "<": lambda s, v: s < v,
    "<=": lambda s, v: s <= v,
    "IN": lambda s, v: s.isin(v),
    "IS NULL": lambda s, v: s.isna(),
    "IS NOT NULL": lambda s, v: s.notna(),
}


NULL_CONDITION_RE = re.compile(
    r"^\s*(\w+)\s+IS\s+(NOT\s+)?NULL\s*$", re.IGNORECASE
)

CONDITION_RE = re.compile(r"^\s*(\w+)\s*(==|!=|>=|<=|>|<|IN)\s*(.+)\s*$", re.IGNORECASE)

DATE_EXPR_RE = re.compile(
    r"^CURRENT_DATE\s*([+-])\s*(\d+)\s*(days|months|years)$",
    re.IGNORECASE,
)

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_literal(raw: str):
    """Parse a boolean, quoted string, or number literal."""
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    if raw.startswith(("'", '"')):
        return raw.strip("\"'")
    try:
        return int(raw)
    except ValueError:
        return float(raw)


def parse_list(value: str):
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        raise ValueError(f"Invalid IN list: {value}")

    inner = value[1:-1].strip()
    if not inner:
        return []

    return [parse_literal(part.strip()) for part in inner.split(",")]


def parse_value(raw_value: str):
    raw = raw_value.strip()

    # Date arithmetic
    match = DATE_EXPR_RE.match(raw)
    if match:
        sign, amount, unit = match.groups()
        amount = int(amount)
        today = pd.Timestamp.today().normalize()
        delta = relativedelta(**{unit.lower(): amount})
        return today + delta if sign == "+" else today - delta

    # Literal date
    if ISO_DATE_RE.match(raw):
        return pd.Timestamp(raw)

    return parse_literal(raw)


def parse_condition(expr: str) -> ParsedCondition:
    null_match = NULL_CONDITION_RE.match(expr)
    if null_match:
        column, is_not = null_match.groups()
        op = "IS NOT NULL" if is_not else "IS NULL"
        return ParsedCondition(column, op, None)

    match = CONDITION_RE.match(expr)
    if not match:
        raise ValueError(f"Invalid condition: {expr}")

    column, op, raw = match.groups()
    op = op.upper()

    if op == "IN":
        value = parse_list(raw)
    else:
        value = parse_value(raw)

    if op not in OP_MAP:
        raise ValueError(f"Unsupported operator: {op}")

    return ParsedCondition(column, op, value)


FRESHNESS_ANCHORS = [(0, 0.0), (3, 1.0), (5, 2.0), (10, 3.0)]


def _piecewise_linear(x: pd.Series, anchors, na_value=0.0) -> pd.Series:
    """Interpolate between (x, y) anchors, capped at the final anchor's value
    beyond it. na_value is used both for NaN input and as the value for any
    input below the first anchor (matches float comparisons against NaN
    always being False, so such rows never get overwritten by the loop).
    """
    score = pd.Series(na_value, index=x.index)
    for (x0, y0), (x1, y1) in zip(anchors[:-1], anchors[1:]):
        mask = (x >= x0) & (x < x1)
        score[mask] = y0 + (x[mask] - x0) * (y1 - y0) / (x1 - x0)
    score[x >= anchors[-1][0]] = anchors[-1][1]
    return score


def freshness_age_score(age_years: pd.Series) -> pd.Series:
    """Piecewise-linear freshness score interpolated between FRESHNESS_ANCHORS
    (age in years -> score), capped at the final anchor's score beyond it.
    """
    return _piecewise_linear(age_years, FRESHNESS_ANCHORS, na_value=0.0)


DEMAND_ANCHORS = [(0, 0.6), (0.20, 1.0), (0.75, 1.5), (0.95, 2.0)]
DEMAND_ANCHORS_NON_HTML_ATTACHMENT = [(0, 0.6), (0.55, 1.0), (0.75, 1.5), (0.95, 2.0)]

# Highest base_score a row can reach under rules.yaml, by file_extension.
# html: accessibility 6 (hard to read + hard to scan) + functionality 10
# (broken page + 3+ broken links) + negative_user_engagement 1 + freshness 3.
# non-html (e.g. pdf): accessibility 11 (critical no-accessible-alternative +
# hard to read + hard to scan) + the same functionality/engagement/freshness
# maxima. Used to normalise base_score onto a common 0-100 scale.
MAX_BASE_SCORE_HTML_PAR = 25
MAX_BASE_SCORE_HTML_ATT = 20
MAX_BASE_SCORE_NON_HTML = 25


def demand_rank_score(demand_rank: pd.Series, anchors=DEMAND_ANCHORS) -> pd.Series:
    """Piecewise-linear demand modifier interpolated between anchors
    (demand_rank -> multiplier), capped at the final anchor's value beyond it.
    """
    return _piecewise_linear(demand_rank, anchors, na_value=1.0)


class MultiCategoryRuleEngine:
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self.config = {}
        self.rules = []
        self.base_scores = {}

    def load_config_from_yaml(self, path: str):
        with open(path, "r") as f:
            self.config = yaml.safe_load(f)

    def load_rules_from_config(self):
        config = self.config
        self.base_score_dict = config.get("base_score")

        for category, strengths in config.get("rules", {}).items():
            for strength, flags in strengths.items():
                for flag, value in flags.items():
                    # a plain list of conditions, or a dict of {variant:
                    # conditions} when the rule differs between html and pdf
                    if isinstance(value, list):
                        variants = {None: value}
                    elif isinstance(value, dict):
                        variants = value
                    else:
                        raise ValueError(
                            f"Invalid rule format for flag '{flag}' "
                            f"in category '{category}': expected list or dict"
                        )

                    for variant, expressions in variants.items():
                        conditions = [parse_condition(e) for e in expressions]
                        self.rules.append(
                            {
                                "category": category,
                                "strength": strength,
                                "flag": flag,
                                "variant": variant,
                                "condition": CombinedCondition(conditions, join="AND"),
                            }
                        )

    def apply_rules(self):
        # Initialize columns per category
        categories = {rule["category"] for rule in self.rules}

        for category in categories:
            self.df[f"{category}_flag"] = [[] for _ in range(len(self.df))]
            self.df[f"{category}_reasons"] = [[] for _ in range(len(self.df))]

        # Apply rules
        for rule in self.rules:
            mask = rule["condition"](self.df)

            for col, value in (
                (f"{rule['category']}_reasons", rule["flag"]),
                (f"{rule['category']}_flag", rule["strength"]),
            ):
                self.df.loc[mask, col] = self.df.loc[mask, col].apply(
                    lambda lst: lst + [value]
                )

    def get_report(self):
        return (
            pd.DataFrame(self.rules)
            .drop(columns=["variant", "condition"])
            .drop_duplicates()
        )

    def _flag_score(self, flags: pd.Series) -> pd.Series:
        return flags.apply(
            lambda lst: sum(self.base_score_dict.get(item, 0) for item in lst)
        )

    def pipeline(self, config_yaml):
        """
        new method that incorporates the latest changes from
        https://dbis-my.sharepoint.com/:w:/g/personal/shivani_dighe_businessandtrade_gov_uk/IQCXSrNdhHccT4iJUAG33K6eAU0XIqEzKl4kRw2RbNtqD9I?e=ONlC7U
        """

        def rank(s):
            return (s.rank(method="min") - 1) / (s.count() - 1)

        # Rows are ranked for demand (and given a base_score ceiling, below)
        # within one of three populations: pages/parents, html attachments,
        # and non-html attachments - each scored against its own peers.
        groups = [
            (~self.df["is_attachment"] | self.df["is_parent"], MAX_BASE_SCORE_HTML_PAR),
            (
                (self.df["is_attachment"] & ~self.df["is_parent"])
                & (self.df["file_extension"] == "html"),
                MAX_BASE_SCORE_HTML_ATT,
            ),
            (self.df["is_attachment"] & (self.df["file_extension"] != "html"), MAX_BASE_SCORE_NON_HTML),
        ]

        self.df["demand_rank"] = np.nan
        for mask, _ in groups:
            self.df.loc[mask, "demand_rank"] = rank(self.df.loc[mask, "sessions"])
        self.df["engagement_rank"] = rank(self.df["engagement_time_secs"])
        self.df["broken_link_ratio"] = (
            self.df["n_broken_links"] / self.df["n_links"]
        ).replace([np.inf, -np.inf], np.nan).fillna(0)

        self.load_config_from_yaml(config_yaml)
        self.load_rules_from_config()

        self.apply_rules()

        demand = self.df["demand_reasons"].str[0]
        # demand_multiplier is a continuous function of demand_rank (see
        # demand_rank_score) so that the score increases smoothly rather
        # than jumping at band boundaries. Non-html attachments use a
        # different anchor set (see DEMAND_ANCHORS_NON_HTML_ATTACHMENT).
        # demand_flag/demand_reasons (from rules.yaml) are still used for
        # the human-readable reason text and the "No Demand" recommendation
        # check below.
        non_html_attachment = self.df["is_attachment"] & (
            self.df["file_extension"] != "html"
        )
        demand_multiplier = pd.Series(np.nan, index=self.df.index)
        demand_multiplier[~non_html_attachment] = demand_rank_score(
            self.df.loc[~non_html_attachment, "demand_rank"]
        )
        demand_multiplier[non_html_attachment] = demand_rank_score(
            self.df.loc[non_html_attachment, "demand_rank"],
            anchors=DEMAND_ANCHORS_NON_HTML_ATTACHMENT,
        )

        data_quality_exclusion = self.df["exclusions_flag"].str[0] == True

        accessibility_score = self._flag_score(self.df["accessibility_flag"])
        functionality_score = self._flag_score(self.df["functionality_flag"])
        negative_user_engagement_score = self._flag_score(
            self.df["negative_user_engagement_flag"]
        )

        # freshness_score is not derived from base_score/freshness_flag - it's a
        # continuous function of content age (see freshness_age_score) so that
        # scores increase gradually rather than jumping at band boundaries.
        # freshness_flag/freshness_reasons (from rules.yaml) are still used for
        # the human-readable reason text.
        freshness_date = self.df["public_updated_at"].fillna(self.df["pdf_modified"])
        age_years = (
            pd.Timestamp.today().normalize() - freshness_date
        ).dt.days / 365.25
        freshness_score = freshness_age_score(age_years)

        freshness_score[data_quality_exclusion] = 0

        base_score = (
            accessibility_score
            + functionality_score
            + negative_user_engagement_score
            + freshness_score
        )

        recommendation = np.where(
            base_score == 0,
            "No action",
            np.where(
                (demand == "No Demand") & (freshness_score >= 3), "Remove", "Review"
            ),
        )

        # Withdrawn content is scored on its own route: it always gets a
        # nominal base/adjusted score and an automatic "Remove" recommendation,
        # regardless of the flags/scores computed above.
        withdrawn = self.df["withdrawn"]
        base_score = np.where(withdrawn, 1, base_score)

        # Same three groups used for demand_rank above, so that the
        # normalisation ceiling lines up with the population each row's
        # demand_rank was computed against.
        max_base_score = np.select([mask for mask, _ in groups], [score for _, score in groups])
        normalized_score = base_score / max_base_score * 100
        adjusted_score = demand_multiplier * normalized_score
        recommendation = np.where(withdrawn, "Remove", recommendation)
        recommend_transfer_to_tna = withdrawn

        recommend_convert = self.df["accessibility_reasons"].apply(
            lambda x: "Post-2018 content with no accessible alternative" in x
            or "Pre-2018 content with no accessible alternative" in x
        ) & np.isin(recommendation, ["Review", "No action"])

        self.df = self.df.drop(
            columns=[
                "demand_rank",
                "engagement_rank",
                "exclusions_flag",
                "exclusions_reasons",
                "demand_flag",
                "demand_reasons",
                "ranking_flag",
            ]
        )

        self.df["accessibility_score"] = accessibility_score
        self.df["functionality_score"] = functionality_score
        self.df["negative_user_engagement_score"] = negative_user_engagement_score
        self.df["freshness_score"] = freshness_score

        self.df["base_score"] = base_score
        self.df["normalized_score"] = normalized_score
        self.df["score_multiplier"] = demand_multiplier
        self.df["adjusted_score"] = adjusted_score

        self.df["recommendation"] = recommendation
        self.df["recommend_convert"] = recommend_convert
        self.df["recommend_transfer_to_tna"] = recommend_transfer_to_tna

        combined_reasons = (
            self.df["freshness_reasons"]
            + self.df["accessibility_reasons"]
            + self.df["negative_user_engagement_reasons"]
            + self.df["functionality_reasons"]
        ).apply(lambda reasons: ", ".join(reasons))

        self.df["issue_summary"] = np.select(
            [
                recommendation == "No action",
                (recommendation == "Remove") & self.df["withdrawn"],
                recommendation == "Remove",
                recommendation == "Review",
            ],
            [
                "No issues found",
                "Recommend remove because content is withdrawn",
                "Recommend remove because content has no traffic and has not been updated in more than 10 years",
                "Review recommendation based on the following issues: " + combined_reasons,
            ],
            default="",
        )

        incomplete_audit_priority = self.df["ranking_reasons"].apply(
            lambda x: "Incomplete audit data" in x
        )
        recommendation_priority = self.df["recommendation"].map(
            {"Review": 0, "Remove": 1, "No action": 2}
        )

        self.df["_incomplete_audit_priority"] = incomplete_audit_priority
        self.df["_recommendation_priority"] = recommendation_priority
        self.df["_freshness_date"] = freshness_date

        self.df = self.df.sort_values(
            [
                "adjusted_score",
                "base_score",
                "_incomplete_audit_priority",
                "_recommendation_priority",
                "_freshness_date",
            ],
            ascending=[False, False, False, True, True],
        )
        self.df = self.df.drop(
            columns=[
                "_incomplete_audit_priority",
                "_recommendation_priority",
                "_freshness_date",
            ]
        )
        self.df["priority_rank"] = range(1, len(self.df) + 1)

        return self.df


if __name__ == "__main__":
    from data_selection import load_data_with_filters

    df = load_data_with_filters()

    df = df.drop(
        columns=[
            "usability_flag",
            "usability_reasons",
            "freshness_flag",
            "freshness_reasons",
            "user_relevance_flag",
            "user_relevance_reasons",
            "recommendation",
        ]
    )
    engine = MultiCategoryRuleEngine(df)

    output = engine.pipeline("rules.yaml")

    print(output)
