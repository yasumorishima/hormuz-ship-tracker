"""The column order of the `positions` table, and nothing else.

This lives on its own because both ends of the pipeline need it and they have
no other dependency in common: the parser reaches the land mask and Shapely,
the store reaches pyarrow and the Hub. Importing one through the other made a
job that only merges parquet files install a polygon library.
"""

COLUMNS = [
    "mmsi", "timestamp", "latitude", "longitude", "speed", "course", "heading",
    "ship_name", "ship_type", "destination", "draught", "length", "width",
    "flag", "received_at",
]
