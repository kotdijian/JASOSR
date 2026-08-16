#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sagamihara archaeological-site Search API test collector
---------------------------------------------------------

目的
- 相模原市 geocloud WebGIS の /webgis/Search を少数回だけ呼び出し、
  mapId=13-36（埋蔵文化財包蔵地）の Search 応答を収集する。
- itemId で重複排除し、ISEKI_NO / TYPE / ERA / x / y を CSV 化する。
- 参照PDFがあれば、その「遺跡No.」候補と照合する。
- 高負荷回避を優先し、キャッシュ・待機・再試行・途中保存を行う。

注意
- これは「試験コード」です。まず --test で動作確認してください。
- Search API の検索範囲は level と中心点に依存するため、
  545という値を固定的な「APIレコード総数」とは仮定しません。
- PDFには 42-1, 186-1 など枝番があるため、「最大番号545」と
  「実レコード数545」は同義とは限りません。PDF照合結果で確認します。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


BASE_URL = "https://sagamihara.geocloud.jp/webgis/Search"
MAP_ID = "13-36"
SRS = "EPSG:4326"

# 画面サイズ由来とみられる u/v は、観測済みリクエスト値を既定値として保持。
DEFAULT_U = None
DEFAULT_V = None

# 1/64,000 相当の画面で観測された level=13 を既定値とする。
DEFAULT_LEVEL = 13

# 相模原市の細長い市域に沿う「少数試験点」。
# まずは東・中央・西の3点だけ。全域走査ではない。
TEST_CENTERS = [
    ("east",    139.3900, 35.5550),
    ("central", 139.3000, 35.5750),
    ("west",    139.22897879561378, 35.57564433403656),
]

# 試験後に使う低密度スイープ候補。
# 1/64,000 相当（level=13）を固定し、
# 市域の細長い形状に沿って西→東へ配置。
SWEEP_CENTERS = [
    ("w1", 139.115, 35.610),
    ("w2", 139.170, 35.595),
    ("w3", 139.22897879561378, 35.57564433403656),
    ("c1", 139.285, 35.575),
    ("c2", 139.335, 35.565),
    ("e1", 139.385, 35.555),
    ("e2", 139.430, 35.545),
]

# 2次元タイル走査（level=13）
# まずは市域を覆う低密度矩形。市域外タイルが含まれても、
# Searchレスポンス0件ならそのまま無視される。
# 既存キャッシュがあれば再利用される。
TILE_GRID = {
    "u_min": 7260,
    "u_max": 7269,
    "v_min": 3226,
    "v_max": 3230,
}




@dataclass
class FetchResult:
    label: str
    lng: float
    lat: float
    level: int
    cache_path: Path
    data: dict[str, Any]
    from_cache: bool


def normalized_site_no(value: str) -> str:
    """PDF/API比較用に全角ダッシュ・枝番前後空白などを正規化。"""
    s = (value or "").strip()
    s = s.replace("－", "-").replace("–", "-").replace("—", "-").replace("−", "-")
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"\s+", "", s)
    return s


def tile_to_center_lonlat(u: int, v: int, level: int) -> tuple[float, float]:
    """
    XYZタイル(u,v,level)の中心点を経緯度で返す。
    Search APIへ渡す lng/lat と u/v を完全に整合させるために使用。
    """
    n = 2 ** level
    x = (u + 0.5) / n
    y = (v + 0.5) / n

    lon = x * 360.0 - 180.0

    merc_y = 3.141592653589793 * (1.0 - 2.0 * y)
    lat = math.degrees(math.atan(math.sinh(merc_y)))

    return lon, lat


def lonlat_to_tile(lon: float, lat: float, level: int) -> tuple[int, int]:
    """
    Web Mercator / XYZ タイル座標を計算。
    geocloud Search の u/v と一致することを確認済み。
    """
    n = 2 ** level
    u = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    v = int(
        (1.0 - math.asinh(math.tan(lat_rad)) / math.pi)
        / 2.0 * n
    )
    return u, v


def make_url(
    lng: float,
    lat: float,
    level: int,
    u: int | None = None,
    v: int | None = None,
) -> str:
    if u is None or v is None:
        u, v = lonlat_to_tile(lng, lat, level)

    params = {
        "srs": SRS,
        "u": u,
        "v": v,
        "mapId": MAP_ID,
        "level": level,
        "lng": f"{lng:.12f}",
        "lat": f"{lat:.12f}",
        "params": "-1",
    }
    return BASE_URL + "?" + urlencode(params)


def cache_name(
    label: str,
    lng: float,
    lat: float,
    level: int,
    u: int | None = None,
    v: int | None = None,
) -> str:
    if u is None or v is None:
        u, v = lonlat_to_tile(lng, lat, level)
    key = f"{MAP_ID}|{level}|{lng:.12f}|{lat:.12f}|{u}|{v}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"{label}_L{level}_{digest}.json"


