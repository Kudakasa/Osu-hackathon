"""Build an interactive utility-network investigation and outage-cascade map.

Put this script in the same folder as:
  Customer_Clip.csv, Fuse_Bank_Clip.csv, Pole_Clip.csv,
  arcos_prod_damage_09_28_Clip.csv, Primary_ExportFeatures.csv
and in the sibling data2output folder:
  Secondary_ExportFeatures.csv, Switch_Clip.csv, Transformer_Clip.csv

Geometry note: the Primary/Secondary/device exports lost their shapefile
geometry during CSV conversion, so none of them carry coordinates. Each record
does record node IDs (location_n = the device/segment node, location_1 = a
segment's to-node, upstream_l = the upstream node, aep_xfmr_l = a customer's
transformer). Most of those node IDs also appear as customer service-location
IDs (aep_locati), so this script geo-locates everything through the customer
coordinate table and draws lines between the resolved node coordinates.
Features whose nodes have no co-located customer are skipped (see legend).

Outage-precedence model (most critical -> least critical):
    Substation > Fuse/Switch > Primary > Transformer > Secondary > Premise
If a component fails, everything BELOW it in precedence that depends on it
also fails. The map encodes this two ways:
  * Each feature is tagged with its precedence tier and its circuit.
  * Click any feature to "simulate a failure": every dependent downstream
    feature (lower tier, same circuit, and downstream in the node graph where
    that topology is known) is highlighted in red. Click again / use the
    Reset button to clear.
"""
from pathlib import Path
import html
import json
import pandas as pda
import folium
from folium.plugins import FastMarkerCluster, MarkerCluster

HERE = Path(__file__).resolve().parent
DATA2 = HERE.parent / "data2output"
CUSTOMERS = HERE / "Customer_Clip.csv"
FUSES = HERE / "Fuse_Bank_Clip.csv"
POLES = HERE / "Pole_Clip.csv"
DAMAGE = HERE / "arcos_prod_damage_09_28_Clip.csv"
PRIMARY = HERE / "Primary_ExportFeatures.csv"
SECONDARY = DATA2 / "Secondary_ExportFeatures.csv"
SWITCHES = DATA2 / "Switch_Clip.csv"
TRANSFORMERS = DATA2 / "Transformer_Clip.csv"
OUTPUT = HERE / "utility_network_map.html"

# ----------------------------------------------------------------------------
# Precedence tiers. Lower number = more critical (fails first, cascades widest).
# ----------------------------------------------------------------------------
TIER_SUBSTATION = 1
TIER_PROTECTION = 2  # Fuse / Switch
TIER_PRIMARY = 3
TIER_TRANSFORMER = 4
TIER_SECONDARY = 5
TIER_PREMISE = 6
TIER_NAME = {
    1: "Substation", 2: "Fuse/Switch", 3: "Primary (MV)",
    4: "Transformer", 5: "Secondary (LV)", 6: "Premise",
}

# ----------------------------------------------------------------------------
# Load data. Identifiers read as strings so exact ID matches are preserved.
# ----------------------------------------------------------------------------
customers = pda.read_csv(CUSTOMERS, dtype={"aep_locati": str, "aep_xfmr_l": str, "circuit_id": str})
fuses = pda.read_csv(FUSES, dtype={"location_n": str, "upstream_l": str, "aep_circui": str, "aep_statio": str}, low_memory=False)
switches = pda.read_csv(SWITCHES, dtype={"location_n": str, "upstream_l": str, "aep_circui": str, "aep_statio": str}, low_memory=False)
transformers = pda.read_csv(TRANSFORMERS, dtype={"location_n": str, "upstream_l": str, "aep_circui": str, "aep_statio": str}, low_memory=False)
poles = pda.read_csv(POLES, dtype={"location_n": str})
damage = pda.read_csv(DAMAGE, dtype={"Circuit": str, "Substation": str})

_wire_dtypes = {"location_n": str, "location_1": str, "aep_circui": str, "aep_statio": str}
primary_lines = pda.read_csv(PRIMARY, dtype=_wire_dtypes, low_memory=False)
secondary_lines = pda.read_csv(SECONDARY, dtype=_wire_dtypes, low_memory=False)

# Customer CSV: service_po = longitude; service__1 = latitude.
customers["lon"] = pda.to_numeric(customers["service_po"], errors="coerce")
customers["lat"] = pda.to_numeric(customers["service__1"], errors="coerce")
customers = customers.dropna(subset=["lat", "lon", "aep_locati"])

