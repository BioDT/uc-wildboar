import ast
import json
import os
import re
import time
from pathlib import Path
import yaml

import numpy as np
from osgeo import gdal
from shapely.geometry import Point, Polygon, shape

from swifco_rs import (
    Model,
    ageing,
    analysis,
    asf,
    carcasses,
    dispersal,
    init_map,
    init_pop,
    inputs,
    management,
    mortality,
    observers,
    reporters,
    reproduction,
    terminate,
)

if os.environ.get('CONFIG_FILE') is not None and os.environ.get('CONFIG_FILE') != "":
    config_file = os.environ.get('CONFIG_FILE')

    with open(config_file, "r") as f:
        config: dict = yaml.safe_load(f)
 
    input_map = config.get("INPUT_MAP", None)
    computed_area = config.get("COMPUTED_AREA", None)
    release_coords = config.get("RELEASE_COORDS", None)
    fence_coords = config.get("FENCE_COORDS", None)
    out_path = config.get("OUTPUT_DIR", None)
else:
    # Retrieve environment variables
    input_map = os.environ.get('INPUT_MAP')
    computed_area = os.environ.get('COMPUTED_AREA')
    release_coords = os.environ.get('RELEASE_COORDS')
    fence_coords = os.environ.get('FENCE_COORDS')
    out_path = Path(os.environ.get("OUTPUT_DIR"))

# Now you can use these variables in your script
#print(f"Input Map: {input_map}")
#print(f"Computed Area: {computed_area}")
#print(f"Release Coordinates: {release_coords}")
#print(f"Fence Coordinates: {fence_coords}")

# Step 1: Add quotes to keys and string values using regex
def fix_json_string(malformed):
    # Add quotes around keys only (not around numbers or already-quoted values)
    fixed = re.sub(r'(?<!")\b(\w+)\b(?!")', r'"\1"', malformed)
    # Ensure numerical values are not quoted
    fixed = re.sub(r"\"(\d+)\"", r"\1", fixed)
    return fixed

in_path = Path("/input")
#out_path = Path(os.environ["OUTPUT_DIR"])

out_path.mkdir(parents=True, exist_ok=True)

m = Model()

landscape = None

# bounds_=[4_506_779, 3_052_929, 4_855_174, 3_353_689]
bounds_ = ast.literal_eval(computed_area)
release_coords_ = ast.literal_eval(release_coords)


landscape_path = Path("/code/outputs") / input_map #in_path / input_map

if landscape_path.exists():
    landscape = inputs.tif_reader(
        path=str(landscape_path),
        dst_srs="EPSG:3035",
        bounds=bounds_,
        resolution=2000,
    )
        
    breeding_capacity = landscape.ReadAsArray()

    breeding_capacity = 1.5 * np.maximum(breeding_capacity, -1)

    m.set_map_init(
        init_map.callback_poisson(
            size=(breeding_capacity.shape[1], breeding_capacity.shape[0]),
            callback=lambda x, y: breeding_capacity[y, x],
        )
    )
else:
    m.set_map_init(init_map.random_uniform(size=(100, 100), limits=(0, 5)))

m.set_pop_init(init_pop.default_population(release_factor=5.0))

m.add_system(asf.disease_course.default_disease_course())

###


def normalize_polygon_fence(polygon_fence, bounds_):
    """
    Normalize polygon fence coordinates so that x_min, y_min becomes [0,0]

    Args:
        polygon_fence: Shapely Polygon object
        bounds_: List [x_min, y_min, x_max, y_max] defining the area bounds

    Returns:
        normalized_fence: Shapely Polygon with normalized coordinates
    """
    x_min, y_min = bounds_[0], bounds_[1]
    x_max, y_max = bounds_[2], bounds_[3]

    # Calculate cell sizes like in tif_reader
    x_cs = (x_max - x_min) / landscape.RasterXSize
    y_cs = (y_max - y_min) / landscape.RasterYSize

    # Get coordinates from the Shapely polygon and normalize them using same formula as coord_to_cell
    coords = list(polygon_fence.exterior.coords)
    # normalized_coords = [(int((x - x_min) / x_cs), int((y - y_min) / y_cs)) for x, y in coords]
    normalized_coords = [
        (int((x - x_min) / x_cs), int((-((y - y_min) / y_cs)) + landscape.RasterYSize))
        for x, y in coords
    ]

    # Create a new Shapely polygon with normalized coordinates
    return Polygon(normalized_coords)

