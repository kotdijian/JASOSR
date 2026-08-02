#!/usr/bin/env python3

"""
============================================================
東京都遺跡位置・町丁目境界チェック（改訂版）
============================================================

【概要】

13Tokyo_total.csv の LGC・Address・Lat・Lon を使い、
TownBoundary.gpkg（13Tokyoレイヤ）の町丁・字等境界と照合する。

従来の「Address文字列から町丁目ポリゴンを先に探す」方式ではなく、
次の順序で判定する。

1. Lat・Lonからポイントを作成する
2. ポイントが入る町丁・字等ポリゴンを空間結合で取得する
3. ポイントの自治体コードとCSVのLGCが一致するか確認する
4. Addressを複数の地名候補に分割する
5. ポリゴン側の町丁・字等名称と次の順序で照合する

   ・完全一致
   ・表記正規化後の一致
   ・親町名一致（例：鑓水 ↔ 鑓水二丁目）
   ・旧地名・別名対応表による一致

問題のない行は出力しない。次の行だけを
13Tokyo_CheckAddress.csv に出力する。

・InvalidCoordinate       座標が欠損または不正
・SpatialUnresolved       ポイントが町丁・字等境界に入らない
・MunicipalityMismatch    ポイントの自治体とLGCが一致しない
・AddressUnresolved       Addressから地名候補を抽出できない
・TownMismatch            自治体は一致するが町丁・字等名称が一致しない

ExactMatch、NormalizedMatch、ParentNameMatch、AliasMatch は正常扱いとし、
出力しない。

【想定ディレクトリ】

JASOSR/
├─ 13Tokyo/
│  ├─ 13Tokyo_total.csv
│  ├─ 13Tokyo_AddressCheck.py
│  ├─ LGC_13Tokyo.csv                  （任意。ただし推奨）
│  └─ 13Tokyo_CheckAddress.csv         （実行後に生成）
│
└─ 00General/
   ├─ TownBoundary.gpkg
   │  └─ 13Tokyo レイヤ
   └─ TownName_Alias_13Tokyo.csv       （任意）

【任意の別名対応表】

TownName_Alias_13Tokyo.csv を置く場合は、次の列を使用する。

    LGC,OldName,CurrentName

例：

    13201,旧大字名,現在町名
    13201,旧大字名,現在町名一丁目

同じOldNameに複数のCurrentNameを登録してよい。
ファイルが存在しない場合も処理は継続する。

【LGC_13Tokyo.csv】

存在する場合は次の列を利用し、Address先頭の自治体名を除去する。

    LGC,Name

例：

    13101,千代田区
    13201,八王子市

ファイルがない場合は、Address先頭にある「○○区」「○○市」等を
簡易規則で除去する。

【必要なライブラリ】

    python -m pip install pandas geopandas pyogrio shapely

【実行例：macOS】

    cd "/Users/noguchiatsushi/Documents/GitHub/JASOSR/13Tokyo"
    source .venv/bin/activate
    python 13Tokyo_AddressCheck.py

【文字コード】

CSVはUTF-8を使用する。

============================================================
"""

from __future__ import annotations

from pathlib import Path
import re
import unicodedata

import geopandas as gpd
import pandas as pd


# ============================================================
# 入出力パス
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_DIR = SCRIPT_DIR.parent

INPUT_CSV = SCRIPT_DIR / "13Tokyo_total.csv"
OUTPUT_CSV = SCRIPT_DIR / "13Tokyo_CheckAddress.csv"

TOWN_BOUNDARY_GPKG = (
    REPOSITORY_DIR
    / "00General"
    / "TownBoundary.gpkg"
)
TOWN_BOUNDARY_LAYER = "13Tokyo"

LGC_MASTER_CSV = SCRIPT_DIR / "LGC_13Tokyo.csv"

ALIAS_CSV = (
    REPOSITORY_DIR
    / "00General"
    / "TownName_Alias_13Tokyo.csv"
)