def fetch_search(
    label: str,
    lng: float,
    lat: float,
    level: int,
    u: int,
    v: int,
    cache_dir: Path,
    timeout: float,
    retries: int,
    force: bool,
) -> FetchResult:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cp = cache_dir / cache_name(label, lng, lat, level, u, v)

    if cp.exists() and not force:
        with cp.open("r", encoding="utf-8") as f:
            return FetchResult(label, lng, lat, level, cp, json.load(f), True)

    url = make_url(lng, lat, level, u, v)
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://sagamihara.geocloud.jp/webgis/",
        "User-Agent": "Mozilla/5.0 (compatible; SagamiharaSearchTest/0.1)",
        "X-Requested-With": "XMLHttpRequest",
    }

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers=headers, method="GET")
            with urlopen(req, timeout=timeout) as r:
                raw = r.read()
            text = raw.decode("utf-8")
            data = json.loads(text)
            tmp = cp.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(cp)
            return FetchResult(label, lng, lat, level, cp, data, False)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            if attempt >= retries:
                break
            # 指数バックオフ。連続アクセスを避ける。
            time.sleep((2 ** attempt) * 2.0 + random.uniform(0.5, 1.5))

    raise RuntimeError(f"Search取得失敗: {label} {url}\n{last_err}")


def points_from_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    ret = data.get("ret") or {}
    pts = ret.get("point") or []
    return pts if isinstance(pts, list) else []


def attr_value(point: dict[str, Any], column_name: str) -> str:
    attrs = point.get("attributes") or {}
    for _, obj in attrs.items():
        if isinstance(obj, dict) and obj.get("columnName") == column_name:
            value = obj.get("value")
            return "" if value is None else str(value)
    return ""


def flatten_point(point: dict[str, Any], source_label: str, source_level: int) -> dict[str, Any]:
    return {
        "itemId": point.get("itemId", ""),
        "ISEKI_NO": attr_value(point, "ISEKI_NO"),
        "TYPE": attr_value(point, "TYPE"),
        "ERA": attr_value(point, "ERA"),
        "x": point.get("x", ""),
        "y": point.get("y", ""),
        "name": point.get("name", ""),
        "layerId": point.get("layerId", ""),
        "layerName": point.get("layerName", ""),
        "mapId": point.get("mapId", ""),
        "orgGeomType": point.get("orgGeomType", ""),
        "source_first_seen": source_label,
        "source_level": source_level,
    }


def merge_points(
    all_points: dict[str, dict[str, Any]],
    point_sources: dict[str, set[str]],
    pts: list[dict[str, Any]],
    source_label: str,
    level: int,
) -> tuple[int, int]:
    added = 0
    duplicates = 0

    for p in pts:
        item_id = p.get("itemId")
        if item_id is None:
            # itemId欠損時だけ座標+遺跡番号で暫定キーを作る。
            item_id = f"NOID:{attr_value(p,'ISEKI_NO')}:{p.get('x')}:{p.get('y')}"
        key = str(item_id)

        point_sources.setdefault(key, set()).add(source_label)
        if key in all_points:
            duplicates += 1
            continue

        all_points[key] = flatten_point(p, source_label, level)
        added += 1

    return added, duplicates


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "itemId", "ISEKI_NO", "TYPE", "ERA", "x", "y",
        "name", "layerId", "layerName", "mapId", "orgGeomType",
        "source_first_seen", "source_level", "source_hits",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def extract_pdf_site_numbers(pdf_path: Path) -> set[str]:
    """
    PDFから遺跡No.候補を抽出。
    PyMuPDFが無ければ空集合を返し、API試験自体は継続する。

    表組みPDFなので完全な表復元は狙わず、
    「PDFに存在するNo.集合」とWebGIS取得No.集合の照合用途に限定する。
    """
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            print("[PDF] PyMuPDF未導入のためPDF照合をスキップします。")
            print("      pip install pymupdf")
            return set()

    doc = pymupdf.open(pdf_path)
    text = "\n".join(page.get_text("text") for page in doc)

    # 行頭付近の番号を優先。42-1 / 186-14 のような枝番を許容。
    # 「令和8年」等の本文数字を拾いにくくするため 1..545 を範囲制限。
    found: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^(\d{1,3})(?:\s*-\s*(\d{1,2}))?(?:\s|$)", line)
        if not m:
            continue
        base = int(m.group(1))
        if 1 <= base <= 545:
            no = str(base)
            if m.group(2):
                no += "-" + str(int(m.group(2)))
            found.add(no)

    return found


