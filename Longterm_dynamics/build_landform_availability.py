#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_landform_availability.py

Purpose
-------
Build a 10 m (configurable) categorical landform-availability raster from
GSI "Landform Classification (Natural Landform)" GeoJSON XYZ tiles without
persistently caching all source GeoJSON tiles, then:

1) rasterize W07 unit-basin polygons to the same grid,
2) create an aligned landscape-unit raster = basin × landform class,
3) calculate availability area by basin × landform,
4) polygonize/dissolve raster-derived boundaries, and
5) write auditable CSV / GeoTIFF / GeoPackage outputs.

Recommended use for the Tokyo archaeological GIS workflow:
    target CRS  : EPSG:6677 (JGD2011 / Japan Plane Rectangular CS IX)
    resolution  : 10 m
    GSI zoom    : 14 (natural-landform "detailed" range is ZL14–16)
    raster rule : pixel-center rule (all_touched=False)

Important interpretation
------------------------
The output polygons are *raster-derived* 10 m availability units. They are
not exact reproductions of the original GSI vector boundaries. This is
intentional: polygon area and availability cell counts remain internally
consistent.

The script does NOT infer a study area from archaeological site points.
Supply an explicit study-area polygon. This avoids conditioning availability
on the observed archaeological distribution.

v1.1.1 geometry fix
-------------------
Invalid GSI polygons repaired by Shapely may become GeometryCollection objects.
Polygon components are now recursively extracted and retained before
reprojection, clipping, and rasterization. This prevents the polygon loss
observed in v1.1.0.

Official GSI source:
  Natural landform GeoJSON XYZ template:
  https://cyberjapandata.gsi.go.jp/xyz/experimental_landformclassification1/{z}/{x}/{y}.geojson

References:
  https://github.com/gsi-cyberjapan/experimental_landformclassification
  https://www.gsi.go.jp/bousaichiri/lfc_index.html
  https://www.gsi.go.jp/bousaichiri/bousaichiri41017.html
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize, shapes
from rasterio.transform import from_origin
from rasterio.windows import Window, from_bounds
import requests
from shapely import make_valid, union_all
from shapely.geometry import box, shape
from shapely.ops import transform as shapely_transform


SCRIPT_VERSION = "1.1.1"

DEFAULT_TILE_URL = (
    "https://cyberjapandata.gsi.go.jp/xyz/"
    "experimental_landformclassification1/{z}/{x}/{y}.geojson"
)

# ---------------------------------------------------------------------
# Official GSI legend crosswalk
# Source: GSI "ベクトルタイル「地形分類」の凡例対応表"
# https://www.gsi.go.jp/bousaichiri/bousaichiri41017.html
#
# Labels are normalized to match the existing Arch-Geo-Selections
# gsi_landform_class convention (e.g. 崖・段丘崖).
# Artificial-landform classes are intentionally omitted because this
# pipeline uses experimental_landformclassification1 (natural landform).
# ---------------------------------------------------------------------

OFFICIAL_CLASS_CODES: Dict[str, Sequence[str]] = {
    "山地斜面等": (
        "10101", "11201", "11202", "11203", "11204", "1010101",
    ),
    "崖・段丘崖": (
        "10202", "10204", "2010201",
    ),
    "地すべり地形": (
        "10205", "10206",
    ),
    "台地・段丘": (
        "10301", "10302", "10303", "10304", "10305", "10306",
        "10307", "10308", "10310", "10312", "10314", "10508",
        "2010101",
    ),
    "山麓堆積地形": (
        "10401", "10402", "10403", "10404", "10406", "10407",
        "3010101",
    ),
    "扇状地": (
        "10501", "10502", "3020101",
    ),
    "自然堤防": (
        "10503", "3040101",
    ),
    "天井川等": (
        "10506", "10507", "10801",
    ),
    "砂州・砂丘": (
        "10504", "10505", "10512", "3050101",
    ),
    "凹地・浅い谷": (
        "10601", "2010301",
    ),
    "氾濫平野・海岸平野": (
        "10701", "10702", "10705", "3030101",
    ),
    "後背低地・湿地": (
        "10703", "10804", "3030201",
    ),
    "旧河道": (
        "10704", "3040201", "3040202",
    ),
    "落堀": (
        "3040301",
    ),
    "河川敷・浜": (
        "10802", "10803", "10807", "10808",
    ),
    "水部": (
        "10805", "10806", "10901", "10903", "5010201",
    ),
    "旧水部": (
        "10904", "5010301",
    ),
}