# ============================================================
# CSV列名
# ============================================================

ADDRESS_COLUMN = "Address"
LGC_COLUMN = "LGC"
LAT_COLUMN = "Lat"
LON_COLUMN = "Lon"


# ============================================================
# 境界データの列設定
# ============================================================

# 列名が確定している場合は文字列で指定する。
# Noneの場合は下記候補から自動検出する。
BOUNDARY_LGC_COLUMN: str | None = None
BOUNDARY_PREF_COLUMN: str | None = None
BOUNDARY_CITY_COLUMN: str | None = None
BOUNDARY_TOWN_COLUMN: str | None = None

BOUNDARY_DIRECT_LGC_CANDIDATES = [
    "LGC",
    "N03_007",
    "CITY_CODE",
    "CITYCODE",
    "MUNICIPALITY_CODE",
]

BOUNDARY_KEY_CODE_CANDIDATES = [
    "KEY_CODE",
    "KEYCODE",
]

BOUNDARY_PREF_COLUMN_CANDIDATES = [
    "PREF",
    "PREF_CODE",
    "PREFCODE",
    "都道府県コード",
]

BOUNDARY_CITY_COLUMN_CANDIDATES = [
    "CITY",
    "CITY_CODE",
    "CITYCODE",
    "市区町村コード",
]

BOUNDARY_TOWN_COLUMN_CANDIDATES = [
    "S_NAME",
    "Town",
    "TOWN",
    "TownName",
    "TOWN_NAME",
    "MOJI",
    "NAME",
    "町丁目名",
    "町丁字名",
]


# ============================================================
# 動作設定
# ============================================================

OUTPUT_ENCODING = "utf-8"
TARGET_CRS = "EPSG:4326"

# LGC共通率がこの値未満の場合は、境界側LGC生成方法に問題がある
# 可能性が高いため停止する。
MIN_LGC_OVERLAP_RATIO = 0.50

# 空間結合で境界線上も候補に含めるため intersects を使う。
SPATIAL_JOIN_PREDICATE = "intersects"

# 正常扱いにする判定
NORMAL_MATCH_RESULTS = {
    "ExactMatch",
    "NormalizedMatch",
    "ParentNameMatch",
    "AliasMatch",
}


# ============================================================
# 表記正規化
# ============================================================

KANJI_DIGIT_MAP = str.maketrans({
    "〇": "0",
    "一": "1",
    "二": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
})

ADDRESS_SEPARATORS_PATTERN = re.compile(
    r"[\s\u3000\r\n\t、，,・;/／|｜]+"
)

MUNICIPALITY_SUFFIX_PATTERN = re.compile(
    r"^.+?(?:特別区|区|市|町|村)$"
)

CHOME_SUFFIX_PATTERN = re.compile(
    r"(?P<base>.+?)(?P<number>[0-9]+)丁目$"
)


# ============================================================
# 汎用関数
# ============================================================

def normalize_unicode(value: object) -> str:
    """NFKCで全角・半角等を統一する。"""
    if value is None:
        return ""

    text = str(value).strip()

    if not text:
        return ""

    return unicodedata.normalize("NFKC", text)


def normalize_name(value: object) -> str:
    """
    町丁・字等名称の照合用表記を作る。

    ・NFKC
    ・空白・区切り記号を除去
    ・漢数字を算用数字に変換
    """
    text = normalize_unicode(value)

    if not text:
        return ""

    text = text.translate(KANJI_DIGIT_MAP)
    text = ADDRESS_SEPARATORS_PATTERN.sub("", text)

    return text


def normalize_lgc_value(value: object) -> str:
    """自治体コードを5桁文字列へ正規化する。"""
    if value is None:
        return ""

    text = str(value).strip()
    text = re.sub(r"\.0$", "", text)
    digits = re.sub(r"\D", "", text)

    if not digits:
        return ""

    if len(digits) >= 5:
        return digits[:5]

    return digits.zfill(5)


