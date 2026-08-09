# ============================================================
# GSI Landform Classification: ZL14 vs ZL16 geometry comparison
#
# Purpose:
#   For one Lon/Lat point, download the GSI natural-landform
#   GeoJSON tiles at two zoom levels, extract the polygon
#   containing the point, clip the lower-ZL polygon to the
#   higher-ZL tile extent, and compare polygon vertices/geometry.
#
# Main outputs:
#   gsi_zl_compare_summary.csv
#   gsi_zl14_vertices.csv
#   gsi_zl16_vertices.csv
#   gsi_zl_compare.gpkg
#   gsi_zl_compare_cache/*.geojson
#
# Tested point requested:
#   y(lat) = 35.688722
#   x(lon) = 139.745764
# ============================================================


# ============================================================
# 0. USER SETTINGS
#    Usually edit only this section.
# ============================================================

POINT_LON <- 139.745764
POINT_LAT <- 35.688722

ZOOM_LOW  <- 14L
ZOOM_HIGH <- 16L

# Tokyo / JGD2011 Plane Rectangular CS IX, used for metric comparison.
ANALYSIS_CRS <- 6677

# A vertex is treated as "the same" if its nearest counterpart
# is within this distance.
VERTEX_TOLERANCE_M <- 0.10

OUTPUT_DIR <- "gsi_zl_compare_output"
CACHE_DIR  <- file.path(OUTPUT_DIR, "cache")

SUMMARY_CSV <- file.path(OUTPUT_DIR, "gsi_zl_compare_summary.csv")
VERTEX_LOW_CSV <- file.path(
  OUTPUT_DIR,
  paste0("gsi_zl", ZOOM_LOW, "_vertices.csv")
)
VERTEX_HIGH_CSV <- file.path(
  OUTPUT_DIR,
  paste0("gsi_zl", ZOOM_HIGH, "_vertices.csv")
)
OUTPUT_GPKG <- file.path(OUTPUT_DIR, "gsi_zl_compare.gpkg")

GSI_TILE_TEMPLATE <- paste0(
  "https://cyberjapandata.gsi.go.jp/xyz/",
  "experimental_landformclassification1/%d/%d/%d.geojson"
)


# ============================================================
# 1. PACKAGE CHECK
# ============================================================

required_packages <- c(
  "sf",
  "dplyr",
  "tibble",
  "readr"
)

missing_packages <- required_packages[
  !vapply(
    required_packages,
    requireNamespace,
    logical(1),
    quietly = TRUE
  )
]

if (length(missing_packages) > 0) {
  stop(
    "Missing packages: ",
    paste(missing_packages, collapse = ", "),
    "\nInstall them first with:\n",
    "install.packages(c(",
    paste(sprintf('"%s"', missing_packages), collapse = ", "),
    "))"
  )
}

if (!ZOOM_LOW %in% 0:22 || !ZOOM_HIGH %in% 0:22) {
  stop("Zoom levels must be integers between 0 and 22.")
}

if (ZOOM_LOW >= ZOOM_HIGH) {
  stop("ZOOM_LOW must be smaller than ZOOM_HIGH.")
}

dir.create(
  OUTPUT_DIR,
  recursive = TRUE,
  showWarnings = FALSE
)

dir.create(
  CACHE_DIR,
  recursive = TRUE,
  showWarnings = FALSE
)

cat(
  "sf_use_s2(): ",
  sf::sf_use_s2(),
  "\n",
  "Topology operations in this script will nevertheless be performed in EPSG:",
  ANALYSIS_CRS,
  " using projected coordinates.\n\n",
  sep = ""
)


# ============================================================
# 2. XYZ TILE FUNCTIONS
# ============================================================

lonlat_to_xyz <- function(lon, lat, z) {
  lat <- pmax(
    pmin(lat, 85.05112878),
    -85.05112878
  )

  n <- 2^z
  lat_rad <- lat * pi / 180

  x <- floor(
    (lon + 180) / 360 * n
  )

  y <- floor(
    (
      1 -
        log(
          tan(lat_rad) +
            1 / cos(lat_rad)
        ) / pi
    ) / 2 * n
  )

  x <- pmax(
    pmin(x, n - 1),
    0
  )

  y <- pmax(
    pmin(y, n - 1),
    0
  )

  tibble::tibble(
    z = as.integer(z),
    x = as.integer(x),
    y = as.integer(y)
  )
}


