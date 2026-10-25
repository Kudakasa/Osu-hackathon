"""
Interactive utility network map with outage cascade simulation.
All CSV files should be in the same folder as this script.
Switch_Clip.csv is optional.
"""

from pathlib import Path
import html
import json

import pandas as pd
import folium
from folium.plugins import FastMarkerCluster, MarkerCluster


# ============================================================
# FILE PATHS
# ============================================================

HERE = Path(__file__).resolve().parent

CUSTOMERS = HERE / "Customer_Clip.csv"
FUSES = HERE / "Fuse_Bank_Clip.csv"
POLES = HERE / "Pole_Clip.csv"
DAMAGE = HERE / "arcos_prod_damage_09_28_Clip.csv"
PRIMARY = HERE / "Primary_ExportFeatures.csv"
SECONDARY = HERE / "Secondary_ExportFeatures.csv"
SWITCHES = HERE / "Switch_Clip.csv"
TRANSFORMERS = HERE / "Transformer_Clip.csv"

OUTPUT = HERE / "utility2_network_combined.html"


# ============================================================
# PRECEDENCE TIERS
# Lower number = more critical
# ============================================================

TIER_SUBSTATION = 1
TIER_PROTECTION = 2
TIER_PRIMARY = 3
TIER_TRANSFORMER = 4
TIER_SECONDARY = 5
TIER_PREMISE = 6

TIER_NAME = {
    1: "Substation",
    2: "Fuse/Switch",
    3: "Primary (MV)",
    4: "Transformer",
    5: "Secondary (LV)",
    6: "Premise",
}


# ============================================================
# LOAD CSV FILES
# ============================================================

customers = pd.read_csv(
    CUSTOMERS,
    dtype={
        "aep_locati": str,
        "aep_xfmr_l": str,
        "circuit_id": str,
    },
)

fuses = pd.read_csv(
    FUSES,
    dtype={
        "location_n": str,
        "upstream_l": str,
        "aep_circui": str,
        "aep_statio": str,
    },
    low_memory=False,
)

if SWITCHES.exists():
    switches = pd.read_csv(
        SWITCHES,
        dtype={
            "location_n": str,
            "upstream_l": str,
            "aep_circui": str,
            "aep_statio": str,
        },
        low_memory=False,
    )
else:
    switches = pd.DataFrame(
        columns=[
            "location_n",
            "upstream_l",
            "aep_circui",
            "aep_statio",
            "device_typ",
            "normal_sta",
        ]
    )

transformers = pd.read_csv(
    TRANSFORMERS,
    dtype={
        "location_n": str,
        "upstream_l": str,
        "aep_circui": str,
        "aep_statio": str,
    },
    low_memory=False,
)

poles = pd.read_csv(POLES, dtype={"location_n": str})

damage = pd.read_csv(
    DAMAGE,
    dtype={
        "Circuit": str,
        "Substation": str,
    },
)

wire_dtypes = {
    "location_n": str,
    "location_1": str,
    "aep_circui": str,
    "aep_statio": str,
}

primary_lines = pd.read_csv(
    PRIMARY,
    dtype=wire_dtypes,
    low_memory=False,
)

secondary_lines = pd.read_csv(
    SECONDARY,
    dtype=wire_dtypes,
    low_memory=False,
)


# ============================================================
# CUSTOMER COORDINATES
# service_po = longitude
# service__1 = latitude
# ============================================================

customers["lon"] = pd.to_numeric(
    customers["service_po"],
    errors="coerce",
)

customers["lat"] = pd.to_numeric(
    customers["service__1"],
    errors="coerce",
)

customers = customers.dropna(
    subset=["lat", "lon", "aep_locati"]
)

coord_table = (
    customers.groupby("aep_locati", as_index=False)
    .agg(
        lat=("lat", "median"),
        lon=("lon", "median"),
    )
)

coord = {
    row.aep_locati: (float(row.lat), float(row.lon))
    for row in coord_table.itertuples(index=False)
}

customer_points = customers.drop_duplicates(
    subset=["aep_locati"]
)

center = [
    float(customer_points["lat"].median()),
    float(customer_points["lon"].median()),
]

m = folium.Map(
    location=center,
    zoom_start=10,
    tiles="OpenStreetMap",
    control_scale=True,
)

map_name = m.get_name()


# ============================================================
# OUTAGE CASCADE REGISTRY
# ============================================================

reg_features = []
downstream_nodes = {}
feature_sequence = 0


def normalize(value):
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    return str(value)