# Node -> coordinate lookup: median customer coordinate at each location ID.
coord_table = (customers.groupby("aep_locati", as_index=False)
               .agg(lat=("lat", "median"), lon=("lon", "median")))
coord = {r.aep_locati: (float(r.lat), float(r.lon)) for r in coord_table.itertuples(index=False)}

customer_points = customers.drop_duplicates(subset=["aep_locati"])
center = [float(customer_points["lat"].median()), float(customer_points["lon"].median())]
m = folium.Map(location=center, zoom_start=10, tiles="OpenStreetMap", control_scale=True)
map_name = m.get_name()

# ----------------------------------------------------------------------------
# Cascade registry. Every drawable feature registers here so the browser can
# compute, on click, which downstream features fail with it.
#   features:  id -> {tier, circuit, station, node, upstream, lat, lon, label}
# Edges (upstream node -> set of downstream nodes) capture known topology.
# ----------------------------------------------------------------------------
reg_features = []          # list of dicts handed to the browser as JSON
downstream_nodes = {}      # node -> set(child nodes) from upstream_l relations
_feat_seq = 0


def _register(tier, circuit, station, node, upstream, lat, lon, label):
    global _feat_seq
    fid = f"f{_feat_seq}"
    _feat_seq += 1
    reg_features.append({
        "fid": fid, "tier": tier, "circuit": str(circuit), "station": str(station),
        "node": None if node is None else str(node),
        "up": None if upstream is None else str(upstream),
        "lat": lat, "lon": lon, "label": label,
    })
    return fid


def _add_edge(upstream, node):
    if upstream is None or node is None:
        return
    upstream, node = str(upstream), str(node)
    if upstream == node:
        return
    downstream_nodes.setdefault(upstream, set()).add(node)


def _norm(v):
    return None if (v is None or pda.isna(v) or str(v).strip() == "") else str(v)

# ----------------------------------------------------------------------------
# Substation anchors. The customer export has no substation column, so map each
# circuit to its substation name from the device/line files, then anchor the
# substation at the centroid of that circuit's customers (best available).
# ----------------------------------------------------------------------------
circuit_to_station = {}
for _df in (fuses, switches, transformers, primary_lines, secondary_lines):
    if "aep_circui" in _df.columns and "aep_statio" in _df.columns:
        pairs = _df[["aep_circui", "aep_statio"]].dropna()
        for circ, st in pairs.itertuples(index=False):
            circuit_to_station.setdefault(str(circ), str(st))

station_center = {}  # station name -> (lat, lon, circuit)
if "circuit_id" in customers.columns:
    for circuit, g in customers.dropna(subset=["circuit_id"]).groupby("circuit_id"):
        circuit = str(circuit)
        station = circuit_to_station.get(circuit, f"CIRCUIT {circuit}")
        slat, slon = float(g["lat"].median()), float(g["lon"].median())
        # key on station+circuit so distinct circuits under one station still anchor
        station_center[f"{station}|{circuit}"] = (slat, slon, circuit, station)

# ----------------------------------------------------------------------------
# Layer: customer service locations (Premise, tier 6).
# ----------------------------------------------------------------------------
customer_layer = folium.FeatureGroup(name="Premises - customer service (tier 6)", show=True)
customer_data = [[float(r.lat), float(r.lon)] for r in customer_points.itertuples(index=False)]
FastMarkerCluster(customer_data, name="Customer service points").add_to(customer_layer)
customer_layer.add_to(m)

# Register premises for cascade (lightweight: tie each to its transformer + circuit).
premise_registered = 0
for r in customer_points.itertuples(index=False):
    node = _norm(getattr(r, "aep_locati", None))
    xfmr = _norm(getattr(r, "aep_xfmr_l", None))
    circuit = _norm(getattr(r, "circuit_id", None)) or "Unknown"
    station = _norm(getattr(r, "aep_statio", None)) if hasattr(r, "aep_statio") else None
    _register(TIER_PREMISE, circuit, station, node, xfmr, float(r.lat), float(r.lon),
              f"Premise {node}")
    _add_edge(xfmr, node)  # transformer -> premise
    premise_registered += 1