tile_x_to_lon <- function(x, z) {
  x / (2^z) * 360 - 180
}


tile_y_to_lat <- function(y, z) {
  n <- pi - 2 * pi * y / (2^z)

  180 / pi * atan(
    sinh(n)
  )
}


xyz_tile_bbox <- function(x, y, z) {
  xmin <- tile_x_to_lon(x, z)
  xmax <- tile_x_to_lon(x + 1, z)
  ymax <- tile_y_to_lat(y, z)
  ymin <- tile_y_to_lat(y + 1, z)

  sf::st_as_sfc(
    sf::st_bbox(
      c(
        xmin = xmin,
        ymin = ymin,
        xmax = xmax,
        ymax = ymax
      ),
      crs = 4326
    )
  )
}


# ============================================================
# 3. DOWNLOAD / READ TILE
# ============================================================

download_gsi_tile <- function(z, x, y) {
  url <- sprintf(
    GSI_TILE_TEMPLATE,
    z,
    x,
    y
  )

  cache_file <- file.path(
    CACHE_DIR,
    sprintf(
      "landform_z%d_x%d_y%d.geojson",
      z,
      x,
      y
    )
  )

  if (
    !file.exists(cache_file) ||
    is.na(file.info(cache_file)$size) ||
    file.info(cache_file)$size <= 0
  ) {
    message(
      "Downloading: ",
      url
    )

    status <- tryCatch(
      suppressWarnings(
        utils::download.file(
          url = url,
          destfile = cache_file,
          method = "libcurl",
          mode = "wb",
          quiet = FALSE
        )
      ),
      error = function(e) {
        message(
          "Download error: ",
          conditionMessage(e)
        )
        NA_integer_
      }
    )

    if (
      is.na(status) ||
      status != 0L ||
      !file.exists(cache_file) ||
      file.info(cache_file)$size <= 0
    ) {
      stop(
        "Failed to download GSI tile:\n",
        url
      )
    }
  } else {
    message(
      "Using cached tile: ",
      cache_file
    )
  }

  tile_sf <- tryCatch(
    suppressWarnings(
      sf::st_read(
        cache_file,
        quiet = TRUE,
        stringsAsFactors = FALSE
      )
    ),
    error = function(e) {
      stop(
        "Failed to read GeoJSON tile:\n",
        cache_file,
        "\n",
        conditionMessage(e)
      )
    }
  )

  if (nrow(tile_sf) == 0) {
    stop(
      "The downloaded tile contains no features:\n",
      cache_file
    )
  }

  if (is.na(sf::st_crs(tile_sf))) {
    sf::st_crs(tile_sf) <- 4326
  } else {
    tile_sf <- sf::st_transform(
      tile_sf,
      4326
    )
  }

  # IMPORTANT:
  # GSI tile polygons can contain rings that S2 rejects before repair.
  # st_make_valid() on longitude/latitude geometries uses S2, so first
  # transform to a projected CRS and then repair with GEOS.
  tile_sf <- sf::st_transform(
    tile_sf,
    ANALYSIS_CRS
  )

  tile_sf <- suppressWarnings(
    sf::st_make_valid(tile_sf)
  )

  list(
    sf = tile_sf,
    url = url,
    cache_file = cache_file
  )
}


# ============================================================
# 4. SELECT THE POLYGON CONTAINING THE POINT
# ============================================================

find_code_column <- function(x) {
  candidates <- names(x)[
    tolower(names(x)) == "code"
  ]

  if (length(candidates) == 0) {
    return(NA_character_)
  }

  candidates[1]
}