def normalize_pref_code(value: object) -> str:
    """都道府県コードを2桁へ正規化する。"""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-2:].zfill(2) if digits else ""


def normalize_city_code(value: object) -> str:
    """市区町村部分コードを3桁へ正規化する。"""
    text = re.sub(r"\.0$", "", str(value or "").strip())
    digits = re.sub(r"\D", "", text)

    if not digits:
        return ""

    # すでに5桁以上なら末尾3桁ではなく、先頭5桁の後半3桁を使う。
    if len(digits) >= 5:
        return digits[:5][-3:]

    return digits.zfill(3)[-3:]


def find_column(
    columns: pd.Index,
    candidates: list[str],
) -> str | None:
    """候補列名を大文字小文字を無視して探す。"""
    lookup = {
        str(column).lower(): str(column)
        for column in columns
    }

    for candidate in candidates:
        found = lookup.get(candidate.lower())

        if found is not None:
            return found

    return None


def validate_required_columns(
    dataframe: pd.DataFrame,
    required_columns: list[str],
    source_name: str,
) -> None:
    missing = [
        column
        for column in required_columns
        if column not in dataframe.columns
    ]

    if missing:
        raise KeyError(
            f"{source_name}に必要な列がありません: "
            + ", ".join(missing)
        )


# ============================================================
# 境界データ読み込み・LGC生成
# ============================================================

def build_boundary_lgc(
    boundaries: gpd.GeoDataFrame,
) -> tuple[pd.Series, str]:
    """
    境界データから5桁LGCを生成する。

    優先順位：
    1. 明示指定されたLGC列
    2. KEY_CODE等の先頭5桁
    3. PREF + CITY
    4. 直接LGC候補列
    """
    if BOUNDARY_LGC_COLUMN is not None:
        if BOUNDARY_LGC_COLUMN not in boundaries.columns:
            raise KeyError(
                "指定した境界LGC列がありません: "
                f"{BOUNDARY_LGC_COLUMN}"
            )

        return (
            boundaries[BOUNDARY_LGC_COLUMN].map(
                normalize_lgc_value
            ),
            BOUNDARY_LGC_COLUMN,
        )

    key_code_column = find_column(
        boundaries.columns,
        BOUNDARY_KEY_CODE_CANDIDATES,
    )

    if key_code_column is not None:
        return (
            boundaries[key_code_column].map(
                normalize_lgc_value
            ),
            f"{key_code_column}の先頭5桁",
        )

    pref_column = (
        BOUNDARY_PREF_COLUMN
        if BOUNDARY_PREF_COLUMN is not None
        else find_column(
            boundaries.columns,
            BOUNDARY_PREF_COLUMN_CANDIDATES,
        )
    )

    city_column = (
        BOUNDARY_CITY_COLUMN
        if BOUNDARY_CITY_COLUMN is not None
        else find_column(
            boundaries.columns,
            BOUNDARY_CITY_COLUMN_CANDIDATES,
        )
    )

    if pref_column is not None and city_column is not None:
        if pref_column not in boundaries.columns:
            raise KeyError(
                f"指定した都道府県コード列がありません: {pref_column}"
            )

        if city_column not in boundaries.columns:
            raise KeyError(
                f"指定した市区町村コード列がありません: {city_column}"
            )

        lgc = (
            boundaries[pref_column].map(normalize_pref_code)
            + boundaries[city_column].map(normalize_city_code)
        )

        return lgc, f"{pref_column}+{city_column}"

    direct_column = find_column(
        boundaries.columns,
        BOUNDARY_DIRECT_LGC_CANDIDATES,
    )

    if direct_column is not None:
        return (
            boundaries[direct_column].map(normalize_lgc_value),
            direct_column,
        )

    raise KeyError(
        "境界データから自治体コードを生成できませんでした。\n"
        f"現在の列: {', '.join(map(str, boundaries.columns))}"
    )