def register_feature(
    tier,
    circuit,
    station,
    node,
    upstream,
    lat,
    lon,
    label,
):
    global feature_sequence

    fid = f"f{feature_sequence}"
    feature_sequence += 1

    reg_features.append(
        {
            "fid": fid,
            "tier": tier,
            "circuit": str(circuit),
            "station": str(station),
            "node": None if node is None else str(node),
            "up": None if upstream is None else str(upstream),
            "lat": lat,
            "lon": lon,
            "label": label,
        }
    )

    return fid


def add_edge(upstream, node):
    if upstream is None or node is None:
        return

    upstream = str(upstream)
    node = str(node)

    if upstream == node:
        return

    downstream_nodes.setdefault(upstream, set()).add(node)


# ============================================================
# INFER SUBSTATION LOCATIONS
# These are customer centroids, not surveyed substation locations.
# ============================================================

circuit_to_station = {}

for df in (
    fuses,
    switches,
    transformers,
    primary_lines,
    secondary_lines,
):
    if "aep_circui" in df.columns and "aep_statio" in df.columns:
        pairs = df[["aep_circui", "aep_statio"]].dropna()

        for circuit, station in pairs.itertuples(index=False):
            circuit_to_station.setdefault(
                str(circuit),
                str(station),
            )

station_center = {}

if "circuit_id" in customers.columns:
    for circuit, group in customers.dropna(
        subset=["circuit_id"]
    ).groupby("circuit_id"):

        circuit = str(circuit)

        station = circuit_to_station.get(
            circuit,
            f"CIRCUIT {circuit}",
        )

        lat = float(group["lat"].median())
        lon = float(group["lon"].median())

        station_center[f"{station}|{circuit}"] = (
            lat,
            lon,
            circuit,
            station,
        )


# ============================================================
# CUSTOMER PREMISES
# ============================================================

customer_layer = folium.FeatureGroup(
    name="Premises - customer service (tier 6)",
    show=True,
)

customer_data = [
    [float(row.lat), float(row.lon)]
    for row in customer_points.itertuples(index=False)
]

FastMarkerCluster(
    customer_data,
    name="Customer service points",
).add_to(customer_layer)

customer_layer.add_to(m)

premise_registered = 0

for row in customer_points.itertuples(index=False):
    node = normalize(getattr(row, "aep_locati", None))
    transformer_node = normalize(
        getattr(row, "aep_xfmr_l", None)
    )
    circuit = normalize(
        getattr(row, "circuit_id", None)
    ) or "Unknown"

    register_feature(
        TIER_PREMISE,
        circuit,
        None,
        node,
        transformer_node,
        float(row.lat),
        float(row.lon),
        f"Premise {node}",
    )

    add_edge(transformer_node, node)
    premise_registered += 1


# ============================================================
# SUBSTATIONS
# ============================================================

substation_layer = folium.FeatureGroup(
    name="Substations (tier 1 - most critical)",
    show=True,
)

substation_count = 0

for _, (lat, lon, circuit, station) in station_center.items():
    fid = register_feature(
        TIER_SUBSTATION,
        circuit,
        station,
        None,
        None,
        lat,
        lon,
        f"Substation {station}",
    )

    popup = (
        f"<b>Substation:</b> {html.escape(str(station))}<br>"
        f"<b>Circuit:</b> {html.escape(str(circuit))}<br>"
        "<b>Tier:</b> 1 (most critical)<br>"
        "<i>Approximate location based on customer centroid.</i>"
    )

    folium.CircleMarker(
        [lat, lon],
        radius=9,
        color="#000000",
        weight=3,
        fill=True,
        fill_color="#ffd166",
        fill_opacity=0.95,
        tooltip=f"Substation {station}",
        popup=folium.Popup(popup, max_width=320),
        **{"className": f"cascade-node {fid}"},
    ).add_to(substation_layer)

    substation_count += 1

substation_layer.add_to(m)


# ============================================================
# FUSES AND SWITCHES
# ============================================================