if fence_coords is not None and fence_coords != "":
    malformed = fence_coords
    fixed_json_string = fix_json_string(malformed)

    # Parse the JSON into a Python dictionary
    polygon_fence_data = json.loads(fixed_json_string)

    if "coordinates" in polygon_fence_data:
        polygon_fence_data["coordinates"] = [
            feature["geometry"]["coordinates"]
            for feature in polygon_fence_data["coordinates"]
        ]
        # Wrap coordinates to create a valid Polygon ring
        polygon_fence_data["coordinates"] = [polygon_fence_data["coordinates"]]

    # Create a Shapely polygon from the GeoJSON data
    polygon_fence = shape(polygon_fence_data)

    polygon_fence = normalize_polygon_fence(polygon_fence, bounds_)
    
    m.add_system(
        management.fence_zones(lambda x, y: 0 if polygon_fence.contains(Point(x, y)) else 1)
    )
    m.add_system(
        management.default_fences(
            {0: [management.FenceParams(low=0, high=1, permeability=0)]}
        )
    )

m.add_system(management.hunting_zones(lambda x, y: 0))
m.add_system(
    management.default_hunting(
        {0: management.HuntingParams(start=5 * 52, duration=52, target_share=0.8)}
    )
)

m.add_system(management.carcass_removal_zones(lambda x, y: 0 if x <= 75 else None))
m.add_system(
    management.default_carcass_removal(
        {
            0: management.CarcassRemovalParams(
                start=5 * 52, duration=104, detection_probability=0.5
            )
        }
    )
)

m.add_system(mortality.default_mortality())
m.add_system(carcasses.seasonal_decay())
m.add_system(reproduction.default_reproduction())
m.add_system(ageing.default_ageing())
m.add_system(dispersal.default_female_dispersal())
m.add_system(dispersal.default_male_dispersal())


def releases(t):
    if t == 3:
        points = [landscape.coord_to_cell(release_coords_[0], release_coords_[1])]

        return points
    else:
        return []


m.add_system(
    asf.release.callback(
        callback=releases,
        radius=5,
    )
)
"""
release_path = in_path / "cleaned_boar_dataset.csv"
m.add_system(
    asf.release.callback(
        callback=inputs.xyt_release(
            path=str(release_path),
            lat="decimalLatitude",
            lon="decimalLongitude",
            date="eventDate",
            ref_date="01.01.2018",
            ref_map=landscape,
            sep=','
        ),
        radius=2,
    )
)
"""

m.add_system(asf.infection.default_infection())
m.add_system(asf.mutation.counting(mutation_probability=1e-2))

# m.add_system(terminate.fixed_tick(num_ticks=10 * 52))
m.add_system(terminate.fixed_tick(num_ticks=120))