def detect_boundary_town_column(
    boundaries: gpd.GeoDataFrame,
) -> str:
    if BOUNDARY_TOWN_COLUMN is not None:
        if BOUNDARY_TOWN_COLUMN not in boundaries.columns:
            raise KeyError(
                "指定した町丁・字等名称列がありません: "
                f"{BOUNDARY_TOWN_COLUMN}"
            )

        return BOUNDARY_TOWN_COLUMN

    found = find_column(
        boundaries.columns,
        BOUNDARY_TOWN_COLUMN_CANDIDATES,
    )

    if found is None:
        raise KeyError(
            "町丁・字等名称列を特定できませんでした。\n"
            f"現在の列: {', '.join(map(str, boundaries.columns))}"
        )

    return found


def read_town_boundaries() -> tuple[
    gpd.GeoDataFrame,
    str,
    str,
]:
    """GeoPackageから町丁・字等境界を読み込む。"""
    if not TOWN_BOUNDARY_GPKG.exists():
        raise FileNotFoundError(
            f"町丁目GeoPackageが見つかりません: {TOWN_BOUNDARY_GPKG}"
        )

    boundaries = gpd.read_file(
        TOWN_BOUNDARY_GPKG,
        layer=TOWN_BOUNDARY_LAYER,
        engine="pyogrio",
    )

    if boundaries.empty:
        raise ValueError(
            f"{TOWN_BOUNDARY_LAYER}レイヤに地物がありません。"
        )

    if boundaries.crs is None:
        raise ValueError(
            "町丁・字等境界にCRSが設定されていません。"
        )

    town_column = detect_boundary_town_column(boundaries)
    boundary_lgc, lgc_source = build_boundary_lgc(boundaries)

    boundaries = boundaries.to_crs(TARGET_CRS)
    boundaries = boundaries.loc[
        boundaries.geometry.notna()
        & ~boundaries.geometry.is_empty
    ].copy()

    # boundariesを抽出した後も元indexでSeriesが整列される。
    boundaries["_LGC"] = boundary_lgc.loc[boundaries.index]
    boundaries["_TOWN_RAW"] = (
        boundaries[town_column]
        .fillna("")
        .astype(str)
        .str.strip()
    )
    boundaries["_TOWN_NORMALIZED"] = (
        boundaries["_TOWN_RAW"].map(normalize_name)
    )

    boundaries = boundaries.loc[
        boundaries["_LGC"].ne("")
        & boundaries["_TOWN_NORMALIZED"].ne("")
    ].copy()

    # 出力に必要な列だけ残す。
    boundaries = boundaries[
        ["_LGC", "_TOWN_RAW", "_TOWN_NORMALIZED", "geometry"]
    ].copy()

    return boundaries, town_column, lgc_source


# ============================================================
# 自治体マスター・別名表
# ============================================================

def read_lgc_master() -> dict[str, str]:
    """LGC→自治体名の辞書を作る。ファイルがなければ空辞書。"""
    if not LGC_MASTER_CSV.exists():
        print(
            "注意: LGC_13Tokyo.csvがないため、"
            "Address先頭の自治体名は簡易規則で除去します。"
        )
        return {}

    master = pd.read_csv(
        LGC_MASTER_CSV,
        encoding="utf-8",
        dtype=str,
        keep_default_na=False,
    )

    validate_required_columns(
        master,
        ["LGC", "Name"],
        LGC_MASTER_CSV.name,
    )

    result: dict[str, str] = {}

    for row in master.itertuples(index=False):
        lgc = normalize_lgc_value(getattr(row, "LGC"))
        name = normalize_unicode(getattr(row, "Name"))

        if lgc and name:
            result[lgc] = name

    return result


