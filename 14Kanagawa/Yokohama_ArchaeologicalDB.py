#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
横浜市「文化財ハマSite」埋蔵文化財包蔵地 属性DB取得ツール
=========================================================

今回の修正点
------------
1. --test-one では全一覧を取得しない。
   JsonThemeAroundPoint の1ページ目から既知の「中区NO.32」を取得し、
   JsonThemeInfo の詳細属性取得だけを検証する。

2. JsonThemeInfo の "items" に uid が存在するだけでは成功と判定しない。
   詳細属性は Response の "item" にある cap/val 等を確認し、
   1件以上の属性ペアが取れた場合だけ成功とする。

3. 基本一覧は、まず JsonThemeInfo 1回で返る "items" 全件を利用する。
   JsonThemeAroundPoint 1ページ目の count と unique uid 数が一致した場合、
   243ページ巡回は行わない。
   一致しない場合だけ JsonThemeAroundPoint のページングへフォールバックする。

4. --full は、最初の1件で詳細属性取得が成功しない限り全件取得を開始しない。
   誤判定のまま2,422件へアクセスすることを防ぐ。

実行
----
1件テスト:
    python3 Yokohama_ArchaeologicalDB.py --test-one

基本一覧のみ:
    python3 Yokohama_ArchaeologicalDB.py --list-only

全件:
    python3 Yokohama_ArchaeologicalDB.py --full

一覧を再取得:
    python3 Yokohama_ArchaeologicalDB.py --list-only --refresh-list

依存
----
Python標準ライブラリのみ。