def compare_with_pdf(rows: list[dict[str, Any]], pdf_path: Path) -> None:
    pdf_ids = extract_pdf_site_numbers(pdf_path)
    if not pdf_ids:
        return

    api_ids = {
        normalized_site_no(str(r.get("ISEKI_NO", "")))
        for r in rows
        if normalized_site_no(str(r.get("ISEKI_NO", "")))
    }

    missing = sorted(pdf_ids - api_ids, key=site_sort_key)
    extra = sorted(api_ids - pdf_ids, key=site_sort_key)

    print("\n[PDF照合]")
    print(f" PDFから抽出した遺跡No.候補 : {len(pdf_ids)}")
    print(f" API取得の非空ISEKI_NO       : {len(api_ids)}")
    print(f" 共通                         : {len(pdf_ids & api_ids)}")
    print(f" PDFにありAPIに未取得         : {len(missing)}")
    print(f" APIにありPDF抽出にない       : {len(extra)}")
    if missing:
        print(" 未取得例:", ", ".join(missing[:30]))
    if extra:
        print(" 追加例  :", ", ".join(extra[:30]))


def site_sort_key(s: str):
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", s)
    if not m:
        return (10**9, 10**9, s)
    return (int(m.group(1)), int(m.group(2) or 0), "")


def build_tile_grid_centers(
    level: int,
    u_min: int,
    u_max: int,
    v_min: int,
    v_max: int,
) -> list[tuple[str, float, float, int, int]]:
    """
    指定したXYZタイル範囲の各タイル中心を検索中心として返す。
    戻り値: (label, lng, lat, u, v)
    """
    centers = []

    for v in range(v_min, v_max + 1):
        for u in range(u_min, u_max + 1):
            lon, lat = tile_to_center_lonlat(u, v, level)
            centers.append((f"u{u}_v{v}", lon, lat, u, v))

    return centers