select_point_polygon <- function(
  tile_sf,
  point_sf,
  z
) {
  hits <- sf::st_intersects(
    point_sf,
    tile_sf
  )[[1]]

  if (length(hits) == 0) {
    stop(
      "No landform polygon contains/intersects the test point at ZL",
      z,
      "."
    )
  }

  if (length(hits) > 1) {
    code_col <- find_code_column(
      tile_sf
    )

    hit_codes <- if (!is.na(code_col)) {
      paste(
        as.character(
          tile_sf[[code_col]][hits]
        ),
        collapse = ", "
      )
    } else {
      "(code column not found)"
    }

    stop(
      "The test point intersects multiple polygons at ZL",
      z,
      ".\n",
      "Matched feature count: ",
      length(hits),
      "\nMatched codes: ",
      hit_codes,
      "\n",
      "This script intentionally stops rather than choosing one ",
      "polygon arbitrarily."
    )
  }

  feature <- tile_sf[
    hits,
    ,
    drop = FALSE
  ]

  code_col <- find_code_column(
    feature
  )

  code_value <- if (!is.na(code_col)) {
    as.character(
      feature[[code_col]][1]
    )
  } else {
    NA_character_
  }

  list(
    feature = feature,
    code = code_value,
    feature_index = hits[1]
  )
}


# ============================================================
# 5. POLYGON / VERTEX HELPERS
# ============================================================

polygon_only <- function(x) {
  x <- suppressWarnings(
    sf::st_make_valid(x)
  )

  gt <- unique(
    as.character(
      sf::st_geometry_type(x)
    )
  )

  if (
    any(
      gt %in% c(
        "POLYGON",
        "MULTIPOLYGON"
      )
    )
  ) {
    return(
      suppressWarnings(
        sf::st_collection_extract(
          x,
          "POLYGON"
        )
      )
    )
  }

  stop(
    "Geometry is not polygonal after validation."
  )
}


extract_vertices <- function(
  x,
  crs_metric
) {
  x_metric <- sf::st_transform(
    x,
    crs_metric
  )

  coord_metric <- sf::st_coordinates(
    x_metric
  )

  if (nrow(coord_metric) == 0) {
    stop("No polygon vertices found.")
  }

  coord_lonlat <- sf::st_coordinates(
    sf::st_transform(
      x,
      4326
    )
  )

  n <- min(
    nrow(coord_metric),
    nrow(coord_lonlat)
  )

  metric_df <- as.data.frame(
    coord_metric[seq_len(n), , drop = FALSE]
  )

  lonlat_df <- as.data.frame(
    coord_lonlat[seq_len(n), , drop = FALSE]
  )

  out <- tibble::tibble(
    vertex_id = seq_len(n),
    x_m = metric_df$X,
    y_m = metric_df$Y,
    lon = lonlat_df$X,
    lat = lonlat_df$Y
  )

  # st_coordinates() includes a duplicated closing vertex for each ring.
  # Do not remove it automatically; keep the actual coordinate sequence
  # returned from the polygon for transparent comparison.
  out
}


nearest_vertex_distances <- function(
  from_xy,
  to_xy,
  block_size = 250L
) {
  if (
    nrow(from_xy) == 0 ||
    nrow(to_xy) == 0
  ) {
    return(
      numeric(0)
    )
  }

  result <- numeric(
    nrow(from_xy)
  )

  starts <- seq(
    1L,
    nrow(from_xy),
    by = block_size
  )

  for (start_i in starts) {
    end_i <- min(
      start_i + block_size - 1L,
      nrow(from_xy)
    )

    a <- from_xy[
      start_i:end_i,
      ,
      drop = FALSE
    ]

    # Rows = current source block, columns = target vertices.
    dx <- outer(
      a[, 1],
      to_xy[, 1],
      "-"
    )

    dy <- outer(
      a[, 2],
      to_xy[, 2],
      "-"
    )

    d2 <- dx^2 + dy^2

    result[start_i:end_i] <- sqrt(
      apply(
        d2,
        1,
        min
      )
    )
  }

  result
}


safe_hausdorff_distance <- function(
  geom_a,
  geom_b
) {
  out <- tryCatch(
    sf::st_distance(
      sf::st_boundary(geom_a),
      sf::st_boundary(geom_b),
      which = "Hausdorff",
      par = 0
    ),
    error = function(e) {
      warning(
        "Hausdorff distance could not be calculated: ",
        conditionMessage(e)
      )
      matrix(
        NA_real_,
        nrow = 1,
        ncol = 1
      )
    }
  )

  as.numeric(out[1, 1])
}