@dataclass(frozen=True)
class GridSpec:
    crs: str
    resolution: float
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    width: int
    height: int
    transform: rasterio.Affine


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_code(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        if math.isfinite(float(value)) and float(value).is_integer():
            return str(int(value))
    s = str(value).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def code_crosswalk_from_builtin() -> Tuple[Dict[str, Tuple[int, str]], pd.DataFrame]:
    # Stable class IDs: 1..N in declared order.
    rows = []
    mapping: Dict[str, Tuple[int, str]] = {}
    for class_id, (class_name, codes) in enumerate(OFFICIAL_CLASS_CODES.items(), start=1):
        for code in codes:
            mapping[str(code)] = (class_id, class_name)
            rows.append(
                {
                    "code": str(code),
                    "class_id": class_id,
                    "gsi_landform_class": class_name,
                    "mapping_source": "GSI_official_legend_builtin",
                }
            )
    lookup = pd.DataFrame(rows)
    return mapping, lookup


def load_code_crosswalk(path: Optional[Path]) -> Tuple[Dict[str, Tuple[int, str]], pd.DataFrame]:
    if path is None:
        return code_crosswalk_from_builtin()

    df = pd.read_csv(path, dtype=str)
    required = {"code", "gsi_landform_class"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Code-map CSV is missing columns: {sorted(missing)}")

    df["code"] = df["code"].map(normalize_code)
    df["gsi_landform_class"] = df["gsi_landform_class"].astype(str).str.strip()

    if "class_id" not in df.columns:
        class_order = list(dict.fromkeys(df["gsi_landform_class"].tolist()))
        class_to_id = {name: i + 1 for i, name in enumerate(class_order)}
        df["class_id"] = df["gsi_landform_class"].map(class_to_id)
    else:
        df["class_id"] = pd.to_numeric(df["class_id"], errors="raise").astype(int)

    if (df["class_id"] <= 0).any() or (df["class_id"] >= 254).any():
        raise ValueError("class_id must be between 1 and 253.")

    # One class ID must represent exactly one class label.
    id_card = df.groupby("class_id")["gsi_landform_class"].nunique()
    if (id_card > 1).any():
        bad = id_card[id_card > 1].index.tolist()
        raise ValueError(f"class_id maps to multiple class labels: {bad}")

    # One code must map to one class.
    code_card = df.groupby("code")[["class_id", "gsi_landform_class"]].nunique()
    if (code_card.max(axis=1) > 1).any():
        bad = code_card[code_card.max(axis=1) > 1].index.tolist()
        raise ValueError(f"code maps to multiple classes: {bad[:20]}")

    df = df.drop_duplicates(["code", "class_id", "gsi_landform_class"]).copy()
    df["mapping_source"] = str(path)

    mapping = {
        r.code: (int(r.class_id), r.gsi_landform_class)
        for r in df.itertuples(index=False)
    }
    return mapping, df



def extract_polygonal(geom):
    """
    Return only Polygon / MultiPolygon components from a geometry.

    Why this is necessary
    ---------------------
    GSI landform GeoJSON features can be invalid. Shapely make_valid() may
    repair an invalid Polygon into a GeometryCollection containing both
    Polygon and LineString components. The previous v1.1.0 implementation
    rejected every non-Polygon/MultiPolygon result after make_valid(), which
    could therefore discard a valid polygonal component and create large
    NoData holes in the 10 m landform raster.

    This function:
      1. runs make_valid(),
      2. recursively extracts Polygon components from GeometryCollection,
      3. flattens MultiPolygon,
      4. drops pure line/point components, and
      5. unions the recovered polygon parts.

    Returns None when no polygonal component exists.
    """
    if geom is None:
        return None

    try:
        if geom.is_empty:
            return None
    except Exception:
        return None

    valid = make_valid(geom)

    polygon_parts = []

    def collect(g):
        if g is None or g.is_empty:
            return

        gtype = g.geom_type

        if gtype == "Polygon":
            polygon_parts.append(g)
            return

        if gtype == "MultiPolygon":
            for part in g.geoms:
                if part is not None and not part.is_empty:
                    polygon_parts.append(part)
            return

        if gtype == "GeometryCollection":
            for part in g.geoms:
                collect(part)
            return

        # LineString / MultiLineString / Point / MultiPoint are intentionally
        # ignored because availability is area-based.

    collect(valid)

    if not polygon_parts:
        return None

    result = union_all(polygon_parts)

    if result is None or result.is_empty:
        return None

    # union_all() of polygon parts should be polygonal. Guard once more in
    # case a GEOS repair creates a collection on an unusual geometry.
    if result.geom_type in ("Polygon", "MultiPolygon"):
        return result

    recovered = []

    def collect_result(g):
        if g is None or g.is_empty:
            return
        if g.geom_type == "Polygon":
            recovered.append(g)
        elif g.geom_type == "MultiPolygon":
            recovered.extend(
                part for part in g.geoms
                if part is not None and not part.is_empty
            )
        elif g.geom_type == "GeometryCollection":
            for part in g.geoms:
                collect_result(part)

    collect_result(result)

    if not recovered:
        return None

    result2 = union_all(recovered)
    return None if result2 is None or result2.is_empty else result2

def read_vector(path: Path, layer: Optional[str] = None) -> gpd.GeoDataFrame:
    if layer:
        gdf = gpd.read_file(path, layer=layer)
    else:
        gdf = gpd.read_file(path)
    if gdf.empty:
        raise ValueError(f"No features found: {path}")
    if gdf.crs is None:
        raise ValueError(f"CRS is missing: {path}")
    gdf = gdf.copy()
    gdf.geometry = gdf.geometry.map(lambda g: make_valid(g) if g is not None else None)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    if gdf.empty:
        raise ValueError(f"No valid geometries remain: {path}")
    return gdf


def dissolve_to_single_geometry(gdf: gpd.GeoDataFrame):
    geom = extract_polygonal(union_all(list(gdf.geometry)))
    if geom is None:
        raise ValueError("Dissolved geometry contains no polygonal area.")
    return geom


def snap_bounds(bounds: Tuple[float, float, float, float], resolution: float):
    xmin, ymin, xmax, ymax = bounds
    xmin = math.floor(xmin / resolution) * resolution
    ymin = math.floor(ymin / resolution) * resolution
    xmax = math.ceil(xmax / resolution) * resolution
    ymax = math.ceil(ymax / resolution) * resolution
    return xmin, ymin, xmax, ymax


def build_grid(study_area: gpd.GeoDataFrame, target_crs: str, resolution: float) -> GridSpec:
    study_target = study_area.to_crs(target_crs)
    xmin, ymin, xmax, ymax = snap_bounds(tuple(study_target.total_bounds), resolution)
    width = int(round((xmax - xmin) / resolution))
    height = int(round((ymax - ymin) / resolution))
    transform = from_origin(xmin, ymax, resolution, resolution)
    return GridSpec(
        crs=target_crs,
        resolution=resolution,
        xmin=xmin,
        ymin=ymin,
        xmax=xmax,
        ymax=ymax,
        width=width,
        height=height,
        transform=transform,
    )


def lon_to_xtile(lon: float, zoom: int) -> int:
    n = 2 ** zoom
    x = int(math.floor((lon + 180.0) / 360.0 * n))
    return max(0, min(n - 1, x))


def lat_to_ytile(lat: float, zoom: int) -> int:
    # Slippy-map/Web-Mercator latitude limit.
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 2 ** zoom
    lat_rad = math.radians(lat)
    y = int(
        math.floor(
            (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
        )
    )
    return max(0, min(n - 1, y))


def tile_lon(x: int, zoom: int) -> float:
    n = 2 ** zoom
    return x / n * 360.0 - 180.0


def tile_lat(y: int, zoom: int) -> float:
    n = 2 ** zoom
    return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))


def tile_bounds(x: int, y: int, zoom: int) -> Tuple[float, float, float, float]:
    west = tile_lon(x, zoom)
    east = tile_lon(x + 1, zoom)
    north = tile_lat(y, zoom)
    south = tile_lat(y + 1, zoom)
    return west, south, east, north


def enumerate_tiles_for_bbox(
    bbox4326: Tuple[float, float, float, float],
    zoom: int,
) -> List[Tuple[int, int, int]]:
    west, south, east, north = bbox4326
    if west > east:
        raise ValueError("Antimeridian-crossing study areas are not supported.")

    x0 = lon_to_xtile(west, zoom)
    x1 = lon_to_xtile(east, zoom)
    y0 = lat_to_ytile(north, zoom)
    y1 = lat_to_ytile(south, zoom)

    return [(zoom, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def raster_profile(grid: GridSpec, dtype: str, nodata, compress: str = "DEFLATE"):
    block = 512
    return {
        "driver": "GTiff",
        "width": grid.width,
        "height": grid.height,
        "count": 1,
        "dtype": dtype,
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": nodata,
        "compress": compress,
        "tiled": True,
        "blockxsize": block,
        "blockysize": block,
        "BIGTIFF": "IF_SAFER",
    }


def create_empty_raster(path: Path, grid: GridSpec, dtype: str, nodata, overwrite: bool):
    if path.exists():
        if overwrite:
            path.unlink()
        else:
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **raster_profile(grid, dtype=dtype, nodata=nodata)) as dst:
        # Rasterio/GDAL initializes newly created tiles to nodata/zero as written below.
        zero = np.zeros((min(512, grid.height), min(512, grid.width)), dtype=dtype)
        for _, window in dst.block_windows(1):
            arr = zero[: int(window.height), : int(window.width)]
            dst.write(arr, 1, window=window)


def window_for_geom(geom, grid: GridSpec) -> Optional[Window]:
    if geom is None or geom.is_empty:
        return None
    minx, miny, maxx, maxy = geom.bounds
    minx = max(minx, grid.xmin)
    miny = max(miny, grid.ymin)
    maxx = min(maxx, grid.xmax)
    maxy = min(maxy, grid.ymax)
    if minx >= maxx or miny >= maxy:
        return None

    w = from_bounds(minx, miny, maxx, maxy, transform=grid.transform)
    col_off = max(0, int(math.floor(w.col_off)))
    row_off = max(0, int(math.floor(w.row_off)))
    col_end = min(grid.width, int(math.ceil(w.col_off + w.width)))
    row_end = min(grid.height, int(math.ceil(w.row_off + w.height)))
    if col_end <= col_off or row_end <= row_off:
        return None
    return Window(col_off, row_off, col_end - col_off, row_end - row_off)


def append_manifest_row(path: Path, row: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def successful_tile_keys(manifest_path: Path) -> set:
    if not manifest_path.exists():
        return set()
    try:
        df = pd.read_csv(manifest_path, dtype={"z": int, "x": int, "y": int})
    except Exception:
        return set()
    if not {"z", "x", "y", "status"}.issubset(df.columns):
        return set()
    ok = df[df["status"].isin(["ok", "empty", "not_found", "ok_with_unmapped"])]
    return set(zip(ok["z"].astype(int), ok["x"].astype(int), ok["y"].astype(int)))


def fetch_geojson(
    session: requests.Session,
    url: str,
    timeout: float,
    retries: int,
    base_backoff: float,
) -> Tuple[str, int, bytes, Optional[dict]]:
    last_status = 0
    for attempt in range(1, retries + 2):
        try:
            resp = session.get(url, timeout=timeout)
            last_status = int(resp.status_code)

            if resp.status_code == 404:
                return "not_found", 404, resp.content, None

            if resp.status_code == 429 or 500 <= resp.status_code <= 599:
                if attempt <= retries:
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except ValueError:
                            wait = base_backoff * (2 ** (attempt - 1))
                    else:
                        wait = base_backoff * (2 ** (attempt - 1))
                    time.sleep(wait)
                    continue

            resp.raise_for_status()

            content = resp.content
            if not content.strip():
                return "empty", resp.status_code, content, {"type": "FeatureCollection", "features": []}
            try:
                data = resp.json()
            except Exception:
                data = json.loads(content.decode("utf-8"))
            return "ok", resp.status_code, content, data

        except (requests.RequestException, json.JSONDecodeError, UnicodeDecodeError):
            if attempt <= retries:
                time.sleep(base_backoff * (2 ** (attempt - 1)))
                continue
            raise

    raise RuntimeError(f"Failed after retries: {url}; last_status={last_status}")


def save_unmapped(unmapped: Counter, path: Path):
    rows = [
        {"code": code, "feature_count": count}
        for code, count in sorted(unmapped.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    pd.DataFrame(rows, columns=["code", "feature_count"]).to_csv(path, index=False)


def burn_landform_tiles(
    raster_path: Path,
    grid: GridSpec,
    study_area: gpd.GeoDataFrame,
    zoom: int,
    tile_url_template: str,
    code_map: Dict[str, Tuple[int, str]],
    manifest_path: Path,
    unmapped_path: Path,
    request_delay: float,
    timeout: float,
    retries: int,
    user_agent: str,
    resume: bool,
    allow_unmapped: bool,
):
    study4326 = study_area.to_crs("EPSG:4326")
    study4326_geom = dissolve_to_single_geometry(study4326)

    study_target = study_area.to_crs(grid.crs)
    study_target_geom = dissolve_to_single_geometry(study_target)

    tiles = enumerate_tiles_for_bbox(tuple(study4326.total_bounds), zoom)
    # Only use bbox for candidate enumeration. Exact study-area clipping occurs below.
    successful = successful_tile_keys(manifest_path) if resume else set()

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept": "application/json,*/*"})

    tfm = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
    unmapped_codes: Counter = Counter()

    total = len(tiles)
    print(f"[landform] candidate XYZ tiles: {total} at z={zoom}")

    with rasterio.open(raster_path, "r+") as dst:
        for idx, (z, x, y) in enumerate(tiles, start=1):
            key = (z, x, y)
            if key in successful:
                continue

            west, south, east, north = tile_bounds(x, y, z)
            tile_poly_4326 = box(west, south, east, north)
            if not study4326_geom.intersects(tile_poly_4326):
                # We intentionally do not add skipped-outside tiles to manifest.
                continue

            url = tile_url_template.format(z=z, x=x, y=y)
            started = time.perf_counter()
            attempted_at = utc_now_iso()

            try:
                fetch_status, http_status, raw, data = fetch_geojson(
                    session=session,
                    url=url,
                    timeout=timeout,
                    retries=retries,
                    base_backoff=max(0.5, request_delay),
                )
            except Exception as exc:
                append_manifest_row(
                    manifest_path,
                    {
                        "z": z,
                        "x": x,
                        "y": y,
                        "url": url,
                        "status": "error",
                        "http_status": "",
                        "bytes": 0,
                        "feature_count": 0,
                        "polygon_feature_count": 0,
                        "nonpolygon_feature_count": 0,
                        "unmapped_feature_count": 0,
                        "elapsed_s": round(time.perf_counter() - started, 3),
                        "attempted_at_utc": attempted_at,
                        "message": repr(exc),
                    },
                )
                raise

            if fetch_status == "not_found":
                append_manifest_row(
                    manifest_path,
                    {
                        "z": z, "x": x, "y": y, "url": url,
                        "status": "not_found",
                        "http_status": http_status,
                        "bytes": len(raw),
                        "feature_count": 0,
                        "polygon_feature_count": 0,
                        "nonpolygon_feature_count": 0,
                        "unmapped_feature_count": 0,
                        "elapsed_s": round(time.perf_counter() - started, 3),
                        "attempted_at_utc": attempted_at,
                        "message": "",
                    },
                )
                time.sleep(request_delay)
                continue

            features = []
            if isinstance(data, dict):
                features = data.get("features") or []

            if not features:
                append_manifest_row(
                    manifest_path,
                    {
                        "z": z, "x": x, "y": y, "url": url,
                        "status": "empty",
                        "http_status": http_status,
                        "bytes": len(raw),
                        "feature_count": 0,
                        "polygon_feature_count": 0,
                        "nonpolygon_feature_count": 0,
                        "unmapped_feature_count": 0,
                        "elapsed_s": round(time.perf_counter() - started, 3),
                        "attempted_at_utc": attempted_at,
                        "message": "",
                    },
                )
                time.sleep(request_delay)
                continue

            # Exact tile footprint in target CRS; clipping prevents seam double-burn.
            tile_poly_target = shapely_transform(tfm.transform, tile_poly_4326)
            clip_geom = extract_polygonal(
                tile_poly_target.intersection(study_target_geom)
            )
            if clip_geom is None:
                continue

            shapes_to_burn: List[Tuple[object, int]] = []
            nonpolygon_count = 0
            tile_unmapped: Counter = Counter()

            for feat in features:
                props = feat.get("properties") or {}
                code = normalize_code(props.get("code"))
                geom_json = feat.get("geometry")
                if not geom_json:
                    continue

                if code not in code_map:
                    tile_unmapped[code if code else "<MISSING>"] += 1
                    continue

                # IMPORTANT:
                # make_valid() may turn an invalid Polygon into a
                # GeometryCollection(Polygon, LineString, ...).  Keep the
                # polygonal components instead of discarding the whole feature.
                geom_source = extract_polygonal(shape(geom_json))
                if geom_source is None:
                    nonpolygon_count += 1
                    continue

                geom_target = extract_polygonal(
                    shapely_transform(tfm.transform, geom_source)
                )
                if geom_target is None:
                    nonpolygon_count += 1
                    continue

                geom_target = extract_polygonal(
                    geom_target.intersection(clip_geom)
                )
                if geom_target is None:
                    # Feature is polygonal but has no area inside this tile /
                    # study-area clip. This is not counted as a nonpolygon.
                    continue

                class_id, _ = code_map[code]
                shapes_to_burn.append((geom_target, int(class_id)))

            if tile_unmapped:
                unmapped_codes.update(tile_unmapped)
                save_unmapped(unmapped_codes, unmapped_path)
                if not allow_unmapped:
                    append_manifest_row(
                        manifest_path,
                        {
                            "z": z, "x": x, "y": y, "url": url,
                            "status": "unmapped_code",
                            "http_status": http_status,
                            "bytes": len(raw),
                            "feature_count": len(features),
                            "polygon_feature_count": len(shapes_to_burn),
                            "nonpolygon_feature_count": nonpolygon_count,
                            "unmapped_feature_count": sum(tile_unmapped.values()),
                            "elapsed_s": round(time.perf_counter() - started, 3),
                            "attempted_at_utc": attempted_at,
                            "message": ";".join(sorted(tile_unmapped.keys())),
                        },
                    )
                    raise RuntimeError(
                        "Unmapped GSI landform code(s) encountered. "
                        f"See {unmapped_path}. No features from this tile were written. "
                        "Update --code-map or rerun with --allow-unmapped if intentional."
                    )

            if shapes_to_burn:
                # Window bounds only: avoid an unnecessary geometric union for every tile.
                bounds = [g.bounds for g, _ in shapes_to_burn]
                geom_bbox = box(
                    min(b[0] for b in bounds),
                    min(b[1] for b in bounds),
                    max(b[2] for b in bounds),
                    max(b[3] for b in bounds),
                )
                win = window_for_geom(geom_bbox, grid)
                if win is not None:
                    win_transform = dst.window_transform(win)
                    out_shape = (int(win.height), int(win.width))
                    burned = rasterize(
                        shapes=shapes_to_burn,
                        out_shape=out_shape,
                        transform=win_transform,
                        fill=0,
                        all_touched=False,
                        dtype="uint8",
                    )
                    existing = dst.read(1, window=win)
                    merged = np.where(burned > 0, burned, existing).astype("uint8")
                    dst.write(merged, 1, window=win)

            tile_status = "ok_with_unmapped" if tile_unmapped else "ok"
            append_manifest_row(
                manifest_path,
                {
                    "z": z,
                    "x": x,
                    "y": y,
                    "url": url,
                    "status": tile_status,
                    "http_status": http_status,
                    "bytes": len(raw),
                    "feature_count": len(features),
                    "polygon_feature_count": len(shapes_to_burn),
                    "nonpolygon_feature_count": nonpolygon_count,
                    "unmapped_feature_count": sum(tile_unmapped.values()),
                    "elapsed_s": round(time.perf_counter() - started, 3),
                    "attempted_at_utc": attempted_at,
                    "message": "",
                },
            )

            if idx % 100 == 0 or idx == total:
                print(f"[landform] processed candidate {idx}/{total}")

            time.sleep(request_delay)

    if unmapped_codes:
        save_unmapped(unmapped_codes, unmapped_path)


def clean_code_series(series: pd.Series, width: Optional[int]) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series.dtype):
        print(
            "[warning] Basin code field was read as numeric. "
            "Leading zeros may already have been lost. "
            "Prefer text fields or use --watersystem-width / --unit-basin-width."
        )
    out = series.map(normalize_code)
    if width is not None:
        out = out.map(lambda s: s.zfill(width) if s else s)
    return out


def prepare_basins(
    basin_paths: Sequence[Path],
    basin_layer: Optional[str],
    basin_layers: Optional[Sequence[Optional[str]]],
    study_area: gpd.GeoDataFrame,
    target_crs: str,
    watersystem_field: str,
    unit_basin_field: str,
    watersystem_name_field: Optional[str],
    watersystem_width: Optional[int],
    unit_basin_width: Optional[int],
) -> gpd.GeoDataFrame:
    """
    Read one or more W07 watershed-mesh vector files, concatenate them,
    clip to the explicit study area, and dissolve across file boundaries by
    watersystem_code × unit_basin_code.

    Typical Tokyo input:
        W07_5338.gpkg + W07_5339.gpkg

    The source files remain separate on disk; only the in-memory analysis
    layer is merged. Provenance is retained in source_files,
    source_file_count, and source_input_feature_n.
    """
    basin_paths = [Path(p) for p in basin_paths]
    if not basin_paths:
        raise ValueError("At least one --basins input is required.")

    if basin_layers is not None:
        if basin_layer is not None:
            raise ValueError("Use either --basin-layer or --basin-layers, not both.")
        if len(basin_layers) != len(basin_paths):
            raise ValueError(
                "--basin-layers must contain exactly one layer name per --basins file."
            )
        layer_list = list(basin_layers)
    else:
        layer_list = [basin_layer] * len(basin_paths)

    parts: List[gpd.GeoDataFrame] = []

    for basin_path, layer_name in zip(basin_paths, layer_list):
        print(
            f"[basin] reading: {basin_path}"
            + (f" | layer={layer_name}" if layer_name else "")
        )
        part = read_vector(basin_path, layer_name)

        if watersystem_field not in part.columns:
            raise KeyError(
                f"Missing basin field {watersystem_field!r} in {basin_path}"
            )
        if unit_basin_field not in part.columns:
            raise KeyError(
                f"Missing basin field {unit_basin_field!r} in {basin_path}"
            )
        if watersystem_name_field and watersystem_name_field not in part.columns:
            raise KeyError(
                f"Missing basin name field {watersystem_name_field!r} in {basin_path}"
            )

        # Reproject each input separately before concatenation so inputs with
        # different valid CRSs can still be combined safely.
        part = part.to_crs(target_crs).copy()
        part["_source_file"] = basin_path.name
        part["_source_path"] = str(basin_path)
        part["_source_layer"] = layer_name or ""
        parts.append(part)

    basins = gpd.GeoDataFrame(
        pd.concat(parts, ignore_index=True, sort=False),
        geometry="geometry",
        crs=target_crs,
    )

    study_target = study_area.to_crs(target_crs)
    study_geom = dissolve_to_single_geometry(study_target)

    basins["watersystem_code"] = clean_code_series(
        basins[watersystem_field], watersystem_width
    )
    basins["unit_basin_code"] = clean_code_series(
        basins[unit_basin_field], unit_basin_width
    )

    if watersystem_name_field:
        basins["watersystem_name"] = (
            basins[watersystem_name_field].astype("string").fillna("").str.strip()
        )
    else:
        basins["watersystem_name"] = ""

    basins = basins[
        basins["watersystem_code"].ne("") & basins["unit_basin_code"].ne("")
    ].copy()

    # Clip every W07 mesh feature to the explicit study area.
    # Keep only polygonal components if a repair/intersection creates a
    # GeometryCollection.
    basins.geometry = basins.geometry.map(
        lambda g: extract_polygonal(g.intersection(study_geom))
    )
    basins = basins[
        basins.geometry.notna() & ~basins.geometry.is_empty
    ].copy()

    if basins.empty:
        raise ValueError(
            "No W07 basin-mesh features intersect the study area after clipping."
        )

    group_cols = ["watersystem_code", "unit_basin_code"]

    # Provenance before dissolve. This also lets one basin span 5338/5339
    # without losing information about which source mesh files contributed.
    def join_unique(values: pd.Series) -> str:
        vals = sorted({str(v) for v in values if pd.notna(v) and str(v) != ""})
        return "|".join(vals)

    provenance = (
        basins.groupby(group_cols, as_index=False)
        .agg(
            watersystem_name=("watersystem_name", "first"),
            source_files=("_source_file", join_unique),
            source_paths=("_source_path", join_unique),
            source_layers=("_source_layer", join_unique),
            source_input_feature_n=("_source_file", "size"),
        )
    )
    provenance["source_file_count"] = provenance["source_files"].map(
        lambda s: 0 if not s else len(s.split("|"))
    )

    # Dissolve W07 mesh cells across BOTH within-file and 5338/5339 boundaries.
    geom_only = basins[group_cols + ["geometry"]].copy()
    dissolved = geom_only.dissolve(
        by=group_cols,
        as_index=False,
    )
    dissolved.geometry = dissolved.geometry.map(extract_polygonal)
    dissolved = dissolved[
        dissolved.geometry.notna() & ~dissolved.geometry.is_empty
    ].copy()

    basins = dissolved.merge(
        provenance,
        on=group_cols,
        how="left",
        validate="one_to_one",
    )

    basins = basins.sort_values(
        ["watersystem_code", "unit_basin_code"], kind="stable"
    ).reset_index(drop=True)

    basins["basin_id"] = np.arange(1, len(basins) + 1, dtype=np.int32)
    basins["basin_unit_id"] = (
        "B_"
        + basins["watersystem_code"].astype(str)
        + "_"
        + basins["unit_basin_code"].astype(str)
    )
    basins["source_vector_area_m2"] = basins.geometry.area.astype(float)

    if len(basins) >= 65535:
        raise ValueError("Too many basins for uint16 basin raster.")

    print(
        f"[basin] merged {len(basin_paths)} source file(s) -> "
        f"{len(basins)} dissolved basin unit(s)"
    )

    return basins


def burn_basins(
    basin_raster_path: Path,
    grid: GridSpec,
    basins: gpd.GeoDataFrame,
):
    with rasterio.open(basin_raster_path, "r+") as dst:
        for i, row in enumerate(basins.itertuples(index=False), start=1):
            geom = make_valid(row.geometry)
            win = window_for_geom(geom, grid)
            if win is None:
                continue
            win_transform = dst.window_transform(win)
            out_shape = (int(win.height), int(win.width))
            arr = rasterize(
                [(geom, int(row.basin_id))],
                out_shape=out_shape,
                transform=win_transform,
                fill=0,
                all_touched=False,
                dtype="uint16",
            )
            existing = dst.read(1, window=win)
            # W07 unit basins are expected to partition space. If overlaps occur,
            # the later basin only fills still-unassigned cells.
            merged = np.where((arr > 0) & (existing == 0), arr, existing).astype("uint16")
            dst.write(merged, 1, window=win)

            if i % 100 == 0 or i == len(basins):
                print(f"[basin] rasterized {i}/{len(basins)}")


def combine_landscape_raster(
    landform_path: Path,
    basin_path: Path,
    landscape_path: Path,
):
    with rasterio.open(landform_path) as lf, rasterio.open(basin_path) as bs:
        if (
            lf.width != bs.width
            or lf.height != bs.height
            or lf.transform != bs.transform
            or lf.crs != bs.crs
        ):
            raise ValueError("Landform and basin rasters are not aligned.")

        profile = lf.profile.copy()
        profile.update(
            dtype="int32",
            nodata=0,
            compress="DEFLATE",
            tiled=True,
            BIGTIFF="IF_SAFER",
        )
        with rasterio.open(landscape_path, "w", **profile) as out:
            for _, win in lf.block_windows(1):
                a = lf.read(1, window=win)
                b = bs.read(1, window=win)
                combo = np.zeros(a.shape, dtype=np.int32)
                mask = (a > 0) & (b > 0)
                # 1000 leaves ample space for class IDs (<254).
                combo[mask] = b[mask].astype(np.int32) * 1000 + a[mask].astype(np.int32)
                out.write(combo, 1, window=win)


def count_availability(
    landform_path: Path,
    basin_path: Path,
    landscape_path: Path,
    basins: gpd.GeoDataFrame,
    class_lookup: pd.DataFrame,
    resolution: float,
) -> pd.DataFrame:
    landscape_counts: Counter = Counter()
    basin_counts: Counter = Counter()
    basin_known_counts: Counter = Counter()

    with (
        rasterio.open(landform_path) as lf,
        rasterio.open(basin_path) as bs,
        rasterio.open(landscape_path) as ls,
    ):
        for _, win in lf.block_windows(1):
            a = lf.read(1, window=win)
            b = bs.read(1, window=win)
            c = ls.read(1, window=win)

            vals, cnts = np.unique(c[c > 0], return_counts=True)
            landscape_counts.update({int(v): int(n) for v, n in zip(vals, cnts)})

            vals, cnts = np.unique(b[b > 0], return_counts=True)
            basin_counts.update({int(v): int(n) for v, n in zip(vals, cnts)})

            known_mask = (b > 0) & (a > 0)
            vals, cnts = np.unique(b[known_mask], return_counts=True)
            basin_known_counts.update({int(v): int(n) for v, n in zip(vals, cnts)})

    class_id_to_name = (
        class_lookup[["class_id", "gsi_landform_class"]]
        .drop_duplicates()
        .set_index("class_id")["gsi_landform_class"]
        .to_dict()
    )
    basin_lookup = basins.set_index("basin_id")

    cell_area = resolution * resolution
    rows = []

    for landscape_code, count in sorted(landscape_counts.items()):
        basin_id = int(landscape_code // 1000)
        class_id = int(landscape_code % 1000)

        if basin_id not in basin_lookup.index:
            raise RuntimeError(f"Unknown basin_id in landscape raster: {basin_id}")
        if class_id not in class_id_to_name:
            raise RuntimeError(f"Unknown class_id in landscape raster: {class_id}")

        b = basin_lookup.loc[basin_id]
        basin_total = int(basin_counts.get(basin_id, 0))
        known_total = int(basin_known_counts.get(basin_id, 0))
        area_m2 = float(count * cell_area)
        basin_grid_area_m2 = float(basin_total * cell_area)
        known_area_m2 = float(known_total * cell_area)

        landform_name = class_id_to_name[class_id]
        basin_unit_id = str(b["basin_unit_id"])
        landscape_unit_id = f"{basin_unit_id}_LF_CLASS::{landform_name}"

        rows.append(
            {
                "landscape_code": int(landscape_code),
                "basin_id": basin_id,
                "class_id": class_id,
                "watersystem_code": str(b["watersystem_code"]),
                "watersystem_name": str(b["watersystem_name"]),
                "unit_basin_code": str(b["unit_basin_code"]),
                "basin_unit_id": basin_unit_id,
                "gsi_landform_class": landform_name,
                "landform_unit_id": f"LF_CLASS::{landform_name}",
                "landscape_unit_id": landscape_unit_id,
                "available_cell_count": int(count),
                "available_area_m2": area_m2,
                "available_area_km2": area_m2 / 1_000_000.0,
                "basin_cell_count": basin_total,
                "basin_grid_area_m2": basin_grid_area_m2,
                "landform_known_cell_count": known_total,
                "landform_known_area_m2": known_area_m2,
                "landform_coverage_ratio": (
                    known_total / basin_total if basin_total > 0 else np.nan
                ),
                "available_share_known": (
                    count / known_total if known_total > 0 else np.nan
                ),
                "available_share_basin": (
                    count / basin_total if basin_total > 0 else np.nan
                ),
                "resolution_m": float(resolution),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        raise RuntimeError("No basin × landform availability cells were produced.")

    # Internal audit.
    share = (
        out.groupby("basin_id", as_index=False)["available_share_known"]
        .sum()
        .rename(columns={"available_share_known": "share_sum"})
    )
    bad = share[~np.isclose(share["share_sum"], 1.0, atol=1e-9)]
    if not bad.empty:
        raise RuntimeError(
            "available_share_known does not sum to 1 in some basins: "
            + bad.head(20).to_string(index=False)
        )

    return out


def balanced_union(geometries: List[object], batch_size: int = 2000):
    geoms = [g for g in geometries if g is not None and not g.is_empty]
    if not geoms:
        return None
    while len(geoms) > 1:
        next_round = []
        for i in range(0, len(geoms), batch_size):
            u = make_valid(union_all(geoms[i : i + batch_size]))
            if u is not None and not u.is_empty:
                next_round.append(u)
        geoms = next_round
    return make_valid(geoms[0])


def polygonize_and_dissolve(
    raster_path: Path,
    value_name: str,
    target_crs,
) -> gpd.GeoDataFrame:
    grouped: Dict[int, List[object]] = defaultdict(list)

    with rasterio.open(raster_path) as src:
        src_band = rasterio.band(src, 1)
        for geom_json, value in shapes(
            src_band,
            mask=None,
            transform=src.transform,
            connectivity=4,
        ):
            ivalue = int(value)
            if ivalue == 0:
                continue
            geom = make_valid(shape(geom_json))
            if geom.is_empty:
                continue
            grouped[ivalue].append(geom)

    print(f"[polygonize] unique {value_name}: {len(grouped)}")

    rows = []
    for idx, (value, geoms) in enumerate(sorted(grouped.items()), start=1):
        dissolved = balanced_union(geoms)
        if dissolved is None or dissolved.is_empty:
            continue
        rows.append({value_name: int(value), "geometry": dissolved})
        if idx % 50 == 0 or idx == len(grouped):
            print(f"[polygonize] dissolved {idx}/{len(grouped)}")

    return gpd.GeoDataFrame(rows, geometry="geometry", crs=target_crs)


def write_geopackage(
    gpkg_path: Path,
    study_area: gpd.GeoDataFrame,
    basins: gpd.GeoDataFrame,
    landform_poly: gpd.GeoDataFrame,
    landscape_poly: gpd.GeoDataFrame,
    basin_grid_poly: gpd.GeoDataFrame,
):
    if gpkg_path.exists():
        gpkg_path.unlink()

    engine = "pyogrio"

    study_out = study_area.copy()
    keep_cols = [c for c in study_out.columns if c == study_out.geometry.name]
    if not keep_cols:
        study_out = gpd.GeoDataFrame(
            {"name": ["study_area"]},
            geometry=[dissolve_to_single_geometry(study_area)],
            crs=study_area.crs,
        )
    else:
        study_out = gpd.GeoDataFrame(
            {"name": ["study_area"]},
            geometry=[dissolve_to_single_geometry(study_area)],
            crs=study_area.crs,
        )

    study_out.to_file(
        gpkg_path, layer="study_area", driver="GPKG", engine=engine
    )

    basin_cols = [
        "basin_id",
        "watersystem_code",
        "watersystem_name",
        "unit_basin_code",
        "basin_unit_id",
        "source_vector_area_m2",
        "source_files",
        "source_file_count",
        "source_input_feature_n",
        "geometry",
    ]
    basin_cols = [c for c in basin_cols if c in basins.columns]
    basins[basin_cols].to_file(
        gpkg_path,
        layer="unit_basin_source_clipped",
        driver="GPKG",
        engine=engine,
    )

    basin_grid_poly.to_file(
        gpkg_path,
        layer="basin_grid_dissolved",
        driver="GPKG",
        engine=engine,
    )
    landform_poly.to_file(
        gpkg_path,
        layer="landform_class_dissolved",
        driver="GPKG",
        engine=engine,
    )
    landscape_poly.to_file(
        gpkg_path,
        layer="landscape_unit_polygon",
        driver="GPKG",
        engine=engine,
    )


def raster_audit(path: Path) -> dict:
    with rasterio.open(path) as src:
        return {
            "file": path.name,
            "width": src.width,
            "height": src.height,
            "count": src.count,
            "dtype": src.dtypes[0],
            "nodata": src.nodata,
            "crs": src.crs.to_string() if src.crs else "",
            "resolution_x": abs(src.transform.a),
            "resolution_y": abs(src.transform.e),
            "size_bytes": path.stat().st_size,
        }


def parse_args():
    p = argparse.ArgumentParser(
        description="Build GSI natural-landform availability raster and basin×landform polygons."
    )

    p.add_argument("--study-area", required=True, type=Path,
                   help="Explicit study-area polygon (GPKG/Shapefile/GeoJSON).")
    p.add_argument("--study-area-layer", default=None)

    p.add_argument(
        "--basins",
        required=True,
        type=Path,
        nargs="+",
        help=(
            "One or more W07 watershed-mesh vector files. "
            "Example: --basins W07_5338.gpkg W07_5339.gpkg"
        ),
    )
    p.add_argument(
        "--basin-layer",
        default=None,
        help="One common layer name applied to every --basins file.",
    )
    p.add_argument(
        "--basin-layers",
        nargs="+",
        default=None,
        help=(
            "Optional per-file layer names in the same order as --basins. "
            "Use only when the input GeoPackages have different layer names."
        ),
    )

    p.add_argument("--watersystem-field", default="W07_002")
    p.add_argument("--unit-basin-field", default="W07_006")
    p.add_argument("--watersystem-name-field", default=None)

    p.add_argument("--watersystem-width", type=int, default=None,
                   help="Optional zero-padding width for watersystem code.")
    p.add_argument("--unit-basin-width", type=int, default=None,
                   help="Optional zero-padding width for unit-basin code.")

    p.add_argument("--output-dir", type=Path, default=Path("ArchGeo_landform_availability"))
    p.add_argument("--resolution", type=float, default=10.0)
    p.add_argument("--target-crs", default="EPSG:6677")
    p.add_argument("--zoom", type=int, default=14)

    p.add_argument("--tile-url-template", default=DEFAULT_TILE_URL)
    p.add_argument("--request-delay", type=float, default=0.20,
                   help="Seconds to wait after each HTTP request.")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--retries", type=int, default=4)
    p.add_argument(
        "--user-agent",
        default="ArchGeoAvailability/1.0 (academic research; rate-limited)",
    )

    p.add_argument("--code-map", type=Path, default=None,
                   help="Optional CSV with code,gsi_landform_class[,class_id].")
    p.add_argument("--allow-unmapped", action="store_true",
                   help="Skip unmapped codes instead of stopping. Not recommended for final output.")

    p.add_argument("--resume", action="store_true",
                   help="Resume successful tile processing using manifest + existing raster.")
    p.add_argument("--overwrite", action="store_true",
                   help="Delete existing output directory contents and rebuild.")
    p.add_argument("--no-polygons", action="store_true",
                   help="Skip polygonization/GPKG output.")
    p.add_argument("--dry-run", action="store_true",
                   help="Only report study grid / tile count; do not fetch or write rasters.")

    return p.parse_args()


def main():
    args = parse_args()

    if args.zoom < 5 or args.zoom > 16:
        raise ValueError("GSI natural-landform tile zoom must be within 5..16.")
    if args.zoom < 14:
        print(
            "[warning] z<14 is not recommended for the 10 m reference raster. "
            "Use z14–16 for the detailed natural-landform representation."
        )
    if args.resolution <= 0:
        raise ValueError("--resolution must be positive.")
    if args.request_delay < 0:
        raise ValueError("--request-delay must be >= 0.")
    if args.basin_layer is not None and args.basin_layers is not None:
        raise ValueError("Use either --basin-layer or --basin-layers, not both.")
    if args.basin_layers is not None and len(args.basin_layers) != len(args.basins):
        raise ValueError(
            "--basin-layers must contain exactly one layer name per --basins file."
        )

    out = args.output_dir
    if out.exists() and args.overwrite:
        shutil.rmtree(out)

    if out.exists() and not args.resume and not args.dry_run:
        # Allow a completely empty directory, otherwise require explicit intent.
        if any(out.iterdir()):
            raise FileExistsError(
                f"{out} already contains files. Use --resume or --overwrite."
            )

    out.mkdir(parents=True, exist_ok=True)

    study = read_vector(args.study_area, args.study_area_layer)
    grid = build_grid(study, args.target_crs, args.resolution)

    study4326 = study.to_crs("EPSG:4326")
    candidate_tiles = enumerate_tiles_for_bbox(tuple(study4326.total_bounds), args.zoom)
    raw_landform_bytes = grid.width * grid.height  # uint8

    print("=== ArchGeo landform availability ===")
    print(f"script_version : {SCRIPT_VERSION}")
    print(f"target_crs     : {grid.crs}")
    print(f"resolution_m   : {grid.resolution}")
    print(f"raster_size    : {grid.width} x {grid.height}")
    print(f"cells          : {grid.width * grid.height:,}")
    print(f"raw uint8 size : {raw_landform_bytes / 1024**2:.1f} MiB")
    print(f"GSI zoom       : {args.zoom}")
    print(f"candidate tiles: {len(candidate_tiles):,}")
    print(f"W07 source files: {len(args.basins)}")
    for pth in args.basins:
        print(f"  - {pth}")

    if args.dry_run:
        return 0

    code_map, class_lookup = load_code_crosswalk(args.code_map)
    class_lookup_path = out / "landform_class_lookup.csv"
    class_lookup.to_csv(class_lookup_path, index=False)

    basins = prepare_basins(
        basin_paths=args.basins,
        basin_layer=args.basin_layer,
        basin_layers=args.basin_layers,
        study_area=study,
        target_crs=args.target_crs,
        watersystem_field=args.watersystem_field,
        unit_basin_field=args.unit_basin_field,
        watersystem_name_field=args.watersystem_name_field,
        watersystem_width=args.watersystem_width,
        unit_basin_width=args.unit_basin_width,
    )
    basin_lookup_path = out / "basin_id_lookup.csv"
    basins.drop(columns="geometry").to_csv(basin_lookup_path, index=False)

    landform_tif = out / f"landform_availability_{int(args.resolution)}m.tif"
    basin_tif = out / f"unit_basin_{int(args.resolution)}m.tif"
    landscape_tif = out / f"landscape_unit_{int(args.resolution)}m.tif"

    manifest_path = out / "landform_tile_manifest.csv"
    unmapped_path = out / "unmapped_landform_codes.csv"

    # Resume safety:
    # a manifest is only meaningful together with the existing landform raster.
    if args.resume and manifest_path.exists() and not landform_tif.exists():
        raise RuntimeError(
            "--resume requested and manifest exists, but landform raster is missing. "
            "Use --overwrite to rebuild."
        )

    create_empty_raster(
        landform_tif, grid, dtype="uint8", nodata=0, overwrite=False
    )
    create_empty_raster(
        basin_tif, grid, dtype="uint16", nodata=0, overwrite=False
    )

    # Basin raster is cheap; rebuild unless resuming an already existing basin raster.
    if not args.resume or basin_tif.stat().st_size == 0:
        burn_basins(basin_tif, grid, basins)
    else:
        # It already exists because create_empty_raster returns when found.
        # To avoid ambiguous state, basin raster is always rebuilt in resume mode too.
        basin_tif.unlink()
        create_empty_raster(basin_tif, grid, dtype="uint16", nodata=0, overwrite=False)
        burn_basins(basin_tif, grid, basins)

    burn_landform_tiles(
        raster_path=landform_tif,
        grid=grid,
        study_area=study,
        zoom=args.zoom,
        tile_url_template=args.tile_url_template,
        code_map=code_map,
        manifest_path=manifest_path,
        unmapped_path=unmapped_path,
        request_delay=args.request_delay,
        timeout=args.timeout,
        retries=args.retries,
        user_agent=args.user_agent,
        resume=args.resume,
        allow_unmapped=args.allow_unmapped,
    )

    if landscape_tif.exists():
        landscape_tif.unlink()
    combine_landscape_raster(landform_tif, basin_tif, landscape_tif)

    availability = count_availability(
        landform_path=landform_tif,
        basin_path=basin_tif,
        landscape_path=landscape_tif,
        basins=basins,
        class_lookup=class_lookup,
        resolution=args.resolution,
    )
    availability_path = out / "landform_availability_by_basin.csv"
    availability.to_csv(availability_path, index=False)

    # Compatibility-oriented handoff for Arch-Geo-Selections.
    handoff_cols = [
        "landscape_unit_id",
        "watersystem_code",
        "watersystem_name",
        "unit_basin_code",
        "gsi_landform_class",
        "available_area_m2",
        "available_area_km2",
        "available_share_known",
        "available_share_basin",
        "landform_coverage_ratio",
        "resolution_m",
    ]
    handoff = availability[handoff_cols].copy()
    handoff["source"] = "GSI_natural_landform_GeoJSON_rasterized"
    handoff["selection_inference_ready"] = True
    handoff.to_csv(out / "future_landscape_availability_filled.csv", index=False)

    if not args.no_polygons:
        print("[polygonize] landform raster")
        landform_poly = polygonize_and_dissolve(
            landform_tif, "class_id", args.target_crs
        )
        class_attr = (
            class_lookup[["class_id", "gsi_landform_class"]]
            .drop_duplicates()
            .copy()
        )
        landform_poly = landform_poly.merge(class_attr, on="class_id", how="left")
        landform_poly["landform_unit_id"] = (
            "LF_CLASS::" + landform_poly["gsi_landform_class"].astype(str)
        )
        landform_poly["geometry_area_m2"] = landform_poly.geometry.area

        print("[polygonize] basin raster")
        basin_grid_poly = polygonize_and_dissolve(
            basin_tif, "basin_id", args.target_crs
        )
        basin_attr = basins.drop(columns="geometry").copy()
        basin_grid_poly = basin_grid_poly.merge(basin_attr, on="basin_id", how="left")
        basin_grid_poly["geometry_area_m2"] = basin_grid_poly.geometry.area

        print("[polygonize] landscape-unit raster")
        landscape_poly = polygonize_and_dissolve(
            landscape_tif, "landscape_code", args.target_crs
        )
        landscape_poly = landscape_poly.merge(
            availability,
            on="landscape_code",
            how="left",
            validate="one_to_one",
        )
        landscape_poly["geometry_area_m2"] = landscape_poly.geometry.area
        landscape_poly["area_diff_m2"] = (
            landscape_poly["geometry_area_m2"] - landscape_poly["available_area_m2"]
        )
        landscape_poly["area_diff_pct"] = np.where(
            landscape_poly["available_area_m2"] > 0,
            landscape_poly["area_diff_m2"] / landscape_poly["available_area_m2"] * 100.0,
            np.nan,
        )

        # Area consistency should be essentially exact for raster-derived polygons.
        max_abs_diff = float(landscape_poly["area_diff_m2"].abs().max())
        tolerance = max(1e-6, args.resolution * args.resolution * 1e-6)
        if max_abs_diff > tolerance:
            print(
                "[warning] polygon area differs from cell-count area more than expected: "
                f"max_abs_diff={max_abs_diff:.6f} m2"
            )

        gpkg = out / f"landscape_units_{int(args.resolution)}m.gpkg"
        write_geopackage(
            gpkg_path=gpkg,
            study_area=study.to_crs(args.target_crs),
            basins=basins,
            landform_poly=landform_poly,
            landscape_poly=landscape_poly,
            basin_grid_poly=basin_grid_poly,
        )

    # Metadata / audit.
    metadata = {
        "script_version": SCRIPT_VERSION,
        "created_at_utc": utc_now_iso(),
        "gsi_tile_url_template": args.tile_url_template,
        "gsi_zoom": args.zoom,
        "target_crs": args.target_crs,
        "resolution_m": args.resolution,
        "pixel_rule": "center; all_touched=False",
        "study_area_path": str(args.study_area),
        "study_area_layer": args.study_area_layer,
        "basin_paths": [str(p) for p in args.basins],
        "basin_layer_common": args.basin_layer,
        "basin_layers_per_file": args.basin_layers,
        "basin_source_file_count": len(args.basins),
        "watersystem_field": args.watersystem_field,
        "unit_basin_field": args.unit_basin_field,
        "candidate_tile_count_bbox": len(candidate_tiles),
        "grid": {
            "xmin": grid.xmin,
            "ymin": grid.ymin,
            "xmax": grid.xmax,
            "ymax": grid.ymax,
            "width": grid.width,
            "height": grid.height,
        },
        "selection_inference_note": (
            "Availability is derived from an explicit study area and basin×landform "
            "grid cells. Compare only with archaeologically observed records whose "
            "landform classification is known, unless a different missing-data policy "
            "is explicitly justified."
        ),
    }
    (out / "run_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    audits = [
        raster_audit(landform_tif),
        raster_audit(basin_tif),
        raster_audit(landscape_tif),
    ]
    pd.DataFrame(audits).to_csv(out / "raster_audit.csv", index=False)

    print("\n=== completed ===")
    print(f"Output directory: {out.resolve()}")
    print(f"Availability CSV: {availability_path.name}")
    if not args.no_polygons:
        print(f"GeoPackage      : landscape_units_{int(args.resolution)}m.gpkg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
