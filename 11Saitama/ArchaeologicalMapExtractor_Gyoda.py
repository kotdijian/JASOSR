#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ArchaeologicalMapExtractor_Gyoda.py
===================================

行田市遺跡地図 PDF（例: 68_gyouda_city.pdf）について、以下を1本で処理します。

  Stage 1  PDF構造確認
  Stage 2  欄外一覧表抽出
  Stage 3  地図上の遺跡番号ラベル（PDF座標）抽出
  Stage 4  赤線ベクタ抽出
  ---- 中間確認 A ----
  Stage 5  GCP読込・ジオリファレンス
  ---- 中間確認 B ----
  Stage 6  赤線を地理座標へ変換・polygonize
  Stage 7  遺跡番号ラベルとPolygonを対応付け
  Stage 8  一覧表 + 代表点 + Polygon をマージ
  Stage 9  QC出力

設計方針
--------
・1本のスクリプトだが、中間成果物を全て保存する。
・各Stageは再実行可能。
・一覧表と地図番号はOCRではなくPDF内部テキストを優先。
・赤線はラスタ画像処理ではなくPDFベクタを優先。
・GCPのみ人手確認を前提とする。
・最終座標はEPSG:4326。
・ジオリファレンス計算は平面直角座標系などの projected CRS 上で実施する。
  埼玉県の既定値は JGD2011 / Japan Plane Rectangular CS IX (EPSG:6677)。
・代表点だけ必要な場合は、Polygon抽出に失敗しても番号ラベル座標を成果として残す。

必要パッケージ
--------------
  python3 -m pip install pymupdf numpy pandas matplotlib shapely pyproj

基本実行
--------
  python3 ArchaeologicalMapExtractor_Gyoda.py 68_gyouda_city.pdf \
      --out gyoda_extract

初回実行では Stage 1-4 後に中間確認し、GCPファイルが無ければ
  gyoda_extract/gcp.csv
を作って停止する。

gcp.csv を編集後、同じコマンドを再実行すれば続行する。

gcp.csv 書式
------------
  id,pdf_x,pdf_y,lon,lat,enabled,note
  1,1234.56,789.01,139.XXXXXX,36.XXXXXX,1,道路交差点
  ...

pdf_x / pdf_y:
    PDFページ座標。Stage 1で生成する map_preview_with_xy.png の軸を参照。
lon / lat:
    EPSG:4326。

推奨GCP数:
    最低4点、実用上10～20点。地図全域に分散させる。

自動停止・確認
--------------
--non-interactive を付けない限り、主要段階で y/n を求めます。

出力例
------
00_inspection.json
01_table.csv
02_map_labels_pdf.csv
03_red_paths_pdf.geojson
04_map_preview.png
04_map_preview_with_xy.png
gcp.csv
05_gcp_residuals.csv
06_map_labels_wgs84.csv
07_red_polygons_wgs84.geojson
08_sites_master.csv
08_sites_master.geojson
09_qc.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

import numpy as np
import pandas as pd

try:
    from shapely.geometry import (
        LineString, MultiLineString, Point, Polygon, MultiPolygon, mapping, shape
    )
    from shapely.ops import unary_union, polygonize, transform as shp_transform
    from shapely.validation import make_valid
except ImportError as e:
    raise SystemExit("shapely が必要です: python3 -m pip install shapely") from e

try:
    from pyproj import CRS, Transformer, Geod
except ImportError as e:
    raise SystemExit("pyproj が必要です: python3 -m pip install pyproj") from e


# ============================================================
# 定数
# ============================================================

TYPE_FIELDS = [
    "旧石器", "貝塚", "集落跡", "古墳群", "古墳", "横穴", "窯跡",
    "祭祀", "経塚", "墓", "寺院跡", "城跡", "石造遺物", "散布地", "その他"
]

PERIOD_FIELDS = [
    "旧石器", "縄文", "弥生", "古墳", "奈良", "平安", "鎌倉",
    "南北朝", "室町", "戦国", "江戸", "不明"
]

FULLWIDTH_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")

