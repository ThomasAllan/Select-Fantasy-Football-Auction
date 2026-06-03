"""
Canonical manager name corrections.

Add entries here for any known misspellings or alternative names.
Import and call canonicalize() when writing manager_name to CSV so
future import scripts automatically produce the correct name even if
the MANAGER_SHEETS list has a legacy spelling.
"""

CANONICAL_NAMES: dict[str, str] = {
    # Thomas Allan — older Excel files use sheet name "Tom Allan"
    "Tom Allan": "Thomas Allan",
    # Niall McLoughlin — various misspellings found in older files
    "Niall Mcloughlin": "Niall McLoughlin",
    "Niall Mclouglin": "Niall McLoughlin",
    # Kev Thulbourne — "Thulbourn" typo seen in some files
    "Kev Thulbourn": "Kev Thulbourne",
}


def canonicalize(name: str) -> str:
    """Return the canonical manager name, correcting known misspellings."""
    return CANONICAL_NAMES.get(name, name)