def read_alias_table() -> dict[
    tuple[str, str],
    set[str],
]:
    """
    (LGC, 正規化旧地名) → 正規化現地名集合 の辞書を作る。
    """
    if not ALIAS_CSV.exists():
        print(
            "情報: TownName_Alias_13Tokyo.csvはありません。"
            "別名照合を省略します。"
        )
        return {}

    alias_df = pd.read_csv(
        ALIAS_CSV,
        encoding="utf-8",
        dtype=str,
        keep_default_na=False,
    )

    validate_required_columns(
        alias_df,
        ["LGC", "OldName", "CurrentName"],
        ALIAS_CSV.name,
    )

    alias_map: dict[tuple[str, str], set[str]] = {}

    for row in alias_df.itertuples(index=False):
        lgc = normalize_lgc_value(getattr(row, "LGC"))
        old_name = normalize_name(getattr(row, "OldName"))
        current_name = normalize_name(getattr(row, "CurrentName"))

        if not lgc or not old_name or not current_name:
            continue

        alias_map.setdefault(
            (lgc, old_name),
            set(),
        ).add(current_name)

    print(f"別名対応表: {len(alias_map):,}キー")

    return alias_map


# ============================================================
# Address解析
# ============================================================

def strip_municipality_name(
    address: str,
    lgc: str,
    municipality_names: dict[str, str],
) -> str:
    """Address先頭の自治体名を除去する。"""
    text = normalize_unicode(address)

    if not text:
        return ""

    municipality_name = municipality_names.get(lgc, "")

    if municipality_name:
        name = normalize_unicode(municipality_name)

        if text.startswith(name):
            return text[len(name):].lstrip()

    # マスターがない、または一致しない場合の簡易除去。
    first_split = re.split(r"[\s\u3000]+", text, maxsplit=1)

    if len(first_split) == 2 and MUNICIPALITY_SUFFIX_PATTERN.fullmatch(
        first_split[0]
    ):
        return first_split[1].strip()

    return text


def extract_address_candidates(
    address: str,
    lgc: str,
    municipality_names: dict[str, str],
) -> list[str]:
    """
    Addressを複数の地名候補へ分割する。

    例：
      大田区 西糀谷一丁目 西糀谷二丁目
      → [西糀谷一丁目, 西糀谷二丁目]
    """
    body = strip_municipality_name(
        address,
        lgc,
        municipality_names,
    )

    if not body:
        return []

    raw_candidates = [
        item.strip()
        for item in ADDRESS_SEPARATORS_PATTERN.split(body)
        if item.strip()
    ]

    # 同じ候補を順序維持で除去。
    unique_candidates: list[str] = []
    seen: set[str] = set()

    for candidate in raw_candidates:
        normalized = normalize_name(candidate)

        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        unique_candidates.append(candidate)

    return unique_candidates


# ============================================================
# 地名照合
# ============================================================

def get_parent_name(normalized_name: str) -> str:
    """「○○1丁目」から「○○」を返す。丁目がなければ元の値。"""
    match = CHOME_SUFFIX_PATTERN.fullmatch(normalized_name)

    if match:
        return match.group("base")

    return normalized_name