# 行田市PDFで確認された右側一覧表（市町村番号68）のヘッダ中心。
# PDF構造から自動抽出できない時だけフォールバックとして使う。
TYPE_X_68 = [
    2861.8, 2874.4, 2886.9, 2899.5, 2912.1, 2924.6, 2937.2,
    2949.7, 2962.2, 2974.8, 2987.4, 2999.9, 3012.5, 3025.0, 3037.6
]
PERIOD_X_68 = [
    3080.0, 3092.6, 3105.1, 3117.6, 3130.2, 3142.7,
    3155.3, 3167.8, 3180.4, 3192.9, 3205.5, 3218.1
]

# 74表はヘッダ位置から動的推定するため、フォールバックは後で推定。


# ============================================================
# 基本ユーティリティ
# ============================================================

def normalize_digits(s: str) -> str:
    return s.translate(FULLWIDTH_TRANS)


def clean_text(s: str) -> str:
    s = normalize_digits(s)
    s = s.replace("\u3000", "")
    s = re.sub(r"\s+", "", s)
    return s.strip()


def confirm(message: str, non_interactive: bool) -> bool:
    if non_interactive:
        return True
    while True:
        ans = input(f"\n{message} [y/n]: ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def geojson_fc(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def save_geojson(path: Path, features: list[dict]) -> None:
    write_json(path, geojson_fc(features))


# ============================================================
# Stage 1: PDF検査
# ============================================================

def is_red(color) -> bool:
    if color is None or len(color) < 3:
        return False
    r, g, b = color[:3]
    return r >= 0.85 and g <= 0.25 and b <= 0.25


def inspect_pdf(pdf_path: Path, page_no: int) -> tuple[Any, Any, dict]:
    doc = pymupdf.open(pdf_path)
    if page_no < 0 or page_no >= len(doc):
        raise ValueError(f"page={page_no} は範囲外です。PDF={len(doc)} pages")
    page = doc[page_no]

    drawings = page.get_drawings()
    red_drawings = [d for d in drawings if is_red(d.get("color"))]

    if not red_drawings:
        raise RuntimeError("赤色ベクタ線を検出できませんでした。")

    x0 = min(d["rect"].x0 for d in red_drawings)
    y0 = min(d["rect"].y0 for d in red_drawings)
    x1 = max(d["rect"].x1 for d in red_drawings)
    y1 = max(d["rect"].y1 for d in red_drawings)

    # 赤線の包絡矩形を地図域の基準にする。
    pad = 30
    map_bbox = [
        max(0, x0 - pad),
        max(0, y0 - pad),
        min(page.rect.width, x1 + pad),
        min(page.rect.height, y1 + pad),
    ]

    result = {
        "pdf": str(pdf_path),
        "page": page_no,
        "page_width": page.rect.width,
        "page_height": page.rect.height,
        "drawing_count": len(drawings),
        "red_drawing_count": len(red_drawings),
        "map_bbox_pdf": map_bbox,
    }
    return doc, page, result


# ============================================================
# Stage 2: 欄外一覧表
# ============================================================

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


def extract_table(page, out_csv: Path) -> pd.DataFrame:
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


# ============================================================
# Stage 3: 地図上番号ラベル
# ============================================================

def extract_numeric_spans(page, map_bbox: list[float]) -> list[dict]:
    x0b, y0b, x1b, y1b = map_bbox
    spans = []

    text_dict = page.get_text("dict")
    seq = 0
    for block in text_dict["blocks"]:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            for s in line["spans"]:
                t = normalize_digits(s["text"].strip())
                if not re.fullmatch(r"\d{1,3}", t):
                    continue
                x0, y0, x1, y1 = s["bbox"]
                xc, yc = (x0+x1)/2, (y0+y1)/2
                if not (x0b <= xc <= x1b and y0b <= yc <= y1b):
                    continue
                # このPDFの地図番号は MS-Gothic 8pt
                if abs(float(s["size"]) - 8.0) > 0.5:
                    continue
                spans.append({
                    "sequence": seq,
                    "label_no": int(t),
                    "pdf_x": xc,
                    "pdf_y": yc,
                    "font": s["font"],
                    "font_size": s["size"],
                })
                seq += 1
    return spans


def assign_municipality_to_labels(spans: list[dict]) -> list[dict]:
    """
    このPDFでは旧市町村番号74の地図ラベル26件が、
    PDFテキストストリーム中で 1..26 をちょうど1回ずつ含む
    連続26要素として埋め込まれている。
    そのチャンクを74、それ以外を68とする。
    """
    values = [s["label_no"] for s in spans]
    chunk_start = None
    target = set(range(1, 27))
    for i in range(0, len(values) - 26 + 1):
        chunk = values[i:i+26]
        if len(set(chunk)) == 26 and set(chunk) == target:
            chunk_start = i
            break

    if chunk_start is None:
        # 74を自動判定できない場合は全て68として出し、QCで警告。
        for s in spans:
            s["municipality_code"] = "68"
            s["site_no"] = f"{s['label_no']:03d}"
            s["site_uid"] = f"68-{s['site_no']}"
            s["municipality_assignment"] = "fallback_68"
        return spans

    for i, s in enumerate(spans):
        city = "74" if chunk_start <= i < chunk_start + 26 else "68"
        s["municipality_code"] = city
        s["site_no"] = f"{s['label_no']:03d}"
        s["site_uid"] = f"{city}-{s['site_no']}"
        s["municipality_assignment"] = "stream_chunk_74" if city == "74" else "stream_68"
    return spans


def extract_map_labels(page, map_bbox: list[float], out_csv: Path) -> pd.DataFrame:
    spans = extract_numeric_spans(page, map_bbox)
    spans = assign_municipality_to_labels(spans)
    df = pd.DataFrame(spans)
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    return df


# ============================================================
# Stage 4: 赤線ベクタ
# ============================================================

def bezier_points(p0, p1, p2, p3, n=12):
    pts = []
    for t in np.linspace(0, 1, n):
        x = (
            (1-t)**3 * p0.x
            + 3*(1-t)**2*t*p1.x
            + 3*(1-t)*t**2*p2.x
            + t**3*p3.x
        )
        y = (
            (1-t)**3 * p0.y
            + 3*(1-t)**2*t*p1.y
            + 3*(1-t)*t**2*p2.y
            + t**3*p3.y
        )
        pts.append((float(x), float(y)))
    return pts


def drawing_to_lines(d: dict) -> list[LineString]:
    lines = []
    current = []

    def flush():
        nonlocal current
        if len(current) >= 2:
            # 連続重複除去
            cleaned = [current[0]]
            for p in current[1:]:
                if p != cleaned[-1]:
                    cleaned.append(p)
            if len(cleaned) >= 2:
                lines.append(LineString(cleaned))
        current = []

    for item in d["items"]:
        op = item[0]
        if op == "l":
            _, p0, p1 = item
            if not current:
                current = [(p0.x, p0.y), (p1.x, p1.y)]
            else:
                if current[-1] != (p0.x, p0.y):
                    flush()
                    current = [(p0.x, p0.y), (p1.x, p1.y)]
                else:
                    current.append((p1.x, p1.y))
        elif op == "c":
            _, p0, p1, p2, p3 = item
            curve = bezier_points(p0, p1, p2, p3)
            if not current:
                current = curve
            else:
                if current[-1] != curve[0]:
                    flush()
                    current = curve
                else:
                    current.extend(curve[1:])
        elif op == "re":
            _, rect, _orientation = item
            flush()
            pts = [
                (rect.x0, rect.y0), (rect.x1, rect.y0),
                (rect.x1, rect.y1), (rect.x0, rect.y1),
                (rect.x0, rect.y0)
            ]
            lines.append(LineString(pts))
        else:
            flush()
    flush()
    return lines


def extract_red_paths(page, out_geojson: Path) -> list[LineString]:
    features = []
    all_lines = []
    draw_index = 0
    for d in page.get_drawings():
        if not is_red(d.get("color")):
            continue
        lines = drawing_to_lines(d)
        for j, line in enumerate(lines):
            all_lines.append(line)
            features.append({
                "type": "Feature",
                "properties": {
                    "draw_index": draw_index,
                    "part": j,
                    "stroke_width": d.get("width"),
                },
                "geometry": mapping(line),
            })
        draw_index += 1
    save_geojson(out_geojson, features)
    return all_lines


# ============================================================
# Preview
# ============================================================

def render_preview(
    page,
    map_bbox: list[float],
    labels_df: pd.DataFrame,
    red_lines: list[LineString],
    out_png: Path,
    out_xy_png: Path,
    dpi: int = 150,
):
    import matplotlib.pyplot as plt

    x0, y0, x1, y1 = map_bbox

    # PDFラスタを背景に
    clip = pymupdf.Rect(x0, y0, x1, y1)
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)

    # 通常プレビュー
    fig, ax = plt.subplots(figsize=(12, 12 * (y1-y0)/(x1-x0)))
    ax.imshow(img, extent=[x0, x1, y1, y0])
    for _, r in labels_df.iterrows():
        ax.text(r.pdf_x, r.pdf_y, f"{r.municipality_code}-{int(r.label_no)}",
                fontsize=5, color="blue")
    ax.set_xlim(x0, x1)
    ax.set_ylim(y1, y0)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_png, dpi=180, bbox_inches="tight")
    plt.close(fig)

    # XY確認用
    fig, ax = plt.subplots(figsize=(13, 13 * (y1-y0)/(x1-x0)))
    ax.imshow(img, extent=[x0, x1, y1, y0])
    for _, r in labels_df.iterrows():
        ax.scatter([r.pdf_x], [r.pdf_y], s=3)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y1, y0)
    ax.set_xlabel("PDF x")
    ax.set_ylabel("PDF y")
    ax.grid(True, alpha=.35)
    fig.tight_layout()
    fig.savefig(out_xy_png, dpi=180)
    plt.close(fig)


