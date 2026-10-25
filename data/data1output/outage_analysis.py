
import csv
from pathlib import Path

try:
    from .risk_calc import calculate_risk
    from . import restoration_estimator as labor
except ImportError:
    from risk_calc import calculate_risk
    import restoration_estimator as labor


DATA_DIR = Path(__file__).resolve().parent

ASSET_FILES = {
    "Transformer_Clip.csv": "transformer",
    "Fuse_Bank_Clip.csv": "fuse",
    "Switch_Clip.csv": "switch",
}

# Candidate CSV column names for transformer size.
SIZE_COLUMNS = {
    "kva",
    "kva_rating",
    "transformer_kva",
    "transformer_size",
    "rated_kva",
    "size_kva",
}


def normalize(value):
    """Normalize a column name for flexible matching."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def find_asset(identifier):
    """
    Find an equipment record by its asset ID or network location ID.
    Returns the asset category and available metadata.
    """
    identifier = str(identifier).strip()

    for filename, category in ASSET_FILES.items():
        path = DATA_DIR / filename

        if not path.is_file():
            continue

        with path.open("r", newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)

            for row in reader:
                normalized_row = {
                    normalize(key): (value or "").strip()
                    for key, value in row.items()
                    if key is not None
                }

                asset_id = normalized_row.get("id", "")
                location = normalized_row.get("location_n", "")

                if identifier not in {asset_id, location}:
                    continue

                size = None

                for column, value in normalized_row.items():
                    if column in SIZE_COLUMNS and value:
                        try:
                            size = float(value)
                            break
                        except ValueError:
                            pass

                return {
                    "identifier": identifier,
                    "asset_id": asset_id,
                    "location": location,
                    "category": category,
                    "size": size,
                    "source_file": filename,
                    "available_columns": list(normalized_row.keys()),
                }

    return None


def build_replacement_tasks(asset):
    """
    Build a provisional replacement scenario.

    This assumes the failed asset needs replacement. Network risk alone
    does not confirm physical damage or prove replacement is necessary.
    """
    category = asset["category"]
    size = asset["size"]

    if category == "transformer" and size is None:
        return {
            "error": (
                "Transformer identified, but its kVA size was not found "
                "in the recognized size columns. Check available_columns "
                "and add the actual CSV size column to SIZE_COLUMNS."
            )
        }

    tasks = []

    # Estimate removing the old asset and installing its replacement.
    for action in ("remove", "install"):
        task = {
            "category": category,
            "action": action,
            "quantity": 1,
        }

        if category == "transformer":
            task["size"] = size

        tasks.append(task)

    return tasks


def analyze_outage(identifier, repair_scenario="replace"):
    """
    Combine network impact analysis with repair labor estimation.
    """
    if repair_scenario != "replace":
        return {
            "error": "Currently supported repair scenario: 'replace'."
        }

    risk = calculate_risk(identifier)

    if "error" in risk:
        return {
            "equipment_id": str(identifier),
            "risk": risk,
            "repair_tasks": [],
            "labor_estimate": None,
            "note": "Repair estimation skipped because risk lookup failed.",
        }

    asset = find_asset(identifier)

    if asset is None:
        return {
            "equipment_id": str(identifier),
            "risk": risk,
            "asset": None,
            "repair_tasks": [],
            "labor_estimate": None,
            "note": (
                "Network impact was calculated, but the identifier did not "
                "match an asset record in the supported equipment CSVs. "
                "A repair task cannot be safely inferred."
            ),
        }

    tasks = build_replacement_tasks(asset)

    if isinstance(tasks, dict) and "error" in tasks:
        return {
            "equipment_id": str(identifier),
            "risk": risk,
            "asset": asset,
            "repair_tasks": [],
            "labor_estimate": tasks,
            "note": "Repair labor could not be estimated with available metadata.",
        }

    estimate = labor.estimate_labor(tasks)

    return {
        "equipment_id": str(identifier),
        "risk": risk,
        "asset": asset,
        "repair_scenario": repair_scenario,
        "repair_tasks": tasks,
        "labor_estimate": estimate,
        "assumptions": [
            "Replacement is a scenario assumption, not confirmed damage.",
            "Downstream affected equipment is not assumed to be damaged.",
            "Labor-hours are not elapsed outage duration.",
        ],
    }


if __name__ == "__main__":
    equipment_id = input("Enter an equipment ID or network location ID: ").strip()
    result = analyze_outage(equipment_id)

    import json
    print(json.dumps(result, indent=2))