# ----------------------------------------------------------------------------
# Layer: substations (tier 1).
# ----------------------------------------------------------------------------
substation_layer = folium.FeatureGroup(name="Substations (tier 1 - most critical)", show=True)
substation_count = 0
for _key, (slat, slon, circuit, station) in station_center.items():
    fid = _register(TIER_SUBSTATION, circuit, station, None, None, slat, slon, f"Substation {station}")
    popup = (f"<b>Substation:</b> {html.escape(str(station))}<br>"
             f"<b>Circuit:</b> {html.escape(str(circuit))}<br>"
             f"<b>Tier:</b> 1 (most critical)<br>"
             "<i>Anchored at circuit customer centroid; failure cascades to the whole circuit.</i>")
    folium.CircleMarker([slat, slon], radius=9, color="#000000", weight=3, fill=True,
                        fill_color="#ffd166", fill_opacity=0.95,
                        tooltip=f"Substation {station}",
                        popup=folium.Popup(popup, max_width=320),
                        **{"className": f"cascade-node {fid}"}).add_to(substation_layer)
    reg_features[-1]["marker"] = True
    substation_count += 1
substation_layer.add_to(m)

# ----------------------------------------------------------------------------
# Protective devices: Fuses and Switches (tier 2).
# ----------------------------------------------------------------------------
def add_devices(df, layer, tier, color, icon_shape, label_kind):
    count = 0
    for r in df.itertuples(index=False):
        node = _norm(getattr(r, "location_n", None))
        if node is None or node not in coord:
            continue
        lat, lon = coord[node]
        upstream = _norm(getattr(r, "upstream_l", None))
        device = getattr(r, "device_typ", label_kind)
        circuit = _norm(getattr(r, "aep_circui", None)) or "Unknown"
        station = _norm(getattr(r, "aep_statio", None))
        normal = getattr(r, "normal_sta", "Unknown")
        fid = _register(tier, circuit, station, node, upstream, lat, lon,
                        f"{label_kind} {node}")
        _add_edge(upstream, node)
        popup = (f"<b>{html.escape(str(label_kind))}:</b> {html.escape(str(device))}<br>"
                 f"<b>Tier:</b> {tier} ({html.escape(TIER_NAME[tier])})<br>"
                 f"<b>Node ID:</b> {html.escape(node)}<br>"
                 f"<b>Upstream node:</b> {html.escape(str(upstream))}<br>"
                 f"<b>Normal state:</b> {html.escape(str(normal))}<br>"
                 f"<b>Circuit:</b> {html.escape(str(circuit))}<br>"
                 f"<b>Substation:</b> {html.escape(str(station))}<br>"
                 "<i>Click to simulate failure and highlight downstream impact.</i>")
        folium.CircleMarker([lat, lon], radius=5, color=color, weight=2, fill=True,
                            fill_color=color, fill_opacity=0.85,
                            tooltip=f"{label_kind} | {circuit}",
                            popup=folium.Popup(popup, max_width=340),
                            **{"className": f"cascade-node {fid}"}).add_to(layer)
        reg_features[-1]["marker"] = True
        count += 1
    return count


protection_layer = folium.FeatureGroup(name="Fuses & Switches (tier 2)", show=True)
fuse_count = add_devices(fuses, protection_layer, TIER_PROTECTION, "#7b2cbf", "circle", "Fuse")
switch_count = add_devices(switches, protection_layer, TIER_PROTECTION, "#b5179e", "square", "Switch")
protection_layer.add_to(m)

# ----------------------------------------------------------------------------
# Transformers (tier 4).
# ----------------------------------------------------------------------------
transformer_layer = folium.FeatureGroup(name="Transformers (tier 4)", show=True)
transformer_count = 0
for r in transformers.itertuples(index=False):
    node = _norm(getattr(r, "location_n", None))
    if node is None or node not in coord:
        continue
    lat, lon = coord[node]
    upstream = _norm(getattr(r, "upstream_l", None))
    circuit = _norm(getattr(r, "aep_circui", None)) or "Unknown"
    station = _norm(getattr(r, "aep_statio", None))
    size = getattr(r, "load_nomin", "Unknown")
    cust_n = getattr(r, "aep_custom", "Unknown")
    fid = _register(TIER_TRANSFORMER, circuit, station, node, upstream, lat, lon,
                    f"Transformer {node}")
    _add_edge(upstream, node)  # upstream fuse/primary node -> transformer
    popup = (f"<b>Transformer (Distribution)</b><br>"
             f"<b>Tier:</b> 4 (Transformer)<br>"
             f"<b>Node ID:</b> {html.escape(node)}<br>"
             f"<b>Upstream node:</b> {html.escape(str(upstream))}<br>"
             f"<b>Secondary voltage:</b> {html.escape(str(size))}<br>"
             f"<b>Customers served:</b> {html.escape(str(cust_n))}<br>"
             f"<b>Circuit:</b> {html.escape(str(circuit))}<br>"
             f"<b>Substation:</b> {html.escape(str(station))}<br>"
             "<i>Click to simulate failure and highlight downstream impact.</i>")
    folium.CircleMarker([lat, lon], radius=4, color="#2a9d8f", weight=2, fill=True,
                        fill_color="#2a9d8f", fill_opacity=0.85,
                        tooltip=f"Transformer | {circuit}",
                        popup=folium.Popup(popup, max_width=340),
                        **{"className": f"cascade-node {fid}"}).add_to(transformer_layer)
    reg_features[-1]["marker"] = True
    transformer_count += 1