# ============================================================
# Stage 5: GCP / affine
# ============================================================

def create_gcp_template(path: Path):
    if path.exists():
        return
    pd.DataFrame(columns=[
        "id", "pdf_x", "pdf_y", "lon", "lat", "enabled", "note"
    ]).to_csv(path, index=False, encoding="utf-8-sig")


def load_gcps(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"pdf_x", "pdf_y", "lon", "lat"}
    if not required.issubset(df.columns):
        raise ValueError(f"GCP CSV に {sorted(required)} が必要です。")
    if "enabled" in df.columns:
        df = df[df["enabled"].fillna(1).astype(str).isin(["1", "True", "true", "YES", "yes"])]
    df = df.dropna(subset=["pdf_x", "pdf_y", "lon", "lat"]).copy()
    for c in ["pdf_x", "pdf_y", "lon", "lat"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["pdf_x", "pdf_y", "lon", "lat"])


def fit_affine_gcps(gcp: pd.DataFrame, fit_crs: str):
    """
    PDF (x,y) -> projected CRS (X,Y) の6パラメータ affine:
      X = a0 + a1*x + a2*y
      Y = b0 + b1*x + b2*y
    """
    if len(gcp) < 3:
        raise ValueError("Affineには最低3 GCP必要です。実用上は10点以上推奨。")

    to_proj = Transformer.from_crs("EPSG:4326", fit_crs, always_xy=True)
    X, Y = to_proj.transform(gcp["lon"].to_numpy(), gcp["lat"].to_numpy())

    A = np.column_stack([
        np.ones(len(gcp)),
        gcp["pdf_x"].to_numpy(),
        gcp["pdf_y"].to_numpy(),
    ])

    ax, *_ = np.linalg.lstsq(A, X, rcond=None)
    ay, *_ = np.linalg.lstsq(A, Y, rcond=None)

    predX = A @ ax
    predY = A @ ay
    residual = np.sqrt((predX-X)**2 + (predY-Y)**2)

    model = {
        "fit_crs": fit_crs,
        "ax": ax.tolist(),
        "ay": ay.tolist(),
        "rmse_m": float(np.sqrt(np.mean(residual**2))),
        "max_residual_m": float(np.max(residual)),
    }
    return model, residual


