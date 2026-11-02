import re
import yaml
import pandas as pd
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
}


CONDITION_RE = re.compile(r"^\s*(\w+)\s*(==|!=|>=|<=|>|<|IN)\s*(.+)\s*$", re.IGNORECASE)

DATE_EXPR_RE = re.compile(
    r"^CURRENT_DATE\s*([+-])\s*(\d+)\s*(days|months|years)$",
    re.IGNORECASE,
)


def parse_list(value: str):
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        raise ValueError(f"Invalid IN list: {value}")

    inner = value[1:-1].strip()
    if not inner:
        return []

    items = []
    for part in inner.split(","):
        part = part.strip()
        if part.lower() == "true":
            items.append(True)
        elif part.lower() == "false":
            items.append(False)
        elif part.startswith(("'", '"')):
            items.append(part.strip("\"'"))
        else:
            try:
                items.append(int(part))
            except ValueError:
                items.append(float(part))
    return items


def parse_value(raw_value: str):
    raw = raw_value.strip()

    # Boolean
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False

    # String
    if raw.startswith(("'", '"')):
        return raw.strip("\"'")

    # Date arithmetic
    match = DATE_EXPR_RE.match(raw)
    if match:
        sign, amount, unit = match.groups()
        amount = int(amount)
        today = pd.Timestamp.today().normalize()
        delta = relativedelta(**{unit.lower(): amount})
        return today + delta if sign == "+" else today - delta

    # Number
    if raw.isdigit():
        return int(raw)
    return float(raw)


def parse_condition(expr: str) -> ParsedCondition:
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


def reduce_strengths(strengths: list[str]) -> str:
    if not strengths:
        return "not flagged"

    strong_remove = strengths.count("strong candidate for removal")
    moderate_remove = strengths.count("moderate candidate for removal")
    weak_remove = strengths.count("weak candidate for removal")

    if strong_remove >= 1:
        return "strong candidate for removal"

    if moderate_remove + weak_remove >= 3:
        return "strong candidate for removal"

    if moderate_remove >= 1:
        return "moderate candidate for removal"

    if weak_remove == 2:
        return "moderate candidate for removal"

    if weak_remove == 1:
        return "weak candidate for removal"

    strong_review = strengths.count("strong candidate for review")
    moderate_review = strengths.count("moderate candidate for review")
    weak_review = strengths.count("weak candidate for review")

    if strong_review >= 1:
        return "strong candidate for review"

    if moderate_review + weak_review >= 3:
        return "strong candidate for review"

    if moderate_review >= 1:
        return "moderate candidate for review"

    if weak_review == 2:
        return "moderate candidate for review"

    if weak_review == 1:
        return "weak candidate for review"

    return "not flagged"


class MultiCategoryRuleEngine:
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self.config = {}
        self.rules = []
        self.interactions = []

    def load_config_from_yaml(self, path: str):
        with open(path, "r") as f:
            self.config = yaml.safe_load(f)

    def load_rules_from_config(self):
        config = self.config
        for category, strengths in config.get("rules", {}).items():
            for strength, flags in strengths.items():
                for flag, value in flags.items():
                    if isinstance(value, list):
                        conditions = [parse_condition(e) for e in value]

                        self.rules.append(
                            {
                                "category": category,
                                "strength": strength,
                                "flag": flag,
                                "variant": None,
                                "condition": CombinedCondition(conditions, join="AND"),
                            }
                        )

                    # if we have different rules for html and pdf
                    elif isinstance(value, dict):
                        for variant, expressions in value.items():
                            conditions = [parse_condition(e) for e in expressions]

                            self.rules.append(
                                {
                                    "category": category,
                                    "strength": strength,
                                    "flag": flag,
                                    "variant": variant,
                                    "condition": CombinedCondition(
                                        conditions, join="AND"
                                    ),
                                }
                            )
                    else:
                        raise ValueError(
                            f"Invalid rule format for flag '{flag}' "
                            f"in category '{category}': expected list or dict"
                        )

    def load_interactions_from_config(self):
        config = self.config

        interactions = {}

        for outcome, rules in config.get("interactions", {}).items():
            parsed = []
            for rule in rules:
                if "all" not in rule:
                    raise ValueError("Each interaction must have an 'all' block")

                parsed.append({r.strip().lower() for r in rule["all"]})

            interactions[outcome] = parsed

        self.interactions = interactions

    def apply_rules(self, keep_flag_status=False):
        STRENGTH_ORDER = {
            "weak candidate for review": 1,
            "moderate candidate for review": 2,
            "strong candidate for review": 3,
            "weak candidate for removal": 4,
            "moderate candidate for removal": 5,
            "strong candidate for removal": 6,
        }

        # Initialize columns per category
        categories = {rule["category"] for rule in self.rules}

        for category in categories:
            self.df[f"{category}_flag"] = [[] for _ in range(len(self.df))]
            self.df[f"{category}_reasons"] = [[] for _ in range(len(self.df))]

        # Apply rules
        for rule in self.rules:
            flag_col = f"{rule['category']}_flag"
            reason_col = f"{rule['category']}_reasons"

            mask = rule["condition"](self.df)

            # Keep *all* reasons
            self.df.loc[mask, reason_col] = self.df.loc[mask, reason_col].apply(
                lambda lst: lst + [rule["flag"]]
            )

            # Collect strengths temporarily
            self.df.loc[mask, flag_col] = self.df.loc[mask, flag_col].apply(
                lambda lst: lst + [rule["strength"]]
            )

        for category in categories:
            flag_col = f"{category}_flag"

            # Reduce list of strengths → max strength
            self.df[flag_col] = self.df[flag_col].apply(
                lambda lst: max(lst, key=lambda x: STRENGTH_ORDER[x]) if lst else None
            )

        FLAG_COLUMNS = [f"{category}_flag" for category in categories]

        self.df["recommendation"] = self.df.apply(
            lambda row: reduce_strengths(
                [row[c] for c in FLAG_COLUMNS if row[c] is not None]
            ),
            axis=1,
        )

        if keep_flag_status is False:
            for flag_col in FLAG_COLUMNS:
                self.df[flag_col] = ~self.df[flag_col].isna()

    def apply_interactions(self):
        df = self.df
        interactions = self.interactions

        all_reasons = []
        for _, row in df.iterrows():
            reasons = set()
            for col in df.columns:
                if col.endswith("_reasons"):
                    for r in row[col]:
                        reasons.add(r.strip().lower())
            all_reasons.append(reasons)

        all_reasons = pd.Series(all_reasons, index=df.index)

        priority = [
            "strong candidate for removal",
            "moderate candidate for removal",
            "weak candidate for removal",
            "consider converting to html",
        ]

        overrides = pd.Series([None] * len(df), index=df.index)

        for outcome in priority:
            for rule in interactions.get(outcome, []):
                mask = all_reasons.apply(lambda s: rule.issubset(s))
                overrides.loc[mask & overrides.isna()] = outcome

        self.df["recommendation"] = overrides.combine_first(df["recommendation"])

    def get_report(self):
        return (
            pd.DataFrame(self.rules)
            .drop(columns=["variant", "condition"])
            .drop_duplicates()
        )


if __name__ == "__main__":
    from data_selection import load_data_with_filters

    df = load_data_with_filters()

    engine = MultiCategoryRuleEngine(df)

    engine.load_config_from_yaml("rules.yaml")
    engine.load_rules_from_config()
    engine.load_interactions_from_config()

    engine.apply_rules()
    engine.apply_interactions()
    print(engine.df)