def classify_name_match(
    lgc: str,
    address_candidates: list[str],
    boundary_towns: list[str],
    alias_map: dict[tuple[str, str], set[str]],
) -> tuple[str, str, str]:
    """
    地名候補とポイント所在町丁・字等名称を照合する。

    戻り値：
      (判定, 一致したAddress候補, 一致した境界名称)
    """
    if not address_candidates:
        return "AddressUnresolved", "", ""

    normalized_candidates = [
        (raw, normalize_name(raw))
        for raw in address_candidates
        if normalize_name(raw)
    ]

    normalized_towns = [
        (raw, normalize_name(raw))
        for raw in boundary_towns
        if normalize_name(raw)
    ]

    # 1. 完全一致（原表記）
    for candidate_raw, _ in normalized_candidates:
        candidate_unicode = normalize_unicode(candidate_raw)

        for town_raw, _ in normalized_towns:
            if candidate_unicode == normalize_unicode(town_raw):
                return "ExactMatch", candidate_raw, town_raw

    # 2. 正規化一致
    for candidate_raw, candidate_norm in normalized_candidates:
        for town_raw, town_norm in normalized_towns:
            if candidate_norm == town_norm:
                return "NormalizedMatch", candidate_raw, town_raw

    # 3. 親町名一致
    for candidate_raw, candidate_norm in normalized_candidates:
        candidate_parent = get_parent_name(candidate_norm)

        for town_raw, town_norm in normalized_towns:
            town_parent = get_parent_name(town_norm)

            # 「鑓水」↔「鑓水2丁目」等を許容。
            if (
                candidate_norm == town_parent
                or candidate_parent == town_norm
                or (
                    candidate_parent == town_parent
                    and candidate_parent != ""
                )
            ):
                return "ParentNameMatch", candidate_raw, town_raw

    # 4. 別名対応表
    town_norm_set = {
        normalized
        for _, normalized in normalized_towns
    }

    for candidate_raw, candidate_norm in normalized_candidates:
        current_names = alias_map.get(
            (lgc, candidate_norm),
            set(),
        )

        matched = current_names & town_norm_set

        if matched:
            matched_name = next(iter(matched))

            for town_raw, town_norm in normalized_towns:
                if town_norm == matched_name:
                    return "AliasMatch", candidate_raw, town_raw

    return "TownMismatch", "", ""


# ============================================================
# 事前検査
# ============================================================

def validate_lgc_overlap(
    records: pd.DataFrame,
    boundaries: gpd.GeoDataFrame,
) -> None:
    """CSVと境界データのLGC共通率を確認する。"""
    record_lgcs = {
        normalize_lgc_value(value)
        for value in records[LGC_COLUMN]
        if normalize_lgc_value(value)
    }

    boundary_lgcs = set(
        boundaries["_LGC"].dropna().astype(str)
    )

    overlap = record_lgcs & boundary_lgcs

    ratio = (
        len(overlap) / len(record_lgcs)
        if record_lgcs
        else 0.0
    )

    print(
        "LGC照合: "
        f"CSV={len(record_lgcs):,}, "
        f"境界={len(boundary_lgcs):,}, "
        f"共通={len(overlap):,}, "
        f"共通率={ratio:.1%}"
    )

    if ratio < MIN_LGC_OVERLAP_RATIO:
        sample_csv = ", ".join(sorted(record_lgcs)[:10])
        sample_boundary = ", ".join(sorted(boundary_lgcs)[:10])

        raise ValueError(
            "CSVと境界データのLGC共通率が低すぎます。\n"
            "境界側のLGC生成列を確認してください。\n"
            f"CSV例: {sample_csv}\n"
            f"境界例: {sample_boundary}"
        )


# ============================================================
# 空間結合
# ============================================================

def create_point_geodataframe(
    records: pd.DataFrame,
) -> tuple[gpd.GeoDataFrame, pd.Series]:
    """有効座標からポイントGeoDataFrameを作る。"""
    latitude = pd.to_numeric(
        records[LAT_COLUMN],
        errors="coerce",
    )
    longitude = pd.to_numeric(
        records[LON_COLUMN],
        errors="coerce",
    )

    valid_mask = (
        latitude.notna()
        & longitude.notna()
        & latitude.between(-90, 90)
        & longitude.between(-180, 180)
    )

    valid_records = records.loc[valid_mask].copy()
    valid_records["_SOURCE_INDEX"] = valid_records.index
    valid_records["_LGC_NORMALIZED"] = (
        valid_records[LGC_COLUMN].map(normalize_lgc_value)
    )

    points = gpd.GeoDataFrame(
        valid_records,
        geometry=gpd.points_from_xy(
            longitude.loc[valid_mask],
            latitude.loc[valid_mask],
        ),
        crs=TARGET_CRS,
    )

    return points, valid_mask