def transform_pdf_xy(x: float, y: float, model: dict) -> tuple[float, float]:
    ax = np.asarray(model["ax"])
    ay = np.asarray(model["ay"])
    v = np.array([1.0, x, y])
    X = float(v @ ax)
    Y = float(v @ ay)
    to_wgs = Transformer.from_crs(model["fit_crs"], "EPSG:4326", always_xy=True)
    lon, lat = to_wgs.transform(X, Y)
    return float(lon), float(lat)


def transform_labels(labels_df: pd.DataFrame, model: dict, out_csv: Path) -> pd.DataFrame:
    df = labels_df.copy()
    ll = [transform_pdf_xy(r.pdf_x, r.pdf_y, model) for _, r in df.iterrows()]
    df["lon"] = [x[0] for x in ll]
    df["lat"] = [x[1] for x in ll]
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    return df


# ============================================================
# Stage 6: Polygonize + georef
# ============================================================

def polygonize_red_lines(red_lines: list[LineString]) -> list[Polygon]:
    # line merge / noding
    merged = unary_union(red_lines)
    polys = list(polygonize(merged))
    out = []
    for p in polys:
        if p.is_empty or p.area <= 2:
            continue
        p2 = make_valid(p)
        if isinstance(p2, Polygon):
            out.append(p2)
        elif isinstance(p2, MultiPolygon):
            out.extend(list(p2.geoms))
    return out