transformer_layer.add_to(m)

# ----------------------------------------------------------------------------
# Power lines: Primary (tier 3) and Secondary (tier 5).
# ----------------------------------------------------------------------------
def add_power_lines(df, layer, color, weight, opacity, label, tier):
    drawn = skipped = 0
    for row in df.itertuples(index=False):
        from_node = _norm(getattr(row, "location_n", None))
        to_node = _norm(getattr(row, "location_1", None))
        if from_node is None or to_node is None or from_node not in coord or to_node not in coord:
            skipped += 1
            continue
        a, b = coord[from_node], coord[to_node]
        if a == b:
            skipped += 1
            continue
        usage = getattr(row, "usage", "Unknown")
        voltage = getattr(row, "nominal_vo", "Unknown")
        circuit = _norm(getattr(row, "aep_circui", None)) or "Unknown"
        station = _norm(getattr(row, "aep_statio", None))
        length = getattr(row, "report_len", "Unknown")
        construct = getattr(row, "aep_constr", "Unknown")
        mid_lat, mid_lon = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        fid = _register(tier, circuit, station, to_node, from_node, mid_lat, mid_lon,
                        f"{label} segment")
        _add_edge(from_node, to_node)  # line connects from-node -> to-node
        popup = (f"<b>{html.escape(label)} power line</b><br>"
                 f"<b>Tier:</b> {tier} ({html.escape(TIER_NAME[tier])})<br>"
                 f"<b>Usage:</b> {html.escape(str(usage))}<br>"
                 f"<b>Nominal voltage:</b> {html.escape(str(voltage))}<br>"
                 f"<b>Circuit:</b> {html.escape(str(circuit))}<br>"
                 f"<b>Substation:</b> {html.escape(str(station))}<br>"
                 f"<b>Construction:</b> {html.escape(str(construct))}<br>"
                 f"<b>Reported length (ft):</b> {html.escape(str(length))}<br>"
                 f"<b>From node:</b> {html.escape(from_node)}<br>"
                 f"<b>To node:</b> {html.escape(to_node)}<br>"
                 "<i>Click to simulate failure and highlight downstream impact.</i>")
        folium.PolyLine([a, b], color=color, weight=weight, opacity=opacity,
                        tooltip=f"{label} | {usage} | {circuit}",
                        popup=folium.Popup(popup, max_width=360),
                        **{"className": f"cascade-node {fid}"}).add_to(layer)
        reg_features[-1]["marker"] = False
        drawn += 1
    return drawn, skipped


primary_layer = folium.FeatureGroup(name="Primary power lines (MV 12.47 kV, tier 3)", show=True)
primary_drawn, primary_skipped = add_power_lines(
    primary_lines, primary_layer, "#1b4332", 3, 0.8, "Primary (MV)", TIER_PRIMARY)
primary_layer.add_to(m)

secondary_layer = folium.FeatureGroup(name="Secondary power lines (LV 240 V, tier 5)", show=True)
secondary_drawn, secondary_skipped = add_power_lines(
    secondary_lines, secondary_layer, "#e07a00", 1.5, 0.65, "Secondary (LV)", TIER_SECONDARY)
secondary_layer.add_to(m)