def spatial_join_points(
    points: gpd.GeoDataFrame,
    boundaries: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    ポイントと町丁・字等境界を空間結合する。
    """
    boundary_view = boundaries[
        [
            "_LGC",
            "_TOWN_RAW",
            "_TOWN_NORMALIZED",
            "geometry",
        ]
    ].copy()

    joined = gpd.sjoin(
        points,
        boundary_view,
        how="left",
        predicate=SPATIAL_JOIN_PREDICATE,
    )

    matched_count = int(
        joined["index_right"].notna().sum()
    )

    matched_source_count = int(
        joined.loc[
            joined["index_right"].notna(),
            "_SOURCE_INDEX",
        ].nunique()
    )

    print(
        "空間結合結果: "
        f"有効ポイント={len(points):,}件、"
        f"結合行={matched_count:,}件、"
        f"境界特定ポイント={matched_source_count:,}件"
    )

    if matched_source_count == 0:
        print(
            "警告: ポイントと町丁目ポリゴンが"
            "1件も空間結合されませんでした。"
        )

        print(
            f"ポイントCRS: {points.crs}"
        )

        print(
            f"境界CRS: {boundaries.crs}"
        )

        print(
            "ポイント範囲: "
            f"{points.total_bounds.tolist()}"
        )

        print(
            "境界範囲: "
            f"{boundaries.total_bounds.tolist()}"
        )

    return pd.DataFrame(
        joined.drop(columns="geometry")
    )


def build_spatial_matches(
    joined: pd.DataFrame,
) -> dict[int, list[dict[str, str]]]:
    """
    元CSV行indexごとに空間結合候補をまとめる。

    先頭がアンダースコアの列名は、pandas.DataFrame.itertuples()
    では属性名が変更されるため、iterrows()と列名アクセスを使う。
    """
    result: dict[int, list[dict[str, str]]] = {}

    for source_index, group in joined.groupby(
        "_SOURCE_INDEX",
        dropna=False,
    ):
        matches: list[dict[str, str]] = []

        for _, row in group.iterrows():
            boundary_lgc_value = row.get("_LGC", "")
            town_raw_value = row.get("_TOWN_RAW", "")

            boundary_lgc = (
                ""
                if pd.isna(boundary_lgc_value)
                else str(boundary_lgc_value).strip()
            )

            town_raw = (
                ""
                if pd.isna(town_raw_value)
                else str(town_raw_value).strip()
            )

            if not boundary_lgc or not town_raw:
                continue

            entry = {
                "LGC": boundary_lgc,
                "Town": town_raw,
            }

            if entry not in matches:
                matches.append(entry)

        result[int(source_index)] = matches

    return result


# ============================================================
# 判定本体
# ============================================================

def check_records(
    records: pd.DataFrame,
    valid_coordinate_mask: pd.Series,
    spatial_matches: dict[int, list[dict[str, str]]],
    municipality_names: dict[str, str],
    alias_map: dict[tuple[str, str], set[str]],
) -> pd.DataFrame:
    """問題があるレコードだけを返す。"""
    problem_rows: list[dict[str, object]] = []
    total = len(records)

    for sequence, (source_index, row) in enumerate(
        records.iterrows(),
        start=1,
    ):
        if (
            sequence == 1
            or sequence % 500 == 0
            or sequence == total
        ):
            print(f"位置確認中: {sequence:,}／{total:,}")

        lgc = normalize_lgc_value(row.get(LGC_COLUMN, ""))
        address = str(row.get(ADDRESS_COLUMN, "") or "").strip()
        address_candidates = extract_address_candidates(
            address,
            lgc,
            municipality_names,
        )

        result = ""
        matched_candidate = ""
        matched_town = ""

        all_matches = spatial_matches.get(int(source_index), [])
        point_lgcs = sorted({
            item["LGC"]
            for item in all_matches
        })
        point_towns_all = sorted({
            item["Town"]
            for item in all_matches
        })

        if not bool(valid_coordinate_mask.loc[source_index]):
            result = "InvalidCoordinate"

        elif not all_matches:
            result = "SpatialUnresolved"

        else:
            same_lgc_matches = [
                item
                for item in all_matches
                if item["LGC"] == lgc
            ]

            if not same_lgc_matches:
                result = "MunicipalityMismatch"

            elif not address_candidates:
                result = "AddressUnresolved"

            else:
                same_lgc_towns = sorted({
                    item["Town"]
                    for item in same_lgc_matches
                })

                (
                    result,
                    matched_candidate,
                    matched_town,
                ) = classify_name_match(
                    lgc,
                    address_candidates,
                    same_lgc_towns,
                    alias_map,
                )

        if result in NORMAL_MATCH_RESULTS:
            continue

        output_row = row.to_dict()
        output_row.update({
            "CheckResult": result,
            "AddressCandidates": "｜".join(address_candidates),
            "PointBoundaryLGC": "｜".join(point_lgcs),
            "PointBoundaryTown": "｜".join(point_towns_all),
            "MatchedAddressCandidate": matched_candidate,
            "MatchedBoundaryTown": matched_town,
            "NormalizedLGC": lgc,
            "SourceRowNumber": int(source_index) + 2,
        })
        problem_rows.append(output_row)

    output_columns = list(records.columns) + [
        "CheckResult",
        "AddressCandidates",
        "PointBoundaryLGC",
        "PointBoundaryTown",
        "MatchedAddressCandidate",
        "MatchedBoundaryTown",
        "NormalizedLGC",
        "SourceRowNumber",
    ]

    if not problem_rows:
        return pd.DataFrame(columns=output_columns)

    return pd.DataFrame(problem_rows)[output_columns]


# ============================================================
# 実行
# ============================================================

def main() -> None:
    if not INPUT_CSV.exists():
        raise FileNotFoundError(
            f"入力CSVが見つかりません: {INPUT_CSV}"
        )

    records = pd.read_csv(
        INPUT_CSV,
        encoding="utf-8",
        dtype={LGC_COLUMN: str},
        keep_default_na=False,
    )

    validate_required_columns(
        records,
        [ADDRESS_COLUMN, LGC_COLUMN, LAT_COLUMN, LON_COLUMN],
        INPUT_CSV.name,
    )

    (
        boundaries,
        town_column,
        lgc_source,
    ) = read_town_boundaries()

    print(f"入力CSV: {INPUT_CSV}")
    print(f"町丁・字等境界: {TOWN_BOUNDARY_GPKG}")
    print(f"レイヤ: {TOWN_BOUNDARY_LAYER}")
    print(f"境界地物数: {len(boundaries):,}")
    print(f"境界町名列: {town_column}")
    print(f"境界LGC生成: {lgc_source}")

    validate_lgc_overlap(records, boundaries)

    municipality_names = read_lgc_master()
    alias_map = read_alias_table()

    points, valid_coordinate_mask = create_point_geodataframe(
        records
    )

    print(f"有効座標件数: {len(points):,}／{len(records):,}")

    joined = spatial_join_points(points, boundaries)
    spatial_matches = build_spatial_matches(joined)

    check_result = check_records(
        records,
        valid_coordinate_mask,
        spatial_matches,
        municipality_names,
        alias_map,
    )

    check_result.to_csv(
        OUTPUT_CSV,
        index=False,
        encoding=OUTPUT_ENCODING,
        lineterminator="\n",
    )

    print()
    print("位置確認が完了しました。")
    print(f"入力件数: {len(records):,}件")
    print(f"確認対象として出力: {len(check_result):,}件")
    print(f"出力先: {OUTPUT_CSV}")

    if not check_result.empty:
        print()
        print("確認結果内訳")

        summary = check_result["CheckResult"].value_counts(
            dropna=False
        )

        for result_name, count in summary.items():
            print(f"  {result_name}: {count:,}件")


if __name__ == "__main__":
    main()
