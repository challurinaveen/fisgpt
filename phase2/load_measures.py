"""
Phase 2 · Load measure dictionary into the warehouse.

Reads the Phase 1 measure_dictionary.json and creates ffx.measure rows,
assigning measure_ids that will be used for the measure_value foreign key.
"""
from __future__ import annotations

import json
import pandas as pd

import config


# Canonical measure names used across all three source tables.
# Maps the physical column name (as it appears in Norm Data) to the
# dictionary's question_code and a normalised measure_name.
# This list is derived from the Phase 1 measure mapping.
MEASURE_REGISTRY = [
    # (measure_id, measure_code, canonical_name, asked_of)
    (1,  "1a", "Notice",                        "All"),
    (2,  "2a", "Exciting New Idea",             "All"),
    (3,  "6",  "Better Than",                   "All"),
    (4,  "2b", "Initial Appeal",                "All"),
    (5,  "2e", "Appearance",                    "All"),
    (6,  "2f", "Smell",                         "All"),   # dict says "Aroma"
    (7,  "3a", "Taste",                         "All"),
    (8,  "3b", "Texture",                       "All"),
    (9,  "2d", "Packaging",                     "All"),
    (10, "3c", "Health",                        "All"),
    (11, "4a", "Value for Money",               "All"),
    (12, "3",  "Overall Impression / Quality",  "All"),
    (13, "2c", "Brand",                         "All"),
    (14, "5a", "Pre-Test Interest in Purchase",  "All"),
    (15, "5b", "Full Price Post Test PI",        "All"),
    (16, "5c", "25% Off PI",                     "All"),  # "25 Off PI" in Norm Data
    (17, "5d", "50% Off PI",                     "All"),
    (18, "3d", "Aftertaste",                    "Drinks"),
    (19, "3e", "Refreshment",                   "Drinks"),
    (20, "3f", "Ease of Drinking",              "Drinks"),
    (21, "3g", "Strength of Flavour",           "Drinks"),
    (22, "2g", "Image",                         "Drinks"),
    (23, "2h", "Label Design",                  "Drinks"),
    (24, "3h", "Touch / Feel",                  "Fresh Produce"),
    (25, "3i", "Freshness",                     "Fresh Produce"),
    (26, "3j", "Colour",                        "Fresh Produce"),
    (27, "7",  "Recommend to a friend",         "All"),
    (28, "8",  "Star Rating",                   "All"),
    (29, "9",  "Purchase Frequency",            "All"),
    # Frequency distribution buckets
    (30, "9a", "Frequency% Never value=0",             "All"),
    (31, "9b", "Frequency% 7 months + value=0.1",      "All"),
    (32, "9c", "Frequency% 4-6 months value=0.2",      "All"),
    (33, "9d", "Frequency% 2-3 months value=0.4",      "All"),
    (34, "9e", "Frequency% Monthly value=1",            "All"),
    (35, "9f", "Frequency% Fortnightly value=2",        "All"),
    (36, "9g", "Frequency% Weekly value=4",             "All"),
    # PI breakdown
    (37, "5b1", "Full Price PI% Definitely",           "All"),
    (38, "5b2", "Full Price PI% Probably",             "All"),
    (39, "5b3", "Full Price PI% Might / Might Not",    "All"),
    (40, "5b4", "Full Price PI% Probably Not",         "All"),
    (41, "5b5", "Full Price PI% Definitely Not",       "All"),
    (42, "5a1", "Pre trial PI% Definitely",            "All"),
    (43, "5a2", "Pre trial PI% Probably",              "All"),
]

# Physical column name aliases — maps variant column names to canonical names
COLUMN_ALIASES = {
    # Norm Data aliases
    "Smell":                        "Smell",
    "Aroma":                        "Smell",
    "Touch/Feel":                   "Touch / Feel",
    "Ease of Dinking":              "Ease of Drinking",  # typo in Norm Data
    "25 Off PI":                    "25% Off PI",
    "Overall Impression/Quality":   "Overall Impression / Quality",
    "Recommend to a friend or colleague": "Recommend to a friend",
    # Pre-2021 aliases
    "Texture/Mouthfeel":            "Texture",
    "New&Different":                "Exciting New Idea",
    "Appeal":                       "Initial Appeal",
    "Quality":                      "Overall Impression / Quality",
    "Pre PI":                       "Pre-Test Interest in Purchase",
    "Unpriced PI":                  "Pre-Test Interest in Purchase",
    "Priced PI":                    "Full Price Post Test PI",
    "Brand Fit":                    "Brand",
    "Relevance":                    "Better Than",
    "Opinion":                      "Overall Impression / Quality",
    # Norm Data column name variants
    "50 Off PI":                    "50% Off PI",
    "Pre-Test PI":                  "Pre-Test Interest in Purchase",
    "Pre-Test Interest in Purchase": "Pre-Test Interest in Purchase",
}


def build_measure_df() -> pd.DataFrame:
    """Build the ffx.measure DataFrame from the registry."""
    rows = []
    for mid, code, name, asked_of in MEASURE_REGISTRY:
        rows.append({
            "measure_id":    mid,
            "measure_code":  code,
            "measure_name":  name,
            "asked_of":      asked_of,
            "is_calculated": False,
            "definition":    None,
        })
    return pd.DataFrame(rows)


def resolve_measure_id(measure_name: str) -> int | None:
    """Resolve a physical column name to a measure_id."""
    # Try direct match first
    for mid, code, name, asked_of in MEASURE_REGISTRY:
        if name == measure_name:
            return mid

    # Try alias resolution
    canonical = COLUMN_ALIASES.get(measure_name)
    if canonical:
        for mid, code, name, asked_of in MEASURE_REGISTRY:
            if name == canonical:
                return mid

    return None


def build_measure_id_lookup() -> dict[str, int]:
    """Build a {measure_name: measure_id} lookup including aliases."""
    lookup = {}
    for mid, code, name, asked_of in MEASURE_REGISTRY:
        lookup[name] = mid
    for alias, canonical in COLUMN_ALIASES.items():
        mid = resolve_measure_id(canonical)
        if mid:
            lookup[alias] = mid
    return lookup
