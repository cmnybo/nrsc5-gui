#!/usr/bin/env python3

"""This program generates the base map for weather radar images.
It fetches map tiles from OpenStreetMap and assembles them into a PNG file."""

import io
import urllib.request
from PIL import Image

START_X, START_Y = 35, 84
END_X, END_Y = 81, 110
ZOOM_LEVEL = 8
TILE_SERVER = "https://a.tile.openstreetmap.org"

WIDTH = END_X - START_X + 1
HEIGHT = END_Y - START_Y + 1
BASE_MAP = Image.new("RGB", (WIDTH*256, HEIGHT*256), "white")

for x in range(WIDTH):
    for y in range(HEIGHT):
        tile_url = "{}/{}/{}/{}.png".format(TILE_SERVER, ZOOM_LEVEL, START_X + x, START_Y + y)
        print(tile_url)
        with urllib.request.urlopen(tile_url) as response:
            tile_png = response.read()
            BASE_MAP.paste(Image.open(io.BytesIO(tile_png)), (x*256, y*256))

BASE_MAP.save("map.png")