def transform_geom_pdf_to_wgs(geom, model: dict):
    def fn(x, y, z=None):
        # shapely transform may pass arrays
        xarr = np.asarray(x)
        yarr = np.asarray(y)
        ax = np.asarray(model["ax"])
        ay = np.asarray(model["ay"])
        X = ax[0] + ax[1]*xarr + ax[2]*yarr
        Y = ay[0] + ay[1]*xarr + ay[2]*yarr
        tr = Transformer.from_crs(model["fit_crs"], "EPSG:4326", always_xy=True)
        lon, lat = tr.transform(X, Y)
        return lon, lat
    return shp_transform(fn, geom)


def georef_polygons(red_lines: list[LineString], model: dict, out_geojson: Path):
    pdf_polys = polygonize_red_lines(red_lines)
    features = []
    wgs_polys = []
    for i, p in enumerate(pdf_polys):
        w = transform_geom_pdf_to_wgs(p, model)
        wgs_polys.append((p, w))
        features.append({
            "type": "Feature",
            "properties": {"polygon_id": i, "pdf_area": p.area},
            "geometry": mapping(w),
        })
    save_geojson(out_geojson, features)
    return wgs_polys


# ============================================================
# Stage 7: Label -> Polygon
# ============================================================

def match_labels_to_polygons(labels_pdf: pd.DataFrame, wgs_polys: list[tuple[Polygon, Polygon]]):
    """
    PDF座標上で contains を優先。
    含まれなければ nearest polygon。
    """
    rows = []
    for _, r in labels_pdf.iterrows():
        pt = Point(float(r.pdf_x), float(r.pdf_y))
        containing = []
        for i, (pp, wp) in enumerate(wgs_polys):
            if pp.contains(pt) or pp.touches(pt):
                containing.append((i, pp, wp))

        if containing:
            # 複数なら最小面積
            i, pp, wp = min(containing, key=lambda x: x[1].area)
            method = "contains"
            dist = 0.0
        else:
            if not wgs_polys:
                i = None
                pp = wp = None
                method = "no_polygon"
                dist = None
            else:
                vals = [(i, pp.distance(pt), pp, wp) for i, (pp, wp) in enumerate(wgs_polys)]
                i, dist, pp, wp = min(vals, key=lambda x: x[1])
                method = "nearest"

        rows.append({
            "site_uid": r.site_uid,
            "polygon_id": i,
            "polygon_match_method": method,
            "polygon_distance_pdf": dist,
        })
    return pd.DataFrame(rows)


# ============================================================
# Stage 8: Merge / final GeoJSON
# ============================================================

