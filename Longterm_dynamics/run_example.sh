#!/bin/bash
set -euo pipefail

python build_landform_availability.py \
  --study-area Tokyo_mainland.gpkg \
  --study-area-layer tokyo_mainland \
  --basins W07_5338.gpkg W07_5339.gpkg \
  --watersystem-field W07_002 \
  --unit-basin-field W07_006 \
  --resolution 10 \
  --zoom 14 \
  --target-crs EPSG:6677 \
  --request-delay 0.20 \
  --output-dir ArchGeo_landform_availability