出力
----
Yokohama_1430_List.csv
Yokohama_1430_Test.json
Yokohama_1430_Detail.jsonl
Yokohama_1430_Attributes.csv
Yokohama_1430_Errors.csv
"""

from __future__ import annotations

import argparse
import csv
import http.cookiejar
import json
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
AROUND_URL = urllib.parse.urljoin(BASE_URL, "JsonThemeAroundPoint")
DETAIL_URL = urllib.parse.urljoin(BASE_URL, "JsonThemeInfo")

MAP_ID = "9"
DATA_TYPE = "9"

TARGET_LAYER_ID = "1430"
TARGET_CONTROL_ID = "225"
TARGET_LAYER_NAME = "埋蔵文化財包蔵地"

# 全レイヤでブラウザと同じ条件を再現するための値
ALL_LAYER_IDS = "1410,1420,1430"
ALL_CONTROL_IDS = "223,224,225"

SEARCH_ORIGIN_LON = 139.63775195265
SEARCH_ORIGIN_LAT = 35.44719612762617
SEARCH_SCALE = 2500

EXPECTED_PAGE_SIZE = 10

# 既知のテスト対象
KNOWN_TEST_UID = "1530-1430"
KNOWN_TEST_T = "中区NO.32"
KNOWN_TEST_LON = 139.637419365
KNOWN_TEST_LAT = 35.44738061

# 通信間隔。並列化しない。
LIST_INTERVAL_SEC = 0.35
DETAIL_INTERVAL_SEC = 0.80
RANDOM_JITTER_SEC = 0.20

MAX_RETRIES = 5
BACKOFF_BASE_SEC = 2.0

# curl で DEFAULT:@SECLEVEL=0 が必要だった環境への対策
TLS_SECURITY_LEVEL = 0

USER_AGENT = (
    "Mozilla/5.0 (compatible; "
    "JASOSR-ArchaeologicalDataResearch/1.1)"
)

SCRIPT_DIR = Path(__file__).resolve().parent
LIST_CSV = SCRIPT_DIR / "Yokohama_1430_List.csv"
DETAIL_JSONL = SCRIPT_DIR / "Yokohama_1430_Detail.jsonl"
FINAL_CSV = SCRIPT_DIR / "Yokohama_1430_Attributes.csv"
ERROR_CSV = SCRIPT_DIR / "Yokohama_1430_Errors.csv"
TEST_JSON = SCRIPT_DIR / "Yokohama_1430_Test.json"

# JsonThemeInfo のブラウザ相当リクエストで使う画面範囲
DETAIL_BBOX_HALF_WIDTH = 0.0010
DETAIL_BBOX_HALF_HEIGHT = 0.0017
DETAIL_SCREEN_WIDTH = 298
DETAIL_SCREEN_HEIGHT = 616

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

# ============================================================
# HTTP
# ============================================================

class JsonRequestError(RuntimeError):
    pass


def make_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        context.set_ciphers(f"DEFAULT:@SECLEVEL={TLS_SECURITY_LEVEL}")
    except ssl.SSLError:
        pass
    return context


def build_opener() -> urllib.request.OpenerDirector:
    cookie_jar = http.cookiejar.CookieJar()
    https_handler = urllib.request.HTTPSHandler(context=make_ssl_context())

    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookie_jar),
        https_handler,
    )
    opener.addheaders = [
        ("User-Agent", USER_AGENT),
        ("Accept", "application/json, text/javascript, */*; q=0.01"),
        ("Accept-Language", "ja-JP,ja;q=0.9"),
        ("Referer", BASE_URL),
        ("X-Requested-With", "XMLHttpRequest"),
    ]
    return opener


def sleep_between_requests(base_seconds: float) -> None:
    time.sleep(base_seconds + random.uniform(0.0, RANDOM_JITTER_SEC))


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
    url: str,
    *,
    params: dict[str, Any] | None = None,
    form: dict[str, Any] | None = None,
    interval_sec: float,
) -> Any:
    if params:
        request_url = url + "?" + urllib.parse.urlencode(params, doseq=True)
    else:
        request_url = url

    data = None
    if form is not None:
        data = urllib.parse.urlencode(form, doseq=True).encode("utf-8")

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        request = urllib.request.Request(
            request_url,
            data=data,
            method="POST" if form is not None else "GET",
        )

        if form is not None:
            request.add_header(
                "Content-Type",
                "application/x-www-form-urlencoded; charset=UTF-8",
            )

        try:
            with opener.open(request, timeout=60) as response:
                body = response.read()

            result = decode_json_bytes(body)
            sleep_between_requests(interval_sec)
            return result

        except urllib.error.HTTPError as error:
            last_error = error
            retryable = error.code == 429 or 500 <= error.code <= 599
            if not retryable:
                raise JsonRequestError(
                    f"HTTP {error.code}: {request_url}"
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
                f"  再試行 {attempt}/{MAX_RETRIES}: "
                f"{type(last_error).__name__}: {last_error}"
            )
            time.sleep(wait)

    raise JsonRequestError(
        f"リクエスト失敗: {request_url}\n{last_error}"
    )


def initialize_session(opener: urllib.request.OpenerDirector) -> None:
    request = urllib.request.Request(BASE_URL)
    try:
        with opener.open(request, timeout=60) as response:
            response.read(1024)
        sleep_between_requests(0.2)
    except Exception as error:
        print(
            "注意: 初期ページへのアクセスに失敗しました。"
            f" API取得を続行します: {error}"
        )


# ============================================================
# JSON 共通
# ============================================================

def unwrap_json_result(payload: Any) -> Any:
    if isinstance(payload, dict) and "JsonResult" in payload:
        value = payload["JsonResult"]

        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith(("{", "[")):
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    pass

        if value is not None:
            return value

    return payload


def recursive_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from recursive_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_dicts(child)


def normalize_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def root_dict(payload: Any) -> dict[str, Any]:
    root = unwrap_json_result(payload)
    if not isinstance(root, dict):
        raise JsonRequestError("Responseのルートがobjectではありません。")
    return root


# ============================================================
# 一覧
# ============================================================

def make_around_params(page_number: int) -> dict[str, Any]:
    return {
        "dtp": DATA_TYPE,
        "mpx": SEARCH_ORIGIN_LON,
        "mpy": SEARCH_ORIGIN_LAT,
        "mps": SEARCH_SCALE,
        "skw": "",
        "mcul": TARGET_CONTROL_ID,
        "tsc": TARGET_CONTROL_ID,
        "nbd": "",
        "pn": page_number,
    }


def normalize_list_item(item: dict[str, Any]) -> dict[str, str]:
    uid = normalize_scalar(item.get("uid"))
    feature_id = uid.split("-", 1)[0] if "-" in uid else ""

    return {
        "uid": uid,
        "FeatureID": feature_id,
        # JsonThemeInfo の items では lid=0 になるため、
        # uid末尾とlnmから対象レイヤと確認できたものは1430へ正規化。
        "lid": TARGET_LAYER_ID
        if uid.endswith("-1430")
        else normalize_scalar(item.get("lid")),
        "lnm": normalize_scalar(item.get("lnm")),
        "t": normalize_scalar(item.get("t")),
        "ProxyLon": normalize_scalar(item.get("x")),
        "ProxyLat": normalize_scalar(item.get("y")),
        "center_x": normalize_scalar(item.get("center_x")),
        "center_y": normalize_scalar(item.get("center_y")),
        "distance": normalize_scalar(item.get("distance")),
    }


def is_target_metadata(item: dict[str, Any]) -> bool:
    uid = normalize_scalar(item.get("uid"))
    lnm = normalize_scalar(item.get("lnm"))
    lid = normalize_scalar(item.get("lid"))

    return (
        uid.endswith("-1430")
        or lid == TARGET_LAYER_ID
        or lnm == TARGET_LAYER_NAME
    )


def extract_target_metadata_items(payload: Any) -> list[dict[str, str]]:
    root = root_dict(payload)
    items = root.get("items", [])

    if not isinstance(items, list):
        return []

    unique: dict[str, dict[str, str]] = {}

    for raw in items:
        if not isinstance(raw, dict) or not is_target_metadata(raw):
            continue

        item = normalize_list_item(raw)
        uid = item["uid"]
        if uid:
            unique[uid] = item

    return list(unique.values())


def fetch_around_first_page(
    opener: urllib.request.OpenerDirector,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    payload = request_json(
        opener,
        AROUND_URL,
        params=make_around_params(1),
        interval_sec=LIST_INTERVAL_SEC,
    )

    root = root_dict(payload)
    items = root.get("items", [])
    normalized: list[dict[str, str]] = []

    if isinstance(items, list):
        for raw in items:
            if isinstance(raw, dict) and is_target_metadata(raw):
                normalized.append(normalize_list_item(raw))

    return root, normalized


def make_catalog_probe_form(item: dict[str, str]) -> dict[str, Any]:
    """
    今回の test-one で全2,422件が返った最小 JsonThemeInfo 形式。
    詳細属性用ではなく、基本一覧一括取得のために利用する。
    """
    lon = float(item["ProxyLon"])
    lat = float(item["ProxyLat"])

    return {
        "mid": MAP_ID,
        "dtp": DATA_TYPE,
        "mtl": TARGET_LAYER_ID,
        "mcul": TARGET_CONTROL_ID,
        "mpx": lon,
        "mpy": lat,
        "mcx": lon,
        "mcy": lat,
        "mps": SEARCH_SCALE,
        "mtp": "dm",
        "mit": "3",
    }


def fetch_catalog_once(
    opener: urllib.request.OpenerDirector,
    probe_item: dict[str, str],
) -> list[dict[str, str]]:
    payload = request_json(
        opener,
        DETAIL_URL,
        form=make_catalog_probe_form(probe_item),
        interval_sec=LIST_INTERVAL_SEC,
    )
    return extract_target_metadata_items(payload)


def fetch_all_list_items_paged(
    opener: urllib.request.OpenerDirector,
    first_root: dict[str, Any],
    first_items: list[dict[str, str]],
) -> list[dict[str, str]]:
    count = int(first_root.get("count", 0) or 0)
    per_page = int(first_root.get("perPage", EXPECTED_PAGE_SIZE) or EXPECTED_PAGE_SIZE)
    page_count = int(first_root.get("pageCount", 0) or 0)

    if page_count <= 0 and count > 0:
        page_count = (count + per_page - 1) // per_page

    unique = {row["uid"]: row for row in first_items if row.get("uid")}

    for page_number in range(2, page_count + 1):
        print(
            f"一覧フォールバック取得: {page_number:,}/{page_count:,}",
            end="\r",
            flush=True,
        )

        payload = request_json(
            opener,
            AROUND_URL,
            params=make_around_params(page_number),
            interval_sec=LIST_INTERVAL_SEC,
        )

        root = root_dict(payload)
        raw_items = root.get("items", [])

        if isinstance(raw_items, list):
            for raw in raw_items:
                if isinstance(raw, dict) and is_target_metadata(raw):
                    row = normalize_list_item(raw)
                    if row["uid"]:
                        unique[row["uid"]] = row

    print()
    return list(unique.values())


def fetch_all_list_items(
    opener: urllib.request.OpenerDirector,
) -> list[dict[str, str]]:
    """
    まずAroundPoint 1ページ目で総件数を取得。
    次にJsonThemeInfo 1回の items が総件数と一致するか確認。
    一致すれば243ページ巡回を省略する。
    """
    first_root, first_items = fetch_around_first_page(opener)
    expected_count = int(first_root.get("count", 0) or 0)

    print(
        f"JsonThemeAroundPoint: count={expected_count:,}, "
        f"pageCount={int(first_root.get('pageCount', 0) or 0):,}"
    )

    probe_item = next(
        (row for row in first_items if row.get("uid") == KNOWN_TEST_UID),
        first_items[0] if first_items else {
            "uid": KNOWN_TEST_UID,
            "FeatureID": "1530",
            "lid": TARGET_LAYER_ID,
            "lnm": TARGET_LAYER_NAME,
            "t": KNOWN_TEST_T,
            "ProxyLon": str(KNOWN_TEST_LON),
            "ProxyLat": str(KNOWN_TEST_LAT),
            "center_x": "",
            "center_y": "",
            "distance": "",
        },
    )

    print("JsonThemeInfo 1回で基本一覧一括取得を試します。")
    catalog_rows = fetch_catalog_once(opener, probe_item)
    catalog_unique = {row["uid"]: row for row in catalog_rows if row.get("uid")}

    print(f"  JsonThemeInfo items: {len(catalog_unique):,} unique uid")

    if expected_count > 0 and len(catalog_unique) == expected_count:
        print("  総件数と一致。ページングを省略します。")
        result = list(catalog_unique.values())
    else:
        print(
            "  総件数と一致しないため、"
            "JsonThemeAroundPoint のページングへフォールバックします。"
        )
        result = fetch_all_list_items_paged(
            opener,
            first_root,
            first_items,
        )

    result.sort(key=lambda row: (row.get("t", ""), row.get("uid", "")))
    return result


def write_list_csv(rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "uid",
        "FeatureID",
        "lid",
        "lnm",
        "t",
        "ProxyLon",
        "ProxyLat",
        "center_x",
        "center_y",
        "distance",
    ]

    with LIST_CSV.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_list_csv() -> list[dict[str, str]]:
    with LIST_CSV.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


# ============================================================
# 詳細属性
# ============================================================

def make_detail_browser_form(
    item: dict[str, str],
    *,
    all_layers: bool,
) -> dict[str, Any]:
    """
    DevToolsで確認済みのJsonThemeInfoのパラメータ構成に寄せる。
    まず1430だけ、失敗時に3レイヤ指定を試す。
    """
    lon = float(item["ProxyLon"])
    lat = float(item["ProxyLat"])

    return {
        "mid": MAP_ID,
        "dtp": DATA_TYPE,
        "mtl": ALL_LAYER_IDS if all_layers else TARGET_LAYER_ID,
        "mcul": ALL_CONTROL_IDS if all_layers else TARGET_CONTROL_ID,
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


def find_metadata_by_uid(
    payload: Any,
    uid: str,
) -> dict[str, Any] | None:
    root = root_dict(payload)
    items = root.get("items", [])

    if not isinstance(items, list):
        return None

    for obj in items:
        if isinstance(obj, dict) and normalize_scalar(obj.get("uid")) == uid:
            return obj

    return None


def get_detail_item_container(payload: Any) -> Any:
    root = root_dict(payload)
    return root.get("item", [])


def extract_attribute_pairs(value: Any) -> dict[str, str]:
    """
    詳細属性の成功条件として使う。
    cap/val、caption/value、label/value を抽出。
    """
    attributes: dict[str, str] = {}

    def add_pair(label: Any, raw_value: Any) -> None:
        clean_label = normalize_scalar(label).strip()
        if not clean_label:
            return

        clean_value = normalize_scalar(raw_value)

        if clean_label not in attributes:
            attributes[clean_label] = clean_value
            return

        if attributes[clean_label] == clean_value:
            return

        suffix = 2
        while f"{clean_label}__{suffix}" in attributes:
            suffix += 1
        attributes[f"{clean_label}__{suffix}"] = clean_value

    for obj in recursive_dicts(value):
        if "cap" in obj and "val" in obj:
            add_pair(obj.get("cap"), obj.get("val"))
        elif "caption" in obj and "value" in obj:
            add_pair(obj.get("caption"), obj.get("value"))
        elif "label" in obj and "value" in obj:
            add_pair(obj.get("label"), obj.get("value"))

    return attributes


def analyze_detail_response(
    payload: Any,
    target_item: dict[str, str],
) -> dict[str, Any]:
    metadata = find_metadata_by_uid(payload, target_item["uid"])
    item_container = get_detail_item_container(payload)
    attributes = extract_attribute_pairs(item_container)

    if isinstance(item_container, list):
        detail_item_count = len(item_container)
    elif isinstance(item_container, dict):
        detail_item_count = 1
    else:
        detail_item_count = 0

    return {
        "metadata_matched": metadata is not None,
        "detail_item_count": detail_item_count,
        "attribute_count": len(attributes),
        "attribute_names": list(attributes.keys()),
        "metadata": metadata,
        "attributes": attributes,
    }


def normalize_detail_record(
    base_item: dict[str, str],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    metadata = analysis.get("metadata") or {}
    attributes = analysis.get("attributes") or {}

    result: dict[str, Any] = {
        "uid": base_item["uid"],
        "FeatureID": base_item["FeatureID"],
        "lid": base_item["lid"],
        "lnm": base_item["lnm"],
        "t": base_item["t"],
        "ProxyLon": base_item["ProxyLon"],
        "ProxyLat": base_item["ProxyLat"],
        "ProxySource": "JsonThemeAroundPoint:x/y",
        "center_x": base_item.get("center_x", ""),
        "center_y": base_item.get("center_y", ""),
        "distance": base_item.get("distance", ""),
        "DetailMatched": "1",
        "DetailAttributeCount": str(analysis.get("attribute_count", 0)),
    }

    for key in (
        "uid",
        "lid",
        "lnm",
        "gmt",
        "x",
        "y",
        "center_x",
        "center_y",
        "t",
        "address",
    ):
        if key in metadata:
            result[f"Detail_{key}"] = normalize_scalar(metadata.get(key))

    for key, value in attributes.items():
        result[key] = value

    for original_name, canonical_name in CANONICAL_ATTRIBUTE_MAP.items():
        value = attributes.get(original_name, "")
        if value and not result.get(canonical_name):
            result[canonical_name] = value

    result.setdefault("SiteLabel", base_item["t"])
    return result


def fetch_detail_attempt(
    opener: urllib.request.OpenerDirector,
    item: dict[str, str],
    *,
    all_layers: bool,
) -> tuple[dict[str, Any], Any]:
    payload = request_json(
        opener,
        DETAIL_URL,
        form=make_detail_browser_form(item, all_layers=all_layers),
        interval_sec=DETAIL_INTERVAL_SEC,
    )
    analysis = analyze_detail_response(payload, item)
    return analysis, payload


def determine_detail_request_mode(
    opener: urllib.request.OpenerDirector,
    test_item: dict[str, str],
) -> tuple[str, dict[str, Any], Any, list[dict[str, Any]]]:
    """
    詳細属性が実際に1件以上取れた場合だけ成功。
    itemsにuidがあるだけでは成功にしない。
    """
    print(
        "JsonThemeInfo 詳細属性1件テスト: "
        f"{test_item['uid']} / {test_item['t']}"
    )
    print(
        f"  proxy = {test_item['ProxyLon']}, {test_item['ProxyLat']}"
    )

    attempts: list[dict[str, Any]] = []

    modes = [
        ("browser-target", False),
        ("browser-all-layers", True),
    ]

    for mode_name, all_layers in modes:
        print(f"  mode={mode_name} を試します。")

        try:
            analysis, payload = fetch_detail_attempt(
                opener,
                test_item,
                all_layers=all_layers,
            )

            summary = {
                "mode": mode_name,
                "metadata_matched": analysis["metadata_matched"],
                "detail_item_count": analysis["detail_item_count"],
                "attribute_count": analysis["attribute_count"],
                "attribute_names": analysis["attribute_names"],
                "raw_response": payload,
            }
            attempts.append(summary)

            print(
                "    metadata_matched="
                f"{analysis['metadata_matched']}, "
                "detail_item_count="
                f"{analysis['detail_item_count']}, "
                "attribute_count="
                f"{analysis['attribute_count']}"
            )

            # ここが今回の重要な修正:
            # item が空、または属性ペアが0なら成功扱いにしない。
            if (
                analysis["metadata_matched"]
                and analysis["detail_item_count"] > 0
                and analysis["attribute_count"] > 0
            ):
                record = normalize_detail_record(test_item, analysis)
                print(
                    f"  詳細属性取得成功: "
                    f"{analysis['attribute_count']}項目"
                )
                return mode_name, record, payload, attempts

        except Exception as error:
            attempts.append({
                "mode": mode_name,
                "error": str(error),
            })
            print(f"    失敗: {error}")

    raise JsonRequestError(
        "JsonThemeInfoから詳細属性を取得できませんでした。"
        " items内のuid一致だけでは成功と判定していません。"
    )


def fetch_detail_with_mode(
    opener: urllib.request.OpenerDirector,
    item: dict[str, str],
    *,
    mode_name: str,
) -> tuple[dict[str, Any], Any]:
    if mode_name == "browser-target":
        all_layers = False
    elif mode_name == "browser-all-layers":
        all_layers = True
    else:
        raise ValueError(f"不明なdetail mode: {mode_name}")

    analysis, payload = fetch_detail_attempt(
        opener,
        item,
        all_layers=all_layers,
    )

    if not (
        analysis["metadata_matched"]
        and analysis["detail_item_count"] > 0
        and analysis["attribute_count"] > 0
    ):
        raise JsonRequestError(
            f"uid={item['uid']} の詳細属性を確認できません。 "
            f"item_count={analysis['detail_item_count']}, "
            f"attribute_count={analysis['attribute_count']}"
        )

    return normalize_detail_record(item, analysis), payload


# ============================================================
# 保存
# ============================================================

def read_completed_records() -> dict[str, dict[str, Any]]:
    completed: dict[str, dict[str, Any]] = {}

    if not DETAIL_JSONL.exists():
        return completed

    with DETAIL_JSONL.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            text = line.strip()
            if not text:
                continue

            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                print(
                    f"注意: {DETAIL_JSONL.name} "
                    f"{line_number}行目を読めません。"
                )
                continue

            uid = str(record.get("uid", ""))
            # 旧版の誤成功レコードは再利用しない。
            attr_count = int(record.get("DetailAttributeCount", 0) or 0)

            if uid and attr_count > 0:
                completed[uid] = record

    return completed


def append_checkpoint(record: dict[str, Any]) -> None:
    with DETAIL_JSONL.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        file.write("\n")


def append_error(item: dict[str, str], error: Exception) -> None:
    exists = ERROR_CSV.exists()

    with ERROR_CSV.open("a", encoding="utf-8", newline="") as file:
        fieldnames = ["uid", "t", "ProxyLon", "ProxyLat", "error"]
        writer = csv.DictWriter(file, fieldnames=fieldnames)

        if not exists:
            writer.writeheader()

        writer.writerow({
            "uid": item.get("uid", ""),
            "t": item.get("t", ""),
            "ProxyLon": item.get("ProxyLon", ""),
            "ProxyLat": item.get("ProxyLat", ""),
            "error": str(error),
        })


def preferred_field_order(records: list[dict[str, Any]]) -> list[str]:
    preferred = [
        "uid",
        "FeatureID",
        "lid",
        "lnm",
        "t",
        "SiteLabel",
        "SiteNo",
        "Address",
        "SiteType",
        "LandCategory",
        "Topography",
        "SiteSize",
        "Chronology",
        "PrefectureSiteNo",
        "Remarks",
        "ProxyLon",
        "ProxyLat",
        "ProxySource",
        "center_x",
        "center_y",
        "distance",
        "DetailMatched",
        "DetailAttributeCount",
        "Detail_uid",
        "Detail_lid",
        "Detail_lnm",
        "Detail_gmt",
        "Detail_x",
        "Detail_y",
        "Detail_center_x",
        "Detail_center_y",
        "Detail_t",
        "Detail_address",
    ]

    all_fields: set[str] = set()
    for record in records:
        all_fields.update(str(key) for key in record.keys())

    ordered = [field for field in preferred if field in all_fields]
    return ordered + sorted(all_fields - set(ordered))


def write_final_csv(records: list[dict[str, Any]]) -> None:
    if not records:
        return

    fieldnames = preferred_field_order(records)
    records_sorted = sorted(
        records,
        key=lambda record: (
            str(record.get("t", "")),
            str(record.get("uid", "")),
        ),
    )

    with FINAL_CSV.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()

        for record in records_sorted:
            writer.writerow({
                key: normalize_scalar(record.get(key, ""))
                for key in fieldnames
            })


# ============================================================
# 実行モード
# ============================================================

def get_test_item(
    opener: urllib.request.OpenerDirector,
) -> dict[str, str]:
    """
    --test-one 用。
    全一覧は取得せず、AroundPointの1ページ目だけを見る。
    """
    root, rows = fetch_around_first_page(opener)

    print(
        f"テスト用一覧: count={int(root.get('count', 0) or 0):,}, "
        f"1ページ目={len(rows)}件"
    )

    item = next(
        (row for row in rows if row.get("uid") == KNOWN_TEST_UID),
        None,
    )

    if item is not None:
        return item

    print(
        "注意: 1ページ目に既知uidがないため、"
        "既知の中区NO.32座標を使用します。"
    )

    return {
        "uid": KNOWN_TEST_UID,
        "FeatureID": "1530",
        "lid": TARGET_LAYER_ID,
        "lnm": TARGET_LAYER_NAME,
        "t": KNOWN_TEST_T,
        "ProxyLon": str(KNOWN_TEST_LON),
        "ProxyLat": str(KNOWN_TEST_LAT),
        "center_x": "",
        "center_y": "",
        "distance": "",
    }


def get_or_fetch_list(
    opener: urllib.request.OpenerDirector,
    *,
    refresh: bool,
) -> list[dict[str, str]]:
    if LIST_CSV.exists() and not refresh:
        rows = read_list_csv()
        print(f"既存一覧を使用: {LIST_CSV} ({len(rows):,}件)")
        return rows

    rows = fetch_all_list_items(opener)
    write_list_csv(rows)
    print(f"一覧CSV: {LIST_CSV} ({len(rows):,}件)")
    return rows


def run_test_one(opener: urllib.request.OpenerDirector) -> None:
    test_item = get_test_item(opener)
    attempts: list[dict[str, Any]] = []

    try:
        mode, record, raw_payload, attempts = determine_detail_request_mode(
            opener,
            test_item,
        )

        result = {
            "success": True,
            "request_mode": mode,
            "list_item": test_item,
            "normalized_record": record,
            "attempts": attempts,
            "raw_response": raw_payload,
        }

        TEST_JSON.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print()
        print("1件テスト成功")
        print(f"request mode: {mode}")
        print(f"uid: {record.get('uid')}")
        print(
            f"詳細属性数: "
            f"{record.get('DetailAttributeCount')}"
        )
        print(f"テストJSON: {TEST_JSON}")

    except Exception as error:
        result = {
            "success": False,
            "list_item": test_item,
            "error": str(error),
            "attempts": attempts,
        }

        TEST_JSON.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print()
        print("1件テスト失敗")
        print(str(error))
        print(
            "この状態では --full は実行しないでください。"
        )
        print(f"診断JSON: {TEST_JSON}")
        raise


def run_full(
    opener: urllib.request.OpenerDirector,
    rows: list[dict[str, str]],
) -> None:
    if not rows:
        raise RuntimeError("詳細取得対象がありません。")

    test_item = next(
        (row for row in rows if row.get("uid") == KNOWN_TEST_UID),
        rows[0],
    )

    # ここで詳細属性が取れなければ即停止。
    request_mode, test_record, _, _ = determine_detail_request_mode(
        opener,
        test_item,
    )

    completed = read_completed_records()

    if test_record["uid"] not in completed:
        append_checkpoint(test_record)
        completed[test_record["uid"]] = test_record

    total = len(rows)

    print()
    print(
        f"詳細属性取得開始: 対象={total:,}件, "
        f"既取得={len(completed):,}件, mode={request_mode}"
    )
    print("並列アクセスは行いません。")

    success_count = 0
    error_count = 0

    for index, item in enumerate(rows, start=1):
        uid = item["uid"]

        if uid in completed:
            continue

        print(f"[{index:,}/{total:,}] {uid} {item.get('t', '')}")

        try:
            record, _ = fetch_detail_with_mode(
                opener,
                item,
                mode_name=request_mode,
            )
            append_checkpoint(record)
            completed[uid] = record
            success_count += 1

        except Exception as error:
            error_count += 1
            append_error(item, error)
            print(f"  ERROR: {error}", file=sys.stderr)

        if success_count > 0 and success_count % 50 == 0:
            write_final_csv(list(completed.values()))
            print(f"  checkpoint CSV更新: {len(completed):,}件")

    write_final_csv(list(completed.values()))

    print()
    print("取得処理終了")
    print(f"詳細取得済み: {len(completed):,}/{total:,}")
    print(f"今回のエラー: {error_count:,}")
    print(f"JSONL: {DETAIL_JSONL}")
    print(f"CSV: {FINAL_CSV}")

    if error_count:
        print(f"エラー一覧: {ERROR_CSV}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="横浜市文化財ハマSite 埋蔵文化財包蔵地属性DB取得"
    )

    mode = parser.add_mutually_exclusive_group(required=True)

    mode.add_argument(
        "--test-one",
        action="store_true",
        help="詳細属性を1件だけ検証。全一覧は取得しない。",
    )
    mode.add_argument(
        "--list-only",
        action="store_true",
        help="基本一覧だけ取得。",
    )
    mode.add_argument(
        "--full",
        action="store_true",
        help="詳細属性テスト成功後に全件取得。",
    )

    parser.add_argument(
        "--refresh-list",
        action="store_true",
        help="既存の一覧CSVを使わず再取得。",
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    opener = build_opener()
    initialize_session(opener)

    if args.test_one:
        try:
            run_test_one(opener)
            return 0
        except Exception:
            return 2

    rows = get_or_fetch_list(
        opener,
        refresh=args.refresh_list,
    )

    if args.list_only:
        print(f"一覧取得完了: {len(rows):,}件")
        return 0

    if args.full:
        try:
            run_full(opener, rows)
            return 0
        except Exception as error:
            print()
            print("全件取得を開始できませんでした。")
            print(str(error))
            print(
                "まず --test-one の Yokohama_1430_Test.json "
                "を確認してください。"
            )
            return 2

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
