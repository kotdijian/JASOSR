#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
横浜市「文化財ハマSite」埋蔵文化財包蔵地
失敗257件・詳細属性 再試行ツール
==============================================

前提ファイル（このスクリプトと同じフォルダ）
------------------------------------------
Yokohama_1430_List.csv
Yokohama_1430_Attributes.csv
Yokohama_1430_Errors.csv

目的
----
初回全件取得で失敗したレコードだけを再試行します。
既に取得済みの2,165件には再アクセスしません。

重要な方針
----------
・ProxyLon / ProxyLat (= JsonThemeAroundPoint の x/y) は
  最終データの代表点（proxy point）としてそのまま保持します。
・API再照会に使う点だけを変更します。
・最初に center_x / center_y を試します。
・失敗時は proxy と center の間の補間点を試します。
・さらに center / midpoint 周辺の小さなオフセット点を試します。
・取得された metadata の uid が対象 uid と完全一致し、
  かつ detail item / 属性が存在する場合だけ成功とします。
・並列アクセスは行いません。
・成功結果はJSONLへ逐次保存し、中断後に再開できます。
・元の Attributes.csv / Errors.csv は上書きしません。

標準再試行点
------------
1. center
2. proxy-center の 75%点
3. midpoint (50%)
4. proxy-center の 25%点
5. center 周囲 5m: N/E/S/W
6. midpoint 周囲 5m: N/E/S/W

--deep を付けると、さらに
・center 周囲 10m / 20m の8方向
を試します。

実行例
------
入力確認のみ（通信なし）:
    python3 Yokohama_1430_Retry.py --dry-run

まず5件だけ試す:
    python3 Yokohama_1430_Retry.py --run --limit 5

257件を再試行:
    python3 Yokohama_1430_Retry.py --run

残件をより広く探索:
    python3 Yokohama_1430_Retry.py --run --deep

特定uidだけ:
    python3 Yokohama_1430_Retry.py --run --uid 1539-1430

出力
----
Yokohama_1430_RetryRecovered.jsonl
    成功レコードの逐次チェックポイント

Yokohama_1430_RetryRecovered.csv
    今回までに回収できた詳細属性

Yokohama_1430_RetryRemaining.csv
    まだ回収できないレコード

Yokohama_1430_RetryAttempts.csv
    各試行点の成否ログ

Yokohama_1430_Attributes_merged.csv
    元のAttributes + 再取得成功分をuidで統合したCSV