# ----------------------------------------------------------------------------
# Damage incidents (actual coordinates).
# ----------------------------------------------------------------------------
damage["lat"] = pda.to_numeric(damage["Latitude"], errors="coerce")
damage["lon"] = pda.to_numeric(damage["Longitude"], errors="coerce")
damage_layer = folium.FeatureGroup(name="Reported damage incidents", show=True)
damage = damage.dropna(subset=["lat", "lon"])
cluster = MarkerCluster(name="Damage incidents").add_to(damage_layer)
for r in damage.itertuples(index=False):
    circuit = getattr(r, "Circuit", "Unknown")
    station = getattr(r, "Substation", "Unknown")
    asset = getattr(r, "Asset_Type", "Unknown")
    work = getattr(r, "Inform_Wor", "Unknown")
    reported = getattr(r, "Report_Tim", "Unknown")
    repaired = getattr(r, "Repaired", "Unknown")
    repair_date = getattr(r, "Repaired_D", "Unknown")
    popup = (f"<b>Circuit:</b> {html.escape(str(circuit))}<br>"
             f"<b>Substation:</b> {html.escape(str(station))}<br>"
             f"<b>Asset type:</b> {html.escape(str(asset))}<br>"
             f"<b>Damage:</b> {html.escape(str(work))}<br>"
             f"<b>Reported:</b> {html.escape(str(reported))}<br>"
             f"<b>Repaired:</b> {html.escape(str(repaired))}<br>"
             f"<b>Repair date field:</b> {html.escape(str(repair_date))}")
    folium.CircleMarker([float(r.lat), float(r.lon)], radius=5, color="#d62828",
                        fill=True, fill_color="#ef233c", fill_opacity=0.85,
                        tooltip=f"Damage | {circuit} | {work}",
                        popup=folium.Popup(popup, max_width=360)).add_to(cluster)
damage_layer.add_to(m)

folium.LayerControl(collapsed=False).add_to(m)

# ----------------------------------------------------------------------------
# Legend.
# ----------------------------------------------------------------------------
legend = f'''<div style="position: fixed; bottom: 16px; left: 16px; z-index:9999; background:rgba(255,255,255,0.9); padding:6px 8px; border:1px solid #bbb; border-radius:4px; font-size:10px; line-height:1.35; max-width:230px;">
<b style="font-size:11px;">Utility outage-cascade map</b>
<details style="margin-top:3px;" open>
<summary style="cursor:pointer; outline:none;">Legend, tiers &amp; counts</summary>
<div style="margin-top:4px;">
<b>Precedence (fails downward):</b><br>
1 <span style="color:#000">&#9733;</span> Substation &rarr; 2 <span style="color:#7b2cbf">&#9679;</span>Fuse/<span style="color:#b5179e">&#9679;</span>Switch<br>
&rarr; 3 <span style="color:#1b4332">&#9472;</span> Primary &rarr; 4 <span style="color:#2a9d8f">&#9679;</span> Transformer<br>
&rarr; 5 <span style="color:#e07a00">&#9472;</span> Secondary &rarr; 6 <span style="color:#3388ff">&#9679;</span> Premise<hr style="margin:4px 0">
<span style="color:#d62828">&#9679;</span> Damage incident<hr style="margin:4px 0">
Substations: {substation_count:,}<br>
Fuses: {fuse_count:,} &nbsp; Switches: {switch_count:,}<br>
Transformers: {transformer_count:,}<br>
Primary (MV) lines: {primary_drawn:,} (skip {primary_skipped:,})<br>
Secondary (LV) lines: {secondary_drawn:,} (skip {secondary_skipped:,})<br>
Premises: {premise_registered:,}<br>
Damage incidents: {len(damage):,}<hr style="margin:4px 0">
<b>Click any element</b> to simulate its failure: dependents downstream in
precedence (same circuit, and downstream in the node graph where known) turn
<span style="color:#e63946;font-weight:bold">red</span>.
<div style="margin-top:4px"><button id="cascadeReset" style="font-size:10px;padding:2px 6px;cursor:pointer">Reset simulation</button>
<span id="cascadeInfo" style="margin-left:4px;color:#555"></span></div>
<i>Positions geo-located from customer location IDs, not surveyed geometry.</i>
</div>
</details>
</div>'''
m.get_root().html.add_child(folium.Element(legend))

# ----------------------------------------------------------------------------
# Cascade JavaScript. Each drawable feature carries CSS class "cascade-node <fid>".
# On click we flood-fill downstream using tier + circuit + known node edges,
# then paint matching SVG/leaflet elements red.
# ----------------------------------------------------------------------------
features_json = json.dumps(reg_features)
edges_json = json.dumps({k: sorted(v) for k, v in downstream_nodes.items()})