safe_equals_exact <- function(
  geom_a,
  geom_b,
  tolerance
) {
  out <- tryCatch(
    sf::st_equals_exact(
      geom_a,
      geom_b,
      par = tolerance,
      sparse = FALSE
    ),
    error = function(e) {
      warning(
        "st_equals_exact() failed: ",
        conditionMessage(e)
      )
      matrix(
        FALSE,
        nrow = 1,
        ncol = 1
      )
    }
  )

  isTRUE(
    out[1, 1]
  )
}


# ============================================================
# 6. CALCULATE TILE NUMBERS
# ============================================================

xyz_low <- lonlat_to_xyz(
  POINT_LON,
  POINT_LAT,
  ZOOM_LOW
)

xyz_high <- lonlat_to_xyz(
  POINT_LON,
  POINT_LAT,
  ZOOM_HIGH
)

cat(
  "\nTest point\n",
  "  Lon: ", POINT_LON, "\n",
  "  Lat: ", POINT_LAT, "\n\n",
  sep = ""
)

cat(
  "ZL",
  ZOOM_LOW,
  " tile: x=",
  xyz_low$x,
  ", y=",
  xyz_low$y,
  "\n",
  sep = ""
)

cat(
  "ZL",
  ZOOM_HIGH,
  " tile: x=",
  xyz_high$x,
  ", y=",
  xyz_high$y,
  "\n\n",
  sep = ""
)


# ============================================================
# 7. DOWNLOAD BOTH TILES
# ============================================================

low_tile <- download_gsi_tile(
  xyz_low$z,
  xyz_low$x,
  xyz_low$y
)

high_tile <- download_gsi_tile(
  xyz_high$z,
  xyz_high$x,
  xyz_high$y
)


# ============================================================
# 8. BUILD TEST POINT AND EXTRACT MATCHED FEATURES
# ============================================================

point_sf_4326 <- sf::st_as_sf(
  tibble::tibble(
    id = "test_point",
    lon = POINT_LON,
    lat = POINT_LAT
  ),
  coords = c(
    "lon",
    "lat"
  ),
  crs = 4326,
  remove = FALSE
)

# All topology operations are performed in the projected CRS.
# This avoids S2 rejecting invalid longitude/latitude polygon rings.
point_sf <- sf::st_transform(
  point_sf_4326,
  ANALYSIS_CRS
)

low_match <- select_point_polygon(
  low_tile$sf,
  point_sf,
  ZOOM_LOW
)

high_match <- select_point_polygon(
  high_tile$sf,
  point_sf,
  ZOOM_HIGH
)

low_feature <- polygon_only(
  low_match$feature
)

high_feature <- polygon_only(
  high_match$feature
)

cat(
  "Matched code at ZL",
  ZOOM_LOW,
  ": ",
  low_match$code,
  "\n",
  sep = ""
)

cat(
  "Matched code at ZL",
  ZOOM_HIGH,
  ": ",
  high_match$code,
  "\n\n",
  sep = ""
)


# ============================================================
# 9. CLIP LOW-ZOOM FEATURE TO HIGH-ZOOM TILE EXTENT
#
# This is essential:
# comparing the raw ZL14 and ZL16 feature vertex counts directly
# would be misleading because the tile extents differ.
# ============================================================

high_tile_bbox_4326 <- xyz_tile_bbox(
  xyz_high$x,
  xyz_high$y,
  xyz_high$z
)

high_tile_bbox_metric <- sf::st_transform(
  high_tile_bbox_4326,
  ANALYSIS_CRS
)

low_feature_clipped <- suppressWarnings(
  sf::st_intersection(
    low_feature,
    high_tile_bbox_metric
  )
)

if (length(low_feature_clipped) == 0) {
  stop(
    "The ZL",
    ZOOM_LOW,
    " matched feature does not intersect the ZL",
    ZOOM_HIGH,
    " tile extent. This is unexpected for the test point."
  )
}

low_feature_clipped <- polygon_only(
  low_feature_clipped
)

high_feature <- suppressWarnings(
  sf::st_intersection(
    high_feature,
    high_tile_bbox_metric
  )
)

high_feature <- polygon_only(
  high_feature
)


# ============================================================
# 10. TRANSFORM TO METRIC CRS
# ============================================================

low_metric <- sf::st_transform(
  low_feature_clipped,
  ANALYSIS_CRS
)

high_metric <- sf::st_transform(
  high_feature,
  ANALYSIS_CRS
)