依存
----
Python標準ライブラリのみ。
requests / pandas は不要です。
"""

from __future__ import annotations

import argparse
import csv
import http.cookiejar
import json
import math
import random
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable


# ============================================================
# 設定
# ============================================================

BASE_URL = "https://wwwm.city.yokohama.lg.jp/yokohama-sp/"
DETAIL_URL = urllib.parse.urljoin(BASE_URL, "JsonThemeInfo")

MAP_ID = "9"
DATA_TYPE = "9"
TARGET_LAYER_ID = "1430"
TARGET_CONTROL_ID = "225"
TARGET_LAYER_NAME = "埋蔵文化財包蔵地"

SEARCH_SCALE = 2500
DETAIL_SCREEN_WIDTH = 298
DETAIL_SCREEN_HEIGHT = 616

# JsonThemeInfo のbbox。照会点を中心にした表示範囲。
DETAIL_BBOX_HALF_WIDTH = 0.0010
DETAIL_BBOX_HALF_HEIGHT = 0.0017

DETAIL_INTERVAL_SEC = 0.80
RANDOM_JITTER_SEC = 0.20
MAX_RETRIES = 5
BACKOFF_BASE_SEC = 2.0
TLS_SECURITY_LEVEL = 0

USER_AGENT = (
    "Mozilla/5.0 (compatible; "
    "JASOSR-ArchaeologicalDataResearch-Retry/1.0)"
)

SCRIPT_DIR = Path(__file__).resolve().parent

LIST_CSV = SCRIPT_DIR / "Yokohama_1430_List.csv"
ATTR_CSV = SCRIPT_DIR / "Yokohama_1430_Attributes.csv"
ERROR_CSV = SCRIPT_DIR / "Yokohama_1430_Errors.csv"

RECOVERED_JSONL = SCRIPT_DIR / "Yokohama_1430_RetryRecovered.jsonl"
RECOVERED_CSV = SCRIPT_DIR / "Yokohama_1430_RetryRecovered.csv"
REMAINING_CSV = SCRIPT_DIR / "Yokohama_1430_RetryRemaining.csv"
ATTEMPTS_CSV = SCRIPT_DIR / "Yokohama_1430_RetryAttempts.csv"
MERGED_CSV = SCRIPT_DIR / "Yokohama_1430_Attributes_merged.csv"

CANONICAL_ATTRIBUTE_MAP = {
    "■遺跡番号": "SiteNo",
    "遺跡番号": "SiteNo",
    "所在": "Ward",
    "所在地": "Address",
    "種類": "SiteType",
    "地目": "LandCategory",
    "立地": "Topography",
    "規模": "SiteSize",
    "時代時期": "Chronology",
    "時代": "Chronology",
    "時代(時期)": "Chronology",
    "時代（時期）": "Chronology",
    "県管理番号": "PrefectureSiteNo",
    "備考": "Remarks",
}


class JsonRequestError(RuntimeError):
    pass


# ============================================================
# CSV
# ============================================================

def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def require_files() -> None:
    missing = [
        str(path.name)
        for path in (LIST_CSV, ATTR_CSV, ERROR_CSV)
        if not path.exists()
    ]
    if missing:
        raise FileNotFoundError(
            "必要ファイルがありません: " + ", ".join(missing)
        )


def validate_inputs(
    list_rows: list[dict[str, str]],
    attr_rows: list[dict[str, str]],
    error_rows: list[dict[str, str]],
) -> None:
    required_list = {
        "uid", "FeatureID", "lid", "lnm", "t",
        "ProxyLon", "ProxyLat", "center_x", "center_y",
    }
    required_error = {"uid", "t", "ProxyLon", "ProxyLat", "error"}

    if list_rows:
        missing = required_list - set(list_rows[0])
        if missing:
            raise ValueError(
                "List.csv の必要列がありません: "
                + ", ".join(sorted(missing))
            )

    if error_rows:
        missing = required_error - set(error_rows[0])
        if missing:
            raise ValueError(
                "Errors.csv の必要列がありません: "
                + ", ".join(sorted(missing))
            )

    list_uids = {r.get("uid", "") for r in list_rows}
    attr_uids = {r.get("uid", "") for r in attr_rows}
    error_uids = {r.get("uid", "") for r in error_rows}

    missing_from_list = sorted(error_uids - list_uids)
    overlap = attr_uids & error_uids

    print("入力確認")
    print(f"  List      : {len(list_rows):,}件 / uid={len(list_uids):,}")
    print(f"  Attributes: {len(attr_rows):,}件 / uid={len(attr_uids):,}")
    print(f"  Errors    : {len(error_rows):,}件 / uid={len(error_uids):,}")
    print(f"  Attributes + Errors = {len(attr_uids | error_uids):,} unique uid")

    if missing_from_list:
        print(
            f"  注意: ErrorsのうちListにないuid={len(missing_from_list)}件"
        )

    if overlap:
        print(
            f"  注意: AttributesとErrorsの両方にあるuid={len(overlap)}件"
        )

    zero_count = sum(
        "item_count=0" in r.get("error", "")
        for r in error_rows
    )
    nonzero_count = len(error_rows) - zero_count

    print(f"  item_count=0 の失敗: {zero_count:,}件")
    print(f"  item_count>0 等の失敗: {nonzero_count:,}件")


# ============================================================
# HTTP
# ============================================================

def make_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        context.set_ciphers(f"DEFAULT:@SECLEVEL={TLS_SECURITY_LEVEL}")
    except ssl.SSLError:
        pass
    return context


def build_opener() -> urllib.request.OpenerDirector:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookie_jar),
        urllib.request.HTTPSHandler(context=make_ssl_context()),
    )
    opener.addheaders = [
        ("User-Agent", USER_AGENT),
        ("Accept", "application/json, text/javascript, */*; q=0.01"),
        ("Accept-Language", "ja-JP,ja;q=0.9"),
        ("Referer", BASE_URL),
        ("X-Requested-With", "XMLHttpRequest"),
    ]
    return opener


def initialize_session(opener: urllib.request.OpenerDirector) -> None:
    try:
        req = urllib.request.Request(BASE_URL)
        with opener.open(req, timeout=60) as response:
            response.read(1024)
        time.sleep(0.2)
    except Exception as error:
        print(
            "注意: 初期ページアクセスに失敗。API照会を続行します: "
            f"{error}"
        )


def normalize_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def decode_json_bytes(data: bytes) -> Any:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return json.loads(data.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            last_error = error
    raise JsonRequestError(f"JSONを解釈できません: {last_error}")


def request_json(
    opener: urllib.request.OpenerDirector,
    form: dict[str, Any],
) -> Any:
    data = urllib.parse.urlencode(form, doseq=True).encode("utf-8")
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(
            DETAIL_URL,
            data=data,
            method="POST",
        )
        req.add_header(
            "Content-Type",
            "application/x-www-form-urlencoded; charset=UTF-8",
        )

        try:
            with opener.open(req, timeout=60) as response:
                body = response.read()

            result = decode_json_bytes(body)
            time.sleep(
                DETAIL_INTERVAL_SEC
                + random.uniform(0.0, RANDOM_JITTER_SEC)
            )
            return result

        except urllib.error.HTTPError as error:
            last_error = error
            if not (error.code == 429 or 500 <= error.code <= 599):
                raise JsonRequestError(
                    f"HTTP {error.code}: {DETAIL_URL}"
                ) from error

        except (
            urllib.error.URLError,
            TimeoutError,
            ssl.SSLError,
            JsonRequestError,
        ) as error:
            last_error = error

        if attempt < MAX_RETRIES:
            wait = BACKOFF_BASE_SEC * (2 ** (attempt - 1))
            print(
                f"    通信再試行 {attempt}/{MAX_RETRIES}: "
                f"{last_error}"
            )
            time.sleep(wait)

    raise JsonRequestError(f"リクエスト失敗: {last_error}")


# ============================================================
# JsonThemeInfo
# ============================================================

def make_detail_form(lon: float, lat: float) -> dict[str, Any]:
    return {
        "mid": MAP_ID,
        "dtp": DATA_TYPE,
        "mtl": TARGET_LAYER_ID,
        "mcul": TARGET_CONTROL_ID,
        "mpx": lon,
        "mpy": lat,
        "mcx": lon,
        "mcy": lat,
        "pmd": "print",
        "mps": SEARCH_SCALE,
        "msw": DETAIL_SCREEN_WIDTH,
        "msh": DETAIL_SCREEN_HEIGHT,
        "iork": "false",
        "xmin": lon - DETAIL_BBOX_HALF_WIDTH,
        "ymin": lat - DETAIL_BBOX_HALF_HEIGHT,
        "xmax": lon + DETAIL_BBOX_HALF_WIDTH,
        "ymax": lat + DETAIL_BBOX_HALF_HEIGHT,
        "mtp": "dm",
        "mit": "3",
        "mpcx": lon,
        "mpcy": lat,
        "langmode": "0",
        "lid": "undefined",
        "IsEditMobile": "true",
    }


def recursive_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from recursive_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_dicts(child)


def extract_attributes(item_container: Any) -> dict[str, str]:
    attrs: dict[str, str] = {}

    def add(label: Any, value: Any) -> None:
        key = normalize_scalar(label).strip()
        if not key:
            return
        val = normalize_scalar(value)

        if key not in attrs:
            attrs[key] = val
        elif attrs[key] != val:
            n = 2
            while f"{key}__{n}" in attrs:
                n += 1
            attrs[f"{key}__{n}"] = val

    for obj in recursive_dicts(item_container):
        if "cap" in obj and "val" in obj:
            add(obj.get("cap"), obj.get("val"))
        elif "caption" in obj and "value" in obj:
            add(obj.get("caption"), obj.get("value"))
        elif "label" in obj and "value" in obj:
            add(obj.get("label"), obj.get("value"))

    return attrs


def analyze_response(
    payload: Any,
    target_uid: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "matched": False,
            "matched_metadata": None,
            "returned_uids": [],
            "item_count": 0,
            "attribute_count": 0,
            "attributes": {},
        }

    items = payload.get("items", [])
    item_container = payload.get("item", [])

    returned_uids: list[str] = []
    matched_metadata = None

    if isinstance(items, list):
        for obj in items:
            if not isinstance(obj, dict):
                continue
            uid = normalize_scalar(obj.get("uid"))
            if uid:
                returned_uids.append(uid)
            if uid == target_uid:
                matched_metadata = obj

    attrs = extract_attributes(item_container)

    if isinstance(item_container, list):
        item_count = len(item_container)
    elif isinstance(item_container, dict):
        item_count = 1
    else:
        item_count = 0

    matched = (
        matched_metadata is not None
        and item_count > 0
        and len(attrs) > 0
    )

    return {
        "matched": matched,
        "matched_metadata": matched_metadata,
        "returned_uids": returned_uids,
        "item_count": item_count,
        "attribute_count": len(attrs),
        "attributes": attrs,
    }


# ============================================================
# 再試行点
# ============================================================

def interpolate(
    proxy_lon: float,
    proxy_lat: float,
    center_lon: float,
    center_lat: float,
    fraction_from_proxy: float,
) -> tuple[float, float]:
    lon = proxy_lon + (center_lon - proxy_lon) * fraction_from_proxy
    lat = proxy_lat + (center_lat - proxy_lat) * fraction_from_proxy
    return lon, lat


def offset_lon_lat(
    lon: float,
    lat: float,
    east_m: float,
    north_m: float,
) -> tuple[float, float]:
    """
    小距離用の近似変換。
    """
    lat_deg_per_m = 1.0 / 111_320.0
    cos_lat = max(math.cos(math.radians(lat)), 0.1)
    lon_deg_per_m = 1.0 / (111_320.0 * cos_lat)

    return (
        lon + east_m * lon_deg_per_m,
        lat + north_m * lat_deg_per_m,
    )


def dedupe_points(
    points: list[tuple[str, float, float]],
) -> list[tuple[str, float, float]]:
    seen: set[tuple[int, int]] = set()
    result: list[tuple[str, float, float]] = []

    for name, lon, lat in points:
        key = (round(lon * 1e9), round(lat * 1e9))
        if key in seen:
            continue
        seen.add(key)
        result.append((name, lon, lat))

    return result


def build_retry_points(
    list_row: dict[str, str],
    *,
    deep: bool,
) -> list[tuple[str, float, float]]:
    px = float(list_row["ProxyLon"])
    py = float(list_row["ProxyLat"])
    cx = float(list_row["center_x"])
    cy = float(list_row["center_y"])

    p75 = interpolate(px, py, cx, cy, 0.75)
    p50 = interpolate(px, py, cx, cy, 0.50)
    p25 = interpolate(px, py, cx, cy, 0.25)

    points: list[tuple[str, float, float]] = [
        ("center", cx, cy),
        ("segment_75", p75[0], p75[1]),
        ("segment_50", p50[0], p50[1]),
        ("segment_25", p25[0], p25[1]),
    ]

    # 5mの小範囲探索
    for base_name, bx, by in (
        ("center", cx, cy),
        ("segment_50", p50[0], p50[1]),
    ):
        for suffix, east, north in (
            ("N5", 0, 5),
            ("E5", 5, 0),
            ("S5", 0, -5),
            ("W5", -5, 0),
        ):
            lon, lat = offset_lon_lat(bx, by, east, north)
            points.append((f"{base_name}_{suffix}", lon, lat))

    if deep:
        # 通常再試行で残ったもの向け。
        directions = (
            ("N", 0.0, 1.0),
            ("NE", 0.7071, 0.7071),
            ("E", 1.0, 0.0),
            ("SE", 0.7071, -0.7071),
            ("S", 0.0, -1.0),
            ("SW", -0.7071, -0.7071),
            ("W", -1.0, 0.0),
            ("NW", -0.7071, 0.7071),
        )

        for radius in (10.0, 20.0):
            for dname, ex, ny in directions:
                lon, lat = offset_lon_lat(
                    cx, cy,
                    east_m=ex * radius,
                    north_m=ny * radius,
                )
                points.append(
                    (f"center_{dname}{int(radius)}", lon, lat)
                )

    return dedupe_points(points)


# ============================================================
# レコード構築
# ============================================================

def normalize_recovered_record(
    list_row: dict[str, str],
    analysis: dict[str, Any],
    *,
    method: str,
    query_lon: float,
    query_lat: float,
) -> dict[str, Any]:
    metadata = analysis["matched_metadata"] or {}
    attrs = analysis["attributes"]

    result: dict[str, Any] = {
        "uid": list_row["uid"],
        "FeatureID": list_row.get("FeatureID", ""),
        "lid": TARGET_LAYER_ID,
        "lnm": list_row.get("lnm", TARGET_LAYER_NAME),
        "t": list_row.get("t", ""),
        "SiteLabel": list_row.get("t", ""),
        "ProxyLon": list_row.get("ProxyLon", ""),
        "ProxyLat": list_row.get("ProxyLat", ""),
        "ProxySource": "JsonThemeAroundPoint:x/y",
        "center_x": list_row.get("center_x", ""),
        "center_y": list_row.get("center_y", ""),
        "distance": list_row.get("distance", ""),
        "DetailMatched": "1",
        "DetailAttributeCount": str(analysis["attribute_count"]),
        "RetryMethod": method,
        "RetryQueryLon": f"{query_lon:.15g}",
        "RetryQueryLat": f"{query_lat:.15g}",
    }

    for key in (
        "uid", "lid", "lnm", "gmt", "x", "y",
        "center_x", "center_y", "t", "address",
    ):
        if key in metadata:
            result[f"Detail_{key}"] = normalize_scalar(metadata.get(key))

    for key, value in attrs.items():
        result[key] = value

    for original, canonical in CANONICAL_ATTRIBUTE_MAP.items():
        value = attrs.get(original, "")
        if value and not result.get(canonical):
            result[canonical] = value

    return result


# ============================================================
# チェックポイント / 出力
# ============================================================

def read_recovered_checkpoint() -> dict[str, dict[str, Any]]:
    recovered: dict[str, dict[str, Any]] = {}

    if not RECOVERED_JSONL.exists():
        return recovered

    with RECOVERED_JSONL.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                print(
                    f"注意: {RECOVERED_JSONL.name} "
                    f"{line_no}行目はJSONとして読めないため無視します。"
                )
                continue

            uid = str(row.get("uid", ""))
            if uid:
                recovered[uid] = row

    return recovered


def append_recovered(row: dict[str, Any]) -> None:
    with RECOVERED_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        f.write("\n")


def append_attempt_log(
    *,
    uid: str,
    t: str,
    method: str,
    lon: float,
    lat: float,
    analysis: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> None:
    exists = ATTEMPTS_CSV.exists()

    fieldnames = [
        "uid", "t", "method", "query_lon", "query_lat",
        "success", "returned_uids", "item_count",
        "attribute_count", "error",
    ]

    row = {
        "uid": uid,
        "t": t,
        "method": method,
        "query_lon": f"{lon:.15g}",
        "query_lat": f"{lat:.15g}",
        "success": "1" if analysis and analysis.get("matched") else "0",
        "returned_uids": "|".join(
            analysis.get("returned_uids", [])
            if analysis else []
        ),
        "item_count": (
            analysis.get("item_count", "")
            if analysis else ""
        ),
        "attribute_count": (
            analysis.get("attribute_count", "")
            if analysis else ""
        ),
        "error": str(error) if error else "",
    }

    with ATTEMPTS_CSV.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def union_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    preferred = [
        "uid", "FeatureID", "lid", "lnm", "t",
        "SiteLabel", "SiteNo", "Ward", "Address",
        "SiteType", "LandCategory", "Topography",
        "SiteSize", "Chronology", "PrefectureSiteNo",
        "Remarks",
        "ProxyLon", "ProxyLat", "ProxySource",
        "center_x", "center_y", "distance",
        "DetailMatched", "DetailAttributeCount",
        "Detail_uid", "Detail_lid", "Detail_lnm",
        "Detail_gmt", "Detail_x", "Detail_y", "Detail_t",
        "RetryMethod", "RetryQueryLon", "RetryQueryLat",
    ]

    all_fields: set[str] = set()
    for row in rows:
        all_fields.update(row.keys())

    ordered = [f for f in preferred if f in all_fields]
    return ordered + sorted(all_fields - set(ordered))


def write_rows(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    fieldnames: list[str] | None = None,
) -> None:
    if fieldnames is None:
        fieldnames = union_fieldnames(rows)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: normalize_scalar(row.get(key, ""))
                for key in fieldnames
            })


def update_outputs(
    original_attrs: list[dict[str, str]],
    original_errors: list[dict[str, str]],
    recovered: dict[str, dict[str, Any]],
) -> None:
    recovered_rows = sorted(
        recovered.values(),
        key=lambda r: (str(r.get("t", "")), str(r.get("uid", ""))),
    )

    if recovered_rows:
        write_rows(RECOVERED_CSV, recovered_rows)
    else:
        # 空でもヘッダだけのファイルを作る。
        write_rows(
            RECOVERED_CSV,
            [],
            fieldnames=[
                "uid", "t", "ProxyLon", "ProxyLat",
                "RetryMethod", "RetryQueryLon", "RetryQueryLat",
            ],
        )

    recovered_uids = set(recovered)

    remaining = [
        row
        for row in original_errors
        if row.get("uid", "") not in recovered_uids
    ]
    write_rows(
        REMAINING_CSV,
        remaining,
        fieldnames=list(original_errors[0].keys())
        if original_errors else ["uid", "t", "ProxyLon", "ProxyLat", "error"],
    )

    # 元AttributesとRecoveredをuidで統合。
    merged_by_uid: dict[str, dict[str, Any]] = {
        row.get("uid", ""): dict(row)
        for row in original_attrs
        if row.get("uid", "")
    }

    for uid, row in recovered.items():
        merged_by_uid[uid] = row

    merged = sorted(
        merged_by_uid.values(),
        key=lambda r: (str(r.get("t", "")), str(r.get("uid", ""))),
    )

    # 元CSV列を先頭に維持し、Retry列等だけ末尾追加。
    original_fields = list(original_attrs[0].keys()) if original_attrs else []
    extra_fields: list[str] = []

    all_merged_fields = set()
    for row in merged:
        all_merged_fields.update(row.keys())

    for field in (
        "RetryMethod", "RetryQueryLon", "RetryQueryLat",
    ):
        if field in all_merged_fields and field not in original_fields:
            extra_fields.append(field)

    # もしRecoveredに元CSVにない属性列があれば、それも落とさない。
    for field in sorted(all_merged_fields):
        if field not in original_fields and field not in extra_fields:
            extra_fields.append(field)

    write_rows(
        MERGED_CSV,
        merged,
        fieldnames=original_fields + extra_fields,
    )


# ============================================================
# メイン再試行
# ============================================================

def retry_one(
    opener: urllib.request.OpenerDirector,
    list_row: dict[str, str],
    *,
    deep: bool,
) -> dict[str, Any] | None:
    uid = list_row["uid"]
    t = list_row.get("t", "")

    points = build_retry_points(list_row, deep=deep)

    for attempt_no, (method, lon, lat) in enumerate(points, start=1):
        print(
            f"    [{attempt_no:02d}/{len(points):02d}] "
            f"{method}: {lon:.9f}, {lat:.9f}"
        )

        try:
            payload = request_json(
                opener,
                make_detail_form(lon, lat),
            )
            analysis = analyze_response(payload, uid)

            append_attempt_log(
                uid=uid,
                t=t,
                method=method,
                lon=lon,
                lat=lat,
                analysis=analysis,
            )

            if analysis["matched"]:
                return normalize_recovered_record(
                    list_row,
                    analysis,
                    method=method,
                    query_lon=lon,
                    query_lat=lat,
                )

        except Exception as error:
            append_attempt_log(
                uid=uid,
                t=t,
                method=method,
                lon=lon,
                lat=lat,
                error=error,
            )
            print(f"      通信/解析エラー: {error}")

    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Yokohama_1430_Errors.csv の失敗レコードだけを"
            "別の照会点で再試行します。"
        )
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="入力ファイルを確認するだけ。通信しない。",
    )
    mode.add_argument(
        "--run",
        action="store_true",
        help="再試行を実行する。",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="今回処理する最大uid数。動作確認用。",
    )
    parser.add_argument(
        "--uid",
        type=str,
        default=None,
        help="特定uidだけ再試行する。例 1539-1430",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="通常探索に加えてcenter周囲10m/20mの8方向を試す。",
    )
    parser.add_argument(
        "--reset-retry",
        action="store_true",
        help=(
            "既存RetryRecovered.jsonl/RetryAttempts.csvを削除して"
            "再試行結果を最初から作り直す。元Attributes/Errorsは削除しない。"
        ),
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    require_files()

    list_rows = read_csv(LIST_CSV)
    attr_rows = read_csv(ATTR_CSV)
    error_rows = read_csv(ERROR_CSV)

    validate_inputs(list_rows, attr_rows, error_rows)

    list_by_uid = {
        row.get("uid", ""): row
        for row in list_rows
        if row.get("uid", "")
    }

    if args.dry_run:
        print()
        print("dry-run完了。通信は行っていません。")
        return 0

    if args.reset_retry:
        for path in (
            RECOVERED_JSONL,
            RECOVERED_CSV,
            REMAINING_CSV,
            ATTEMPTS_CSV,
            MERGED_CSV,
        ):
            if path.exists():
                path.unlink()
        print("既存のRetry出力だけを削除しました。")

    recovered = read_recovered_checkpoint()
    print(f"既取得Retry成功: {len(recovered):,}件")

    targets = []

    for error_row in error_rows:
        uid = error_row.get("uid", "")

        if not uid or uid in recovered:
            continue

        if args.uid and uid != args.uid:
            continue

        list_row = list_by_uid.get(uid)
        if list_row is None:
            print(f"注意: uid={uid} がList.csvにありません。")
            continue

        if not list_row.get("center_x") or not list_row.get("center_y"):
            print(f"注意: uid={uid} にcenter_x/yがありません。")
            continue

        targets.append(list_row)

    if args.limit is not None:
        targets = targets[: max(args.limit, 0)]

    print()
    print(
        f"今回の再試行対象: {len(targets):,}件 "
        f"({'deep' if args.deep else 'standard'})"
    )

    if not targets:
        update_outputs(attr_rows, error_rows, recovered)
        print("新規対象はありません。出力CSVを更新しました。")
        return 0

    opener = build_opener()
    initialize_session(opener)

    newly_recovered = 0

    for index, list_row in enumerate(targets, start=1):
        uid = list_row["uid"]
        t = list_row.get("t", "")

        print()
        print(f"[{index:,}/{len(targets):,}] {uid} {t}")

        record = retry_one(
            opener,
            list_row,
            deep=args.deep,
        )

        if record is not None:
            append_recovered(record)
            recovered[uid] = record
            newly_recovered += 1
            print(
                f"  RECOVERED: {record.get('RetryMethod')} / "
                f"{record.get('DetailAttributeCount')} attributes"
            )
        else:
            print("  未回収")

        # 10件ごとにCSV更新
        if index % 10 == 0:
            update_outputs(attr_rows, error_rows, recovered)
            print(
                f"  checkpoint: Retry成功累計={len(recovered):,}"
            )

    update_outputs(attr_rows, error_rows, recovered)

    remaining_count = len(error_rows) - len(recovered)

    print()
    print("再試行終了")
    print(f"  今回回収: {newly_recovered:,}件")
    print(f"  Retry成功累計: {len(recovered):,}件")
    print(f"  残件: {remaining_count:,}件")
    print(f"  回収CSV: {RECOVERED_CSV}")
    print(f"  残件CSV: {REMAINING_CSV}")
    print(f"  統合CSV: {MERGED_CSV}")
    print(f"  試行ログ: {ATTEMPTS_CSV}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