def add_devices(df, layer, tier, color, label_kind):
    count = 0

    for row in df.itertuples(index=False):
        node = normalize(getattr(row, "location_n", None))

        if node is None or node not in coord:
            continue

        lat, lon = coord[node]

        upstream = normalize(
            getattr(row, "upstream_l", None)
        )

        device = getattr(row, "device_typ", label_kind)

        circuit = normalize(
            getattr(row, "aep_circui", None)
        ) or "Unknown"

        station = normalize(
            getattr(row, "aep_statio", None)
        )

        normal_state = getattr(row, "normal_sta", "Unknown")

        fid = register_feature(
            tier,
            circuit,
            station,
            node,
            upstream,
            lat,
            lon,
            f"{label_kind} {node}",
        )

        add_edge(upstream, node)

        popup = (
            f"<b>{html.escape(label_kind)}:</b> "
            f"{html.escape(str(device))}<br>"
            f"<b>Tier:</b> {tier} "
            f"({html.escape(TIER_NAME[tier])})<br>"
            f"<b>Node ID:</b> {html.escape(node)}<br>"
            f"<b>Upstream node:</b> "
            f"{html.escape(str(upstream))}<br>"
            f"<b>Normal state:</b> "
            f"{html.escape(str(normal_state))}<br>"
            f"<b>Circuit:</b> {html.escape(str(circuit))}<br>"
            f"<b>Substation:</b> {html.escape(str(station))}<br>"
            "<i>Click to simulate failure.</i>"
        )

        folium.CircleMarker(
            [lat, lon],
            radius=5,
            color=color,
            weight=2,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            tooltip=f"{label_kind} | {circuit}",
            popup=folium.Popup(popup, max_width=340),
            **{"className": f"cascade-node {fid}"},
        ).add_to(layer)

        count += 1

    return count


protection_layer = folium.FeatureGroup(
    name="Fuses & Switches (tier 2)",
    show=True,
)

fuse_count = add_devices(
    fuses,
    protection_layer,
    TIER_PROTECTION,
    "#7b2cbf",
    "Fuse",
)

switch_count = add_devices(
    switches,
    protection_layer,
    TIER_PROTECTION,
    "#b5179e",
    "Switch",
)

protection_layer.add_to(m)


# ============================================================
# INFERRED FUSE/SWITCH UPSTREAM LINKS
# These are approximate connections, not surveyed wire geometry.
# ============================================================

inferred_layer = folium.FeatureGroup(
    name="Inferred fuse/switch upstream links (approximate)",
    show=False,
)

inferred_seen = set()
inferred_link_count = 0

for df, kind in ((fuses, "Fuse"), (switches, "Switch")):
    for row in df.itertuples(index=False):
        node = normalize(getattr(row, "location_n", None))
        upstream = normalize(getattr(row, "upstream_l", None))

        if (
            node is None
            or upstream is None
            or node == upstream
            or node not in coord
            or upstream not in coord
        ):
            continue

        edge_key = (upstream, node)

        if edge_key in inferred_seen:
            continue

        inferred_seen.add(edge_key)

        circuit = normalize(
            getattr(row, "aep_circui", None)
        ) or "Unknown"

        popup = (
            f"<b>Inferred {kind} upstream link</b><br>"
            f"<b>Upstream node:</b> {html.escape(upstream)}<br>"
            f"<b>Device node:</b> {html.escape(node)}<br>"
            f"<b>Circuit:</b> {html.escape(circuit)}<br>"
            "<i>Approximate identifier-based connection.</i>"
        )

        folium.PolyLine(
            [coord[upstream], coord[node]],
            color="#5b5bd6",
            weight=2,
            opacity=0.55,
            tooltip=f"Inferred {kind} link | {circuit}",
            popup=folium.Popup(popup, max_width=350),
        ).add_to(inferred_layer)

        inferred_link_count += 1

inferred_layer.add_to(m)


# ============================================================
# TRANSFORMERS
# ============================================================

transformer_layer = folium.FeatureGroup(
    name="Transformers (tier 4)",
    show=True,
)

transformer_count = 0

for row in transformers.itertuples(index=False):
    node = normalize(getattr(row, "location_n", None))

    if node is None or node not in coord:
        continue

    lat, lon = coord[node]

    upstream = normalize(
        getattr(row, "upstream_l", None)
    )

    circuit = normalize(
        getattr(row, "aep_circui", None)
    ) or "Unknown"

    station = normalize(
        getattr(row, "aep_statio", None)
    )

    size = getattr(row, "load_nomin", "Unknown")
    customers_served = getattr(row, "aep_custom", "Unknown")

    fid = register_feature(
        TIER_TRANSFORMER,
        circuit,
        station,
        node,
        upstream,
        lat,
        lon,
        f"Transformer {node}",
    )

    add_edge(upstream, node)

    popup = (
        "<b>Distribution Transformer</b><br>"
        "<b>Tier:</b> 4 (Transformer)<br>"
        f"<b>Node ID:</b> {html.escape(node)}<br>"
        f"<b>Upstream node:</b> {html.escape(str(upstream))}<br>"
        f"<b>Nominal load:</b> {html.escape(str(size))}<br>"
        f"<b>Customers served:</b> "
        f"{html.escape(str(customers_served))}<br>"
        f"<b>Circuit:</b> {html.escape(str(circuit))}<br>"
        f"<b>Substation:</b> {html.escape(str(station))}<br>"
        "<i>Click to simulate failure.</i>"
    )

    folium.CircleMarker(
        [lat, lon],
        radius=4,
        color="#2a9d8f",
        weight=2,
        fill=True,
        fill_color="#2a9d8f",
        fill_opacity=0.85,
        tooltip=f"Transformer | {circuit}",
        popup=folium.Popup(popup, max_width=340),
        **{"className": f"cascade-node {fid}"},
    ).add_to(transformer_layer)

    transformer_count += 1