bbox_metric <- high_tile_bbox_metric


# ============================================================
# 11. VERTEX EXTRACTION
# ============================================================

vertices_low <- extract_vertices(
  low_feature_clipped,
  ANALYSIS_CRS
) |>
  dplyr::mutate(
    zoom = ZOOM_LOW,
    source = paste0(
      "ZL",
      ZOOM_LOW,
      "_clipped_to_ZL",
      ZOOM_HIGH,
      "_tile"
    )
  )

vertices_high <- extract_vertices(
  high_feature,
  ANALYSIS_CRS
) |>
  dplyr::mutate(
    zoom = ZOOM_HIGH,
    source = paste0(
      "ZL",
      ZOOM_HIGH,
      "_original"
    )
  )

readr::write_csv(
  vertices_low,
  VERTEX_LOW_CSV,
  na = ""
)

readr::write_csv(
  vertices_high,
  VERTEX_HIGH_CSV,
  na = ""
)


# ============================================================
# 12. NEAREST-VERTEX COMPARISON
# ============================================================

xy_low <- as.matrix(
  vertices_low[
    ,
    c(
      "x_m",
      "y_m"
    )
  ]
)

xy_high <- as.matrix(
  vertices_high[
    ,
    c(
      "x_m",
      "y_m"
    )
  ]
)

nn_low_to_high <- nearest_vertex_distances(
  xy_low,
  xy_high
)

nn_high_to_low <- nearest_vertex_distances(
  xy_high,
  xy_low
)

vertices_low$nearest_vertex_distance_to_high_m <- nn_low_to_high
vertices_low$matched_within_tolerance <- (
  nn_low_to_high <= VERTEX_TOLERANCE_M
)

vertices_high$nearest_vertex_distance_to_low_m <- nn_high_to_low
vertices_high$matched_within_tolerance <- (
  nn_high_to_low <= VERTEX_TOLERANCE_M
)

# Re-write with the comparison columns.
readr::write_csv(
  vertices_low,
  VERTEX_LOW_CSV,
  na = ""
)

readr::write_csv(
  vertices_high,
  VERTEX_HIGH_CSV,
  na = ""
)


# ============================================================
# 13. GEOMETRY-LEVEL COMPARISON
# ============================================================

area_low_m2 <- as.numeric(
  sum(
    sf::st_area(low_metric)
  )
)

area_high_m2 <- as.numeric(
  sum(
    sf::st_area(high_metric)
  )
)

intersection_geom <- suppressWarnings(
  sf::st_intersection(
    sf::st_union(
      sf::st_geometry(low_metric)
    ),
    sf::st_union(
      sf::st_geometry(high_metric)
    )
  )
)

union_geom <- suppressWarnings(
  sf::st_union(
    sf::st_union(
      sf::st_geometry(low_metric)
    ),
    sf::st_union(
      sf::st_geometry(high_metric)
    )
  )
)

symdiff_geom <- suppressWarnings(
  sf::st_sym_difference(
    sf::st_union(
      sf::st_geometry(low_metric)
    ),
    sf::st_union(
      sf::st_geometry(high_metric)
    )
  )
)

intersection_area_m2 <- if (
  length(intersection_geom) > 0
) {
  as.numeric(
    sum(
      sf::st_area(intersection_geom)
    )
  )
} else {
  0
}

union_area_m2 <- if (
  length(union_geom) > 0
) {
  as.numeric(
    sum(
      sf::st_area(union_geom)
    )
  )
} else {
  NA_real_
}

symmetric_difference_area_m2 <- if (
  length(symdiff_geom) > 0
) {
  as.numeric(
    sum(
      sf::st_area(symdiff_geom)
    )
  )
} else {
  0
}

iou <- if (
  is.finite(union_area_m2) &&
  union_area_m2 > 0
) {
  intersection_area_m2 / union_area_m2
} else {
  NA_real_
}

symmetric_difference_ratio <- if (
  is.finite(union_area_m2) &&
  union_area_m2 > 0
) {
  symmetric_difference_area_m2 / union_area_m2
} else {
  NA_real_
}

hausdorff_m <- safe_hausdorff_distance(
  sf::st_union(
    sf::st_geometry(low_metric)
  ),
  sf::st_union(
    sf::st_geometry(high_metric)
  )
)

