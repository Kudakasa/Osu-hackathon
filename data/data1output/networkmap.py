"""Build an interactive utility network investigation map from four CSV exports.

Put this script in the same folder as:
  Customer_Clip.csv, Fuse_Bank_Clip.csv, Pole_Clip.csv,
  arcos_prod_damage_09_28_Clip.csv

Important: the pole CSV has no coordinates. The script estimates pole/device
positions from customer service points whose aep_locati matches a pole/fuse
location_n. The optional topology lines are therefore approximate, not surveyed
wire geometry. They connect a fuse's upstream_l identifier to location_n.
"""
from pathlib import Path
import html
import pandas as pda
import folium
from folium.plugins import FastMarkerCluster, MarkerCluster

HERE = Path(__file__).resolve().parent
CUSTOMERS = HERE / "Customer_Clip.csv"
FUSES = HERE / "Fuse_Bank_Clip.csv"
POLES = HERE / "Pole_Clip.csv"
DAMAGE = HERE / "arcos_prod_damage_09_28_Clip.csv"
OUTPUT = HERE / "utility_network_map.html"

# Read identifiers as strings so leading zeros and exact ID matches are preserved.
customers = pda.read_csv(CUSTOMERS, dtype={"aep_locati": str, "aep_xfmr_l": str, "circuit_id": str})
fuses = pda.read_csv(FUSES, dtype={"location_n": str, "upstream_l": str, "circuit_id": str, "aep_circui": str, "aep_statio": str})
poles = pda.read_csv(POLES, dtype={"location_n": str})
damage = pda.read_csv(DAMAGE, dtype={"Circuit": str, "Substation": str})

# Customer CSV service_po = longitude; service__1 = latitude.
customers["lon"] = pda.to_numeric(customers["service_po"], errors="coerce")
customers["lat"] = pda.to_numeric(customers["service__1"], errors="coerce")
customers = customers.dropna(subset=["lat", "lon", "aep_locati"])

# Approximate location lookup: use median customer coordinates at each location ID.
coord_table = (customers.groupby("aep_locati", as_index=False)
               .agg(lat=("lat", "median"), lon=("lon", "median")))
coord = {r.aep_locati: (float(r.lat), float(r.lon)) for r in coord_table.itertuples(index=False)}

# Deduplicate customer points for display; preserve one circuit label per location.
customer_points = customers.drop_duplicates(subset=["aep_locati"])
center = [float(customer_points["lat"].median()), float(customer_points["lon"].median())]
m = folium.Map(location=center, zoom_start=10, tiles="OpenStreetMap", control_scale=True)

# Customer service locations: FastMarkerCluster keeps the map manageable.
customer_layer = folium.FeatureGroup(name="Customer service locations (approx. 49k)", show=True)
customer_data = [[float(r.lat), float(r.lon)] for r in customer_points.itertuples(index=False)]
FastMarkerCluster(customer_data, name="Customer service points").add_to(customer_layer)
customer_layer.add_to(m)

# Draw identifier-based upstream links only when both IDs have an estimated coordinate.
# Hidden by default because coordinates are inferred from customer service points.
network_layer = folium.FeatureGroup(name="Inferred upstream links (approximate; toggle on)", show=False)
seen_edges = set()
edge_count = 0
for r in fuses[["location_n", "upstream_l", "aep_circui", "aep_statio", "device_typ"]].itertuples(index=False, name=None):
    loc, upstream, circuit, station, device = r
    if pda.isna(loc) or pda.isna(upstream):
        continue
    loc, upstream = str(loc), str(upstream)
    if loc == upstream or loc not in coord or upstream not in coord:
        continue
    edge_key = (loc, upstream)
    if edge_key in seen_edges:
        continue
    seen_edges.add(edge_key)
    a, b = coord[upstream], coord[loc]
    popup = (f"<b>Inferred equipment link</b><br>Upstream location: {html.escape(upstream)}"
             f"<br>Device location: {html.escape(loc)}<br>Device: {html.escape(str(device))}"
             f"<br>Circuit: {html.escape(str(circuit))}<br>Substation: {html.escape(str(station))}"
             "<br><i>Line uses estimated coordinates, not actual wire geometry.</i>")
    folium.PolyLine([a, b], color="#5b5bd6", weight=2, opacity=0.55,
                    tooltip=f"Inferred link | {circuit}",
                    popup=folium.Popup(popup, max_width=360)).add_to(network_layer)
    edge_count += 1
network_layer.add_to(m)

# Fuse devices with coordinate estimates based on matching location IDs.
fuse_layer = folium.FeatureGroup(name="Fuse bank devices (estimated positions)", show=True)
fuse_count = 0
for r in fuses.itertuples(index=False):
    loc = getattr(r, "location_n", None)
    if pda.isna(loc) or str(loc) not in coord:
        continue
    lat, lon = coord[str(loc)]
    device = getattr(r, "device_typ", "Unknown")
    circuit = getattr(r, "aep_circui", "Unknown")
    station = getattr(r, "aep_statio", "Unknown")
    upstream = getattr(r, "upstream_l", "Unknown")
    popup = (f"<b>Device:</b> {html.escape(str(device))}<br>"
             f"<b>Location ID:</b> {html.escape(str(loc))}<br>"
             f"<b>Circuit:</b> {html.escape(str(circuit))}<br>"
             f"<b>Substation:</b> {html.escape(str(station))}<br>"
             f"<b>Upstream ID:</b> {html.escape(str(upstream))}<br>"
             "<i>Position estimated from matching customer location ID.</i>")
    folium.CircleMarker([lat, lon], radius=3, color="#7b2cbf", fill=True,
                        fill_opacity=0.75, tooltip=f"Fuse/device | {circuit}",
                        popup=folium.Popup(popup, max_width=330)).add_to(fuse_layer)
    fuse_count += 1
fuse_layer.add_to(m)

# Damage points retain their actual coordinates and descriptive popups.
damage["lat"] = pda.to_numeric(damage["Latitude"], errors="coerce")
damage["lon"] = pda .to_numeric(damage["Longitude"], errors="coerce")
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
legend = f'''<div style="position: fixed; bottom: 24px; left: 24px; z-index:9999; background:white; padding:10px 12px; border:1px solid #999; border-radius:5px; font-size:12px; max-width:330px;">
<b>Utility network investigation</b><br>
<span style="color:#d62828">●</span> Damage incident (reported coordinates)<br>
<span style="color:#7b2cbf">●</span> Fuse/device (estimated position)<br>
<span style="color:#5b5bd6">━</span> Inferred upstream link (toggle on)<br>
<span style="color:#3388ff">●</span> Customer service location<br><hr style="margin:5px 0">
Customer locations shown: {len(customer_points):,}<br>
Damage incidents shown: {len(damage):,}<br>
Fuse/device records with estimated positions: {fuse_count:,}<br>
Inferred links drawn: {edge_count:,}<br>
<b>Important:</b> Pole/fuse positions are estimated from matching customer location IDs. Lines indicate identifier-based relationships, not verified physical wire routes.
</div>'''
m.get_root().html.add_child(folium.Element(legend))
m.save(OUTPUT)
print(f"Saved map: {OUTPUT}")
print(f"Customer locations: {len(customer_points):,}")
print(f"Damage incidents: {len(damage):,}")
print(f"Fuse/device records with estimated positions: {fuse_count:,}")
print(f"Inferred upstream links: {edge_count:,}")
print("Turn on 'Inferred upstream links' in the layer control to view the tentative connections.")