transformer_layer.add_to(m)


# ============================================================
# PRIMARY AND SECONDARY POWER LINES
# ============================================================

def add_power_lines(
    df,
    layer,
    color,
    weight,
    opacity,
    label,
    tier,
):
    drawn = 0
    skipped = 0

    for row in df.itertuples(index=False):
        from_node = normalize(
            getattr(row, "location_n", None)
        )

        to_node = normalize(
            getattr(row, "location_1", None)
        )

        if (
            from_node is None
            or to_node is None
            or from_node not in coord
            or to_node not in coord
        ):
            skipped += 1
            continue

        point_a = coord[from_node]
        point_b = coord[to_node]

        if point_a == point_b:
            skipped += 1
            continue

        usage = getattr(row, "usage", "Unknown")
        voltage = getattr(row, "nominal_vo", "Unknown")

        circuit = normalize(
            getattr(row, "aep_circui", None)
        ) or "Unknown"

        station = normalize(
            getattr(row, "aep_statio", None)
        )

        length = getattr(row, "report_len", "Unknown")
        construction = getattr(row, "aep_constr", "Unknown")

        midpoint_lat = (point_a[0] + point_b[0]) / 2
        midpoint_lon = (point_a[1] + point_b[1]) / 2

        fid = register_feature(
            tier,
            circuit,
            station,
            to_node,
            from_node,
            midpoint_lat,
            midpoint_lon,
            f"{label} segment",
        )

        add_edge(from_node, to_node)

        popup = (
            f"<b>{html.escape(label)} power line</b><br>"
            f"<b>Tier:</b> {tier} "
            f"({html.escape(TIER_NAME[tier])})<br>"
            f"<b>Usage:</b> {html.escape(str(usage))}<br>"
            f"<b>Nominal voltage:</b> {html.escape(str(voltage))}<br>"
            f"<b>Circuit:</b> {html.escape(str(circuit))}<br>"
            f"<b>Substation:</b> {html.escape(str(station))}<br>"
            f"<b>Construction:</b> "
            f"{html.escape(str(construction))}<br>"
            f"<b>Reported length (ft):</b> "
            f"{html.escape(str(length))}<br>"
            f"<b>From node:</b> {html.escape(from_node)}<br>"
            f"<b>To node:</b> {html.escape(to_node)}<br>"
            "<i>Click to simulate failure.</i>"
        )

        folium.PolyLine(
            [point_a, point_b],
            color=color,
            weight=weight,
            opacity=opacity,
            tooltip=f"{label} | {usage} | {circuit}",
            popup=folium.Popup(popup, max_width=360),
            **{"className": f"cascade-node {fid}"},
        ).add_to(layer)

        drawn += 1

    return drawn, skipped


primary_layer = folium.FeatureGroup(
    name="Primary power lines (MV, tier 3)",
    show=True,
)

primary_drawn, primary_skipped = add_power_lines(
    primary_lines,
    primary_layer,
    "#1b4332",
    3,
    0.8,
    "Primary (MV)",
    TIER_PRIMARY,
)

primary_layer.add_to(m)

secondary_layer = folium.FeatureGroup(
    name="Secondary power lines (LV, tier 5)",
    show=True,
)

secondary_drawn, secondary_skipped = add_power_lines(
    secondary_lines,
    secondary_layer,
    "#e07a00",
    1.5,
    0.65,
    "Secondary (LV)",
    TIER_SECONDARY,
)

secondary_layer.add_to(m)


# ============================================================
# DAMAGE INCIDENTS
# ============================================================

damage["lat"] = pd.to_numeric(
    damage["Latitude"],
    errors="coerce",
)

