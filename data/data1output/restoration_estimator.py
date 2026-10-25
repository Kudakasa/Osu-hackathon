
import csv
import re
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parent

LABOR_CSV = DATA_DIR / "Install_Labor_HackOHIO-1-Labor time material related.csv"
CNC_CSV = DATA_DIR / "Install_Labor_HackOHIO-1-CNC.csv"


def normalize(value):
    """Normalize labels for matching."""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def number(value):
    """Convert a CSV value to a float, or return None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_labor_data():
    """Load general equipment and material labor averages."""
    if not LABOR_CSV.exists():
        raise FileNotFoundError(f"Labor CSV not found: {LABOR_CSV}")

    with LABOR_CSV.open("r", newline="", encoding="utf-8-sig") as file:
        rows = list(csv.reader(file))

    equipment = {}
    sections = []

    # Find each equipment section in the CSV.
    for index, row in enumerate(rows):
        title = row[0].strip() if row else ""

        if "CU Average Hours" in title:
            category = title.split(" CU Average Hours")[0].strip()
            sections.append((index, category, title))

    for section_index, (start, category, title) in enumerate(sections):
        end = (
            sections[section_index + 1][0]
            if section_index + 1 < len(sections)
            else len(rows)
        )

        header_index = None
        install_index = None
        removal_index = None

        # Locate the column headers for this section.
        for index in range(start + 1, end):
            row = rows[index]
            normalized_row = [normalize(value) for value in row]

            for col, value in enumerate(normalized_row):
                if value == "avg install hours":
                    install_index = col
                elif value == "avg removal hours":
                    removal_index = col

            if install_index is not None and removal_index is not None:
                header_index = index
                break

        if header_index is None:
            continue

        category_key = normalize(category)
        entries = []

        for row in rows[header_index + 1:end]:
            if max(install_index, removal_index) >= len(row):
                continue

            install = number(row[install_index])
            removal = number(row[removal_index])

            if install is None or removal is None:
                continue

            # Preserve size labels when the CSV provides them.
            label = row[0].strip() if row else ""
            secondary_label = (
                row[1].strip()
                if len(row) > 1
                else ""
            )

            entry = {
                "label": label,
                "secondary_label": secondary_label,
                "install_hours": install,
                "removal_hours": removal,
            }

            entries.append(entry)

        if entries:
            equipment[category_key] = entries

    return equipment


def load_connector_data():
    """Load individual connector codes and their labor hours."""
    if not CNC_CSV.exists():
        raise FileNotFoundError(f"Connector CSV not found: {CNC_CSV}")

    connectors = {}

    with CNC_CSV.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)

        for row in reader:
            code = row.get("CD_CU", "").strip()

            if not code:
                continue

            install = number(row.get("INSTALL_HOURS"))
            removal = number(row.get("REMOVE_HOURS"))

            if install is None or removal is None:
                continue

            connectors[code.upper()] = {
                "install_hours": install,
                "removal_hours": removal,
            }

    return connectors


EQUIPMENT = load_labor_data()
CONNECTORS = load_connector_data()


ALIASES = {
    "crossarm": "crossarm",
    "bracket": "bracket",
    "pole": "pole by size",
    "transformer": "transformer by size",
    "anchor": "anchor",
    "equipment lead": "equipment lead",
    "grounding": "grounding",
    "guying": "guying",
    "dead end": "dead end",
    "dead end fitting": "dead end",
    "insulator": "insulator",
    "pin": "pin",
    "recloser": "recloser",
    "secondary conductor attachment": "secondary conductor attachment",
    "service": "service",
    "switch": "switch",
    "fuse": "fuse cutout",
    "fuse cutout": "fuse cutout",
}


def lookup_equipment(category, action="install", size=None):
    """
    Look up average labor hours for an equipment category.

    action must be 'install' or 'remove'.
    size can be a transformer kVA value or pole height in feet.
    """
    if action not in ("install", "remove"):
        raise ValueError("action must be 'install' or 'remove'")

    normalized_category = normalize(category)
    category_key = ALIASES.get(normalized_category, normalized_category)

    entries = EQUIPMENT.get(category_key)

    if not entries:
        return {
            "error": f"No labor data found for category '{category}'."
        }

    # Size-specific transformer entries.
    if category_key == "transformer by size":
        if size is None:
            return {
                "error": (
                    "Transformer size in kVA is required. "
                    "Available sizes are listed in the labor CSV."
                )
            }

        requested_size = number(size)

        if requested_size is None:
            return {"error": "Transformer size must be a number."}

        for entry in entries:
            if number(entry["label"]) == requested_size:
                return _format_result(
                    category, action, entry, "CSV size-specific average"
                )

        return {
            "error": f"No transformer labor entry found for {size} kVA."
        }

    # Size-specific pole entries use Small, Medium, or Large groups.
    if category_key == "pole by size":
        if size is None:
            return {
                "error": (
                    "Pole height in feet is required. "
                    "Available groups are 25–35 ft, 40–55 ft, "
                    "and 60 ft and above."
                )
            }

        height = number(size)

        if height is None or height <= 0:
            return {"error": "Pole height must be a positive number."}

        if height <= 35:
            group = "small"
        elif height <= 55:
            group = "medium"
        else:
            group = "large"

        for entry in entries:
            if normalize(entry["label"]) == group:
                return _format_result(
                    category, action, entry, "CSV height-group average"
                )

        return {"error": f"No labor entry found for a {height}-foot pole."}

    # For categories with one average, use the first valid entry.
    entry = entries[0]

    return _format_result(
        category, action, entry, "CSV category average"
    )


def _format_result(category, action, entry, source):
    hours_key = f"{action}_hours"

    return {
        "category": category,
        "action": action,
        "labor_hours_per_item": entry[hours_key],
        "source": source,
        "data_label": entry["label"],
    }


def lookup_connector(code, action="install"):
    """Look up labor hours for an exact CNC connector code."""
    if action not in ("install", "remove"):
        raise ValueError("action must be 'install' or 'remove'")

    normalized_code = str(code).strip().upper()
    entry = CONNECTORS.get(normalized_code)

    if entry is None:
        return {
            "error": f"No connector labor entry found for '{code}'."
        }

    return {
        "category": "connector",
        "code": normalized_code,
        "action": action,
        "labor_hours_per_item": entry[f"{action}_hours"],
        "source": "CSV connector-specific average",
    }


def estimate_labor(tasks):
    """
    Sum labor hours for a list of known repair tasks.

    Each task is a dictionary such as:
        {"category": "fuse", "action": "install", "quantity": 1}

    Or:
        {"category": "transformer", "size": 50,
         "action": "install", "quantity": 1}

    Or:
        {"connector_code": "CNC-HTA-10",
         "action": "install", "quantity": 2}

    This calculates labor hours, not elapsed restoration time.
    """
    if not isinstance(tasks, list) or not tasks:
        return {"error": "Provide at least one repair task."}

    task_results = []
    total_hours = 0.0

    for task in tasks:
        if not isinstance(task, dict):
            return {"error": "Each task must be a dictionary."}

        action = task.get("action", "install")
        quantity = task.get("quantity", 1)

        if action not in ("install", "remove"):
            return {"error": "Each action must be 'install' or 'remove'."}

        if isinstance(quantity, bool) or not isinstance(quantity, int):
            return {"error": "Task quantity must be a whole number."}

        if quantity < 1:
            return {"error": "Task quantity must be at least 1."}

        if task.get("connector_code"):
            result = lookup_connector(
                task["connector_code"],
                action=action
            )
        elif task.get("category"):
            result = lookup_equipment(
                task["category"],
                action=action,
                size=task.get("size")
            )
        else:
            return {
                "error": (
                    "Each task needs a category or connector_code."
                )
            }

        if "error" in result:
            return result

        task_hours = result["labor_hours_per_item"] * quantity
        total_hours += task_hours

        task_results.append({
            **result,
            "quantity": quantity,
            "total_labor_hours": round(task_hours, 3),
        })

    return {
        "tasks": task_results,
        "total_labor_hours": round(total_hours, 3),
        "unit": "labor-hours",
        "elapsed_restoration_time_estimated": False,
        "note": (
            "This is the sum of average task labor hours. "
            "It is not an elapsed restoration-time prediction. "
            "It assumes the supplied tasks are the required work "
            "and does not model crew scheduling, travel, waiting, "
            "or task dependencies."
        ),
    }


if __name__ == "__main__":
    print(f"Loaded {len(EQUIPMENT)} equipment categories.")
    print(f"Loaded {len(CONNECTORS)} connector codes.")

    print("\nExample: replace one fuse cutout")
    print(estimate_labor([
        {
            "category": "fuse",
            "action": "remove",
            "quantity": 1,
        },
        {
            "category": "fuse",
            "action": "install",
            "quantity": 1,
        },
    ]))

    print("\nExample: install one 50 kVA transformer")
    print(estimate_labor([
        {
            "category": "transformer",
            "size": 50,
            "action": "install",
            "quantity": 1,
        }
    ]))

    print("\nExample: install two connectors")
    print(estimate_labor([
        {
            "connector_code": "CNC-HTA-10",
            "action": "install",
            "quantity": 2,
        }
    ]))
