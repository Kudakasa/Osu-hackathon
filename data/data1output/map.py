
import pandas as pd
import folium
from folium.plugins import MarkerCluster

CSV_PATH = (
    r"C:\Users\mukun\Desktop\Ohio hackathon"
    r"\Osu-hackathon\data\data1output"
    r"\arcos_prod_damage_09_28_Clip.csv"
)

df = pd.read_csv(CSV_PATH)

# Make sure coordinates are usable
df["Latitude"] = pd.to_numeric(df["Latitude"], errors="coerce")
df["Longitude"] = pd.to_numeric(df["Longitude"], errors="coerce")

df = df.dropna(subset=["Latitude", "Longitude", "Circuit"])

center = [df["Latitude"].mean(), df["Longitude"].mean()]

m = folium.Map(location=center, zoom_start=10)

# One selectable layer for each circuit
for circuit, group in df.groupby("Circuit"):
    layer = folium.FeatureGroup(name=f"Circuit {circuit}")

    cluster = MarkerCluster().add_to(layer)

    for _, row in group.iterrows():
        popup = f"""
        <b>Circuit:</b> {row['Circuit']}<br>
        <b>Substation:</b> {row.get('Substation', 'Unknown')}<br>
        <b>Asset type:</b> {row.get('Asset_Type', 'Unknown')}<br>
        <b>Damage type:</b> {row.get('Damage_Type', 'Unknown')}<br>
        <b>Reported:</b> {row.get('Report_Tim', 'Unknown')}<br>
        <b>Repaired:</b> {row.get('Repaired', 'Unknown')}<br>
        <b>Repair date:</b> {row.get('Repair_Date', 'Unknown')}
        """

        folium.Marker(
            location=[row["Latitude"], row["Longitude"]],
            tooltip=f"{circuit} | {row.get('Substation', '')}",
            popup=folium.Popup(popup, max_width=320),
        ).add_to(cluster)

    layer.add_to(m)

folium.LayerControl(collapsed=False).add_to(m)

m.save("circuit_map.html")
print("Saved map to circuit_map.html")
