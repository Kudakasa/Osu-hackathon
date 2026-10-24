import geopandas as gpd
import os

input_dir = "C:\\Users\\mukun\\Desktop\\Ohio hackathon\\aep_challenge_hack26\\data\\extracted\\Data2"
output_dir = "C:\\Users\\mukun\\Desktop\\Ohio hackathon\\Osu-hackathon\\data\\data2output"
for root, dirs, files in os.walk(input_dir):
    for file in files:
        if file.endswith(".shp"):
            shp_path = os.path.join(root, file)
            csv_name = os.path.splitext(file)[0] + ".csv"
            csv_path = os.path.join(output_dir, csv_name)

            gdf = gpd.read_file(shp_path)
            gdf.drop(columns="geometry").to_csv(csv_path, index=False)

            print(f"Converted: {shp_path} -> {csv_path}")