def build_final(
    table_df: pd.DataFrame,
    labels_wgs: pd.DataFrame,
    matches_df: pd.DataFrame,
    wgs_polys: list[tuple[Polygon, Polygon]],
    out_csv: Path,
    out_geojson: Path,
):
    # 同じsite_uidが複数ラベルになる場合はまず最初を採用し、QCへ
    label_first = labels_wgs.sort_values("sequence").drop_duplicates("site_uid", keep="first")
    merged = table_df.merge(
        label_first[["site_uid", "pdf_x", "pdf_y", "lon", "lat"]],
        on="site_uid", how="left"
    ).merge(matches_df.drop_duplicates("site_uid"), on="site_uid", how="left")

    merged.to_csv(out_csv, index=False, encoding="utf-8-sig")

    features = []
    for _, row in merged.iterrows():
        geom = None
        geom_method = None

        pid = row.get("polygon_id")
        if pd.notna(pid):
            pid = int(pid)
            if 0 <= pid < len(wgs_polys):
                geom = wgs_polys[pid][1]
                geom_method = "polygon"

        if geom is None and pd.notna(row.get("lon")) and pd.notna(row.get("lat")):
            geom = Point(float(row.lon), float(row.lat))
            geom_method = "label_point"

        if geom is None:
            continue

        props = {}
        for c, v in row.items():
            if c in ("lon", "lat"):
                continue
            if pd.isna(v):
                props[c] = None
            elif isinstance(v, (np.integer,)):
                props[c] = int(v)
            elif isinstance(v, (np.floating,)):
                props[c] = float(v)
            else:
                props[c] = v
        props["geometry_method"] = geom_method

        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": mapping(geom),
        })
    save_geojson(out_geojson, features)
    return merged


# ============================================================
# QC
# ============================================================

def qc_report(
    table_df, labels_df, labels_wgs, final_df, model, wgs_polys
):
    table_uids = set(table_df["site_uid"])
    label_uids = set(labels_df["site_uid"])
    active_uids = set(table_df.loc[table_df["record_status"] == "active", "site_uid"])

    duplicate_labels = (
        labels_df.groupby("site_uid").size().loc[lambda s: s > 1].to_dict()
    )

    return {
        "table_records": len(table_df),
        "table_active": int((table_df["record_status"] == "active").sum()),
        "table_merged": int((table_df["record_status"] == "merged").sum()),
        "table_missing_number": int((table_df["record_status"] == "missing_number").sum()),
        "map_label_records": len(labels_df),
        "unique_map_label_uids": len(label_uids),
        "active_table_without_label": sorted(active_uids - label_uids),
        "labels_not_in_table": sorted(label_uids - table_uids),
        "duplicate_map_labels": duplicate_labels,
        "red_polygon_count": len(wgs_polys),
        "gcp_model": model,
        "final_records": len(final_df),
        "final_with_lonlat": int(final_df["lon"].notna().sum()),
        "final_with_polygon": int(final_df["polygon_id"].notna().sum()),
    }


# ============================================================
# Main
# ============================================================

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--out", type=Path, default=Path("archaeological_map_extract"))
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--fit-crs", default="EPSG:6677",
                    help="GCP fitting用 projected CRS。埼玉県既定 EPSG:6677")
    ap.add_argument("--gcp", type=Path, default=None)
    ap.add_argument("--non-interactive", action="store_true")
    ap.add_argument("--skip-polygons", action="store_true",
                    help="代表点のみ作成し赤線polygonizeをスキップ")
    return ap.parse_args()