equals_exact_with_tolerance <- safe_equals_exact(
  sf::st_union(
    sf::st_geometry(low_metric)
  ),
  sf::st_union(
    sf::st_geometry(high_metric)
  ),
  VERTEX_TOLERANCE_M
)


# ============================================================
# 14. SUMMARY TABLE
# ============================================================

safe_mean <- function(x) {
  if (length(x) == 0) {
    return(NA_real_)
  }
  mean(x)
}

safe_median <- function(x) {
  if (length(x) == 0) {
    return(NA_real_)
  }
  stats::median(x)
}

safe_max <- function(x) {
  if (length(x) == 0) {
    return(NA_real_)
  }
  max(x)
}

comparison_summary <- tibble::tibble(
  point_lon = POINT_LON,
  point_lat = POINT_LAT,

  zoom_low = ZOOM_LOW,
  zoom_high = ZOOM_HIGH,

  low_tile_x = xyz_low$x,
  low_tile_y = xyz_low$y,
  high_tile_x = xyz_high$x,
  high_tile_y = xyz_high$y,

  low_tile_url = low_tile$url,
  high_tile_url = high_tile$url,

  low_code = low_match$code,
  high_code = high_match$code,
  same_code = identical(
    low_match$code,
    high_match$code
  ),

  analysis_crs = ANALYSIS_CRS,
  vertex_tolerance_m = VERTEX_TOLERANCE_M,

  low_vertex_n = nrow(vertices_low),
  high_vertex_n = nrow(vertices_high),

  low_vertices_matched_n = sum(
    vertices_low$matched_within_tolerance,
    na.rm = TRUE
  ),
  high_vertices_matched_n = sum(
    vertices_high$matched_within_tolerance,
    na.rm = TRUE
  ),

  low_vertices_matched_prop = mean(
    vertices_low$matched_within_tolerance,
    na.rm = TRUE
  ),
  high_vertices_matched_prop = mean(
    vertices_high$matched_within_tolerance,
    na.rm = TRUE
  ),

  low_to_high_nn_mean_m = safe_mean(
    nn_low_to_high
  ),
  low_to_high_nn_median_m = safe_median(
    nn_low_to_high
  ),
  low_to_high_nn_max_m = safe_max(
    nn_low_to_high
  ),

  high_to_low_nn_mean_m = safe_mean(
    nn_high_to_low
  ),
  high_to_low_nn_median_m = safe_median(
    nn_high_to_low
  ),
  high_to_low_nn_max_m = safe_max(
    nn_high_to_low
  ),

  low_area_m2 = area_low_m2,
  high_area_m2 = area_high_m2,
  intersection_area_m2 = intersection_area_m2,
  union_area_m2 = union_area_m2,
  symmetric_difference_area_m2 = symmetric_difference_area_m2,

  intersection_over_union = iou,
  symmetric_difference_ratio = symmetric_difference_ratio,

  hausdorff_distance_m = hausdorff_m,

  equals_exact_with_tolerance = equals_exact_with_tolerance
)

readr::write_csv(
  comparison_summary,
  SUMMARY_CSV,
  na = ""
)


# ============================================================
# 15. GPKG FOR VISUAL INSPECTION IN QGIS
# ============================================================

if (file.exists(OUTPUT_GPKG)) {
  unlink(OUTPUT_GPKG)
}

point_out <- point_sf

low_original_out <- sf::st_transform(
  low_feature,
  ANALYSIS_CRS
)

low_clipped_out <- low_metric
high_out <- high_metric

bbox_out <- sf::st_sf(
  layer = paste0(
    "ZL",
    ZOOM_HIGH,
    "_tile_bbox"
  ),
  geometry = bbox_metric
)

sf::st_write(
  point_out,
  OUTPUT_GPKG,
  layer = "test_point",
  quiet = TRUE
)

sf::st_write(
  low_original_out,
  OUTPUT_GPKG,
  layer = paste0(
    "zl",
    ZOOM_LOW,
    "_matched_original"
  ),
  quiet = TRUE
)

sf::st_write(
  low_clipped_out,
  OUTPUT_GPKG,
  layer = paste0(
    "zl",
    ZOOM_LOW,
    "_clipped_to_zl",
    ZOOM_HIGH
  ),
  quiet = TRUE
)