cascade_js = f'''
<script>
(function() {{
  var FEATURES = {features_json};
  var EDGES = {edges_json};          // upstream node -> [downstream nodes]
  var byFid = {{}}, byNode = {{}};
  FEATURES.forEach(function(f) {{
    byFid[f.fid] = f;
    if (f.node) {{ (byNode[f.node] = byNode[f.node] || []).push(f); }}
  }});

  // Reachable set of nodes downstream of a start node via the edge graph.
  function nodeDescendants(startNode) {{
    var seen = {{}}, stack = startNode ? [startNode] : [], out = {{}};
    while (stack.length) {{
      var n = stack.pop();
      if (seen[n]) continue; seen[n] = true;
      (EDGES[n] || []).forEach(function(c) {{ out[c] = true; stack.push(c); }});
    }}
    return out;
  }}

  // Features that fail when `src` fails.
  function impacted(src) {{
    var hits = [];
    var topo = nodeDescendants(src.node);        // known downstream topology
    var topoHasAny = Object.keys(topo).length > 0;
    FEATURES.forEach(function(f) {{
      if (f.fid === src.fid) return;
      if (f.tier <= src.tier) return;            // only lower precedence can fail
      if (f.circuit !== src.circuit) return;     // scope to the same circuit
      if (topoHasAny) {{
        // If we know topology from the source, prefer it; still include
        // lower-tier same-circuit features that sit on a downstream node.
        if (f.node && topo[f.node]) hits.push(f);
        else if (!f.node) hits.push(f);          // lines without a resolved node
      }} else {{
        hits.push(f);                            // fallback: whole lower-tier circuit
      }}
    }});
    return hits;
  }}

  function paint(fid, on) {{
    var els = document.getElementsByClassName(fid);
    for (var i = 0; i < els.length; i++) {{
      var el = els[i];
      if (on) {{
        if (!el.dataset.origStroke) {{
          el.dataset.origStroke = el.getAttribute('stroke') || '';
          el.dataset.origFill = el.getAttribute('fill') || '';
        }}
        el.setAttribute('stroke', '#e63946');
        if (el.getAttribute('fill') && el.getAttribute('fill') !== 'none')
          el.setAttribute('fill', '#e63946');
        el.setAttribute('stroke-opacity', '1');
      }} else if (el.dataset.origStroke !== undefined) {{
        if (el.dataset.origStroke) el.setAttribute('stroke', el.dataset.origStroke);
        if (el.dataset.origFill) el.setAttribute('fill', el.dataset.origFill);
      }}
    }}
  }}

  var active = [];
  function reset() {{
    active.forEach(function(fid) {{ paint(fid, false); }});
    active = [];
    var info = document.getElementById('cascadeInfo');
    if (info) info.textContent = '';
  }}

  function simulate(src) {{
    reset();
    var hits = impacted(src);
    hits.forEach(function(f) {{ paint(f.fid, true); active.push(f.fid); }});
    paint(src.fid, true); active.push(src.fid);
    var info = document.getElementById('cascadeInfo');
    if (info) info.textContent = hits.length + ' downstream affected';
  }}

  function wire() {{
    var map = window["{map_name}"];
    if (!map) {{ setTimeout(wire, 300); return; }}
    // Delegate clicks: find the cascade-node element under the pointer.
    map.getContainer().addEventListener('click', function(ev) {{
      var el = ev.target;
      while (el && el !== map.getContainer()) {{
        if (el.classList && el.classList.contains('cascade-node')) {{
          var fid = null;
          el.classList.forEach(function(c) {{ if (byFid[c]) fid = c; }});
          if (fid) {{ simulate(byFid[fid]); }}
          return;
        }}
        el = el.parentNode;
      }}
    }}, true);
    var btn = document.getElementById('cascadeReset');
    if (btn) btn.addEventListener('click', reset);
  }}
  if (document.readyState === 'complete') wire();
  else window.addEventListener('load', wire);
}})();
</script>
'''
m.get_root().html.add_child(folium.Element(cascade_js))

m.save(OUTPUT)
print(f"Saved map: {OUTPUT}")
print(f"Substations (tier 1): {substation_count:,}")
print(f"Fuses: {fuse_count:,}  Switches: {switch_count:,} (tier 2)")
print(f"Primary (MV) lines drawn: {primary_drawn:,} (skipped {primary_skipped:,}) (tier 3)")
print(f"Transformers drawn: {transformer_count:,} (tier 4)")
print(f"Secondary (LV) lines drawn: {secondary_drawn:,} (skipped {secondary_skipped:,}) (tier 5)")
print(f"Premises registered: {premise_registered:,} (tier 6)")
print(f"Damage incidents: {len(damage):,}")
print(f"Cascade features registered: {len(reg_features):,}  topology edges: {len(downstream_nodes):,}")
print("Click any element in the map to simulate its failure and highlight downstream impact.")