def main():
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    inspection_json = args.out / "00_inspection.json"
    table_csv = args.out / "01_table.csv"
    labels_pdf_csv = args.out / "02_map_labels_pdf.csv"
    red_pdf_geojson = args.out / "03_red_paths_pdf.geojson"
    preview_png = args.out / "04_map_preview.png"
    preview_xy_png = args.out / "04_map_preview_with_xy.png"
    gcp_csv = args.gcp or (args.out / "gcp.csv")
    residual_csv = args.out / "05_gcp_residuals.csv"
    labels_wgs_csv = args.out / "06_map_labels_wgs84.csv"
    polygons_wgs_geojson = args.out / "07_red_polygons_wgs84.geojson"
    final_csv = args.out / "08_sites_master.csv"
    final_geojson = args.out / "08_sites_master.geojson"
    qc_json = args.out / "09_qc.json"

    print("\n=== Stage 1: PDF検査 ===")
    doc, page, inspection = inspect_pdf(args.pdf, args.page)
    write_json(inspection_json, inspection)
    print(json.dumps(inspection, ensure_ascii=False, indent=2))

    print("\n=== Stage 2: 欄外一覧表抽出 ===")
    table_df = extract_table(page, table_csv)
    print(f"table records: {len(table_df)}")
    print(table_df[["site_uid", "site_name", "record_status"]].head(15).to_string(index=False))

    print("\n=== Stage 3: 地図番号ラベル抽出 ===")
    labels_df = extract_map_labels(page, inspection["map_bbox_pdf"], labels_pdf_csv)
    print(f"map labels: {len(labels_df)} / unique={labels_df.site_uid.nunique()}")
    print(labels_df[["sequence", "site_uid", "pdf_x", "pdf_y"]].head(15).to_string(index=False))

    print("\n=== Stage 4: 赤線ベクタ抽出 ===")
    red_lines = extract_red_paths(page, red_pdf_geojson)
    print(f"red line parts: {len(red_lines)}")

    render_preview(
        page, inspection["map_bbox_pdf"], labels_df, red_lines,
        preview_png, preview_xy_png
    )
    print(f"preview: {preview_png}")
    print(f"XY preview: {preview_xy_png}")

    # 初期QC
    stage_a = {
        "table_count": len(table_df),
        "label_count": len(labels_df),
        "label_unique_count": labels_df.site_uid.nunique(),
        "red_line_count": len(red_lines),
        "table_status_counts": table_df.record_status.value_counts().to_dict(),
    }
    write_json(args.out / "04_stageA_summary.json", stage_a)

    if not confirm(
        "中間確認A: 01_table.csv、02_map_labels_pdf.csv、04_map_preview.png を確認しましたか？ 続行しますか？",
        args.non_interactive
    ):
        print("Stage 4で停止しました。中間成果物を修正・確認後、同じコマンドで再実行してください。")
        return 0

    print("\n=== Stage 5: GCP ===")
    create_gcp_template(gcp_csv)
    gcp = load_gcps(gcp_csv)

    if len(gcp) < 3:
        print(f"""
GCPが不足しています ({len(gcp)} points)。
以下を編集してください:
  {gcp_csv}

04_map_preview_with_xy.png の PDF x/y を参照し、
地理院地図/QGIS等で同一点の lon/lat (EPSG:4326) を入力してください。
最低3点、推奨10～20点です。

編集後、同じコマンドを再実行してください。
""")
        return 2

    model, residual = fit_affine_gcps(gcp, args.fit_crs)
    gcp2 = gcp.copy()
    gcp2["residual_m"] = residual
    gcp2.to_csv(residual_csv, index=False, encoding="utf-8-sig")

    print(json.dumps(model, ensure_ascii=False, indent=2))
    print(f"GCP residuals: {residual_csv}")

    if not confirm(
        f"中間確認B: GCP RMSE={model['rmse_m']:.2f} m, max={model['max_residual_m']:.2f} m。続行しますか？",
        args.non_interactive
    ):
        print("GCP確認で停止しました。gcp.csv を修正して再実行してください。")
        return 0

    labels_wgs = transform_labels(labels_df, model, labels_wgs_csv)

    print("\n=== Stage 6: 赤線Polygon ===")
    if args.skip_polygons:
        wgs_polys = []
        save_geojson(polygons_wgs_geojson, [])
        print("skip-polygons: 代表点のみ")
    else:
        wgs_polys = georef_polygons(red_lines, model, polygons_wgs_geojson)
        print(f"polygon count: {len(wgs_polys)}")

    print("\n=== Stage 7: 番号とPolygon対応付け ===")
    matches_df = match_labels_to_polygons(labels_df, wgs_polys)
    matches_df.to_csv(args.out / "07_label_polygon_matches.csv",
                      index=False, encoding="utf-8-sig")
    print(matches_df["polygon_match_method"].value_counts(dropna=False).to_string())

    print("\n=== Stage 8: 一覧表 + 座標 + Polygon マージ ===")
    final_df = build_final(
        table_df, labels_wgs, matches_df, wgs_polys,
        final_csv, final_geojson
    )
    print(f"CSV:     {final_csv}")
    print(f"GeoJSON: {final_geojson}")

    print("\n=== Stage 9: QC ===")
    qc = qc_report(table_df, labels_df, labels_wgs, final_df, model, wgs_polys)
    write_json(qc_json, qc)
    print(json.dumps(qc, ensure_ascii=False, indent=2))
    print(f"QC: {qc_json}")

    print("\n完了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