sf::st_write(
  high_out,
  OUTPUT_GPKG,
  layer = paste0(
    "zl",
    ZOOM_HIGH,
    "_matched"
  ),
  quiet = TRUE
)

sf::st_write(
  bbox_out,
  OUTPUT_GPKG,
  layer = paste0(
    "zl",
    ZOOM_HIGH,
    "_tile_bbox"
  ),
  quiet = TRUE
)

if (
  length(symdiff_geom) > 0 &&
  !all(
    sf::st_is_empty(symdiff_geom)
  )
) {
  symdiff_out <- sf::st_sf(
    layer = "symmetric_difference",
    geometry = symdiff_geom
  )

  sf::st_write(
    symdiff_out,
    OUTPUT_GPKG,
    layer = "symmetric_difference",
    quiet = TRUE
  )
}


# ============================================================
# 16. CONSOLE REPORT
# ============================================================

cat(
  "\n============================================================\n",
  "GSI ZL geometry comparison completed\n",
  "============================================================\n",
  sep = ""
)

cat(
  "Point: ",
  POINT_LON,
  ", ",
  POINT_LAT,
  "\n",
  sep = ""
)

cat(
  "ZL",
  ZOOM_LOW,
  " code: ",
  low_match$code,
  "\n",
  sep = ""
)

cat(
  "ZL",
  ZOOM_HIGH,
  " code: ",
  high_match$code,
  "\n",
  sep = ""
)

cat(
  "Same code: ",
  comparison_summary$same_code,
  "\n\n",
  sep = ""
)

cat(
  "Vertices after clipping ZL",
  ZOOM_LOW,
  " to ZL",
  ZOOM_HIGH,
  " tile:\n",
  sep = ""
)

cat(
  "  ZL",
  ZOOM_LOW,
  ": ",
  comparison_summary$low_vertex_n,
  "\n",
  sep = ""
)

cat(
  "  ZL",
  ZOOM_HIGH,
  ": ",
  comparison_summary$high_vertex_n,
  "\n\n",
  sep = ""
)

cat(
  "Vertex match tolerance: ",
  VERTEX_TOLERANCE_M,
  " m\n",
  sep = ""
)

cat(
  "  ZL",
  ZOOM_LOW,
  " vertices matched: ",
  round(
    100 *
      comparison_summary$low_vertices_matched_prop,
    3
  ),
  "%\n",
  sep = ""
)

cat(
  "  ZL",
  ZOOM_HIGH,
  " vertices matched: ",
  round(
    100 *
      comparison_summary$high_vertices_matched_prop,
    3
  ),
  "%\n\n",
  sep = ""
)

cat(
  "Maximum nearest-vertex distance:\n"
)

cat(
  "  ZL",
  ZOOM_LOW,
  " -> ZL",
  ZOOM_HIGH,
  ": ",
  comparison_summary$low_to_high_nn_max_m,
  " m\n",
  sep = ""
)

cat(
  "  ZL",
  ZOOM_HIGH,
  " -> ZL",
  ZOOM_LOW,
  ": ",
  comparison_summary$high_to_low_nn_max_m,
  " m\n\n",
  sep = ""
)

cat(
  "Hausdorff distance: ",
  comparison_summary$hausdorff_distance_m,
  " m\n",
  sep = ""
)

cat(
  "Intersection over Union (IoU): ",
  comparison_summary$intersection_over_union,
  "\n",
  sep = ""
)

cat(
  "Symmetric-difference ratio: ",
  comparison_summary$symmetric_difference_ratio,
  "\n",
  sep = ""
)

cat(
  "st_equals_exact within ",
  VERTEX_TOLERANCE_M,
  " m: ",
  comparison_summary$equals_exact_with_tolerance,
  "\n\n",
  sep = ""
)

cat(
  "Outputs:\n",
  "  ",
  SUMMARY_CSV,
  "\n",
  "  ",
  VERTEX_LOW_CSV,
  "\n",
  "  ",
  VERTEX_HIGH_CSV,
  "\n",
  "  ",
  OUTPUT_GPKG,
  "\n",
  sep = ""
)

print(
  comparison_summary
)
