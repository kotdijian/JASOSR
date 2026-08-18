#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ArchaeologicalMapExtractor_AutoGeoref_v9_5_gcp_z15.py

自治体の遺跡地図PDFを対象に、
1) 地図部分抽出
2) 地理院タイル自動取得
3) 中央ROI多段探索 + SIFT/ORB + Similarity/Affine/Homography RANSACによる自動ジオリファレンス
4) QC画像出力
5) PDF内部テキストから遺跡番号代表点抽出
6) 赤線ベクタ抽出・polygonize
7) EPSG:4326へ変換
までを一本化する試験版。

一覧表抽出は既存の ArchaeologicalMapExtractor.py 等の成果
01_table.csv があればそれを利用し、無ければ簡易抽出を行う。

必要:
  pip install pymupdf numpy pandas opencv-python requests shapely matplotlib

実行:
  python3 ArchaeologicalMapExtractor_AutoGeoref.py 68_gyouda_city.pdf --out gyoda_auto

自動QCに通らない場合は
  05_matches.png
  05_overlay.png
  05_candidates.csv
を確認する。

検索範囲:
  --search-bbox を省略するとPDF本文から自治体名を抽出し、
  Nominatim (OpenStreetMap) で自治体bboxを取得して自動設定する。
  自動判定に失敗した場合のみ --municipality または --search-bbox を指定する。

変更:
  --search-bbox 139.38,36.08,139.55,36.20
"""

from __future__ import annotations

import argparse, json, math, re, time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import cv2

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

from shapely.geometry import LineString, Point, Polygon, MultiPolygon, mapping
from shapely.ops import unary_union, polygonize, transform as shp_transform
from shapely.validation import make_valid

GSI = {
    "std": "https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png",
    "pale": "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png",
}
DEFAULT_BBOX = None
FW = str.maketrans("０１２３４５６７８９", "0123456789")



def normalize_digits(s):
    return str(s).translate(FW)

def clean_text(s):
    s = normalize_digits(s)
    s = s.replace("\u3000", "")
    s = re.sub(r"\s+", "", s)
    return s.strip()

def save_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def save_geojson(path, features):
    save_json(path, {"type": "FeatureCollection", "features": features})


def parse_bbox(s):
    vals = tuple(float(x.strip()) for x in s.split(","))
    if len(vals) != 4:
        raise argparse.ArgumentTypeError("WEST,SOUTH,EAST,NORTH")
    return vals


def is_red(c):
    return bool(c and len(c) >= 3 and c[0] >= .80 and c[1] <= .35 and c[2] <= .35)



def extract_municipality_candidates(page):
    """PDF本文から自治体名候補を抽出する。"""
    text = page.get_text("text")
    compact = re.sub(r"[ \t\u3000]+", "", text)

    candidates = []

    # 「○○市遺跡地図」等を優先
    for pat in (
        r"([一-龥々ヶヵぁ-んァ-ヶー]{1,20}(?:市|町|村))遺跡地図",
        r"([一-龥々ヶヵぁ-んァ-ヶー]{1,20}(?:市|町|村))埋蔵文化財",
        r"([一-龥々ヶヵぁ-んァ-ヶー]{1,20}(?:市|町|村))内埋蔵文化財",
    ):
        candidates.extend(m.group(1) for m in re.finditer(pat, compact))

    # 補助候補
    candidates.extend(
        m.group(1)
        for m in re.finditer(
            r"([一-龥々ヶヵぁ-んァ-ヶー]{2,20}(?:市|町|村))",
            compact,
        )
    )

    cleaned = []
    for c in candidates:
        # 都道府県名が連結した場合だけ除去
        c = re.sub(
            r"^(?:北海道|東京都|京都府|大阪府|"
            r"埼玉県|神奈川県|千葉県|群馬県|栃木県|茨城県|"
            r"山梨県|長野県|静岡県|愛知県|岐阜県|三重県|"
            r"新潟県|富山県|石川県|福井県|滋賀県|兵庫県|"
            r"奈良県|和歌山県|鳥取県|島根県|岡山県|広島県|"
            r"山口県|徳島県|香川県|愛媛県|高知県|福岡県|"
            r"佐賀県|長崎県|熊本県|大分県|宮崎県|鹿児島県|沖縄県)",
            "",
            c,
        )
        if 2 <= len(c) <= 20:
            cleaned.append(c)

    out, seen = [], set()
    for c in cleaned:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def nominatim_bbox(municipality, padding_ratio=0.12, timeout=30):
    """
    Nominatimで自治体を検索し、
    WEST,SOUTH,EAST,NORTH のbboxを返す。
    """
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": municipality,
        "format": "jsonv2",
        "countrycodes": "jp",
        "limit": 5,
        "addressdetails": 1,
        "accept-language": "ja",
    }
    headers = {
        "User-Agent": "ArchaeologicalMapExtractor/1.0 (research georeferencing)"
    }
    res = requests.get(url, params=params, headers=headers, timeout=timeout)
    res.raise_for_status()
    results = res.json()
    if not results:
        raise RuntimeError(f"Nominatim検索結果なし: {municipality}")

    def rank(r):
        display = str(r.get("display_name", ""))
        cls = str(r.get("class", ""))
        typ = str(r.get("type", ""))
        at = str(r.get("addresstype", ""))
        score = 0.0
        if municipality in display:
            score += 100
        if cls == "boundary":
            score += 50
        if typ == "administrative":
            score += 40
        if at in {"city", "town", "village", "municipality", "administrative"}:
            score += 30
        score += float(r.get("importance") or 0) * 10
        return score

    best = max(results, key=rank)
    bb = best.get("boundingbox")
    if not bb or len(bb) != 4:
        raise RuntimeError(f"boundingboxなし: {municipality}")

    # Nominatim = south,north,west,east
    south, north, west, east = map(float, bb)
    dx, dy = east-west, north-south
    pad_x = max(dx * padding_ratio, 0.015)
    pad_y = max(dy * padding_ratio, 0.012)

    bbox = (west-pad_x, south-pad_y, east+pad_x, north+pad_y)
    meta = {
        "query": municipality,
        "display_name": best.get("display_name"),
        "class": best.get("class"),
        "type": best.get("type"),
        "addresstype": best.get("addresstype"),
        "osm_type": best.get("osm_type"),
        "osm_id": best.get("osm_id"),
        "raw_boundingbox": [south, north, west, east],
        "padding_ratio": padding_ratio,
        "search_bbox": list(bbox),
    }
    return bbox, meta


def determine_search_bbox(page, manual_bbox, municipality_override, padding, out_dir):
    """
    優先順位:
      1. --search-bbox
      2. --municipality
      3. PDF本文から自治体名
    """
    if manual_bbox is not None:
        meta = {
            "method": "manual_search_bbox",
            "search_bbox": list(manual_bbox),
        }
        save_json(out_dir/"00_search_bbox.json", meta)
        return manual_bbox, meta

    if municipality_override:
        candidates = [municipality_override]
        source = "manual_municipality"
    else:
        candidates = extract_municipality_candidates(page)
        source = "pdf_text"

    if not candidates:
        raise RuntimeError(
            "自治体名をPDFから抽出できません。"
            " --municipality または --search-bbox を指定してください。"
        )

    errors = []
    for name in candidates[:8]:
        try:
            bbox, geocode = nominatim_bbox(name, padding_ratio=padding)
            meta = {
                "method": "nominatim_municipality_bbox",
                "municipality_source": source,
                "municipality_candidates": candidates,
                "selected_municipality": name,
                "geocoder": geocode,
                "search_bbox": list(bbox),
            }
            save_json(out_dir/"00_search_bbox.json", meta)
            return bbox, meta
        except Exception as e:
            errors.append(f"{name}: {e}")

    raise RuntimeError(
        "search-bbox自動決定に失敗しました。\n"
        + "\n".join(errors)
        + "\n--municipality または --search-bbox を指定してください。"
    )


def detect_main_map_bbox(page, dpi=120):
    """
    主地図自動切出し v2

    非白画素そのものではなく「地図らしい局所エッジ密度」を使う。
    一覧表・凡例・小挿図も線が多いが、主地図より空間的に小さいため、
    ブロック密度マスクの最大連結領域として主地図を選ぶ。

    出力 bbox は PDF point 座標。
    """
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    rgb = arr[:, :, :3]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    # 赤い遺跡線は主地図領域検出には不要なので白くする
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    r1 = cv2.inRange(hsv, np.array([0, 50, 60]), np.array([15, 255, 255]))
    r2 = cv2.inRange(hsv, np.array([165, 50, 60]), np.array([180, 255, 255]))
    red = cv2.bitwise_or(r1, r2)
    gray2 = gray.copy()
    gray2[red > 0] = 255

    # 地図の道路・水路・街区線を捉える
    edges = cv2.Canny(cv2.GaussianBlur(gray2, (3, 3), 0), 45, 145)

    # ブロックごとのエッジ密度
    block = max(18, round(min(pix.width, pix.height) / 85))
    gw = math.ceil(pix.width / block)
    gh = math.ceil(pix.height / block)
    density = np.zeros((gh, gw), np.float32)

    for gy in range(gh):
        y0 = gy * block
        y1 = min(pix.height, y0 + block)
        for gx in range(gw):
            x0 = gx * block
            x1 = min(pix.width, x0 + block)
            cell = edges[y0:y1, x0:x1]
            density[gy, gx] = float(np.count_nonzero(cell)) / max(1, cell.size)

    # 地図域は「ほぼ白」より密で、表罫線/文字の極端な密集よりは低い。
    nonzero = density[density > 0]
    if len(nonzero) == 0:
        raise RuntimeError("主地図領域を検出できませんでした。")

    # PDFごとの差に追随する動的閾値
    lo = max(0.006, float(np.quantile(nonzero, 0.18)))
    hi = min(0.22, max(lo + 0.015, float(np.quantile(nonzero, 0.96))))

    map_cells = ((density >= lo) & (density <= hi)).astype(np.uint8) * 255

    # 地図の内部空白を連結し、表・凡例との細い接続は切りやすくする
    map_cells = cv2.morphologyEx(
        map_cells,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)),
        iterations=2,
    )
    map_cells = cv2.morphologyEx(
        map_cells,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        iterations=1,
    )

    n, lab, stats, _ = cv2.connectedComponentsWithStats(map_cells, 8)
    candidates = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < 4 or h < 4:
            continue
        bbox_area = w * h
        fullness = area / max(1, bbox_area)
        # 主地図は大きく、ある程度面として連続する
        score = bbox_area * (0.65 + 0.35 * fullness)
        candidates.append((score, i, x, y, w, h, area, fullness))

    if not candidates:
        raise RuntimeError("主地図候補が見つかりませんでした。")

    _, idx, gx, gy, gwid, ghei, area, fullness = max(candidates, key=lambda t: t[0])

    # grid bbox -> raster px bbox
    x0 = gx * block
    y0 = gy * block
    x1 = min(pix.width, (gx + gwid) * block)
    y1 = min(pix.height, (gy + ghei) * block)

    # 少量の余白
    pad = max(12, round(min(pix.width, pix.height) * 0.008))
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(pix.width, x1 + pad)
    y1 = min(pix.height, y1 + pad)

    bbox = [x0 / scale, y0 / scale, x1 / scale, y1 / scale]

    # debug maskを元画像サイズに戻す
    debug_mask = cv2.resize(
        map_cells,
        (pix.width, pix.height),
        interpolation=cv2.INTER_NEAREST,
    )

    debug = {
        "detect_method": "edge_density_largest_component",
        "detect_dpi": dpi,
        "render_width": pix.width,
        "render_height": pix.height,
        "block_px": block,
        "density_threshold_low": lo,
        "density_threshold_high": hi,
        "component_grid_bbox": [int(gx), int(gy), int(gwid), int(ghei)],
        "component_grid_area": int(area),
        "component_fullness": float(fullness),
        "map_bbox_pdf": bbox,
    }
    return bbox, debug, debug_mask



def detect_matching_bbox_from_red_vectors(page, page_bbox, pad_ratio=0.035):
    """
    位置合わせ専用ROIを赤い遺跡ベクタの分布から決める。

    表・凡例を含む全ページを画像マッチングに使うと誤対応が増えるため、
    遺跡分布が存在する主地図部分だけを使う。
    これは「最終地図範囲」ではなくマッチング専用のROI。
    """
    reds = [d for d in page.get_drawings() if is_red(d.get("color"))]
    rects = []
    for d in reds:
        r = d.get("rect")
        if r is None:
            continue
        w = max(0.0, r.x1-r.x0)
        h = max(0.0, r.y1-r.y0)
        # 1点・極小装飾を除外
        if w*h < 1.0 and max(w,h) < 2.0:
            continue
        rects.append(r)

    if not rects:
        return list(page_bbox)

    x0 = min(r.x0 for r in rects)
    y0 = min(r.y0 for r in rects)
    x1 = max(r.x1 for r in rects)
    y1 = max(r.y1 for r in rects)

    W = x1-x0
    H = y1-y0
    pad_x = max(20.0, W*pad_ratio)
    pad_y = max(20.0, H*pad_ratio)

    pb = page.rect
    return [
        max(pb.x0, x0-pad_x),
        max(pb.y0, y0-pad_y),
        min(pb.x1, x1+pad_x),
        min(pb.y1, y1+pad_y),
    ]


def inspect_pdf(pdf, page_no):
    doc = pymupdf.open(pdf)
    page = doc[page_no]

    reds = [d for d in page.get_drawings() if is_red(d.get("color"))]
    if not reds:
        raise RuntimeError("赤線ベクタが見つかりません")

    bbox, crop_debug, crop_mask = detect_main_map_bbox(page)

    match_bbox = detect_matching_bbox_from_red_vectors(page, bbox)

    info = {
        "page_width": float(page.rect.width),
        "page_height": float(page.rect.height),
        "red_drawing_count": len(reds),
        "map_bbox_pdf": bbox,
        "match_bbox_pdf": match_bbox,
        "map_crop": crop_debug,
    }
    return doc, page, bbox, match_bbox, info, crop_mask


@dataclass
class PdfRaster:
    img: np.ndarray
    bbox: tuple
    sx: float
    sy: float


def render_pdf_map(page, bbox, dpi):
    x0,y0,x1,y1 = bbox
    scale = dpi/72
    pix = page.get_pixmap(
        matrix=pymupdf.Matrix(scale,scale),
        clip=pymupdf.Rect(*bbox),
        alpha=False
    )
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height,pix.width,pix.n)
    bgr = cv2.cvtColor(arr[:,:,:3], cv2.COLOR_RGB2BGR)
    return PdfRaster(bgr, tuple(bbox), pix.width/(x1-x0), pix.height/(y1-y0))


def pdf_to_raster(x,y,r):
    x0,y0,_,_ = r.bbox
    return (x-x0)*r.sx, (y-y0)*r.sy


def raster_to_pdf(x,y,r):
    x0,y0,_,_ = r.bbox
    return x0 + float(x)/r.sx, y0 + float(y)/r.sy


def lonlat_to_tile(lon,lat,z):
    n=2**z
    x=(lon+180)/360*n
    lr=math.radians(lat)
    y=(1-math.asinh(math.tan(lr))/math.pi)/2*n
    return x,y


def tile_to_lonlat(x,y,z):
    n=2**z
    lon=x/n*360-180
    lat=math.degrees(math.atan(math.sinh(math.pi*(1-2*y/n))))
    return lon,lat


@dataclass
class Mosaic:
    img: np.ndarray
    z: int
    xmin: int
    ymin: int
    layer: str
    bbox: tuple | None = None
    tile_size: int = 256

    def pixel_to_lonlat(self,x,y):
        return tile_to_lonlat(
            self.xmin+x/self.tile_size,
            self.ymin+y/self.tile_size,
            self.z
        )

    def lonlat_to_pixel(self, lon, lat):
        tx, ty = lonlat_to_tile(float(lon), float(lat), self.z)
        return (
            (tx - self.xmin) * self.tile_size,
            (ty - self.ymin) * self.tile_size,
        )


def get_tile(session, layer,z,x,y,cache):
    cp=cache/layer/str(z)/str(x)/f"{y}.png"
    if cp.exists():
        data=cp.read_bytes()
    else:
        url=GSI[layer].format(z=z,x=x,y=y)
        res=session.get(url,timeout=30,headers={"User-Agent":"ArchaeologicalMapExtractor/1.0"})
        res.raise_for_status()
        data=res.content
        cp.parent.mkdir(parents=True,exist_ok=True)
        cp.write_bytes(data)
        time.sleep(.05)
    img=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"decode failed {cp}")
    return img


def build_mosaic(layer,z,bbox,cache):
    west,south,east,north=bbox
    x0,y1=lonlat_to_tile(west,south,z)
    x1,y0=lonlat_to_tile(east,north,z)
    xmin,xmax=math.floor(min(x0,x1)),math.floor(max(x0,x1))
    ymin,ymax=math.floor(min(y0,y1)),math.floor(max(y0,y1))
    count=(xmax-xmin+1)*(ymax-ymin+1)
    if count>900:
        raise RuntimeError(f"too many tiles: {count}")
    s=requests.Session()
    rows=[]
    for y in range(ymin,ymax+1):
        rows.append(cv2.hconcat([
            get_tile(s,layer,z,x,y,cache) for x in range(xmin,xmax+1)
        ]))
    return Mosaic(cv2.vconcat(rows),z,xmin,ymin,layer,tuple(bbox))


def preprocess_pdf_variants(img):
    """
    PDF背景地図を3表現にする:
      gray : 線画濃淡
      edge : 道路・河川・街区線の輪郭
      mix  : gray + edge

    赤い遺跡線・赤文字は白に置換してマッチングから除外。
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    a = cv2.inRange(hsv, np.array([0, 55, 55]), np.array([14, 255, 255]))
    b = cv2.inRange(hsv, np.array([166, 55, 55]), np.array([180, 255, 255]))
    red = cv2.bitwise_or(a, b)

    clean = img.copy()
    clean[red > 0] = (255, 255, 255)

    gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(2.0, (8, 8)).apply(gray)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    edge = cv2.Canny(blur, 45, 140)

    # 太すぎる線を少し均質化
    edge = cv2.morphologyEx(
        edge,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
    )

    mix = cv2.addWeighted(gray, 0.50, edge, 0.50, 0)
    return {"gray": gray, "edge": edge, "mix": mix}