damage["lon"] = pd.to_numeric(
    damage["Longitude"],
    errors="coerce",
)

damage = damage.dropna(subset=["lat", "lon"])

damage_layer = folium.FeatureGroup(
    name="Reported damage incidents",
    show=True,
)

damage_cluster = MarkerCluster(
    name="Damage incidents"
).add_to(damage_layer)

for row in damage.itertuples(index=False):
    circuit = getattr(row, "Circuit", "Unknown")
    station = getattr(row, "Substation", "Unknown")
    asset = getattr(row, "Asset_Type", "Unknown")
    work = getattr(row, "Inform_Wor", "Unknown")
    reported = getattr(row, "Report_Tim", "Unknown")
    repaired = getattr(row, "Repaired", "Unknown")
    repair_date = getattr(row, "Repaired_D", "Unknown")

    popup = (
        f"<b>Circuit:</b> {html.escape(str(circuit))}<br>"
        f"<b>Substation:</b> {html.escape(str(station))}<br>"
        f"<b>Asset type:</b> {html.escape(str(asset))}<br>"
        f"<b>Damage:</b> {html.escape(str(work))}<br>"
        f"<b>Reported:</b> {html.escape(str(reported))}<br>"
        f"<b>Repaired:</b> {html.escape(str(repaired))}<br>"
        f"<b>Repair date:</b> {html.escape(str(repair_date))}"
    )

    folium.CircleMarker(
        [float(row.lat), float(row.lon)],
        radius=5,
        color="#d62828",
        fill=True,
        fill_color="#ef233c",
        fill_opacity=0.85,
        tooltip=f"Damage | {circuit} | {work}",
        popup=folium.Popup(popup, max_width=360),
    ).add_to(damage_cluster)

damage_layer.add_to(m)


# ============================================================
# LAYER CONTROL
# ============================================================

folium.LayerControl(collapsed=False).add_to(m)


# ============================================================
# LEGEND
# ============================================================

legend = f"""
<div style="
 position:fixed;
 bottom:16px;
 left:16px;
 z-index:9999;
 background:rgba(255,255,255,0.94);
 padding:8px;
 border:1px solid #bbb;
 border-radius:4px;
 font-size:11px;
 line-height:1.4;
 max-width:250px;
">
<b>Utility outage-cascade map</b>
<details open>
<summary style="cursor:pointer">Legend, tiers &amp; counts</summary>

<b>Precedence (fails downward):</b><br>
1 <span style="color:#000">★</span> Substation<br>
2 <span style="color:#7b2cbf">●</span> Fuse /
<span style="color:#b5179e">●</span> Switch<br>
3 <span style="color:#1b4332">━</span> Primary power line<br>
4 <span style="color:#2a9d8f">●</span> Transformer<br>
5 <span style="color:#e07a00">━</span> Secondary power line<br>
6 <span style="color:#3388ff">●</span> Premise<br>
<span style="color:#d62828">●</span> Damage incident

<hr>

Substations: {substation_count:,}<br>
Fuses: {fuse_count:,}<br>
Switches: {switch_count:,}<br>
Transformers: {transformer_count:,}<br>
Primary lines: {primary_drawn:,}
(skipped {primary_skipped:,})<br>
Secondary lines: {secondary_drawn:,}
(skipped {secondary_skipped:,})<br>
Inferred links: {inferred_link_count:,}<br>
Premises: {premise_registered:,}<br>
Damage incidents: {len(damage):,}

<hr>

<b>Click a substation, fuse, switch, transformer, or power-line segment</b>
to simulate failure. Affected downstream registered features turn red.
Customer premises are clustered for performance and are not individually
clickable for simulation.

<div style="margin-top:5px">
<button id="cascadeReset"
style="font-size:10px;padding:3px 6px;cursor:pointer">
Reset simulation
</button>
<span id="cascadeInfo" style="margin-left:5px;color:#555"></span>
</div>

<i>Locations are inferred from customer IDs, not surveyed geometry.</i>
</details>
</div>
"""

m.get_root().html.add_child(folium.Element(legend))


# ============================================================
# OUTAGE CASCADE JAVASCRIPT
# ============================================================

features_json = json.dumps(reg_features)

edges_json = json.dumps(
    {
        node: sorted(children)
        for node, children in downstream_nodes.items()
    }
)

