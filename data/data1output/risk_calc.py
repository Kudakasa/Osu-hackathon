
from pathlib import Path
from collections import defaultdict, deque

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent

CUSTOMERS_FILE = DATA_DIR / "Customer_Clip.csv"

EQUIPMENT_FILES = [
    "Transformer_Clip.csv",
    "Fuse_Bank_Clip.csv",
    "Switch_Clip.csv",
]
STORM_FACTORS = {
    "normal": 1.0,
    "moderate": 1.5,
    "severe": 3.0,
}
STORM_SCENARIO = "severe"

# Keep identifiers as strings so leading zeros are preserved.
customers = pd.read_csv(CUSTOMERS_FILE, dtype=str).fillna("")

# One service location may appear in multiple customer records.
# Count distinct locations to estimate the number of affected services.
customers = customers[
    customers["aep_locati"].str.strip() != ""
].copy()

total_services = customers["aep_locati"].nunique()

# Build a graph of upstream -> downstream equipment.
children = defaultdict(set)
node_circuits = defaultdict(set)
asset_to_node = {}
known_nodes = set()

for filename in EQUIPMENT_FILES:
    path = DATA_DIR / filename

    # Switches are optional.
    if not path.exists():
        continue

    equipment = pd.read_csv(path, dtype=str).fillna("")

    for _, row in equipment.iterrows():
        node = row.get("location_n", "").strip()
        upstream = row.get("upstream_l", "").strip()
        asset_id = row.get("id", "").strip()
        circuit = (
            row.get("circuit_id", "").strip()
            or row.get("aep_circui", "").strip()
        )

        if node:
            known_nodes.add(node)

            if circuit:
                node_circuits[node].add(circuit)

        if asset_id and node:
            asset_to_node[asset_id] = node

        if upstream and node and upstream != node:
            children[upstream].add(node)

# Connect each transformer to the service locations assigned to it.
services_at_transformer = defaultdict(set)
services_by_circuit = defaultdict(set)

for _, row in customers.iterrows():
    location = row["aep_locati"].strip()
    transformer = row.get("aep_xfmr_l", "").strip()
    circuit = row.get("circuit_id", "").strip()

    if transformer:
        services_at_transformer[transformer].add(location)

    if circuit:
        services_by_circuit[circuit].add(location)


def get_downstream_nodes(start_node):
    """Return the node and all reachable downstream nodes."""
    visited = set()
    queue = deque([start_node])

    while queue:
        node = queue.popleft()

        if node in visited:
            continue

        visited.add(node)

        for child in children.get(node, []):
            if child not in visited:
                queue.append(child)

    return visited


def calculate_risk(identifier):
    """
    Estimate outage impact for a circuit, equipment ID,
    or network location ID, with an assumed storm adjustment.
    """
    identifier = str(identifier).strip()

    if not identifier:
        return {"error": "Please provide a node or circuit ID."}

    # Equipment record IDs can differ from network location IDs.
    start_node = asset_to_node.get(identifier, identifier)

    # Direct circuit lookup.
    if identifier in services_by_circuit:
        affected = services_by_circuit[identifier]
        method = "Circuit service-location count"
        confidence = "Moderate"

    elif start_node in known_nodes or start_node in children:
        downstream = get_downstream_nodes(start_node)
        affected = set()

        # Include customers whose transformer is downstream.
        for node in downstream:
            affected.update(services_at_transformer.get(node, set()))

        # Include service locations directly associated with this node.
        if start_node in services_at_transformer:
            affected.update(services_at_transformer[start_node])

        # Do not invent a count if customers cannot be traced.
        if not affected:
            return {
                "node": identifier,
                "error": (
                    "This node was found, but its downstream "
                    "customers could not be reliably identified."
                ),
                "confidence": "Low",
            }

        method = "Downstream equipment graph"
        confidence = "Moderate"

    else:
        # The identifier may be a service location.
        matching = set(
            customers.loc[
                customers["aep_locati"] == identifier,
                "aep_locati"
            ]
        )

        if matching:
            affected = matching
            method = "Direct service-location match"
            confidence = "High"
        else:
            return {
                "node": identifier,
                "error": "ID not found in the loaded network data.",
            }

    # Calculate baseline outage impact.
    count = len(affected)
    share = count / total_services if total_services else 0
    score = round(share * 100, 2)

    if share <= 0.01:
        rating = "Low"
    elif share <= 0.05:
        rating = "Moderate"
    elif share <= 0.20:
        rating = "High"
    else:
        rating = "Critical"

    # Apply the assumed storm scenario.
    storm_factor = STORM_FACTORS[STORM_SCENARIO]
    storm_risk_score = round(score * storm_factor, 3)

    return {
        "node": identifier,
        "estimated_affected_service_locations": count,
        "total_service_locations_in_dataset": total_services,
        "percent_of_dataset_affected": round(share * 100, 2),
        "impact_score": score,
        "rating": rating,
        "storm_scenario": STORM_SCENARIO,
        "storm_factor": storm_factor,
        "storm_risk_score": storm_risk_score,
        "method": method,
        "data_confidence": confidence,
        "explanation": (
            f"This estimate identifies {count:,} service locations "
            f"out of {total_services:,} in the dataset "
            f"({share * 100:.2f}%). The impact rating describes "
            f"potential outage impact. The storm-adjusted score "
            f"uses an assumed {STORM_FACTOR if False else 'storm'} "
            f"scenario and is not a probability of equipment failure."
        ),
    }



if __name__ == "__main__":
    print(f"Loaded {total_services:,} distinct service locations.")
    print("Enter a circuit ID, equipment ID, or location node ID.")
    print("Type 'quit' to exit.")

    while True:
        query = input("\nEnter an ID: ").strip()

        if query.lower() in {"quit", "exit"}:
            break

        result = calculate_risk(query)

        for key, value in result.items():
            print(f"{key}: {value}")