def preprocess_gsi_variants(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(2.0, (8, 8)).apply(gray)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    edge = cv2.Canny(blur, 45, 140)
    edge = cv2.morphologyEx(
        edge,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
    )
    mix = cv2.addWeighted(gray, 0.50, edge, 0.50, 0)
    return {"gray": gray, "edge": edge, "mix": mix}


def resize_max(img,maxdim):
    h,w=img.shape[:2]
    s=min(1,maxdim/max(h,w))
    if s<1:
        return cv2.resize(img,(round(w*s),round(h*s)),interpolation=cv2.INTER_AREA),s
    return img,1.0



def central_roi(img, fraction):
    """
    画像中央 fraction (0..1) を切り出す。
    fraction=0.70 なら上下左右15%ずつを除外。
    """
    h, w = img.shape[:2]
    f = float(fraction)
    if not (0.2 <= f <= 1.0):
        raise ValueError(f"invalid ROI fraction: {fraction}")
    rw = max(32, int(round(w * f)))
    rh = max(32, int(round(h * f)))
    x0 = max(0, (w - rw) // 2)
    y0 = max(0, (h - rh) // 2)
    x1 = min(w, x0 + rw)
    y1 = min(h, y0 + rh)
    return img[y0:y1, x0:x1], (x0, y0, x1, y1)


def roi_transform_full_to_crop(roi_rect):
    """full raster pixel -> central crop pixel."""
    x0, y0, _, _ = roi_rect
    return np.array([
        [1.0, 0.0, -float(x0)],
        [0.0, 1.0, -float(y0)],
        [0.0, 0.0, 1.0],
    ], dtype=float)


def transform_points_h(points, H):
    if points is None or len(points) == 0:
        return np.empty((0, 2), dtype=float)
    p = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(p, H).reshape(-1, 2)


@dataclass
class Match:
    layer: str
    z: int
    roi_fraction: float
    mode: str
    model: str
    good: int
    inliers: int
    ratio: float
    mederr: float
    src_span_x: float
    src_span_y: float
    src_grid_coverage: float
    dst_span_x: float
    dst_span_y: float
    global_agreement: float
    transform_sane: bool
    H: np.ndarray | None
    mosaic: Mosaic
    src: np.ndarray | None
    dst: np.ndarray | None
    mask: np.ndarray | None


def detector():
    if hasattr(cv2, "SIFT_create"):
        return (
            "SIFT",
            cv2.SIFT_create(
                nfeatures=18000,
                contrastThreshold=.015,
                edgeThreshold=18,
                sigma=1.4,
            ),
            cv2.NORM_L2,
            .76,
        )
    return (
        "ORB",
        cv2.ORB_create(
            nfeatures=20000,
            scaleFactor=1.12,
            nlevels=12,
            fastThreshold=8,
        ),
        cv2.NORM_HAMMING,
        .78,
    )


def _coverage_metrics(points, width, height):
    if points is None or len(points) == 0:
        return 0.0, 0.0, 0.0
    p = np.asarray(points, float)
    span_x = float((p[:, 0].max() - p[:, 0].min()) / max(1.0, width))
    span_y = float((p[:, 1].max() - p[:, 1].min()) / max(1.0, height))

    # 4x4 grid occupancy
    gx = np.clip((p[:, 0] / max(1.0, width) * 4).astype(int), 0, 3)
    gy = np.clip((p[:, 1] / max(1.0, height) * 4).astype(int), 0, 3)
    cells = len(set(zip(gx.tolist(), gy.tolist())))
    grid = cells / 16.0
    return span_x, span_y, grid



def linear_transform_metrics(H):
    """
    Similarity/Affine の2x2線形部分を評価。
    condition_number ≈ 1 なら等方的。
    axis_ratio が大きい候補は、地図を細長く潰して一致させた偽陽性。
    """
    if H is None:
        return {
            "condition_number": float("inf"),
            "scale_max": float("nan"),
            "scale_min": float("nan"),
            "determinant": float("nan"),
            "orthogonality": 0.0,
        }

    A = np.asarray(H, dtype=float)[:2, :2]
    if not np.isfinite(A).all():
        return {
            "condition_number": float("inf"),
            "scale_max": float("nan"),
            "scale_min": float("nan"),
            "determinant": float("nan"),
            "orthogonality": 0.0,
        }

    try:
        s = np.linalg.svd(A, compute_uv=False)
    except np.linalg.LinAlgError:
        s = np.array([np.nan, np.nan])

    smax = float(np.nanmax(s))
    smin = float(np.nanmin(s))
    cond = smax / max(smin, 1e-12)

    c0 = A[:, 0]
    c1 = A[:, 1]
    denom = max(np.linalg.norm(c0) * np.linalg.norm(c1), 1e-12)
    # 1.0 = perpendicular, 0.0 = parallel
    orth = 1.0 - abs(float(np.dot(c0, c1))) / denom

    return {
        "condition_number": float(cond),
        "scale_max": smax,
        "scale_min": smin,
        "determinant": float(np.linalg.det(A)),
        "orthogonality": float(orth),
    }


def low_dof_geometry_sane(m, max_condition=1.60, min_orthogonality=0.80):
    """
    Similarity は理論上 condition=1。
    Affine は軽微な紙面伸縮だけ許容し、強い剪断・異方縮尺を拒否。
    """
    if m.model == "homography":
        return True
    g = linear_transform_metrics(m.H)
    return (
        np.isfinite(g["condition_number"])
        and g["condition_number"] <= max_condition
        and g["orthogonality"] >= min_orthogonality
        and g["determinant"] > 0
    )


def _homography_sanity(H, src_w, src_h, dst_w, dst_h):
    if H is None or not np.isfinite(H).all():
        return False

    corners = np.array(
        [[[0, 0]], [[src_w, 0]], [[src_w, src_h]], [[0, src_h]]],
        dtype=np.float32,
    )
    try:
        q = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
    except cv2.error:
        return False

    if not np.isfinite(q).all():
        return False

    contour = q.astype(np.float32).reshape(-1, 1, 2)
    area = abs(float(cv2.contourArea(contour)))
    dst_area = max(1.0, dst_w * dst_h)
    area_ratio = area / dst_area

    # PDF主地図がモザイク全体の0.5%未満 / 200%超になる変換は不自然
    if not (0.005 <= area_ratio <= 2.0):
        return False

    # 変換中心がモザイク周辺にあるか
    center = np.array([[[src_w / 2, src_h / 2]]], dtype=np.float32)
    c = cv2.perspectiveTransform(center, H)[0, 0]
    margin_x = dst_w * 0.25
    margin_y = dst_h * 0.25
    if not (-margin_x <= c[0] <= dst_w + margin_x):
        return False
    if not (-margin_y <= c[1] <= dst_h + margin_y):
        return False

    # 極端な細長い四角形を除外
    edges = [
        np.linalg.norm(q[(i + 1) % 4] - q[i])
        for i in range(4)
    ]
    if min(edges) < 5:
        return False
    if max(edges) / min(edges) > 15:
        return False

    return True


def _evaluate_model(
    model_name,
    Hsmall,
    mask,
    src_small,
    dst_small,
    sa,
    sb,
    good_count,
    mos,
    src_original_shape,
):
    if Hsmall is None or mask is None:
        return None

    Sa = np.array([[sa, 0, 0], [0, sa, 0], [0, 0, 1]], float)
    Sb = np.array([[sb, 0, 0], [0, sb, 0], [0, 0, 1]], float)
    H = np.linalg.inv(Sb) @ Hsmall @ Sa

    src = src_small.reshape(-1, 2) / sa
    dst = dst_small.reshape(-1, 2) / sb
    mk = mask.ravel().astype(bool)
    nin = int(mk.sum())
    if nin == 0:
        return None

    pred = cv2.perspectiveTransform(
        src.reshape(-1, 1, 2).astype(np.float32), H
    ).reshape(-1, 2)
    err = np.linalg.norm(pred - dst, axis=1)
    med = float(np.median(err[mk]))

    src_h, src_w = src_original_shape[:2]
    dst_h, dst_w = mos.img.shape[:2]

    sx, sy, grid = _coverage_metrics(src[mk], src_w, src_h)
    dx, dy, _ = _coverage_metrics(dst[mk], dst_w, dst_h)

    sane = _homography_sanity(H, src_w, src_h, dst_w, dst_h)

    return {
        "model": model_name,
        "good": good_count,
        "inliers": nin,
        "ratio": nin / max(1, good_count),
        "mederr": med,
        "src_span_x": sx,
        "src_span_y": sy,
        "src_grid_coverage": grid,
        "dst_span_x": dx,
        "dst_span_y": dy,
        "transform_sane": sane,
        "H": H,
        "src": src,
        "dst": dst,
        "mask": mask.ravel(),
    }


def _fit_models(src_small, dst_small, sa, sb, good_count, mos, src_shape):
    """
    変換モデルは自由度の低い順に評価:
      1. similarity (rotation + uniform scale + translation)
      2. affine
      3. homography

    PDF地図自体に局所歪みがない前提では similarity / affine を優先。
    """
    models = []

    # 1) Similarity (OpenCV: estimateAffinePartial2D)
    S, smask = cv2.estimateAffinePartial2D(
        src_small.reshape(-1, 2),
        dst_small.reshape(-1, 2),
        method=cv2.RANSAC,
        ransacReprojThreshold=5.0,
        maxIters=12000,
        confidence=.999,
        refineIters=25,
    )
    if S is not None:
        Hsim = np.vstack([S, [0, 0, 1]])
        ev = _evaluate_model(
            "similarity", Hsim, smask, src_small, dst_small,
            sa, sb, good_count, mos, src_shape
        )
        if ev:
            models.append(ev)

    # 2) Affine
    A, amask = cv2.estimateAffine2D(
        src_small.reshape(-1, 2),
        dst_small.reshape(-1, 2),
        method=cv2.RANSAC,
        ransacReprojThreshold=5.0,
        maxIters=12000,
        confidence=.999,
        refineIters=25,
    )
    if A is not None:
        Haff = np.vstack([A, [0, 0, 1]])
        ev = _evaluate_model(
            "affine", Haff, amask, src_small, dst_small,
            sa, sb, good_count, mos, src_shape
        )
        if ev:
            models.append(ev)

    # 3) Homography: 最終候補
    Hh, hmask = cv2.findHomography(
        src_small,
        dst_small,
        cv2.RANSAC,
        5.0,
        maxIters=18000,
        confidence=.999,
    )
    if Hh is not None:
        ev = _evaluate_model(
            "homography", Hh, hmask, src_small, dst_small,
            sa, sb, good_count, mos, src_shape
        )
        if ev:
            models.append(ev)

    return models


def global_edge_agreement(pdf_img, mosaic_img, H):
    """
    局所特徴点だけでなく画像全体の線構造が一致するかを評価する。
    正しい位置合わせならPDFの道路・水路等のedgeが、
    地理院側edgeの近傍へ多数落ちる。

    戻り値:
      agreement 0..1（大きいほど良い）
    """
    if H is None:
        return 0.0

    pvars = preprocess_pdf_variants(pdf_img)
    gvars = preprocess_gsi_variants(mosaic_img)
    pe = pvars["edge"]
    ge = gvars["edge"]

    # 地理院edgeまでの距離変換
    inv = (ge == 0).astype(np.uint8)
    dist = cv2.distanceTransform(inv, cv2.DIST_L2, 3)

    warped = cv2.warpPerspective(
        pe,
        H,
        (mosaic_img.shape[1], mosaic_img.shape[0]),
        flags=cv2.INTER_NEAREST,
        borderValue=0,
    )
    ys, xs = np.where(warped > 0)
    if len(xs) < 100:
        return 0.0

    # 3px以内のedge一致率。大量ならサンプリング。
    if len(xs) > 120000:
        ids = np.linspace(0, len(xs)-1, 120000).astype(int)
        xs = xs[ids]
        ys = ys[ids]

    d = dist[ys, xs]
    return float(np.mean(d <= 3.0))


def score(m):
    """
    軽量スクリーニング用スコア。
    global_edge_agreement はここでは使わない。

    まず特徴点/RANSAC由来の軽量指標だけで候補を順位付けし、
    上位候補に対してのみ後段で global_edge_agreement を計算する。
    """
    if m.H is None or not m.transform_sane:
        return -1e9

    if m.model in ("similarity", "affine") and not low_dof_geometry_sane(m):
        return -1e9

    if m.inliers < 6:
        return -1e9
    if m.src_span_x < 0.12 or m.src_span_y < 0.12:
        return -1e9
    if m.src_grid_coverage < 0.125:
        return -1e9
    if m.dst_span_x < 0.10 or m.dst_span_y < 0.10:
        return -1e9

    model_bonus = {
        "similarity": 34.0,
        "affine": 18.0,
        "homography": 0.0,
    }.get(m.model, 0.0)

    roi_bonus = {
        0.60: 10.0,
        0.70: 14.0,
        0.80: 6.0,
        0.50: 4.0,
    }.get(round(float(m.roi_fraction), 2), 0.0)

    return (
        9.0 * m.inliers
        + 45.0 * m.ratio
        + 42.0 * m.src_span_x
        + 42.0 * m.src_span_y
        + 75.0 * m.src_grid_coverage
        + 14.0 * m.dst_span_x
        + 14.0 * m.dst_span_y
        - 2.0 * m.mederr
        + model_bonus
        + roi_bonus
    )


def final_score(m):
    """
    global評価後の最終スコア。

    神川町で見られた
      inliers多数 + ratio低値 + global中高値
    の偽陽性を防ぐため、ratio<0.15は最終候補から除外する。
    """
    base = score(m)
    if base <= -1e8:
        return base
    if m.ratio < 0.15:
        return -1e9
    if m.model in ("similarity", "affine") and not low_dof_geometry_sane(m):
        return -1e9

    ga = max(0.0, float(m.global_agreement))
    return base + 260.0 * ga


def match_candidates(pdf_img, mos, roi_fractions=(0.60, 0.70, 0.80)):
    """
    PDF位置合わせは図郭外周を使わず、中央ROIを複数試す。

    各ROIで:
      edge / mix / gray
      × similarity / affine / homography
    を評価し、最良モデルを返す。

    重要:
    最終Hは「元のmatch raster全体 -> GSI mosaic」へ合成して返すため、
    ROIで推定しても後続の代表点・Polygon変換をそのまま利用できる。
    """
    gsi_variants = preprocess_gsi_variants(mos.img)
    det_name, det, norm, ratio_test = detector()
    all_results = []

    full_h, full_w = pdf_img.shape[:2]

    ordered_rois = sorted(roi_fractions, key=lambda f: (abs(float(f)-0.70), abs(float(f)-0.60)))
    for roi_fraction in ordered_rois:
        crop_img, roi_rect = central_roi(pdf_img, roi_fraction)
        pdf_variants = preprocess_pdf_variants(crop_img)
        T_full_to_crop = roi_transform_full_to_crop(roi_rect)
        x0, y0, x1, y1 = roi_rect

        for mode in ("gray", "edge", "mix"):
            a, sa = resize_max(pdf_variants[mode], 2600)
            b, sb = resize_max(gsi_variants[mode], 3200)

            kp1, d1 = det.detectAndCompute(a, None)
            kp2, d2 = det.detectAndCompute(b, None)
            if d1 is None or d2 is None or len(kp1) < 10 or len(kp2) < 10:
                continue

            bf = cv2.BFMatcher(norm)
            pairs12 = bf.knnMatch(d1, d2, k=2)
            ratio12 = [m for m, n in pairs12 if m.distance < ratio_test * n.distance]

            pairs21 = bf.knnMatch(d2, d1, k=2)
            ratio21 = [m for m, n in pairs21 if m.distance < ratio_test * n.distance]
            reverse = {(m.trainIdx, m.queryIdx) for m in ratio21}
            mutual = [m for m in ratio12 if (m.queryIdx, m.trainIdx) in reverse]

            good = mutual if len(mutual) >= 10 else ratio12
            if len(good) < 8:
                continue

            s1 = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            s2 = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

            fitted = _fit_models(
                s1, s2, sa, sb, len(good), mos, crop_img.shape
            )
            match_mode = mode + ("_mutual" if good is mutual else "_ratio")

            for ev in fitted:
                # ev["H"] は crop original -> mosaic。
                # full -> crop の平行移動を右から合成。
                H_full = ev["H"] @ T_full_to_crop

                # src点をcrop座標からfull raster座標に戻す。
                src_full = np.asarray(ev["src"], dtype=float).copy()
                src_full[:, 0] += x0
                src_full[:, 1] += y0

                mk = np.asarray(ev["mask"]).ravel().astype(bool)
                sx, sy, grid = _coverage_metrics(src_full[mk], full_w, full_h)

                # full raster に対する変換健全性を再評価。
                sane = _homography_sanity(
                    H_full,
                    full_w,
                    full_h,
                    mos.img.shape[1],
                    mos.img.shape[0],
                )

                # global_edge_agreement はここでは計算しない。
                # 軽量スクリーニング後、上位候補だけ遅延評価する。
                ga = -1.0

                all_results.append(
                    Match(
                        mos.layer,
                        mos.z,
                        float(roi_fraction),
                        match_mode,
                        ev["model"],
                        ev["good"],
                        ev["inliers"],
                        ev["ratio"],
                        ev["mederr"],
                        sx,
                        sy,
                        grid,
                        ev["dst_span_x"],
                        ev["dst_span_y"],
                        ga,
                        sane,
                        H_full,
                        mos,
                        src_full,
                        ev["dst"],
                        ev["mask"],
                    )
                )

    # 常に list[Match] を返す。
    # strong candidate による早期終了は auto_match() 側で、
    # global評価後にだけ判定する。
    return all_results



def subdivide_bbox(bbox, n=5, overlap=0.10):
    """
    WEST,SOUTH,EAST,NORTH を n x n に分割。
    各セルに少しoverlapを持たせる。
    """
    west, south, east, north = map(float, bbox)
    dx = (east - west) / n
    dy = (north - south) / n
    cells = []

    for iy in range(n):
        for ix in range(n):
            w = west + ix * dx
            e = west + (ix + 1) * dx
            s = south + iy * dy
            nrt = south + (iy + 1) * dy

            px = dx * overlap
            py = dy * overlap
            cells.append((
                max(west, w - px),
                max(south, s - py),
                min(east, e + px),
                min(north, nrt + py),
            ))
    return cells


def expand_bbox(bbox, ratio, bounds=None):
    west, south, east, north = map(float, bbox)
    dx = east - west
    dy = north - south
    out = (
        west - dx * ratio,
        south - dy * ratio,
        east + dx * ratio,
        north + dy * ratio,
    )
    if bounds is None:
        return out

    bw, bs, be, bn = map(float, bounds)
    return (
        max(bw, out[0]),
        max(bs, out[1]),
        min(be, out[2]),
        min(bn, out[3]),
    )


def bbox_iou(a, b):
    aw, aS, ae, an = a
    bw, bS, be, bn = b
    iw = max(0.0, min(ae, be) - max(aw, bw))
    ih = max(0.0, min(an, bn) - max(aS, bS))
    inter = iw * ih
    if inter <= 0:
        return 0.0
    aa = max(1e-12, (ae-aw)*(an-aS))
    ba = max(1e-12, (be-bw)*(bn-bS))
    return inter / (aa + ba - inter)


def coarse_candidate_score(m):
    """
    粗探索専用スコア。

    粗探索の目的は「だいたいどこか」を見つけることなので、
    自由度の高い変換で無理に合わせない。
    PDF地図無歪み前提から Similarity を最優先し、
    Affine は軽微な変形だけ許可する。
    Homography は粗探索では使用しない。
    """
    if m.H is None or not m.transform_sane:
        return -1e9
    if m.model == "homography":
        return -1e9
    if not low_dof_geometry_sane(m, max_condition=1.45, min_orthogonality=0.88):
        return -1e9
    if m.inliers < 5:
        return -1e9
    if m.ratio < 0.12:
        return -1e9

    roi_span_x = m.src_span_x / max(0.01, m.roi_fraction)
    roi_span_y = m.src_span_y / max(0.01, m.roi_fraction)

    if roi_span_x < 0.18 or roi_span_y < 0.18:
        return -1e9

    model_bonus = 35.0 if m.model == "similarity" else 10.0

    return (
        8.0 * m.inliers
        + 90.0 * m.ratio
        + 35.0 * roi_span_x
        + 35.0 * roi_span_y
        + 45.0 * m.src_grid_coverage
        + 12.0 * m.dst_span_x
        + 12.0 * m.dst_span_y
        - 2.0 * m.mederr
        + model_bonus
    )


def coarse_locate_pdf(
    raster,
    municipality_bbox,
    cache,
    layers=("pale", "std"),
    coarse_zooms=(11, 12),
    grid_n=5,
    top_k=3,
    expand_ratio=0.35,
):
    """
    自治体全域から「このPDF図郭がどの局所領域に相当するか」を粗探索する。

    1) 自治体bboxを grid_n x grid_n に分割
    2) 各セルを低zoomでモザイク化
    3) PDF中央ROI 70%を gray/edge で Similarity/Affine中心に軽量照合
    4) 上位セルだけを精密探索用bboxとして返す

    Homographyは候補には残るが、スコア上は優先しない。
    """
    cells = subdivide_bbox(municipality_bbox, n=grid_n, overlap=0.12)
    coarse_hits = []
    loose_hits = []

    print(
        f"=== 4B 粗位置探索 ===\n"
        f"  municipality bbox: {','.join(f'{v:.6f}' for v in municipality_bbox)}\n"
        f"  grid={grid_n}x{grid_n}, zooms={','.join(map(str, coarse_zooms))}"
    )

    for ci, cell in enumerate(cells, 1):
        best_for_cell = None

        for layer in layers:
            for z in coarse_zooms:
                try:
                    mos = build_mosaic(layer, z, cell, cache / "coarse")
                    candidates = match_candidates(
                        raster.img,
                        mos,
                        roi_fractions=(0.70,),
                    )
                except Exception as e:
                    print(f"  coarse cell {ci}/{len(cells)} {layer} z={z}: error={e}")
                    continue

                if not candidates:
                    continue

                # loose候補: 万一strict条件を満たすセルがない場合の局所fallback用。
                loose = [
                    m for m in candidates
                    if m.H is not None
                    and m.transform_sane
                    and m.model in ("similarity", "affine")
                    and low_dof_geometry_sane(m, max_condition=1.60, min_orthogonality=0.80)
                    and m.inliers >= 4
                ]
                if loose:
                    loose_best = max(
                        loose,
                        key=lambda m: (
                            6*m.inliers + 50*m.ratio
                            + 20*(m.src_span_x/max(.01,m.roi_fraction))
                            + 20*(m.src_span_y/max(.01,m.roi_fraction))
                            - m.mederr
                        )
                    )
                    loose_hits.append({
                        "bbox": cell,
                        "match": loose_best,
                        "layer": layer,
                        "zoom": z,
                    })

                # strict coarse候補
                cands = sorted(candidates, key=coarse_candidate_score, reverse=True)
                if not cands or coarse_candidate_score(cands[0]) <= -1e8:
                    continue

                m = cands[0]
                if best_for_cell is None or coarse_candidate_score(m) > coarse_candidate_score(best_for_cell["match"]):
                    best_for_cell = {
                        "bbox": cell,
                        "match": m,
                        "layer": layer,
                        "zoom": z,
                    }

        if best_for_cell is not None:
            coarse_hits.append(best_for_cell)

    coarse_hits.sort(
        key=lambda x: coarse_candidate_score(x["match"]),
        reverse=True,
    )

    # 近接/重複セルを抑制して top_k
    selected = []
    for hit in coarse_hits:
        if any(bbox_iou(hit["bbox"], s["bbox"]) > 0.55 for s in selected):
            continue
        selected.append(hit)
        if len(selected) >= top_k:
            break

    # strict候補が無い場合も自治体全域へ戻さない。
    # 歪みの少ないloose候補から局所bboxを選ぶ。
    if not selected and loose_hits:
        loose_hits.sort(
            key=lambda x: (
                6*x["match"].inliers
                + 50*x["match"].ratio
                - x["match"].mederr
            ),
            reverse=True,
        )
        for hit in loose_hits:
            if any(bbox_iou(hit["bbox"], s["bbox"]) > 0.55 for s in selected):
                continue
            selected.append(hit)
            if len(selected) >= top_k:
                break
        print("  strict coarse candidateなし -> geometry-safe loose cellsを使用")

    print(f"  coarse valid cells: {len(coarse_hits)}")
    print(f"  selected local bboxes: {len(selected)}")

    out = []
    for i, hit in enumerate(selected, 1):
        m = hit["match"]
        expanded = expand_bbox(hit["bbox"], expand_ratio, bounds=municipality_bbox)
        print(
            f"    [{i}] layer={hit['layer']} z={hit['zoom']} "
            f"inliers={m.inliers} ratio={m.ratio:.3f} "
            f"err={m.mederr:.1f}px score={coarse_candidate_score(m):.1f}\n"
            f"        bbox={','.join(f'{v:.6f}' for v in expanded)}"
        )
        out.append({
            "bbox": expanded,
            "source_bbox": hit["bbox"],
            "layer": hit["layer"],
            "zoom": hit["zoom"],
            "score": coarse_candidate_score(m),
            "inliers": m.inliers,
            "ratio": m.ratio,
            "mederr": m.mederr,
            "model": m.model,
        })

    return out

def evaluate_global_for_top_candidates(candidates, pdf_img, top_k=5):
    """
    軽量スコア上位 top_k のみ global_edge_agreement を計算する。
    同一変換に近い候補が多数ある場合も最大 top_k 件に制限。
    """
    valid = [m for m in candidates if score(m) > -1e8 and m.H is not None]
    valid.sort(key=score, reverse=True)

    selected = valid[:max(1, int(top_k))]

    for m in selected:
        if m.global_agreement < 0:
            m.global_agreement = global_edge_agreement(
                pdf_img,
                m.mosaic.img,
                m.H,
            )

    # 未評価候補は global=-1 のまま。
    # 最終ランキングでは評価済み候補のみを優先する。
    selected.sort(key=final_score, reverse=True)
    return selected


def candidate_is_strong(m):
    """
    早期終了用の厳格判定。
    神川町の誤対応（global高値だがratio≈0.07）を排除する。
    """
    if m is None or m.global_agreement < 0:
        return False

    roi_span_x = m.src_span_x / max(0.01, m.roi_fraction)
    roi_span_y = m.src_span_y / max(0.01, m.roi_fraction)

    # globalが高くても ratio が極端に低い候補は採用しない
    if m.ratio < 0.15:
        return False

    if m.model in ("similarity", "affine"):
        return (
            m.inliers >= 8
            and m.ratio >= 0.18
            and m.mederr <= 4.0
            and roi_span_x >= 0.30
            and roi_span_y >= 0.30
            and m.src_grid_coverage >= 0.1875
            and m.dst_span_x >= 0.15
            and m.dst_span_y >= 0.15
            and m.global_agreement >= 0.70
            and m.transform_sane
        )

    return (
        m.inliers >= 12
        and m.ratio >= 0.25
        and m.mederr <= 4.0
        and roi_span_x >= 0.40
        and roi_span_y >= 0.40
        and m.src_grid_coverage >= 0.25
        and m.global_agreement >= 0.75
        and m.transform_sane
    )


def auto_match(
    raster,
    layers,
    zooms,
    bbox,
    cache,
    roi_fractions=(0.60, 0.70, 0.80),
    global_top_k=4,
    early_stop=True,
):
    """
    遅延 global 評価版。

    各 layer/zoom:
      1) ROI/mode/model 全候補を軽量評価
      2) 軽量スコア上位 global_top_k 件だけ global_edge_agreement
      3) 強い候補なら探索を早期終了

    これにより全候補で warpPerspective + distanceTransform を行う
    旧方式より大幅な高速化を狙う。
    """
    all_evaluated = []
    all_lightweight = []

    for layer in layers:
        for z in zooms:
            print(f"  {layer} z={z}")
            mos = build_mosaic(layer, z, bbox, cache)
            print(f"    mosaic {mos.img.shape[1]}x{mos.img.shape[0]}")

            # match() はこのモザイク内で最良1件だけ返す旧構造だったため、
            # delayed版では match_candidates() を使う。
            candidates = match_candidates(
                raster.img,
                mos,
                roi_fractions=roi_fractions,
            )
            all_lightweight.extend(candidates)

            valid = [m for m in candidates if score(m) > -1e8]
            valid.sort(key=score, reverse=True)

            if valid:
                b0 = valid[0]
                print(
                    f"    lightweight best: roi={b0.roi_fraction:.2f} "
                    f"mode={b0.mode} model={b0.model} "
                    f"inliers={b0.inliers} ratio={b0.ratio:.3f} "
                    f"err={b0.mederr:.1f}px light_score={score(b0):.1f}"
                )
            else:
                print("    lightweight candidates: none")
                continue

            evaluated = evaluate_global_for_top_candidates(
                valid,
                raster.img,
                top_k=global_top_k,
            )
            all_evaluated.extend(evaluated)

            if evaluated:
                best = evaluated[0]
                print(
                    f"    global best: roi={best.roi_fraction:.2f} "
                    f"mode={best.mode} model={best.model} "
                    f"inliers={best.inliers} ratio={best.ratio:.3f} "
                    f"err={best.mederr:.1f}px "
                    f"global={best.global_agreement:.3f} "
                    f"final_score={final_score(best):.1f}"
                )

                if early_stop and candidate_is_strong(best):
                    print("    strong candidate -> early stop")
                    return sorted(
                        all_evaluated,
                        key=final_score,
                        reverse=True,
                    ), all_lightweight

    all_evaluated.sort(key=final_score, reverse=True)
    return all_evaluated, all_lightweight


def save_matches(best,pdf_img,path):
    if best.H is None or best.src is None:
        return
    mk=best.mask.astype(bool)
    src,dst=best.src[mk],best.dst[mk]
    if len(src)>120:
        ids=np.linspace(0,len(src)-1,120).astype(int)
        src,dst=src[ids],dst[ids]
    A=pdf_img; B=best.mosaic.img
    h=max(A.shape[0],B.shape[0])
    canvas=np.full((h,A.shape[1]+B.shape[1],3),255,np.uint8)
    canvas[:A.shape[0],:A.shape[1]]=A
    canvas[:B.shape[0],A.shape[1]:]=B
    for p,q in zip(src,dst):
        p1=(int(p[0]),int(p[1])); p2=(int(q[0]+A.shape[1]),int(q[1]))
        cv2.circle(canvas,p1,3,(0,255,0),-1)
        cv2.circle(canvas,p2,3,(0,255,0),-1)
        cv2.line(canvas,p1,p2,(0,180,0),1)
    cv2.imwrite(str(path),canvas)


def save_overlay(best,pdf_img,path):
    mos=best.mosaic.img
    warp=cv2.warpPerspective(pdf_img,best.H,(mos.shape[1],mos.shape[0]),borderValue=(255,255,255))
    mask=cv2.cvtColor(warp,cv2.COLOR_BGR2GRAY)<245
    out=mos.copy()
    out[mask]=cv2.addWeighted(mos[mask],.45,warp[mask],.55,0)
    cv2.imwrite(str(path),out)


def extract_labels(page, bbox, table=None):
    """
    地図上の青い遺跡番号ラベルを抽出する。

    旧版は municipality_code=68/74 を固定していたため、
    神川町(57)などで一覧表site_uidと一致せず、Stage 8 merge後に
    pdf_x/pdf_y/lon/latが全件空欄になる問題があった。

    新版:
      - 一覧表のmunicipality_codeが1種類なら、そのコードを使用。
      - 複数コードなら、site_no存在集合と出現順で割当。
      - tableが無い場合のみ旧Gyoda互換へフォールバック。
    """
    x0,y0,x1,y1=bbox
    rows=[]
    seq=0

    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines",[]):
            for s in line.get("spans",[]):
                t=s["text"].strip().translate(FW)
                if not re.fullmatch(r"\d{1,3}",t):
                    continue

                bx=s["bbox"]
                x=(bx[0]+bx[2])/2
                y=(bx[1]+bx[3])/2

                if not(x0<=x<=x1 and y0<=y<=y1):
                    continue

                if abs(float(s["size"])-8)>0.7:
                    continue

                rows.append({
                    "sequence":seq,
                    "label_no":int(t),
                    "pdf_x":float(x),
                    "pdf_y":float(y),
                })
                seq+=1

    if not rows:
        return pd.DataFrame(columns=[
            "sequence","label_no","municipality_code",
            "site_no","site_uid","pdf_x","pdf_y"
        ])

    if table is None or table.empty or "municipality_code" not in table.columns:
        vals=[r["label_no"] for r in rows]
        start74=None
        target=set(range(1,27))
        for i in range(max(0,len(vals)-25)):
            if set(vals[i:i+26])==target and len(set(vals[i:i+26]))==26:
                start74=i
                break
        for i,r in enumerate(rows):
            city="74" if start74 is not None and start74<=i<start74+26 else "68"
            r["municipality_code"]=city
            r["site_no"]=f"{r['label_no']:03d}"
            r["site_uid"]=f"{city}-{r['site_no']}"
        return pd.DataFrame(rows)

    t=table.copy()
    t["municipality_code"]=(
        t["municipality_code"].astype(str).str.replace(r"\.0$","",regex=True)
    )
    t["site_no_int"]=pd.to_numeric(t["site_no"],errors="coerce")

    codes=[
        c for c in t["municipality_code"].dropna().unique().tolist()
        if c and c.lower()!="nan"
    ]
    codes=sorted(codes)
    if not codes:
        raise RuntimeError("一覧表からmunicipality_codeを取得できません。")

    counts=t["municipality_code"].value_counts()
    primary=str(counts.index[0])

    code_to_nos={
        c:set(
            int(v) for v in
            t.loc[t["municipality_code"]==c,"site_no_int"].dropna().tolist()
        )
        for c in codes
    }

    if len(codes)==1:
        city=codes[0]
        for r in rows:
            r["municipality_code"]=city
            r["site_no"]=f"{r['label_no']:03d}"
            r["site_uid"]=f"{city}-{r['site_no']}"
        return pd.DataFrame(rows)

    # 複数コード: 一意に決まる番号はまず確定。
    for r in rows:
        n=r["label_no"]
        candidates=[c for c in codes if n in code_to_nos.get(c,set())]
        r["_candidates"]=candidates
        r["municipality_code"]=candidates[0] if len(candidates)==1 else None

    # 連番ブロックを持つsecondary codeを出現順から推定。
    vals=[r["label_no"] for r in rows]
    for c in codes:
        if c==primary:
            continue
        nums=code_to_nos.get(c,set())
        if not nums:
            continue
        maxn=max(nums)
        expected=set(range(1,maxn+1))
        if nums != expected:
            continue

        best_start=None
        best_score=-1
        for i in range(0,max(0,len(vals)-maxn+1)):
            window=vals[i:i+maxn]
            score=len(set(window)&expected)
            if score>best_score:
                best_score=score
                best_start=i

        if best_start is not None and best_score >= max(3,int(maxn*0.90)):
            for i in range(best_start,best_start+maxn):
                if 0<=i<len(rows) and c in rows[i].get("_candidates",[]):
                    rows[i]["municipality_code"]=c

    for r in rows:
        if r["municipality_code"] is None:
            cand=r.get("_candidates",[])
            if primary in cand or not cand:
                r["municipality_code"]=primary
            else:
                r["municipality_code"]=cand[0]

        r["site_no"]=f"{r['label_no']:03d}"
        r["site_uid"]=f"{r['municipality_code']}-{r['site_no']}"
        r.pop("_candidates",None)

    return pd.DataFrame(rows)


def drawing_lines(page):
    lines=[]
    for d in page.get_drawings():
        if not is_red(d.get("color")):
            continue
        cur=[]
        def flush():
            nonlocal cur
            if len(cur)>=2:
                lines.append(LineString(cur))
            cur=[]
        for it in d["items"]:
            if it[0]=="l":
                _,p0,p1=it
                if not cur: cur=[(p0.x,p0.y),(p1.x,p1.y)]
                elif cur[-1]==(p0.x,p0.y): cur.append((p1.x,p1.y))
                else: flush(); cur=[(p0.x,p0.y),(p1.x,p1.y)]
            else:
                flush()
        flush()
    return lines


def pdfxy_to_lonlat(x,y,r,best):
    rx,ry=pdf_to_raster(x,y,r)
    q=cv2.perspectiveTransform(np.array([[[rx,ry]]],np.float32),best.H)[0,0]
    return best.mosaic.pixel_to_lonlat(float(q[0]),float(q[1]))


def transform_geom(geom,r,best):
    def f(x,y,z=None):
        xa=np.asarray(x,float); ya=np.asarray(y,float)
        flat=np.column_stack([xa.ravel(),ya.ravel()])
        x0,y0,_,_=r.bbox
        rx=(flat[:,0]-x0)*r.sx
        ry=(flat[:,1]-y0)*r.sy
        q=cv2.perspectiveTransform(np.column_stack([rx,ry]).astype(np.float32).reshape(-1,1,2),best.H).reshape(-1,2)
        ll=[best.mosaic.pixel_to_lonlat(float(a),float(b)) for a,b in q]
        return (
            np.array([v[0] for v in ll]).reshape(xa.shape),
            np.array([v[1] for v in ll]).reshape(ya.shape)
        )
    return shp_transform(f,geom)



# ============================================================
# Verified table profile: Gyoda 2023 archaeological map
# ============================================================

GYODA_TYPE_FIELDS = [
    "旧石器", "貝塚", "集落跡", "古墳群", "古墳", "横穴", "窯跡",
    "祭祀", "経塚", "墓", "寺院跡", "城跡", "石造遺物", "散布地", "その他",
]
GYODA_PERIOD_FIELDS = [
    "旧石器", "縄文", "弥生", "古墳", "奈良", "平安", "鎌倉",
    "南北朝", "室町", "戦国", "江戸", "不明",
]

# 市町村番号68の右側表。PDF上の実測列中心。
GYODA_TYPE_X_68 = [
    2861.8, 2874.4, 2886.9, 2899.5, 2912.1,
    2924.6, 2937.2, 2949.7, 2962.2, 2974.8,
    2987.4, 2999.9, 3012.5, 3025.0, 3037.6,
]
GYODA_PERIOD_X_68 = [
    3080.0, 3092.6, 3105.1, 3117.6, 3130.2, 3142.7,
    3155.3, 3167.8, 3180.4, 3192.9, 3205.5, 3218.1,
]

# 市町村番号74の左下表。
# PDF raw character coordinatesと○印中心を照合して確定。
GYODA_TYPE_X_74 = [
    213.3, 232.1, 251.1, 270.0, 288.9,
    307.7, 326.6, 345.4, 364.3, 383.4,
    402.2, 421.0, 439.9, 458.9, 477.8,
]
GYODA_PERIOD_X_74 = [
    541.4, 560.3, 579.1, 598.1, 616.9, 635.9,
    654.8, 673.7, 692.5, 711.4, 730.3, 749.2,
]

# Backward-compatible aliases. Older experimental profile functions below may
# reference these names; keeping aliases prevents NameError even though the
# verified profile is the only Gyoda extractor used by the router.
TYPE_FIELDS = GYODA_TYPE_FIELDS
PERIOD_FIELDS = GYODA_PERIOD_FIELDS
TYPE_X_68 = GYODA_TYPE_X_68
PERIOD_X_68 = GYODA_PERIOD_X_68


def _gyoda_table_segments(rows):
    """
    68表はPDF上で上下2ブロックに分割されている。
    大きなYギャップでセグメント化し、行境界が地図本体まで広がるのを防ぐ。
    """
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: r["yc"])
    ys = np.array([r["yc"] for r in rows], dtype=float)
    ds = np.diff(ys)
    usable = ds[(ds > 0) & (ds < 100)]
    median_step = float(np.median(usable)) if len(usable) else 10.0
    threshold = max(4.0 * median_step, 40.0)

    segments = []
    cur = [rows[0]]
    for prev, now in zip(rows, rows[1:]):
        if now["yc"] - prev["yc"] > threshold:
            segments.append(cur)
            cur = [now]
        else:
            cur.append(now)
    segments.append(cur)
    return segments


def _gyoda_extract_row_anchors(words):
    """
    行アンカーをPDFの実際の表構造から取得。

    重要:
    遺跡番号と遺跡名が同一wordになる行
      '０１６大稲荷１号墳'
    があるため、exact 3-digit word を要求しない。
    word先頭3桁を遺跡番号として読む。
    """
    anchors = []

    for cw in words:
        city = clean_text(cw["text"])
        if city not in {"68", "74"}:
            continue

        # 地図番号などを誤認しないよう、表の実座標帯に限定。
        if city == "68" and cw["x0"] < 2500:
            continue
        if city == "74" and cw["x0"] > 500:
            continue

        candidates = []
        for w in words:
            if w["x0"] <= cw["x0"]:
                continue
            if abs(w["yc"] - cw["yc"]) > 5.0:
                continue

            text = clean_text(w["text"])
            m = re.match(r"^(\d{3})(.*)$", text)
            if not m:
                continue

            no = m.group(1)
            remainder = m.group(2)
            candidates.append((w, no, remainder))

        if not candidates:
            continue

        sw, no, remainder = min(candidates, key=lambda x: x[0]["x0"])

        anchors.append({
            "municipality_code": city,
            "site_no": no,
            "site_uid": f"{city}-{no}",
            "yc": (cw["yc"] + sw["yc"]) / 2.0,
            "city_word": cw,
            "site_word": sw,
            "site_remainder": remainder,
        })

    # 同じIDを複数拾った場合は最初の表位置を採る。
    unique = {}
    for a in anchors:
        unique.setdefault((a["municipality_code"], a["site_no"]), a)

    return sorted(
        unique.values(),
        key=lambda a: (a["municipality_code"], a["yc"]),
    )


def _gyoda_hit_circle(marks, center, tolerance=5.5):
    return int(any(abs(float(mx) - float(center)) <= tolerance for mx in marks))


def extract_table_gyoda_verified(page, out_csv):
    """
    68_gyouda_city.pdf 用に実PDFで検証した一覧表抽出。

    検証条件:
      municipality 68 = 001..194 (194 rows)
      municipality 74 = 001..026 (26 rows)
      total = 220 unique rows

    OCRは使用せず、PyMuPDF内部text/word座標のみ利用する。
    """
    words = get_words(page)
    anchors = _gyoda_extract_row_anchors(words)

    by_city = {
        "68": [a for a in anchors if a["municipality_code"] == "68"],
        "74": [a for a in anchors if a["municipality_code"] == "74"],
    }

    records = []

    profiles = {
        "68": {
            "table_x": (2795.0, 3230.0),
            "name_x": (2810.0, 2858.0),
            "remarks_x": (3041.0, 3076.0),
            "type_x": GYODA_TYPE_X_68,
            "period_x": GYODA_PERIOD_X_68,
        },
        "74": {
            "table_x": (115.0, 760.0),
            "name_x": (139.0, 200.0),
            "remarks_x": (490.0, 525.0),
            "type_x": GYODA_TYPE_X_74,
            "period_x": GYODA_PERIOD_X_74,
        },
    }

    for city in ("68", "74"):
        prof = profiles[city]

        for segment in _gyoda_table_segments(by_city[city]):
            for i, a in enumerate(segment):
                yc = a["yc"]
                prev_y = segment[i - 1]["yc"] if i > 0 else yc - 10.0
                next_y = segment[i + 1]["yc"] if i + 1 < len(segment) else yc + 10.0
                ylo = (prev_y + yc) / 2.0
                yhi = (yc + next_y) / 2.0

                # 同じyでも地図中の数字等が混ざらないよう、表のX範囲にも限定。
                tx0, tx1 = prof["table_x"]
                row_words = [
                    w for w in words
                    if ylo <= w["yc"] < yhi
                    and tx0 <= w["xc"] <= tx1
                ]

                # 名称
                nx0, nx1 = prof["name_x"]
                name_parts = []
                for w in sorted(row_words, key=lambda w: (w["yc"], w["x0"])):
                    if not (nx0 <= w["xc"] < nx1):
                        continue
                    if w["text"].strip() == "○":
                        continue

                    part = clean_text(w["text"])

                    # 遺跡番号wordは、名称より上下位置がわずかにずれる行があり、
                    # y順ソートでは名称の途中へ入り込む場合がある。
                    # そのため「結合後の先頭」ではなく各word単位で番号を除去する。
                    if part.startswith(a["site_no"]):
                        part = part[len(a["site_no"]):]

                    if part:
                        name_parts.append(part)

                site_name = "".join(name_parts)

                # 備考
                rx0, rx1 = prof["remarks_x"]
                remark_parts = [
                    clean_text(w["text"])
                    for w in sorted(row_words, key=lambda w: (w["yc"], w["x0"]))
                    if rx0 <= w["xc"] < rx1
                    and w["text"].strip() != "○"
                ]
                remarks = "".join(remark_parts)

                marks = [
                    w["xc"]
                    for w in row_words
                    if w["text"].strip() == "○"
                ]

                rec = {
                    "municipality_code": city,
                    "site_no": a["site_no"],
                    "site_uid": a["site_uid"],
                    "site_name": site_name,
                    "remarks": remarks,
                    "record_status": "active",
                }

                raw_text = "|".join(
                    clean_text(w["text"])
                    for w in sorted(row_words, key=lambda w: (w["yc"], w["x0"]))
                    if clean_text(w["text"])
                )
                rec["raw_row_text"] = raw_text

                if "欠番" in raw_text:
                    rec["record_status"] = "missing_number"
                elif "統合" in raw_text:
                    rec["record_status"] = "merged"

                for field, cx in zip(GYODA_TYPE_FIELDS, prof["type_x"]):
                    rec[f"type_{field}"] = _gyoda_hit_circle(marks, cx)

                for field, cx in zip(GYODA_PERIOD_FIELDS, prof["period_x"]):
                    rec[f"period_{field}"] = _gyoda_hit_circle(marks, cx)

                records.append(rec)

    df = pd.DataFrame(records)

    if not df.empty:
        df = df.sort_values(
            ["municipality_code", "site_no"],
            kind="stable",
        ).reset_index(drop=True)

    # 強制QC: このprofileを使う以上、期待構造と一致しなければ成功扱いしない。
    qc = validate_gyoda_table(df)
    if not qc["pass"]:
        raise RuntimeError(
            "Gyoda table extraction QC failed: "
            + "; ".join(qc["errors"])
        )

    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    return df, qc


def validate_gyoda_table(df):
    errors = []

    if len(df) != 220:
        errors.append(f"row_count={len(df)} expected=220")

    if "site_uid" not in df.columns:
        errors.append("site_uid column missing")
        return {"pass": False, "errors": errors}

    if df["site_uid"].nunique() != 220:
        errors.append(
            f"unique_site_uid={df['site_uid'].nunique()} expected=220"
        )

    expected_68 = {f"68-{i:03d}" for i in range(1, 195)}
    expected_74 = {f"74-{i:03d}" for i in range(1, 27)}
    actual = set(df["site_uid"].astype(str))

    miss68 = sorted(expected_68 - actual)
    miss74 = sorted(expected_74 - actual)
    extra = sorted(actual - expected_68 - expected_74)

    if miss68:
        errors.append(f"missing 68 ids: {miss68[:12]}")
    if miss74:
        errors.append(f"missing 74 ids: {miss74[:12]}")
    if extra:
        errors.append(f"unexpected ids: {extra[:12]}")

    # PDF本文で確認できる代表的な遺跡名をspot check。
    expected_names = {
        "74-001": "十二天古墳",
        "74-002": "あたご山古墳",
        "74-003": "とやま古墳",
        "74-010": "南河原条里遺跡",
        "74-011": "南河原石塔婆",
        "74-020": "西新井遺跡",
        "68-016": "大稲荷1号墳",
        "68-017": "大稲荷2号墳",
        "68-019": "浅間塚古墳",
        "68-020": "愛宕神社古墳",
        "68-021": "虚空蔵山古墳（屋敷通西遺跡）",
        "68-022": "篭山古墳",
        "68-023": "小見真観寺古墳",
        "68-024": "文珠前遺跡",
        "68-025": "柳坪遺跡",
        "68-027": "池守遺跡",
        "68-029": "馬場裏遺跡",
        "68-186": "天神遺跡",
        "68-187": "中村東遺跡",
        "68-188": "船川遺跡",
        "68-189": "砂原遺跡",
        "68-190": "立野遺跡",
        "68-191": "行田市No.191遺跡",
        "68-192": "陣馬2遺跡",
        "68-193": "小針鎧塚古墳",
        "68-194": "片原通遺跡",
    }

    if len(df):
        name_map = dict(zip(df["site_uid"], df["site_name"]))
        for uid, expected in expected_names.items():
            actual_name = str(name_map.get(uid, ""))
            if actual_name != expected:
                errors.append(
                    f"name mismatch {uid}: {actual_name!r} != {expected!r}"
                )

    return {
        "pass": not errors,
        "errors": errors,
        "rows": int(len(df)),
        "unique_site_uid": int(df["site_uid"].nunique()) if "site_uid" in df else 0,
        "rows_68": int((df["municipality_code"] == "68").sum()) if "municipality_code" in df else 0,
        "rows_74": int((df["municipality_code"] == "74").sum()) if "municipality_code" in df else 0,
    }


def get_words(page) -> list[dict]:
    rows = []
    for w in page.get_text("words"):
        x0, y0, x1, y1, text, block_no, line_no, word_no = w
        rows.append({
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "xc": (x0+x1)/2, "yc": (y0+y1)/2,
            "text": text,
            "block": block_no, "line": line_no, "word": word_no
        })
    return rows


def extract_row_anchors(words: list[dict]) -> list[dict]:
    """
    「68」「001」のような市町村番号・遺跡番号を同一行から検出。
    """
    anchors = []
    by_y = sorted(words, key=lambda w: (w["yc"], w["x0"]))

    # 市町村番号候補
    city_words = []
    for w in by_y:
        t = clean_text(w["text"])
        if t in {"68", "74"}:
            city_words.append((w, t))

    for cw, city in city_words:
        candidates = []
        for w in words:
            t = clean_text(w["text"])
            if not re.fullmatch(r"\d{3}", t):
                continue
            if abs(w["yc"] - cw["yc"]) <= 4.5 and w["x0"] > cw["x0"]:
                candidates.append(w)
        if not candidates:
            continue
        sw = min(candidates, key=lambda w: w["x0"])
        anchors.append({
            "municipality_code": city,
            "site_no": clean_text(sw["text"]),
            "x_city": cw["xc"],
            "x_site": sw["xc"],
            "yc": (cw["yc"] + sw["yc"]) / 2,
            "city_word": cw,
            "site_word": sw,
        })

    # 重複排除
    uniq = {}
    for a in anchors:
        key = (a["municipality_code"], a["site_no"], round(a["yc"], 1))
        uniq[key] = a
    return sorted(uniq.values(), key=lambda a: (a["municipality_code"], a["yc"]))


def estimate_header_centers_74(words: list[dict]) -> tuple[list[float], list[float]]:
    """
    74表のヘッダはページ左下。○の列位置を、68表と同じ相対順序で
    行内の○分布から推定する。検出できない場合は例外。
    """
    # 74行で使われる○の x 座標をクラスタリング
    circle_x = []
    for w in words:
        if w["text"].strip() == "○" and w["yc"] > 3000 and w["x0"] < 900:
            circle_x.append(w["xc"])
    if len(circle_x) < 10:
        raise RuntimeError("74表の○列位置を推定できません。")

    xs = sorted(circle_x)
    clusters = []
    for x in xs:
        if not clusters or abs(x - np.mean(clusters[-1])) > 4.0:
            clusters.append([x])
        else:
            clusters[-1].append(x)
    centers = [float(np.mean(c)) for c in clusters if len(c) >= 1]

    # 表の列は type15 + period12 = 27列。
    # 実際に印が無い列もあるので、ヘッダの文字位置から補完する方針。
    # まずヘッダ単語の中心を拾う。
    header_words = [
        w for w in words if 3000 <= w["yc"] <= 3038 and w["x0"] < 900
    ]
    # 68表の列間隔を参考に、○のクラスタの中央値間隔で規則格子を復元
    if len(centers) >= 8:
        diffs = np.diff(centers)
        step = float(np.median(diffs[(diffs > 5) & (diffs < 30)])) if np.any((diffs > 5) & (diffs < 30)) else 12.5
        start = min(centers)
        grid = [start + i * step for i in range(27)]
        return grid[:15], grid[15:]
    raise RuntimeError("74表の列格子推定に失敗しました。")


def extract_table_gyoda_profile(page, out_csv: Path) -> pd.DataFrame:
    words = get_words(page)
    anchors = extract_row_anchors(words)

    # 68/74を分けて処理
    records = []

    type_x_74, period_x_74 = estimate_header_centers_74(words)

    for city in ("68", "74"):
        aa = [a for a in anchors if a["municipality_code"] == city]
        aa.sort(key=lambda a: a["yc"])

        if city == "68":
            type_x = TYPE_X_68
            period_x = PERIOD_X_68
            # 68表: 右側
            name_left, name_right = 2827, 2856.5
            remark_left, remark_right = 3042, 3074.5
        else:
            type_x = type_x_74
            period_x = period_x_74
            # 74表は位置から動的設定
            x_site = np.median([a["x_site"] for a in aa])
            first_type = min(type_x)
            name_left = x_site + 6
            name_right = first_type - 4
            # typeとperiodの間に備考列がある想定だが、74表では備考が空欄中心。
            remark_left = max(type_x) + 4
            remark_right = min(period_x) - 4 if period_x else remark_left + 30

        for i, a in enumerate(aa):
            yc = a["yc"]
            prev_y = aa[i-1]["yc"] if i > 0 else yc - 10
            next_y = aa[i+1]["yc"] if i+1 < len(aa) else yc + 10
            ylo = (prev_y + yc) / 2
            yhi = (yc + next_y) / 2

            row_words = [w for w in words if ylo <= w["yc"] < yhi]

            # 名称
            name_parts = [
                clean_text(w["text"])
                for w in sorted(row_words, key=lambda w: (w["yc"], w["x0"]))
                if name_left <= w["xc"] < name_right
                and w["text"].strip() != "○"
            ]
            site_name = "".join(name_parts)

            # 備考
            remark_parts = [
                clean_text(w["text"])
                for w in sorted(row_words, key=lambda w: (w["yc"], w["x0"]))
                if remark_left <= w["xc"] < remark_right
                and w["text"].strip() != "○"
            ]
            remarks = "".join(remark_parts)

            # 行内○のx位置
            marks = [w["xc"] for w in row_words if w["text"].strip() == "○"]

            type_flags = {}
            period_flags = {}

            def hit(center: float, tolerance: float = 5.5) -> int:
                return int(any(abs(mx - center) <= tolerance for mx in marks))

            for field, cx in zip(TYPE_FIELDS, type_x):
                type_flags[f"type_{field}"] = hit(cx)

            for field, cx in zip(PERIOD_FIELDS, period_x):
                period_flags[f"period_{field}"] = hit(cx)

            raw_text = "|".join(
                w["text"].strip()
                for w in sorted(row_words, key=lambda w: (w["yc"], w["x0"]))
                if w["text"].strip()
            )

            status = "active"
            if "欠番" in site_name or "欠番" in remarks or "欠番" in raw_text:
                status = "missing_number"
            if "統合" in remarks or "統合" in raw_text:
                status = "merged"

            rec = {
                "municipality_code": city,
                "site_no": a["site_no"],
                "site_uid": f"{city}-{a['site_no']}",
                "site_name": site_name,
                "remarks": remarks,
                "record_status": status,
                "raw_row_text": raw_text,
            }
            rec.update(type_flags)
            rec.update(period_flags)
            records.append(rec)

    df = pd.DataFrame(records)
    df = df.sort_values(["municipality_code", "site_no"]).reset_index(drop=True)
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    return df





GENERIC_TYPE_FIELDS = [
    "旧石器", "貝塚", "集落跡", "古墳群", "古墳", "横穴", "窯跡",
    "祭祀", "経塚", "墓", "寺院跡", "城跡", "石造遺物", "散布地", "その他",
]
GENERIC_PERIOD_FIELDS = [
    "旧石器", "縄文", "弥生", "古墳", "奈良", "平安", "鎌倉",
    "南北朝", "室町", "戦国", "江戸", "不明",
]

# この埼玉県遺跡地図系列の表は、列ピッチが一定で、
# 複数の表ブロックが同一レイアウトを平行移動して配置される。
# 下記は57_kamikawa PDFの最初の表ブロックから実測した○印中心。
def _generic_word_dicts(page):
    out = []
    for w in page.get_text("words"):
        x0,y0,x1,y1,text,*_ = w
        out.append({
            "x0": float(x0), "y0": float(y0),
            "x1": float(x1), "y1": float(y1),
            "xc": (float(x0)+float(x1))/2.0,
            "yc": (float(y0)+float(y1))/2.0,
            "text": str(text),
        })
    return out


def _generic_split_site_token(text):
    """
    遺跡番号セルと遺跡名がPyMuPDFで結合された場合にも対応。
      "003" -> ("003", "")
      "003浅間山古墳" -> ("003", "浅間山古墳")
      "1平遺跡" -> ("1", "平遺跡")
    """
    t = clean_text(text)
    m = re.fullmatch(r"(\d{1,3})(.*)", t)
    if not m:
        return None, None
    return f"{int(m.group(1)):03d}", m.group(2).strip()


def _generic_find_table_anchors(words):
    """
    PDF上の座標を使って一覧表の行アンカーを検出する。

    重要:
    - 55上里町のように1ページ内に4個の一覧表があるPDFに対応。
    - "55" と "003浅間山古墳" のように遺跡番号と名称が
      1つのPyMuPDF wordへ結合される行にも対応。
    - 地図中の数字は、市町村番号のすぐ右に遺跡番号セルがある
      という表の幾何条件で除外する。
    """
    by_y = sorted(words, key=lambda w: (w["yc"], w["x0"]))
    anchors = []

    for cw in by_y:
        city = clean_text(cw["text"])
        if not re.fullmatch(r"\d{2}", city):
            continue

        candidates = []
        for nw in by_y:
            if nw is cw:
                continue
            if abs(nw["yc"] - cw["yc"]) > 2.2:
                continue
            dx = nw["x0"] - cw["x0"]
            if not (5.0 <= dx <= 35.0):
                continue

            no, name_tail = _generic_split_site_token(nw["text"])
            if no is None:
                continue
            candidates.append((nw, no, name_tail))

        if not candidates:
            continue

        nw, no, name_tail = min(candidates, key=lambda z: z[0]["x0"])
        anchors.append({
            "municipality_code": city,
            "site_no": no,
            "site_uid": f"{city}-{no}",
            "yc": (cw["yc"] + nw["yc"]) / 2.0,
            "city_word": cw,
            "site_word": nw,
            "name_tail": name_tail,
        })

    if not anchors:
        return []

    # municipality codeごとに評価。
    by_code = {}
    for x in anchors:
        by_code.setdefault(x["municipality_code"], []).append(x)

    def code_score(item):
        city, aa = item
        nums = {int(x["site_no"]) for x in aa}
        if not nums:
            return (-1, -1, -1)
        maxn = max(nums)
        coverage = len(nums) / max(1, maxn)
        return (len(nums), coverage, maxn)

    main_city = max(by_code.items(), key=code_score)[0]
    selected = by_code[main_city]

    # 同一UIDが複数検出された場合は表領域らしい左上順で1件化。
    unique = {}
    for x in sorted(selected, key=lambda z: (z["city_word"]["x0"], z["yc"])):
        unique.setdefault(x["site_uid"], x)

    return sorted(unique.values(), key=lambda x: int(x["site_no"]))



def _generic_detect_table_grid(page, anchors):
    """
    PDFベクタの縦罫線から表の列境界を自動取得する。

    埼玉県遺跡地図系列では
      市町村番号 | 遺跡番号 | 遺跡名 |
      種別15列 | 備考 | 時代12列
    という27フラグ列の構造を持つ。

    57/58 PDFで表の位置・縮尺・列ピッチが違っても、
    固定x座標ではなく罫線自身から中心座標を求める。
    """
    if not anchors:
        return None

    ys = [a["yc"] for a in anchors]
    y0 = min(ys) - 40.0
    y1 = max(ys) + 20.0

    # anchorから表の左側位置を推定
    city_x = min(a["city_word"]["x0"] for a in anchors)
    site_x = min(a["site_word"]["x0"] for a in anchors)

    vertical_x = []
    for d in page.get_drawings():
        for item in d.get("items", []):
            if not item or item[0] != "l":
                continue
            p0, p1 = item[1], item[2]
            if abs(float(p0.x)-float(p1.x)) > 0.35:
                continue

            ymin = min(float(p0.y), float(p1.y))
            ymax = max(float(p0.y), float(p1.y))
            if ymax < y0 or ymin > y1:
                continue

            x = (float(p0.x)+float(p1.x))/2.0
            # 表左端から十分右、かつ1ページ内の現実的な範囲
            if city_x-15.0 <= x <= site_x+900.0:
                vertical_x.append(x)

    if not vertical_x:
        return None

    # 近接する同一罫線をクラスタ化
    vertical_x = sorted(vertical_x)
    clustered = []
    for x in vertical_x:
        if not clustered or abs(x-clustered[-1][-1]) > 0.8:
            clustered.append([x])
        else:
            clustered[-1].append(x)
    xs = [sum(g)/len(g) for g in clustered]

    # site番号列の左/右罫線をanchor位置から決める。
    # 市町村列・遺跡番号列・遺跡名列の後に15種別列が続く。
    site_center = sum(a["site_word"]["xc"] for a in anchors)/len(anchors)

    # site_centerを挟む罫線
    lefts = [x for x in xs if x < site_center]
    rights = [x for x in xs if x > site_center]
    if not lefts or not rights:
        return None
    site_left = max(lefts)
    site_right = min(rights)

    # site_rightの次が遺跡名右端。その後15種別列。
    after = [x for x in xs if x > site_right + 0.5]
    if len(after) < 1 + 15 + 1 + 12:
        return None

    name_right = after[0]
    flag_bounds = [name_right] + after[1:]

    # 必要な境界数: 種別15列=16境界、その後備考右端1、
    # 時代12列の終端まで12追加 → 合計29境界相当
    # 実際には flag_bounds[0] がtype左端。
    if len(flag_bounds) < 29:
        return None

    type_bounds = flag_bounds[:16]              # 15 type columns
    remarks_left = type_bounds[-1]
    remarks_right = flag_bounds[16]
    period_bounds = flag_bounds[16:29]         # 12 period columns

    type_centers = [
        (type_bounds[i]+type_bounds[i+1])/2.0 for i in range(15)
    ]
    period_centers = [
        (period_bounds[i]+period_bounds[i+1])/2.0 for i in range(12)
    ]

    return {
        "site_left": site_left,
        "site_right": site_right,
        "name_right": name_right,
        "type_centers": type_centers,
        "remarks_left": remarks_left,
        "remarks_right": remarks_right,
        "period_centers": period_centers,
        "vertical_grid_x": xs,
    }


def _generic_circle_hit(mark_xs, center, tolerance):
    return int(any(abs(float(x)-float(center)) <= tolerance for x in mark_xs))


def enrich_generic_table_columns(page, df):
    """
    generic表に remarks / record_status / type_* / period_* を追加。
    固定列座標を使用せず、PDFベクタ罫線から列位置を自動推定する。
    """
    if df is None or df.empty:
        return df

    words = _generic_word_dicts(page)
    anchors = _generic_find_table_anchors(words)
    anchor_by_uid = {a["site_uid"]: a for a in anchors}
    grid = _generic_detect_table_grid(page, anchors)

    if "remarks" not in df.columns:
        df["remarks"] = ""
    if "record_status" not in df.columns:
        df["record_status"] = "active"

    for f in GENERIC_TYPE_FIELDS:
        df[f"type_{f}"] = 0
    for f in GENERIC_PERIOD_FIELDS:
        df[f"period_{f}"] = 0

    if grid is None:
        # レコード自体は返すが、QC側でフラグ列未推定を検出できるよう属性を付ける
        df.attrs["generic_grid_detected"] = False
        return df

    df.attrs["generic_grid_detected"] = True

    circle_words = [w for w in words if w["text"].strip() == "○"]

    # 最小列ピッチから許容幅を自動決定
    all_centers = grid["type_centers"] + grid["period_centers"]
    pitches = [
        all_centers[i+1]-all_centers[i]
        for i in range(len(all_centers)-1)
        if all_centers[i+1] > all_centers[i]
    ]
    tol = max(2.5, min(pitches)*0.38) if pitches else 4.0

    for ridx,row in df.iterrows():
        uid = str(row["site_uid"])
        a = anchor_by_uid.get(uid)
        if a is None:
            continue
        yc = a["yc"]

        marks = [
            w["xc"] for w in circle_words
            if abs(w["yc"]-yc) <= 3.8
        ]

        for f,cx in zip(GENERIC_TYPE_FIELDS,grid["type_centers"]):
            df.at[ridx,f"type_{f}"] = _generic_circle_hit(marks,cx,tol)

        for f,cx in zip(GENERIC_PERIOD_FIELDS,grid["period_centers"]):
            df.at[ridx,f"period_{f}"] = _generic_circle_hit(marks,cx,tol)

        raw = str(row.get("raw_row_text","") or "")
        if "欠番" in raw:
            df.at[ridx,"record_status"] = "missing_number"
        elif "統合" in raw:
            df.at[ridx,"record_status"] = "merged"

        remark_words = [
            w for w in words
            if abs(w["yc"]-yc) <= 4.8
            and grid["remarks_left"] <= w["xc"] < grid["remarks_right"]
            and w["text"].strip() != "○"
        ]
        if remark_words:
            remark = "".join(
                clean_text(w["text"])
                for w in sorted(remark_words,key=lambda w:w["x0"])
            )
            if remark:
                df.at[ridx,"remarks"] = remark

    base_cols = [
        "municipality_code","site_no","site_uid","site_name",
        "remarks","record_status","raw_row_text",
    ]
    flag_cols = (
        [f"type_{f}" for f in GENERIC_TYPE_FIELDS]
        + [f"period_{f}" for f in GENERIC_PERIOD_FIELDS]
    )
    extras = [c for c in df.columns if c not in base_cols+flag_cols]
    return df[base_cols+flag_cols+extras]



def simple_table(page):
    """
    汎用一覧表抽出 v3: spatial multi-table parser

    page.get_text("text") の読み順には依存しない。
    PyMuPDF word座標から各行を独立に復元するため、
    1ページ内に複数の一覧表が離れて配置されるPDFにも対応する。

    55上里町で必要になった追加対応:
      * 4個の一覧表を同時抽出
      * "003浅間山古墳" のような番号+名称のword結合
      * 同一Y座標に左右2表が存在しても混線しない
    """
    words = _generic_word_dicts(page)
    anchors = _generic_find_table_anchors(words)

    if not anchors:
        return pd.DataFrame(columns=[
            "municipality_code", "site_no", "site_uid",
            "site_name", "raw_row_text",
        ])

    # 各表パネルをcity_wordのX位置でクラスタ化する。
    xs = sorted(a["city_word"]["x0"] for a in anchors)
    xgroups = []
    for x in xs:
        if not xgroups or abs(x - sum(xgroups[-1])/len(xgroups[-1])) > 80.0:
            xgroups.append([x])
        else:
            xgroups[-1].append(x)
    xcenters = [sum(g)/len(g) for g in xgroups]

    def panel_id(anchor):
        x = anchor["city_word"]["x0"]
        return min(range(len(xcenters)), key=lambda i: abs(x-xcenters[i]))

    panels = {}
    for an in anchors:
        panels.setdefault(panel_id(an), []).append(an)

    records = []

    for pid, aa in panels.items():
        aa = sorted(aa, key=lambda z: z["yc"])

        # このパネルの典型的な行ピッチを推定。
        dys = [
            aa[i+1]["yc"] - aa[i]["yc"]
            for i in range(len(aa)-1)
            if 3.0 < aa[i+1]["yc"] - aa[i]["yc"] < 30.0
        ]
        row_pitch = sorted(dys)[len(dys)//2] if dys else 10.0
        half_h = max(3.5, min(6.0, row_pitch * 0.46))

        # 表の横幅は、city xから右へ約400pt程度。
        # 隣の左右表へ侵入しないようパネル間中点でもclipする。
        city_x = sum(z["city_word"]["x0"] for z in aa) / len(aa)
        left_x = city_x - 4.0
        right_x = city_x + 390.0
        if pid + 1 < len(xcenters):
            right_x = min(right_x, (xcenters[pid] + xcenters[pid+1]) / 2.0)

        for an in aa:
            yc = an["yc"]

            row_words = [
                w for w in words
                if left_x <= w["xc"] <= right_x
                and abs(w["yc"] - yc) <= half_h
            ]
            row_words = sorted(row_words, key=lambda w: (w["x0"], w["yc"]))

            # city / site tokenを除き、最初の○までを遺跡名とする。
            # site_wordに名称末尾が結合されていれば先頭に採用。
            name_parts = []
            if an.get("name_tail"):
                name_parts.append(an["name_tail"])

            site_x1 = an["site_word"]["x1"]
            for w in row_words:
                if w is an["city_word"] or w is an["site_word"]:
                    continue
                if w["x0"] < site_x1 - 0.5:
                    continue
                tok = clean_text(w["text"])
                if not tok:
                    continue
                if tok == "○":
                    break
                # 市町村番号・遺跡番号を再度拾わない
                if tok == an["municipality_code"] or tok == an["site_no"]:
                    continue
                name_parts.append(tok)

            site_name = "".join(name_parts).strip()

            raw_tokens = [clean_text(w["text"]) for w in row_words if clean_text(w["text"])]
            records.append({
                "municipality_code": an["municipality_code"],
                "site_no": an["site_no"],
                "site_uid": an["site_uid"],
                "site_name": site_name,
                "raw_row_text": " ".join(raw_tokens[:40]),
            })

    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=[
            "municipality_code", "site_no", "site_uid",
            "site_name", "raw_row_text",
        ])

    df = df.drop_duplicates("site_uid", keep="first").copy()
    df["_site_no_int"] = pd.to_numeric(df["site_no"], errors="coerce")
    df = df.sort_values(["municipality_code", "_site_no_int", "site_no"])
    df = df.drop(columns=["_site_no_int"]).reset_index(drop=True)

    df = enrich_generic_table_columns(page, df)
    return df



def extract_table_auto(page, out_csv):
    """
    Table extractor router v2.

    重要:
    Gyoda判定をページ上の絶対x/y位置に依存させない。
    まず verified Gyoda profile を実行し、そのQCが通れば採用する。
    QC不合格・構造不一致の場合だけ generic extractor へ進む。

    これにより、PDFレンダリング/ページ座標の差や将来のcrop処理が
    profile detectionを壊すことを避ける。
    """
    try:
        df, qc = extract_table_gyoda_verified(page, out_csv)
        if qc.get("pass"):
            print(
                "  table profile: gyoda_verified "
                f"(rows={qc['rows']}, 68={qc['rows_68']}, 74={qc['rows_74']})"
            )
            print("  table QC     : PASS")
            return df
        else:
            print("  gyoda_verified profile QC failed:")
            for e in qc.get("errors", []):
                print("    -", e)
    except Exception as e:
        print(f"  gyoda_verified profile not applicable: {e}")

    # Other municipalities: generic fallback.
    df = simple_table(page)

    required = [
        "municipality_code", "site_no", "site_uid",
        "site_name", "raw_row_text",
    ]
    for c in required:
        if c not in df.columns:
            df[c] = pd.Series(dtype="object")

    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"  table profile: generic ({len(df)} records)")
    return df


def detect_table_profile(page):
    """
    Deterministic table profile detection v2.

    Gyoda判定は絶対座標ではなく、
      - municipality_code=68 の3桁遺跡番号行が多数
      - municipality_code=74 の3桁遺跡番号行が複数
    存在することを利用する。

    行田PDFの実績値は 68=194行 / 74=26行。
    """
    try:
        words = get_words(page)
        anchors = extract_row_anchors(words)

        count68 = sum(1 for a in anchors if a.get("municipality_code") == "68")
        count74 = sum(1 for a in anchors if a.get("municipality_code") == "74")

        # 厳密件数ではなく十分特徴的な下限でprofile認識。
        if count68 >= 150 and count74 >= 20:
            return "gyoda_verified"
    except Exception:
        pass

    return "generic"


def validate_table_for_profile(page, df):
    profile = detect_table_profile(page)
    if profile == "gyoda_verified":
        qc = validate_gyoda_table(df)
        qc["profile"] = profile
        return qc

    errors = []
    required = {"municipality_code", "site_no", "site_uid", "site_name"}
    missing_cols = sorted(required - set(df.columns))
    if missing_cols:
        errors.append(f"missing columns: {missing_cols}")

    rows = int(len(df))
    unique = int(df["site_uid"].nunique()) if "site_uid" in df else 0

    if rows == 0:
        errors.append("row_count=0")
    else:
        expected_flags = (
            [f"type_{f}" for f in GENERIC_TYPE_FIELDS]
            + [f"period_{f}" for f in GENERIC_PERIOD_FIELDS]
        )
        missing_flags = [c for c in expected_flags if c not in df.columns]
        if missing_flags:
            errors.append(
                "missing type/period columns: "
                + ",".join(missing_flags[:6])
            )

    if "site_uid" in df and df["site_uid"].duplicated().any():
        errors.append("duplicate site_uid")

    generic_meta = {}
    if rows > 0 and {"municipality_code", "site_no"}.issubset(df.columns):
        counts = df["municipality_code"].astype(str).value_counts()
        main_code = str(counts.index[0])
        sub = df[df["municipality_code"].astype(str) == main_code].copy()
        nums = sorted({
            int(x) for x in sub["site_no"].astype(str)
            if re.fullmatch(r"\d{3}", str(x))
        })

        max_no = max(nums) if nums else 0
        expected = set(range(1, max_no + 1))
        missing = sorted(expected - set(nums))
        coverage = len(nums) / max(1, max_no)

        generic_meta = {
            "municipality_code": main_code,
            "max_site_no": max_no,
            "sequence_coverage": coverage,
            "missing_site_numbers": [f"{n:03d}" for n in missing[:50]],
        }

        # 十分な件数の表では、連続性をQCに使う。
        if max_no >= 20 and coverage < 0.90:
            errors.append(
                f"site_no sequence coverage={coverage:.3f} (<0.90)"
            )

    return {
        "profile": profile,
        "pass": not errors,
        "errors": errors,
        "rows": rows,
        "unique_site_uid": unique,
        **generic_meta,
    }


def load_or_extract_table(page, csv_path, qc_path, force=False):
    """
    Existing CSV is reused only if it passes the same profile QC as a newly
    extracted table. A stale/empty/partially wrong CSV is never silently reused.
    """
    csv_path = Path(csv_path)
    qc_path = Path(qc_path)
    profile = detect_table_profile(page)

    if csv_path.exists() and not force:
        try:
            if csv_path.stat().st_size > 8:
                old = pd.read_csv(csv_path)
                old_qc = validate_table_for_profile(page, old)
                if old_qc.get("pass"):
                    save_json(qc_path, old_qc)
                    print(
                        f"既存利用: {csv_path} ({len(old)} records) "
                        f"profile={profile} QC=PASS"
                    )
                    return old, old_qc
                print("既存CSVはQC不合格のため再生成:", csv_path)
                for e in old_qc.get("errors", []):
                    print("  -", e)
            else:
                print("既存CSVは空のため再生成:", csv_path)
        except Exception as e:
            print("既存CSVを検証できないため再生成:", e)

    df = extract_table_auto(page, csv_path)
    qc = validate_table_for_profile(page, df)

    save_json(qc_path, qc)

    if not qc.get("pass"):
        raise RuntimeError(
            "Table extraction failed QC after regeneration: "
            + "; ".join(qc.get("errors", []))
        )

    print(
        f"一覧表抽出確認: profile={profile}, rows={len(df)}, "
        f"unique={qc.get('unique_site_uid', 0)}, QC=PASS"
    )
    return df, qc



def roi_normalized_spans(m):
    """Normalize source inlier spread to the central ROI actually searched."""
    f = max(float(getattr(m, "roi_fraction", 1.0) or 1.0), 1e-6)
    return min(1.0, m.src_span_x / f), min(1.0, m.src_span_y / f)


def strong_georef_candidate_legacy(m):
    """Fast-path criterion for a strong low-degree-of-freedom solution."""
    if m is None or m.H is None or not m.transform_sane:
        return False
    sx, sy = roi_normalized_spans(m)
    return (
        m.model in {"similarity", "affine"}
        and m.inliers >= 8
        and m.ratio >= 0.22
        and m.mederr <= 3.0
        and sx >= 0.35 and sy >= 0.35
        and m.src_grid_coverage >= 0.1875
        and m.dst_span_x >= 0.15 and m.dst_span_y >= 0.15
        and m.global_agreement >= 0.80
    )



def tile_count_for_bbox(z, bbox):
    west,south,east,north = bbox
    x0,y1 = lonlat_to_tile(west,south,z)
    x1,y0 = lonlat_to_tile(east,north,z)
    xmin,xmax = math.floor(min(x0,x1)), math.floor(max(x0,x1))
    ymin,ymax = math.floor(min(y0,y1)), math.floor(max(y0,y1))
    return (xmax-xmin+1)*(ymax-ymin+1)


def choose_gcp_zoom(bbox, preferred=15, max_tiles=900, min_zoom=15):
    """
    手動GCP用zoomを決定する。

    旧版は自治体全域のタイル数が多いとz=14以下へ自動降下したため、
    地物判読が困難になった。

    新版ではmin_zoom=15を下限とし、タイル数が多い場合は
    zoomを下げず、後段で参照bboxを局所化する。
    """
    return max(int(min_zoom), int(preferred))


def load_gcp_csv(path):
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    required = {"pdf_x","pdf_y","lon","lat"}
    if not required.issubset(df.columns) or len(df) < 3:
        return None
    return df


def save_gcp_csv(path, pairs, raster, mosaic):
    rows=[]
    for i,(lp,rp) in enumerate(pairs,1):
        rx,ry = lp
        gx,gy = rp
        pdf_x,pdf_y = raster_to_pdf(rx,ry,raster)
        lon,lat = mosaic.pixel_to_lonlat(gx,gy)
        rows.append({
            "gcp_id": i,
            "pdf_x": pdf_x,
            "pdf_y": pdf_y,
            "pdf_raster_x": rx,
            "pdf_raster_y": ry,
            "gsi_x": gx,
            "gsi_y": gy,
            "lon": lon,
            "lat": lat,
            "gsi_layer": mosaic.layer,
            "gsi_zoom": mosaic.z,
        })
    df=pd.DataFrame(rows)
    df.to_csv(path,index=False,encoding="utf-8-sig")
    return df



def configure_matplotlib_japanese_font():
    """
    GUI用日本語フォントをOSにインストール済みのフォントから選択する。
    フォントファイルを同梱・配布はしない。

    macOS: Hiragino / Yu Gothic
    Windows: Yu Gothic / Meiryo
    Linux: Noto Sans CJK JP / IPAexGothic
    """
    try:
        import matplotlib
        from matplotlib import font_manager
    except ImportError:
        return None

    preferred = [
        "Hiragino Sans",
        "Hiragino Kaku Gothic ProN",
        "Hiragino Kaku Gothic Pro",
        "Yu Gothic",
        "YuGothic",
        "Meiryo",
        "Noto Sans CJK JP",
        "Noto Sans JP",
        "IPAexGothic",
        "IPAGothic",
    ]

    available = {}
    for f in font_manager.fontManager.ttflist:
        name = (f.name or "").strip()
        if name and name not in available:
            available[name] = f.fname

    selected = None
    for name in preferred:
        if name in available:
            selected = name
            break

    if selected:
        matplotlib.rcParams["font.family"] = selected
        matplotlib.rcParams["axes.unicode_minus"] = False
        return selected

    # 部分一致も試す
    lowered = {k.lower(): k for k in available}
    for name in preferred:
        nl = name.lower()
        for kl, original in lowered.items():
            if nl in kl or kl in nl:
                matplotlib.rcParams["font.family"] = original
                matplotlib.rcParams["axes.unicode_minus"] = False
                return original

    matplotlib.rcParams["axes.unicode_minus"] = False
    return None


def interactive_gcp_gui(raster, mosaic, csv_path, title_suffix=""):
    """
    左: PDF地図
    右: 地理院地図
    を表示し、左→右→左→右...の順でクリックしてGCPを取得する。

    Enter : 完了（3組以上）
    Backspace/Delete : 最後の1組を削除
    Esc : 中止
    """
    try:
        import matplotlib.pyplot as plt
        selected_jp_font = configure_matplotlib_japanese_font()
        if selected_jp_font:
            print(f"  GCP GUI font: {selected_jp_font}")
        else:
            print(
                "  WARNING: 日本語対応フォントを自動検出できませんでした。"
                " GUI文字が□になる場合があります。"
            )
    except ImportError as e:
        raise RuntimeError(
            "手動GCP GUIには matplotlib が必要です。"
            " `python3 -m pip install matplotlib` を実行してください。"
        ) from e

    pdf_rgb=cv2.cvtColor(raster.img,cv2.COLOR_BGR2RGB)
    gsi_rgb=cv2.cvtColor(mosaic.img,cv2.COLOR_BGR2RGB)

    fig, (ax_pdf,ax_gsi)=plt.subplots(1,2,figsize=(18,9))
    try:
        fig.canvas.manager.set_window_title("Archaeological Map GCP Picker")
    except Exception:
        pass

    ax_pdf.imshow(pdf_rgb)
    ax_pdf.set_title("PDF地図：ここをクリック →")
    ax_gsi.imshow(gsi_rgb)
    ax_gsi.set_title(f"← 同じ地点を地理院地図でクリック  [z={mosaic.z}]")
    for ax in (ax_pdf,ax_gsi):
        ax.set_axis_off()

    fig.suptitle(
        "GCP指定: 左(PDF)→右(地理院)を交互にクリック / "
        "Enter=完了 / Backspace=1組取消 / Esc=中止"
        + (f"  {title_suffix}" if title_suffix else ""),
        fontsize=12
    )

    pairs=[]
    pending_left=None
    artists=[]
    state={"done":False,"aborted":False}

    def redraw_points():
        nonlocal artists
        for a in artists:
            try: a.remove()
            except Exception: pass
        artists=[]

        for i,(lp,rp) in enumerate(pairs,1):
            for ax,p in ((ax_pdf,lp),(ax_gsi,rp)):
                art=ax.plot(p[0],p[1],"o",markersize=7)[0]
                txt=ax.text(
                    p[0]+6,p[1]-6,str(i),
                    fontsize=10,
                    bbox=dict(boxstyle="round,pad=0.15",fc="white",alpha=.7)
                )
                artists.extend([art,txt])

        if pending_left is not None:
            art=ax_pdf.plot(
                pending_left[0],pending_left[1],
                "x",markersize=10,markeredgewidth=2
            )[0]
            artists.append(art)

        ax_pdf.set_title(
            f"PDF地図：GCP {len(pairs)}組 "
            + ("／右側の同一点をクリック" if pending_left is not None else "／次の点をクリック")
        )
        ax_gsi.set_title(
            f"地理院地図 z={mosaic.z} {mosaic.layer}："
            + ("同一点をクリック" if pending_left is not None else "PDF側を先にクリック")
        )
        fig.canvas.draw_idle()

    def onclick(event):
        nonlocal pending_left
        if event.xdata is None or event.ydata is None:
            return
        if pending_left is None:
            if event.inaxes is not ax_pdf:
                return
            pending_left=(float(event.xdata),float(event.ydata))
        else:
            if event.inaxes is not ax_gsi:
                return
            pairs.append((
                pending_left,
                (float(event.xdata),float(event.ydata))
            ))
            pending_left=None
        redraw_points()

    def onkey(event):
        nonlocal pending_left
        key=(event.key or "").lower()
        if key in ("backspace","delete"):
            if pending_left is not None:
                pending_left=None
            elif pairs:
                pairs.pop()
            redraw_points()
        elif key in ("enter","return"):
            if pending_left is not None:
                print("右側の対応点が未指定です。")
                return
            if len(pairs)<3:
                print("GCPは最低3組必要です。推奨5～8組です。")
                return
            state["done"]=True
            plt.close(fig)
        elif key=="escape":
            state["aborted"]=True
            plt.close(fig)

    fig.canvas.mpl_connect("button_press_event",onclick)
    fig.canvas.mpl_connect("key_press_event",onkey)
    redraw_points()
    plt.tight_layout()
    plt.show()

    if state["aborted"] or not state["done"]:
        return None
    return save_gcp_csv(csv_path,pairs,raster,mosaic)


def fit_similarity(src, dst):
    A,mask=cv2.estimateAffinePartial2D(
        np.asarray(src,np.float32),
        np.asarray(dst,np.float32),
        method=cv2.LMEDS,
        refineIters=25,
    )
    if A is None:
        return None
    return np.vstack([A,[0,0,1]])


def fit_affine_manual(src, dst):
    A,mask=cv2.estimateAffine2D(
        np.asarray(src,np.float32),
        np.asarray(dst,np.float32),
        method=cv2.LMEDS,
        refineIters=25,
    )
    if A is None:
        return None
    return np.vstack([A,[0,0,1]])


def gcp_fit_error(src,dst,H):
    pred=transform_points_h(src,H)
    err=np.linalg.norm(pred-np.asarray(dst,float),axis=1)
    return {
        "errors_px": err,
        "median_px": float(np.median(err)),
        "rmse_px": float(np.sqrt(np.mean(err**2))),
        "max_px": float(np.max(err)),
    }


def fit_manual_gcp(df, raster, mosaic, model="auto"):
    """
    GCPから raster pixel -> GSI mosaic pixel の変換を求める。
    autoではSimilarityを標準とし、5点以上でAffineが明確に改善し、
    かつ歪みが小さい場合だけAffineを採用する。
    """
    src=[]
    dst=[]
    for _,r in df.iterrows():
        rx,ry=pdf_to_raster(float(r.pdf_x),float(r.pdf_y),raster)
        gx,gy=mosaic.lonlat_to_pixel(float(r.lon),float(r.lat))
        src.append((rx,ry)); dst.append((gx,gy))
    src=np.asarray(src,float)
    dst=np.asarray(dst,float)

    Hs=fit_similarity(src,dst)
    if Hs is None:
        raise RuntimeError("GCPからSimilarity変換を推定できません。")
    es=gcp_fit_error(src,dst,Hs)

    selected="similarity"
    H=Hs
    fit=es

    if model in ("auto","affine") and len(df)>=4:
        Ha=fit_affine_manual(src,dst)
        if Ha is not None:
            ea=gcp_fit_error(src,dst,Ha)
            gm=linear_transform_metrics(Ha)
            affine_sane=(
                gm["condition_number"]<=1.25
                and gm["orthogonality"]>=0.90
                and gm["determinant"]>0
            )
            if model=="affine":
                if not affine_sane:
                    raise RuntimeError(
                        "指定GCPから得られたAffine変換の歪みが大きすぎます。"
                    )
                selected="affine"; H=Ha; fit=ea
            elif len(df)>=5 and affine_sane and ea["rmse_px"] < es["rmse_px"]*0.75:
                selected="affine"; H=Ha; fit=ea

    if model=="similarity":
        selected="similarity"; H=Hs; fit=es

    sh,sw=raster.img.shape[:2]
    mh,mw=mosaic.img.shape[:2]
    sx,sy,grid=_coverage_metrics(src,sw,sh)
    dx,dy,_=_coverage_metrics(dst,mw,mh)
    sane=_homography_sanity(H,sw,sh,mw,mh)
    ga=global_edge_agreement(raster.img,mosaic.img,H)

    m=Match(
        mosaic.layer,mosaic.z,1.0,
        "manual_gcp",selected,
        len(df),len(df),1.0,
        fit["median_px"],
        sx,sy,grid,dx,dy,ga,sane,
        H,mosaic,src,dst,np.ones(len(df),dtype=np.uint8)
    )
    return m, fit


def assess_manual_gcp_qc(m, fit, n):
    gm=linear_transform_metrics(m.H)
    reasons=[]
    if n<3:
        reasons.append("GCP<3")
    if not m.transform_sane:
        reasons.append("transform_not_sane")
    if m.model=="affine":
        if gm["condition_number"]>1.25:
            reasons.append("affine_condition>1.25")
        if gm["orthogonality"]<0.90:
            reasons.append("affine_orthogonality<0.90")
    if n>=4 and fit["rmse_px"]>12:
        reasons.append("GCP_RMSE>12px")
    if n>=5 and fit["max_px"]>25:
        reasons.append("GCP_max_error>25px")
    if m.src_span_x<0.20 or m.src_span_y<0.20:
        reasons.append("GCP_distribution_too_narrow")
    return len(reasons)==0, reasons, gm


def bbox_center(bbox):
    w,s,e,n = map(float,bbox)
    return ((w+e)/2.0, (s+n)/2.0)


def shrink_bbox_to_tile_budget(bbox, z, max_tiles, center=None):
    """
    zoomを下げずに、表示範囲だけ縮めてmax_tiles以下にする。
    """
    bbox = tuple(map(float,bbox))
    if tile_count_for_bbox(z,bbox) <= max_tiles:
        return bbox

    w,s,e,n = bbox
    cx,cy = center if center is not None else bbox_center(bbox)

    tiles = max(1, tile_count_for_bbox(z,bbox))
    scale = min(1.0, (max_tiles / tiles) ** 0.5 * 0.92)

    result = bbox
    for _ in range(12):
        hw = (e-w) * scale / 2.0
        hh = (n-s) * scale / 2.0
        result = (
            max(w, cx-hw),
            max(s, cy-hh),
            min(e, cx+hw),
            min(n, cy+hh),
        )
        if tile_count_for_bbox(z,result) <= max_tiles:
            return result
        scale *= 0.86

    return result


def best_gcp_reference_bbox(search_bbox, automatic_best=None, z=15, max_tiles=900):
    """
    自動候補のlocal bboxがあればそれを優先し、
    高zoomで表示可能な範囲へ局所化する。
    """
    candidate = None
    if automatic_best is not None:
        try:
            mb = getattr(automatic_best, "mosaic", None)
            if mb is not None and getattr(mb, "bbox", None):
                candidate = tuple(mb.bbox)
        except Exception:
            candidate = None

    if candidate is None:
        candidate = tuple(search_bbox)

    return shrink_bbox_to_tile_budget(
        candidate,
        z,
        max_tiles,
        center=bbox_center(candidate),
    )


def prepare_manual_gcp_mosaic(
    search_bbox,
    outdir,
    layer="pale",
    preferred_zoom=15,
    max_zoom=17,
    max_tiles=900,
    automatic_best=None,
):
    """
    手動GCP参照画像を高解像度で作成。
    原則 z>=15。タイル数超過時はzoomを下げずbboxを局所化する。
    """
    z = choose_gcp_zoom(
        search_bbox,
        preferred=max(15, preferred_zoom),
        max_tiles=max_tiles,
        min_zoom=15,
    )
    z = min(int(max_zoom), max(15, z))

    ref_bbox = best_gcp_reference_bbox(
        search_bbox,
        automatic_best=automatic_best,
        z=z,
        max_tiles=max_tiles,
    )

    ntiles = tile_count_for_bbox(z, ref_bbox)
    print(
        f"  手動GCP参照地図: layer={layer}, z={z}, "
        f"tiles={ntiles}"
    )
    if tuple(ref_bbox) != tuple(search_bbox):
        print(
            "  高解像度維持のため参照範囲を局所化: "
            + ",".join(f"{v:.6f}" for v in ref_bbox)
        )

    mos = build_mosaic(
        layer,
        z,
        ref_bbox,
        Path(outdir)/"gsi_tiles"
    )
    cv2.imwrite(str(Path(outdir)/"05_gcp_reference.png"), mos.img)

    save_json(
        Path(outdir)/"05_gcp_reference.json",
        {
            "layer": layer,
            "zoom": z,
            "bbox": list(ref_bbox),
            "tile_count": int(ntiles),
            "minimum_zoom_policy": 15,
        }
    )
    return mos


def manual_gcp_fallback(raster, search_bbox, outdir, args, municipality_name=None, automatic_best=None):
    """
    既存GCPがあれば再利用。無ければGUIを起動。
    """
    outdir=Path(outdir)
    csv_path=outdir/"05_gcp.csv"

    mos=prepare_manual_gcp_mosaic(
        search_bbox,
        outdir,
        layer=args.gcp_layer,
        preferred_zoom=max(15,args.gcp_zoom),
        max_zoom=args.gcp_max_zoom,
        max_tiles=args.gcp_max_tiles,
        automatic_best=automatic_best,
    )

    df=load_gcp_csv(csv_path)
    if df is not None:
        print(f"  既存GCP利用: {csv_path} ({len(df)} points)")
    else:
        if args.non_interactive:
            print(
                "  自動QC FAIL。--non-interactive のためGUIは起動しません。\n"
                f"  GCPを {csv_path} に作成して再実行してください。"
            )
            return None
        print("=== 5B 手動GCPフォールバック ===")
        print("  左PDF → 右地理院地図 の順で同一点をクリックしてください。")
        print("  最低3点、推奨5～8点。地図全体に分散させてください。")
        df=interactive_gcp_gui(
            raster,mos,csv_path,
            title_suffix=municipality_name or ""
        )
        if df is None:
            print("  GCP指定を中止しました。")
            return None

    m,fit=fit_manual_gcp(df,raster,mos,model=args.gcp_model)
    ok,reasons,gm=assess_manual_gcp_qc(m,fit,len(df))

    info={
        "mode":"manual_gcp",
        "model":m.model,
        "gcp_count":int(len(df)),
        "median_error_px":fit["median_px"],
        "rmse_px":fit["rmse_px"],
        "max_error_px":fit["max_px"],
        "global_agreement":m.global_agreement,
        "src_span_x":m.src_span_x,
        "src_span_y":m.src_span_y,
        "transform_sane":m.transform_sane,
        "transform_metrics":gm,
        "H":m.H.tolist(),
        "gsi_layer":mos.layer,
        "gsi_zoom":mos.z,
        "search_bbox":list(search_bbox),
        "qc_pass":ok,
        "qc_reasons":reasons,
    }
    save_json(outdir/"05_gcp_fit.json",info)
    save_matches(m,raster.img,outdir/"05_gcp_matches.png")
    save_overlay(m,raster.img,outdir/"05_gcp_overlay.png")

    print(
        f"  GCP fit: model={m.model}, n={len(df)}, "
        f"RMSE={fit['rmse_px']:.2f}px, median={fit['median_px']:.2f}px, "
        f"global={m.global_agreement:.3f}"
    )
    print("  MANUAL GCP QC:", "PASS" if ok else "FAIL")
    if reasons:
        print("  reasons:", ", ".join(reasons))

    if not ok:
        return None
    return m, info

def assess_georef_qc(m, args):
    """ROI-aware QC for central-ROI georeferencing."""
    sx, sy = roi_normalized_spans(m)
    metrics = {
        "model": m.model,
        "roi_fraction": float(m.roi_fraction),
        "inliers": int(m.inliers),
        "inlier_ratio": float(m.ratio),
        "median_error_px": float(m.mederr),
        "src_span_x_full": float(m.src_span_x),
        "src_span_y_full": float(m.src_span_y),
        "src_span_x_roi": float(sx),
        "src_span_y_roi": float(sy),
        "src_grid_coverage": float(m.src_grid_coverage),
        "dst_span_x": float(m.dst_span_x),
        "dst_span_y": float(m.dst_span_y),
        "global_agreement": float(m.global_agreement),
        "transform_sane": bool(m.transform_sane),
        **linear_transform_metrics(m.H),
    }

    fatal = []
    if m.H is None: fatal.append("no_transform")
    if not m.transform_sane: fatal.append("transform_not_sane")
    if m.model in ("similarity", "affine") and not low_dof_geometry_sane(m):
        fatal.append("affine_geometry_distorted")
    if m.ratio < 0.15: fatal.append("inlier_ratio<0.15")
    if m.mederr > 8.0: fatal.append("median_error>8px")
    if m.global_agreement < 0.10: fatal.append("global_agreement<0.10")
    if m.dst_span_x < 0.10 or m.dst_span_y < 0.10:
        fatal.append("destination_span_too_small")
    if fatal:
        return False, "FAIL", metrics, fatal

    # Preferred case for an undistorted printed/PDF map.
    low_dof_pass = (
        m.model in {"similarity", "affine"}
        and m.inliers >= 8
        and m.ratio >= 0.20
        and m.mederr <= 4.0
        and sx >= 0.35 and sy >= 0.35
        and m.src_grid_coverage >= 0.1875
        and m.dst_span_x >= 0.15 and m.dst_span_y >= 0.15
        and m.global_agreement >= 0.70
    )
    if low_dof_pass:
        return True, "HIGH", metrics, []

    # Homography needs stronger evidence because it has more freedom.
    homography_pass = (
        m.model == "homography"
        and m.inliers >= 12
        and m.ratio >= 0.22
        and m.mederr <= 4.0
        and sx >= 0.40 and sy >= 0.40
        and m.src_grid_coverage >= 0.25
        and m.dst_span_x >= 0.18 and m.dst_span_y >= 0.18
        and m.global_agreement >= 0.75
    )
    if homography_pass:
        return True, "STANDARD", metrics, []

    # CLI thresholds remain available, but source span is ROI-normalized.
    general_pass = (
        m.inliers >= args.min_inliers
        and m.ratio >= args.min_ratio
        and m.mederr <= args.max_error
        and sx >= args.min_src_span and sy >= args.min_src_span
        and m.src_grid_coverage >= args.min_grid_coverage
        and m.dst_span_x >= args.min_dst_span and m.dst_span_y >= args.min_dst_span
        and m.global_agreement >= args.min_global_agreement
    )
    if general_pass:
        return True, "STANDARD", metrics, []

    reasons = []
    if m.inliers < 8: reasons.append("too_few_inliers_for_low_dof")
    if sx < 0.35 or sy < 0.35: reasons.append("source_distribution_too_narrow_in_roi")
    if m.src_grid_coverage < 0.1875: reasons.append("source_grid_coverage_low")
    if m.global_agreement < 0.70: reasons.append("global_agreement_not_strong")
    if m.mederr > 4.0: reasons.append("reprojection_error_not_strong")
    return False, "FAIL", metrics, reasons

def parse_args():
    p=argparse.ArgumentParser()
    p.add_argument("pdf",type=Path)
    p.add_argument("--out",type=Path,default=None,
                   help="出力フォルダ。未指定時はPDFと同じ場所に <PDF名>_extract を自動作成")
    p.add_argument("--page",type=int,default=0)
    p.add_argument("--search-bbox",type=parse_bbox,default=None,
                   help="WEST,SOUTH,EAST,NORTH。未指定時はPDFから自治体bboxを自動決定")
    p.add_argument("--municipality",default=None,
                   help="自治体名を明示指定。例: 行田市")
    p.add_argument("--bbox-padding",type=float,default=0.12,
                   help="自治体bboxへの追加余白率 (default: 0.12)")
    p.add_argument("--layers",default="std,pale")
    p.add_argument("--zooms",default="13,14,15")
    p.add_argument("--roi-fractions",default="0.60,0.70,0.80",
                   help="位置合わせに使う中央ROI比率。例: 0.60,0.70,0.80")
    p.add_argument("--dpi",type=int,default=220)
    p.add_argument("--min-inliers",type=int,default=12)
    p.add_argument("--min-ratio",type=float,default=.20)
    p.add_argument("--max-error",type=float,default=12)
    p.add_argument("--min-src-span",type=float,default=.35,
                   help="inlierのPDF上X/Y広がりの最低値")
    p.add_argument("--min-grid-coverage",type=float,default=.25,
                   help="4x4グリッドでinlierが占有する割合の最低値")
    p.add_argument("--min-dst-span",type=float,default=.20,
                   help="inlierの地理院モザイク上X/Y広がりの最低値")
    p.add_argument("--min-global-agreement",type=float,default=.06,
                   help="全体edge一致率の最低値 (default: 0.06)")
    p.add_argument("--global-top-k",type=int,default=4,
                   help="各layer/zoomでglobal評価する上位候補数 (default: 4)")
    p.add_argument("--coarse-zooms",default="11,12",
                   help="自治体全域の粗探索zoom。例: 11,12")
    p.add_argument("--coarse-grid",type=int,default=5,
                   help="粗探索で自治体bboxを分割する各辺の数 (default: 5)")
    p.add_argument("--coarse-top-k",type=int,default=3,
                   help="粗探索で精密探索へ渡す局所bbox数 (default: 3)")
    p.add_argument("--coarse-expand",type=float,default=0.35,
                   help="粗探索ヒットbboxの拡張率 (default: 0.35)")
    p.add_argument("--disable-coarse",action="store_true",
                   help="coarse-to-fine探索を無効化し、従来どおり自治体bbox全体で探索")
    p.add_argument("--no-gcp-fallback",action="store_true",
                   help="自動QC FAIL時に手動GCP GUIへフォールバックしない")
    p.add_argument("--gcp-layer",choices=["std","pale"],default="pale",
                   help="手動GCP右画面の地理院レイヤ (default: pale)")
    p.add_argument("--gcp-zoom",type=int,default=15,
                   help="手動GCP参照地図の最小zoom (default: 15)")
    p.add_argument("--gcp-max-zoom",type=int,default=17,
                   help="手動GCP GUIで選択可能な最大zoom (default: 17)")
    p.add_argument("--gcp-max-tiles",type=int,default=900,
                   help="手動GCP参照モザイクの最大タイル数。超過時もz<15には下げず、局所表示へ切替 (default: 900)")
    p.add_argument("--gcp-model",choices=["auto","similarity","affine"],default="auto",
                   help="GCP変換モデル。autoはSimilarity優先 (default: auto)")
    p.add_argument("--no-early-stop",action="store_true",
                   help="強い候補が出ても全layer/zoomを最後まで探索")
    p.add_argument("--accept-low-qc",action="store_true")
    p.add_argument("--non-interactive",action="store_true")
    p.add_argument("--skip-polygons",action="store_true")
    p.add_argument("--table-only",action="store_true",
                   help="一覧表抽出とQCだけ実行して終了")
    p.add_argument("--force-table",action="store_true",
                   help="既存01_table.csvを再利用せず一覧表を再抽出")
    return p.parse_args()


def main():
    a=parse_args()

    # 出力先はPDFファイル名から自動決定。
    # 例: 68_gyouda_city.pdf -> 68_gyouda_city_extract/
    if a.out is None:
        a.out = a.pdf.resolve().parent / f"{a.pdf.stem}_extract"
    else:
        a.out = a.out.resolve()

    a.out.mkdir(parents=True, exist_ok=True)

    print(f"Input PDF : {a.pdf}")
    print(f"Output dir: {a.out}")

    print("=== 1 PDF検査 ===")
    doc,page,bbox,match_bbox,inspection,crop_mask=inspect_pdf(a.pdf,a.page)
    save_json(a.out/"00_inspection.json",inspection)
    cv2.imwrite(str(a.out/"00_map_crop_mask.png"), crop_mask)
    print("  map bbox PDF  :", ",".join(f"{v:.1f}" for v in bbox))
    print("  match bbox PDF:", ",".join(f"{v:.1f}" for v in match_bbox))

    print("=== 2 一覧表 ===")
    table, table_qc = load_or_extract_table(
        page,
        a.out / "01_table.csv",
        a.out / "01_table_qc.json",
        force=a.force_table,
    )

    if a.table_only:
        print("table-only: 完了")
        print(json.dumps(table_qc, ensure_ascii=False, indent=2))
        return 0

    print("=== 1B search-bbox 自動決定 ===")
    search_bbox, search_meta = determine_search_bbox(
        page,
        a.search_bbox,
        a.municipality,
        a.bbox_padding,
        a.out,
    )
    print(f"  method       : {search_meta.get('method')}")
    print(f"  municipality : {search_meta.get('selected_municipality', '-')}")
    print("  search bbox  : " + ",".join(f"{v:.6f}" for v in search_bbox))

    print("=== 3 地図番号 ===")
    labels=extract_labels(page,bbox,table=table)
    labels.to_csv(a.out/"02_map_labels_pdf.csv",index=False,encoding="utf-8-sig")

    table_uids=set(table["site_uid"].astype(str)) if "site_uid" in table.columns else set()
    label_uids=set(labels["site_uid"].astype(str)) if "site_uid" in labels.columns else set()
    uid_overlap=len(table_uids & label_uids)

    print(
        f"{len(labels)} labels / {labels.site_uid.nunique()} unique "
        f"/ table UID overlap={uid_overlap}"
    )

    if len(labels)>0 and uid_overlap==0:
        raise RuntimeError(
            "地図ラベルsite_uidと一覧表site_uidが1件も一致しません。"
            " municipality_code割当を確認してください。"
        )

    print("=== 4 赤線 ===")
    red=drawing_lines(page)
    save_geojson(a.out/"03_red_paths_pdf.geojson",[
        {"type":"Feature","properties":{"id":i},"geometry":mapping(g)}
        for i,g in enumerate(red)
    ])
    raster=render_pdf_map(page,match_bbox,a.dpi)
    cv2.imwrite(str(a.out/"04_map_preview.png"),raster.img)

    # 中央ROI候補を可視化
    roi_preview = raster.img.copy()
    h0, w0 = roi_preview.shape[:2]
    roi_colors = [(0,160,0), (255,0,0), (0,0,200)]
    for f, col in zip((0.60,0.70,0.80), roi_colors):
        _, (rx0,ry0,rx1,ry1) = central_roi(raster.img, f)
        cv2.rectangle(roi_preview,(rx0,ry0),(rx1,ry1),col,3)
        cv2.putText(
            roi_preview, f"{int(f*100)}%",
            (rx0+8,ry0+30),
            cv2.FONT_HERSHEY_SIMPLEX, 1.0, col, 2, cv2.LINE_AA
        )
    cv2.imwrite(str(a.out/"04_matching_rois.png"),roi_preview)

    print(f"{len(red)} red line parts")

    print("=== 5 自動ジオリファレンス ===")
    layers=[s.strip() for s in a.layers.split(",") if s.strip()]
    zooms=[int(s) for s in a.zooms.split(",") if s.strip()]
    roi_fractions=[float(s) for s in a.roi_fractions.split(",") if s.strip()]
    for f in roi_fractions:
        if not (0.2 <= f <= 1.0):
            raise ValueError(f"ROI fraction must be 0.2..1.0: {f}")
    print("  central ROI  :", ",".join(f"{f:.2f}" for f in roi_fractions))
    # 複数図郭PDFに対応する coarse-to-fine。
    # 自治体bbox全体を精密マッチングするのではなく、
    # まず低zoomでPDFに対応する局所領域を推定する。
    if a.disable_coarse:
        fine_bboxes = [{
            "bbox": search_bbox,
            "source": "municipality_bbox",
        }]
    else:
        coarse_zooms = [int(s) for s in a.coarse_zooms.split(",") if s.strip()]
        coarse_hits = coarse_locate_pdf(
            raster,
            search_bbox,
            a.out/"gsi_tiles",
            layers=tuple(layers),
            coarse_zooms=tuple(coarse_zooms),
            grid_n=a.coarse_grid,
            top_k=a.coarse_top_k,
            expand_ratio=a.coarse_expand,
        )

        save_json(
            a.out/"04_coarse_candidates.json",
            {
                "municipality_bbox": list(search_bbox),
                "coarse_zooms": coarse_zooms,
                "grid_n": a.coarse_grid,
                "selected": coarse_hits,
            },
        )

        if coarse_hits:
            fine_bboxes = [
                {"bbox": tuple(h["bbox"]), "source": "coarse"}
                for h in coarse_hits
            ]
        else:
            raise RuntimeError(
                "粗位置探索でgeometry-safeな局所候補を取得できませんでした。"
                " 自治体bbox全体への自動fallbackは誤マッチを生むため停止します。"
                " --disable-coarse を明示した場合のみ従来方式を使用できます。"
            )

    results = []
    lightweight_results = []

    for bi, fb in enumerate(fine_bboxes, 1):
        local_bbox = fb["bbox"]
        print(
            f"=== 5 精密位置合わせ [{bi}/{len(fine_bboxes)}] ===\n"
            f"  source bbox   : {fb['source']}\n"
            f"  local bbox    : {','.join(f'{v:.6f}' for v in local_bbox)}"
        )

        r2, l2 = auto_match(
            raster,
            layers,
            zooms,
            local_bbox,
            a.out/"gsi_tiles",
            roi_fractions=roi_fractions,
            global_top_k=a.global_top_k,
            early_stop=not a.no_early_stop,
        )
        results.extend(r2)
        lightweight_results.extend(l2)

        # その局所bboxで強い解が出れば次の粗候補は試さない
        if r2:
            best_local = max(r2, key=final_score)
            if candidate_is_strong(best_local):
                print("  strong local solution -> remaining coarse candidates skipped")
                break

    results.sort(key=final_score, reverse=True)


    # 全軽量候補
    light_df = pd.DataFrame([{
        "layer":m.layer,
        "zoom":m.z,
        "roi_fraction":m.roi_fraction,
        "mode":m.mode,
        "model":m.model,
        "good_matches":m.good,
        "inliers":m.inliers,
        "inlier_ratio":m.ratio,
        "median_error_px":m.mederr,
        "src_span_x":m.src_span_x,
        "src_span_y":m.src_span_y,
        "src_grid_coverage":m.src_grid_coverage,
        "dst_span_x":m.dst_span_x,
        "dst_span_y":m.dst_span_y,
        "transform_sane":m.transform_sane,
        "light_score":score(m),
    } for m in lightweight_results])
    light_df.to_csv(
        a.out/"05_candidates_lightweight.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # global評価済み候補だけ
    cand=pd.DataFrame([{
        "layer":m.layer,
        "zoom":m.z,
        "roi_fraction":m.roi_fraction,
        "mode":m.mode,
        "model":m.model,
        "good_matches":m.good,
        "inliers":m.inliers,
        "inlier_ratio":m.ratio,
        "median_error_px":m.mederr,
        "src_span_x":m.src_span_x,
        "src_span_y":m.src_span_y,
        "src_grid_coverage":m.src_grid_coverage,
        "dst_span_x":m.dst_span_x,
        "dst_span_y":m.dst_span_y,
        "global_agreement":m.global_agreement,
        "transform_sane":m.transform_sane,
        "light_score":score(m),
        "final_score":final_score(m),
    } for m in results])
    cand.to_csv(a.out/"05_candidates.csv",index=False,encoding="utf-8-sig")

    if not results:
        print("global評価まで進んだ有効候補がありません。")
        return 4

    best=results[0]
    if best.H is None:
        print("自動マッチング失敗")
        return 4

    bestinfo=cand.iloc[0].to_dict()
    bestinfo["H"]=best.H.tolist()
    bestinfo["search_bbox"]=list(best.mosaic.bbox if best.mosaic.bbox is not None else search_bbox)
    bestinfo["municipality_bbox"]=list(search_bbox)
    bestinfo["transform_metrics"]=linear_transform_metrics(best.H)
    bestinfo["search_bbox_method"]=search_meta.get("method")
    bestinfo["selected_municipality"]=search_meta.get("selected_municipality")
    save_json(a.out/"05_best.json",bestinfo)
    save_matches(best,raster.img,a.out/"05_matches.png")
    save_overlay(best,raster.img,a.out/"05_overlay.png")

    qc, qc_grade, qc_metrics, qc_reasons = assess_georef_qc(best, a)
    print(json.dumps(bestinfo,ensure_ascii=False,indent=2))
    print("AUTO QC:", "PASS" if qc else "FAIL", f"({qc_grade})")
    print(
        f"  ROI-normalized src span x/y: "
        f"{qc_metrics['src_span_x_roi']:.3f}/{qc_metrics['src_span_y_roi']:.3f}"
    )

    georef_method="auto"
    manual_gcp_info=None

    if not qc and not a.accept_low_qc:
        print(
            "QC未達。05_matches.png / 05_overlay.png を確認してください。\n"
            f"  model         : {best.model}\n"
            f"  ROI fraction  : {best.roi_fraction:.2f}\n"
            f"  inliers       : {best.inliers}\n"
            f"  inlier ratio  : {best.ratio:.3f}\n"
            f"  median error  : {best.mederr:.2f}px\n"
            f"  src span full : {best.src_span_x:.3f}/{best.src_span_y:.3f}\n"
            f"  src span ROI  : {qc_metrics['src_span_x_roi']:.3f}/"
            f"{qc_metrics['src_span_y_roi']:.3f}\n"
            f"  grid coverage : {best.src_grid_coverage:.3f}\n"
            f"  dst span x/y  : {best.dst_span_x:.3f}/{best.dst_span_y:.3f}\n"
            f"  global agree  : {best.global_agreement:.3f}\n"
            f"  affine cond.  : {qc_metrics.get('condition_number', float('nan')):.3f}\n"
            f"  orthogonality : {qc_metrics.get('orthogonality', float('nan')):.3f}\n"
            f"  transform sane: {best.transform_sane}\n"
            f"  QC reasons    : {', '.join(qc_reasons) if qc_reasons else '-'}"
        )

        if a.no_gcp_fallback:
            print("手動GCPフォールバックは --no-gcp-fallback により無効です。")
            return 5

        fallback=manual_gcp_fallback(
            raster,
            search_bbox,
            a.out,
            a,
            municipality_name=search_meta.get("selected_municipality"),
            automatic_best=best,
        )
        if fallback is None:
            print("手動GCPによる位置合わせを完了できませんでした。")
            return 5

        best,manual_gcp_info=fallback
        georef_method="manual_gcp"
        qc=True
        qc_grade="MANUAL_GCP"
        qc_metrics={
            "model":best.model,
            "gcp_count":manual_gcp_info["gcp_count"],
            "median_error_px":manual_gcp_info["median_error_px"],
            "rmse_px":manual_gcp_info["rmse_px"],
            "global_agreement":manual_gcp_info["global_agreement"],
            **manual_gcp_info["transform_metrics"],
        }
        qc_reasons=[]
        print("手動GCP位置合わせを採用します。")


    if not a.non_interactive:
        check_image = "05_gcp_overlay.png" if georef_method=="manual_gcp" else "05_overlay.png"
        ans=input(f"{check_image} を確認しましたか。続行 [y/n]: ").strip().lower()
        if ans not in ("y","yes"):
            return 0

    print("=== 6 代表点 EPSG:4326 ===")
    vals=[pdfxy_to_lonlat(r.pdf_x,r.pdf_y,raster,best) for _,r in labels.iterrows()]
    labels["lon"]=[v[0] for v in vals]
    labels["lat"]=[v[1] for v in vals]
    labels.to_csv(a.out/"06_map_labels_wgs84.csv",index=False,encoding="utf-8-sig")

    print("=== 7 Polygon ===")
    wgs_polys=[]
    if not a.skip_polygons:
        polys=[]
        for p in polygonize(unary_union(red)):
            p=make_valid(p)
            if isinstance(p,Polygon) and p.area>2: polys.append(p)
            elif isinstance(p,MultiPolygon): polys.extend([x for x in p.geoms if x.area>2])
        for p in polys:
            wgs_polys.append((p,transform_geom(p,raster,best)))
    save_geojson(a.out/"07_red_polygons_wgs84.geojson",[
        {"type":"Feature","properties":{"polygon_id":i},"geometry":mapping(w)}
        for i,(p,w) in enumerate(wgs_polys)
    ])

    matches=[]
    for _,r in labels.iterrows():
        pt=Point(r.pdf_x,r.pdf_y)
        hits=[(i,p) for i,(p,w) in enumerate(wgs_polys) if p.contains(pt) or p.touches(pt)]
        if hits:
            pid=min(hits,key=lambda x:x[1].area)[0]; method="contains"
        elif wgs_polys:
            pid=min(range(len(wgs_polys)),key=lambda i:wgs_polys[i][0].distance(pt)); method="nearest"
        else:
            pid=None; method="no_polygon"
        matches.append({"site_uid":r.site_uid,"polygon_id":pid,"polygon_match_method":method})
    mdf=pd.DataFrame(matches)
    mdf.to_csv(a.out/"07_label_polygon_matches.csv",index=False,encoding="utf-8-sig")

    print("=== 8 Merge ===")
    first=labels.sort_values("sequence").drop_duplicates("site_uid")

    master=table.merge(
        first[["site_uid","lon","lat","pdf_x","pdf_y"]],
        on="site_uid",
        how="left",
        validate="one_to_one",
    )
    master=master.merge(
        mdf.drop_duplicates("site_uid"),
        on="site_uid",
        how="left",
        validate="one_to_one",
    )

    coord_count=int(master["pdf_x"].notna().sum())
    lonlat_count=int((master["lon"].notna() & master["lat"].notna()).sum())

    print(
        f"  merged PDF coordinates : {coord_count}/{len(master)}\n"
        f"  merged WGS84 coords    : {lonlat_count}/{len(master)}"
    )

    master.to_csv(a.out/"08_sites_master.csv",index=False,encoding="utf-8-sig")

    save_json(
        a.out/"08_merge_qc.json",
        {
            "table_records": int(len(table)),
            "map_labels": int(len(labels)),
            "unique_label_uids": int(labels["site_uid"].nunique()) if len(labels) else 0,
            "table_label_uid_overlap": int(uid_overlap),
            "records_with_pdf_xy": coord_count,
            "records_with_lonlat": lonlat_count,
            "georef_method": georef_method,
        }
    )

    if len(labels)>0 and coord_count==0:
        raise RuntimeError(
            "Stage 8 merge後のpdf_x/pdf_yが全件空欄です。"
            " 02_map_labels_pdf.csv と一覧表のsite_uid対応を確認してください。"
        )

    feats=[]
    for _,r in master.iterrows():
        geom=None; method=None
        if pd.notna(r.get("polygon_id")):
            i=int(r.polygon_id)
            if 0<=i<len(wgs_polys):
                geom=wgs_polys[i][1]; method="polygon"
        if geom is None and pd.notna(r.get("lon")) and pd.notna(r.get("lat")):
            geom=Point(float(r.lon),float(r.lat)); method="label_point"
        if geom is None: continue
        props={}
        for k,v in r.items():
            if k in ("lon","lat"): continue
            if pd.isna(v): props[k]=None
            elif isinstance(v,np.integer): props[k]=int(v)
            elif isinstance(v,np.floating): props[k]=float(v)
            else: props[k]=v
        props["geometry_method"]=method
        feats.append({"type":"Feature","properties":props,"geometry":mapping(geom)})
    save_geojson(a.out/"08_sites_master.geojson",feats)

    qcinfo={
        "georef":bestinfo,
        "georef_method":georef_method,
        "manual_gcp":manual_gcp_info,
        "auto_qc_pass": bool(georef_method=="auto" and qc),
        "auto_qc_grade": qc_grade if georef_method=="auto" else None,
        "auto_qc_metrics": qc_metrics if georef_method=="auto" else None,
        "auto_qc_reasons": qc_reasons if georef_method=="auto" else [],
        "manual_gcp_qc_pass": bool(georef_method=="manual_gcp" and qc),
        "table_records":len(table),
        "map_labels":len(labels),
        "unique_map_labels":int(labels.site_uid.nunique()),
        "red_line_parts":len(red),
        "red_polygons":len(wgs_polys),
        "final_records":len(master),
        "final_with_lonlat":int(master.lon.notna().sum()),
        "gsi_attribution":"国土地理院 / 地理院タイル"
    }
    save_json(a.out/"09_qc.json",qcinfo)
    print("完了:", a.out)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