cascade_js = f"""
<script>
(function() {{
    var FEATURES = {features_json};
    var EDGES = {edges_json};
    var byFid = {{}};

    FEATURES.forEach(function(feature) {{
        byFid[feature.fid] = feature;
    }});

    function descendants(startNode) {{
        var seen = {{}};
        var stack = startNode ? [startNode] : [];
        var result = {{}};

        while (stack.length) {{
            var node = stack.pop();

            if (seen[node]) continue;
            seen[node] = true;

            (EDGES[node] || []).forEach(function(child) {{
                result[child] = true;
                stack.push(child);
            }});
        }}

        return result;
    }}

    function impacted(source) {{
        var hits = [];
        var downstream = descendants(source.node);
        var hasTopology = Object.keys(downstream).length > 0;

        FEATURES.forEach(function(feature) {{
            if (feature.fid === source.fid) return;
            if (feature.tier <= source.tier) return;
            if (feature.circuit !== source.circuit) return;

            if (!hasTopology ||
                (feature.node && downstream[feature.node]) ||
                !feature.node) {{
                hits.push(feature);
            }}
        }});

        return hits;
    }}

    function paint(fid, enabled) {{
        var elements = document.getElementsByClassName(fid);

        for (var i = 0; i < elements.length; i++) {{
            var element = elements[i];

            if (enabled) {{
                if (!element.dataset.originalStroke) {{
                    element.dataset.originalStroke =
                        element.getAttribute("stroke") || "";
                    element.dataset.originalFill =
                        element.getAttribute("fill") || "";
                }}

                element.setAttribute("stroke", "#e63946");

                if (element.getAttribute("fill") &&
                    element.getAttribute("fill") !== "none") {{
                    element.setAttribute("fill", "#e63946");
                }}

                element.setAttribute("stroke-opacity", "1");
            }} else {{
                if (element.dataset.originalStroke !== undefined) {{
                    element.setAttribute(
                        "stroke",
                        element.dataset.originalStroke
                    );

                    if (element.dataset.originalFill !== undefined) {{
                        element.setAttribute(
                            "fill",
                            element.dataset.originalFill
                        );
                    
                }}
            }}
        }}
    }}

    var active = [];

    function reset() {{
        active.forEach(function(fid) {{
            paint(fid, false);
        }});

        active = [];

        var info = document.getElementById("cascadeInfo");
        if (info) info.textContent = "";
    }}

    function simulate(source) {{
        reset();

        var hits = impacted(source);

        hits.forEach(function(feature) {{
            paint(feature.fid, true);
            active.push(feature.fid);
        }});

        paint(source.fid, true);
        active.push(source.fid);

        var info = document.getElementById("cascadeInfo");
        if (info) {{
            info.textContent = hits.length + " downstream affected";
        }}
    }}

    function connectMap() {{
        var map = window["{map_name}"];

        if (!map) {{
            setTimeout(connectMap, 300);
            return;
        }}

        map.getContainer().addEventListener("click", function(event) {{
            var element = event.target;

            while (element && element !== map.getContainer()) {{
                if (element.classList &&
                    element.classList.contains("cascade-node")) {{

                    var fid = null;

                    element.classList.forEach(function(className) {{
                        if (byFid[className]) fid = className;
                    }});

                    if (fid) simulate(byFid[fid]);
                    return;
                }}

                element = element.parentNode;
            }}
        }}, true);

        var resetButton = document.getElementById("cascadeReset");

        if (resetButton) {{
            resetButton.addEventListener("click", reset);
        }}
    }}

    if (document.readyState === "complete") {{
        connectMap();
    }} else {{
        window.addEventListener("load", connectMap);
    }}
}})();
</script>
"""

m.get_root().html.add_child(folium.Element(cascade_js))


# ============================================================
# SAVE OUTPUT
# ============================================================

m.save(OUTPUT)

print(f"Saved map: {OUTPUT.resolve()}")
print(f"Substations: {substation_count:,}")
print(f"Fuses: {fuse_count:,}")
print(f"Switches: {switch_count:,}")
print(f"Primary lines drawn: {primary_drawn:,}")
print(f"Primary lines skipped: {primary_skipped:,}")
print(f"Transformers drawn: {transformer_count:,}")
print(f"Secondary lines drawn: {secondary_drawn:,}")
print(f"Secondary lines skipped: {secondary_skipped:,}")
print(f"Premises registered: {premise_registered:,}")
print(f"Damage incidents: {len(damage):,}")
print(f"Inferred fuse/switch links: {inferred_link_count:,}")
print(f"Cascade features registered: {len(reg_features):,}")

print(
    "Click a substation, fuse, switch, transformer, or power line "
    "to simulate failure."
)