def parse_centers_file(path: Path) -> list[tuple[str, float, float]]:
    centers = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=1):
            label = (row.get("label") or f"p{i}").strip()
            lng = float(row["lng"])
            lat = float(row["lat"])
            centers.append((label, lng, lat))
    return centers


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--test", action="store_true",
                      help="東・中央・西の3点だけを試験取得")
    mode.add_argument("--sweep", action="store_true",
                      help="低密度7点を試験取得")
    mode.add_argument("--centers-csv", type=Path,
                      help="label,lng,lat のCSVを使う")
    mode.add_argument("--tile-grid", action="store_true",
                      help="XYZタイル(u/v)基準の2次元走査を実行")

    ap.add_argument("--level", type=int, default=DEFAULT_LEVEL)
    ap.add_argument("--u-min", type=int, default=TILE_GRID["u_min"])
    ap.add_argument("--u-max", type=int, default=TILE_GRID["u_max"])
    ap.add_argument("--v-min", type=int, default=TILE_GRID["v_min"])
    ap.add_argument("--v-max", type=int, default=TILE_GRID["v_max"])
    ap.add_argument("--u", type=int, default=None, help="通常は未指定。指定時のみuを固定")
    ap.add_argument("--v", type=int, default=None, help="通常は未指定。指定時のみvを固定")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="新規リクエスト間の基本待機秒数 (default: 2.0)")
    ap.add_argument("--jitter", type=float, default=0.8,
                    help="待機時間に加える0..N秒のランダム値")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--force", action="store_true",
                    help="既存キャッシュを無視して再取得")
    ap.add_argument("--cache-dir", type=Path, default=Path("sagamihara_cache"))
    ap.add_argument("--output", type=Path, default=Path("Sagamihara_Search_Test.csv"))
    ap.add_argument("--summary", type=Path, default=Path("Sagamihara_Search_Summary.json"))
    ap.add_argument("--pdf", type=Path,
                    help="参照用『相模原市内埋蔵文化財包蔵地一覧』PDF")
    args = ap.parse_args()

    tile_mode = False

    if args.test:
        centers = [(label, lng, lat, None, None) for label, lng, lat in TEST_CENTERS]
    elif args.sweep:
        centers = [(label, lng, lat, None, None) for label, lng, lat in SWEEP_CENTERS]
    elif args.tile_grid:
        tile_mode = True
        centers = build_tile_grid_centers(
            args.level,
            args.u_min,
            args.u_max,
            args.v_min,
            args.v_max,
        )
    else:
        centers = [
            (label, lng, lat, None, None)
            for label, lng, lat in parse_centers_file(args.centers_csv)
        ]

    print("Sagamihara Search API test")
    print(f" mapId={MAP_ID}, level={args.level}, centers={len(centers)}")
    if args.u is not None or args.v is not None:
        print(f" WARNING: fixed tile override is active: u={args.u}, v={args.v}")
    else:
        print(" u/v mode: auto (computed from lng/lat + level)")
    print(f" cache={args.cache_dir}")
    print(" ※ itemIdを主キーとして重複排除します。\n")

    all_points: dict[str, dict[str, Any]] = {}
    point_sources: dict[str, set[str]] = {}
    request_stats = []

    for idx, (label, lng, lat, tile_u, tile_v) in enumerate(centers, start=1):
        try:
            if tile_u is not None and tile_v is not None:
                u, v = tile_u, tile_v
            elif args.u is None or args.v is None:
                u, v = lonlat_to_tile(lng, lat, args.level)
            else:
                u, v = args.u, args.v

            print(
                f"    {label}: lng={lng:.6f}, lat={lat:.6f}, "
                f"level={args.level}, u={u}, v={v}"
            )

            fr = fetch_search(
                label, lng, lat, args.level, u, v,
                args.cache_dir, args.timeout, args.retries, args.force
            )
        except Exception as e:
            print(f"[{idx}/{len(centers)}] ERROR {label}: {e}", file=sys.stderr)
            request_stats.append({
                "label": label, "lng": lng, "lat": lat,
                "level": args.level, "error": str(e)
            })
            continue

        pts = points_from_response(fr.data)
        added, dup = merge_points(all_points, point_sources, pts, label, args.level)
        print(
            f"[{idx}/{len(centers)}] {label:8s} "
            f"response={len(pts):4d}  new={added:4d}  overlap={dup:4d}  "
            f"unique_total={len(all_points):4d}"
            + ("  [cache]" if fr.from_cache else "")
        )

        request_stats.append({
            "label": label,
            "lng": lng,
            "lat": lat,
            "level": args.level,
            "response_points": len(pts),
            "new_points": added,
            "overlap_points": dup,
            "cache": fr.from_cache,
            "cache_path": str(fr.cache_path),
        })

        # 新規通信をしたときだけ待つ。
        if not fr.from_cache and idx < len(centers):
            time.sleep(max(0.0, args.delay) + random.uniform(0.0, max(0.0, args.jitter)))

    rows = list(all_points.values())
    for row in rows:
        key = str(row["itemId"])
        row["source_hits"] = len(point_sources.get(key, set()))

    rows.sort(key=lambda r: (
        site_sort_key(normalized_site_no(str(r.get("ISEKI_NO", "")))),
        str(r.get("itemId", ""))
    ))
    write_csv(args.output, rows)

    nonempty_nos = {
        normalized_site_no(str(r.get("ISEKI_NO", "")))
        for r in rows
        if normalized_site_no(str(r.get("ISEKI_NO", "")))
    }
    empty_no_count = sum(
        1 for r in rows
        if not normalized_site_no(str(r.get("ISEKI_NO", "")))
    )

    zero_new_tiles = sum(
        1 for s in request_stats
        if isinstance(s, dict) and s.get("new_points") == 0
    )

    summary = {
        "mapId": MAP_ID,
        "level": args.level,
        "mode": (
            "tile-grid" if tile_mode
            else "test" if args.test
            else "sweep" if args.sweep
            else "centers-csv"
        ),
        "centers": len(centers),
        "unique_itemId_count": len(rows),
        "unique_nonempty_ISEKI_NO_count": len(nonempty_nos),
        "empty_ISEKI_NO_records": empty_no_count,
        "zero_new_searches": zero_new_tiles,
        "request_stats": request_stats,
    }
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print("\n[集計]")
    print(f" unique itemId            : {len(rows)}")
    print(f" unique non-empty ISEKI_NO: {len(nonempty_nos)}")
    print(f" empty ISEKI_NO records   : {empty_no_count}")
    print(f" CSV                      : {args.output}")
    print(f" Summary                  : {args.summary}")

    if args.pdf:
        if args.pdf.exists():
            compare_with_pdf(rows, args.pdf)
        else:
            print(f"[PDF] 見つかりません: {args.pdf}")

    print("\n次の判断:")
    if tile_mode:
        print(f"- 2次元走査範囲: u={args.u_min}..{args.u_max}, v={args.v_min}..{args.v_max}")
        print(f"- new=0 の検索点: {zero_new_tiles}/{len(request_stats)}")
        print("- PDF未取得番号が市域端部に偏る場合だけu/v範囲を1タイル拡張")
        print("- 周囲の複数検索点でnew=0が続けば、その方向の拡張は不要")
    else:
        print("- 次は --tile-grid で2次元タイル走査")
        print("- 既存キャッシュは自動再利用されるため、同条件の再通信は不要")
    print("- PDF照合は補助指標。itemId総数とPDFの番号体系を分けて評価")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