with analysis.video_writer(
    f"{out_path}/epistat.mp4"
) as add_epistat_frame, analysis.video_writer(
    f"{out_path}/variants.mp4", text_color=(0, 0, 0)
) as add_variants_frame:
    # Create directory for epi_stat outputs
    epi_stat_dir = Path(out_path) / "epi_stat_outputs"
    epi_stat_dir.mkdir(exist_ok=True)

    # Create directory for secondary infections outputs
    sec_inf_dir = Path(out_path) / "sec_inf_outputs"
    sec_inf_dir.mkdir(exist_ok=True)

    # Add EpiStatMap observer and save its data
    # epi_stat_observer = observers.asf.epi_stat_map()

    def create_grid_callback():
        tick = 0

        def save_grid_callback(grids, _):
            nonlocal tick

            # Save each grid (S/I/R) as a separate CSV
            for idx, state in enumerate(["susceptible", "infected", "resistant"]):
                np.savetxt(
                    f"{epi_stat_dir}/epi_stat_{state}_tick_{tick}.csv",
                    grids[idx],
                    delimiter=",",
                    fmt="%d",
                )
            tick += 1

        return save_grid_callback

    def create_secondary_infections_csv_callback():
        tick = 0

        def save_secondary_infections_csv(grids, _):
            nonlocal tick
            grid = grids[0]

            # Create header matching the Rust columns
            header = ["total", "within", "between", "carcass"]

            # Save as CSV with header and data
            with open(
                f"{sec_inf_dir}/secondary_infections_tick_{tick}.csv", "w", newline=""
            ) as f:
                f.write(",".join(header) + "\n")  # Write header

                # Grid is a 2D array where:
                # - Columns represent number of secondary infections
                # - Rows capture total, within, between, carcass
                for col in range(grid.shape[1]):
                    row_data = grid[:, col]
                    # if np.any(row_data > 0):
                    f.write(",".join(map(str, row_data)) + "\n")

            tick += 1

        return save_secondary_infections_csv

    # Add the grid callback system
    m.add_system(
        reporters.grid.callback(
            observer=observers.asf.epi_stat_map(),
            callback=create_grid_callback(),
            finalize=False,
        )
    )
    m.add_system(
        reporters.grid.callback(
            observer=observers.asf.secondary_infections(),
            callback=create_secondary_infections_csv_callback(),
            finalize=False,
        )
    )

    m.add_system(
        reporters.time_series.csv_writer(
            observer=observers.asf.new_infections_table(),
            path=f"{out_path}/new_infections.csv",
        )
    )

    # Original observers
    m.add_system(
        reporters.time_series.csv_writer(
            observer=observers.population.age_class_table(),
            path=f"{out_path}/population.csv",
        )
    )
    m.add_system(
        reporters.time_series.csv_writer(
            observer=observers.population.cause_of_death_table(),
            path=f"{out_path}/deaths.csv",
        )
    )
    m.add_system(
        reporters.time_series.csv_writer(
            observer=observers.population.carcasses_table(),
            path=f"{out_path}/carcasses.csv",
        )
    )
    m.add_system(
        reporters.time_series.csv_writer(
            observer=observers.asf.epi_stat_table(), path=f"{out_path}/epistat.csv"
        )
    )
    """
    m.add_system(
        reporters.events.csv_writer(
            observer=observers.asf.infectious_carcasses_list(),
            path=f"{out_path}/infectious_carcasses.csv",
            append=("timestamp", str(int(time.time()))),
        )
    )
    """
    m.add_system(
        reporters.grid.rgb_grid.renderer(
            observer=observers.asf.epi_stat_map(),
            adapter=reporters.grid.rgb_grid.int_array_to_rgb(
                rgb_indices=(1, 2, 0),
                limits_red=(0, 5),
                limits_green=(0, 25),
                limits_blue=(0, 25),
            ),
            callback=add_epistat_frame,
        )
    )

    m.add_system(
        reporters.grid.rgb_grid.renderer(
            observer=observers.asf.variants_map(first_variant=True),
            adapter=reporters.grid.rgb_grid.color_cycle(
                next_color=analysis.next_color()
            ),
            callback=add_variants_frame,
        )
    )

    m.add_system(
        reporters.grid.callback(
            observer=observers.population.population_map(),
            callback=analysis.tif_writer(
                f"{out_path}/population.tif", gdal.GDT_Int16, ref_map=landscape
            ),
        )
    )

    start_time = time.perf_counter()
    # print(sys.argv[1:])
    # print(input_map)
    m.run()

    duration = time.perf_counter() - start_time
    print(
        # f"Total runtime: {duration:.2f}s, Runtime per tick: {duration / 10 / 52 * 1000:.2f}ms (520 ticks)"
        f"Total runtime: {duration:.2f}s, Runtime per tick: {duration / 10 / 52 * 1000:.2f}ms (120 ticks)"
    )