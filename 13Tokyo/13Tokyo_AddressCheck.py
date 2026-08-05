#!/usr/bin/env python3

"""
============================================================
東京都遺跡位置・町丁字境界チェック（負荷最適化版）
============================================================

【概要】

13Tokyo_total.csv の LGC・Address・Lat・Lon と、
TownBoundary.gpkg の 13Tokyo レイヤを照合する。

判定を次の3列へ分離する。

    LGCCheck
    TownCheck
    OverallCheck

LGCCheck:
    Match
    Mismatch
    Unresolved
    InvalidCoordinate

TownCheck:
    ExactMatch
    NormalizedMatch
    PartialMatch
    ParentNameMatch
    AliasMatch
    Mismatch
    Unresolved
    NotEvaluated

OverallCheck:
    OK
    LGCMismatch
    TownMismatch
    LGCAndTownMismatch
    Unresolved
    InvalidCoordinate

問題のあるレコードだけを 13Tokyo_CheckAddress.csv に出力する。

【高速化方針】

1. GeoPackageは実行時に1回だけ読み込む。
2. 町丁字名称一覧はLGC別に1回だけ構築する。
3. ポイントと境界の空間結合は全件まとめて1回だけ行う。
4. 通常は座標地点の町丁字名だけをAddressと直接比較する。
5. LGC別町丁字一覧の部分一致検索は、直接比較で解決しない行だけに限定する。
6. 部分一致検索では長い名称を優先する。
7. 同じAddress・LGCの解析結果をキャッシュする。
8. 検索辞書にはgeometryを重複保持しない。

【想定ディレクトリ】

JASOSR/
├─ 13Tokyo/
│  ├─ 13Tokyo_total.csv
│  ├─ 13Tokyo_AddressCheck.py
│  ├─ LGC_13Tokyo.csv
│  └─ 13Tokyo_CheckAddress.csv
│
└─ 00General/
   ├─ TownBoundary.gpkg
   │  └─ 13Tokyo レイヤ
   └─ TownName_Alias_13Tokyo.csv   （任意）

【別名表（任意）】

    LGC,OldName,CurrentName

【必要ライブラリ】

    python -m pip install pandas geopandas pyogrio shapely

【実行】

    cd "/Users/noguchiatsushi/Documents/GitHub/JASOSR/13Tokyo"
    source .venv/bin/activate
    python 13Tokyo_AddressCheck.py

============================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
import re
import unicodedata

import geopandas as gpd
import pandas as pd


# ============================================================
# 入出力
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
# 入力CSV列
# ============================================================

ADDRESS_COLUMN = "Address"
LGC_COLUMN = "LGC"
LAT_COLUMN = "Lat"
LON_COLUMN = "Lon"


# ============================================================
# 境界属性列
# ============================================================

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

TARGET_CRS = "EPSG:4326"
OUTPUT_ENCODING = "utf-8"

MIN_LGC_OVERLAP_RATIO = 0.50
SPATIAL_JOIN_PREDICATE = "intersects"

# 部分一致フォールバックで検索する町名の最小文字数。
# 1文字名称は偶然一致が多いため除外する。
MIN_PARTIAL_NAME_LENGTH = 2

NORMAL_TOWN_RESULTS = {
    "ExactMatch",
    "NormalizedMatch",
    "PartialMatch",
    "ParentNameMatch",
    "AliasMatch",
}


# ============================================================
# 正規化
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
    r"^(?P<base>.+?)(?P<number>[0-9]+)丁目$"
)


@dataclass(frozen=True)
class TownEntry:
    raw: str
    normalized: str
    parent: str


def normalize_unicode(value: object) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if not text:
        return ""

    return unicodedata.normalize("NFKC", text)


def normalize_name(value: object) -> str:
    """
    比較用表記。

    ・NFKC
    ・空白、区切り記号除去
    ・丁目に使われる漢数字を算用数字化
    """
    text = normalize_unicode(value)

    if not text:
        return ""

    text = text.translate(KANJI_DIGIT_MAP)
    text = ADDRESS_SEPARATORS_PATTERN.sub("", text)

    return text


def normalize_lgc_value(value: object) -> str:
    if value is None:
        return ""

    text = re.sub(
        r"\.0$",
        "",
        str(value).strip(),
    )
    digits = re.sub(r"\D", "", text)

    if not digits:
        return ""

    if len(digits) >= 5:
        return digits[:5]

    return digits.zfill(5)


def normalize_pref_code(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-2:].zfill(2) if digits else ""


def normalize_city_code(value: object) -> str:
    text = re.sub(
        r"\.0$",
        "",
        str(value or "").strip(),
    )
    digits = re.sub(r"\D", "", text)

    if not digits:
        return ""

    if len(digits) >= 5:
        return digits[:5][-3:]

    return digits.zfill(3)[-3:]


def get_parent_name(normalized_name: str) -> str:
    match = CHOME_SUFFIX_PATTERN.fullmatch(normalized_name)

    if match:
        return match.group("base")

    return normalized_name


def find_column(
    columns: pd.Index,
    candidates: list[str],
) -> str | None:
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
# 境界データ
# ============================================================

def build_boundary_lgc(
    boundaries: gpd.GeoDataFrame,
) -> tuple[pd.Series, str]:
    """
    5桁LGC生成の優先順位:

    1. 明示した列
    2. KEY_CODEの先頭5桁
    3. PREF + CITY
    4. 直接LGC候補列
    """
    if BOUNDARY_LGC_COLUMN is not None:
        if BOUNDARY_LGC_COLUMN not in boundaries.columns:
            raise KeyError(
                f"境界LGC列がありません: {BOUNDARY_LGC_COLUMN}"
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

    if (
        pref_column is not None
        and city_column is not None
    ):
        if pref_column not in boundaries.columns:
            raise KeyError(
                f"都道府県コード列がありません: {pref_column}"
            )

        if city_column not in boundaries.columns:
            raise KeyError(
                f"市区町村コード列がありません: {city_column}"
            )

        return (
            boundaries[pref_column].map(normalize_pref_code)
            + boundaries[city_column].map(normalize_city_code),
            f"{pref_column}+{city_column}",
        )

    direct_column = find_column(
        boundaries.columns,
        BOUNDARY_DIRECT_LGC_CANDIDATES,
    )

    if direct_column is not None:
        return (
            boundaries[direct_column].map(
                normalize_lgc_value
            ),
            direct_column,
        )

    raise KeyError(
        "境界データからLGCを生成できません。\n"
        f"現在の列: {', '.join(map(str, boundaries.columns))}"
    )


def detect_boundary_town_column(
    boundaries: gpd.GeoDataFrame,
) -> str:
    if BOUNDARY_TOWN_COLUMN is not None:
        if BOUNDARY_TOWN_COLUMN not in boundaries.columns:
            raise KeyError(
                f"町丁字名称列がありません: {BOUNDARY_TOWN_COLUMN}"
            )

        return BOUNDARY_TOWN_COLUMN

    found = find_column(
        boundaries.columns,
        BOUNDARY_TOWN_COLUMN_CANDIDATES,
    )

    if found is None:
        raise KeyError(
            "町丁字名称列を特定できません。\n"
            f"現在の列: {', '.join(map(str, boundaries.columns))}"
        )

    return found


def read_town_boundaries() -> tuple[
    gpd.GeoDataFrame,
    str,
    str,
]:
    if not TOWN_BOUNDARY_GPKG.exists():
        raise FileNotFoundError(
            f"GeoPackageが見つかりません: {TOWN_BOUNDARY_GPKG}"
        )

    started = perf_counter()

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
            "町丁字境界にCRSが設定されていません。"
        )

    town_column = detect_boundary_town_column(boundaries)
    boundary_lgc, lgc_source = build_boundary_lgc(boundaries)

    boundaries = boundaries.to_crs(TARGET_CRS)

    boundaries = boundaries.loc[
        boundaries.geometry.notna()
        & ~boundaries.geometry.is_empty
    ].copy()

    boundaries["_LGC"] = (
        boundary_lgc.loc[boundaries.index]
        .fillna("")
        .astype(str)
    )

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

    boundaries = boundaries[
        [
            "_LGC",
            "_TOWN_RAW",
            "_TOWN_NORMALIZED",
            "geometry",
        ]
    ].copy()

    print(
        f"境界読込・整形: {perf_counter() - started:.2f}秒"
    )

    return boundaries, town_column, lgc_source


def build_town_index(
    boundaries: gpd.GeoDataFrame,
) -> dict[str, tuple[TownEntry, ...]]:
    """
    LGC別の町丁字名辞書を一度だけ作る。

    geometryは保持しない。
    名称を長い順に並べ、部分一致時に最長名称を優先する。
    """
    started = perf_counter()
    result: dict[str, tuple[TownEntry, ...]] = {}

    unique_table = (
        boundaries[
            ["_LGC", "_TOWN_RAW", "_TOWN_NORMALIZED"]
        ]
        .drop_duplicates()
    )

    for lgc, group in unique_table.groupby(
        "_LGC",
        sort=False,
    ):
        entries = [
            TownEntry(
                raw=str(row["_TOWN_RAW"]),
                normalized=str(row["_TOWN_NORMALIZED"]),
                parent=get_parent_name(
                    str(row["_TOWN_NORMALIZED"])
                ),
            )
            for _, row in group.iterrows()
            if len(str(row["_TOWN_NORMALIZED"])) >= MIN_PARTIAL_NAME_LENGTH
        ]

        entries.sort(
            key=lambda entry: len(entry.normalized),
            reverse=True,
        )

        result[str(lgc)] = tuple(entries)

    total_names = sum(
        len(entries)
        for entries in result.values()
    )

    print(
        "町丁字検索辞書: "
        f"{len(result):,}自治体、"
        f"{total_names:,}名称、"
        f"{perf_counter() - started:.2f}秒"
    )

    return result


# ============================================================
# 自治体名・別名
# ============================================================

def read_lgc_master() -> dict[str, str]:
    if not LGC_MASTER_CSV.exists():
        print(
            "注意: LGC_13Tokyo.csvがありません。"
            "自治体名除去は簡易規則を使用します。"
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

    for _, row in master.iterrows():
        lgc = normalize_lgc_value(row["LGC"])
        name = normalize_unicode(row["Name"])

        if lgc and name:
            result[lgc] = name

    return result


def read_alias_table() -> dict[
    tuple[str, str],
    set[str],
]:
    if not ALIAS_CSV.exists():
        print(
            "情報: 別名対応表はありません。"
            "AliasMatchを省略します。"
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

    result: dict[tuple[str, str], set[str]] = {}

    for _, row in alias_df.iterrows():
        lgc = normalize_lgc_value(row["LGC"])
        old_name = normalize_name(row["OldName"])
        current_name = normalize_name(row["CurrentName"])

        if lgc and old_name and current_name:
            result.setdefault(
                (lgc, old_name),
                set(),
            ).add(current_name)

    print(f"別名対応表: {len(result):,}キー")

    return result


# ============================================================
# Address解析
# ============================================================

def strip_municipality_name(
    address: str,
    lgc: str,
    municipality_names: dict[str, str],
) -> str:
    text = normalize_unicode(address)

    if not text:
        return ""

    municipality_name = municipality_names.get(lgc, "")

    if municipality_name:
        name = normalize_unicode(municipality_name)

        if text.startswith(name):
            return text[len(name):].lstrip()

    split = re.split(
        r"[\s\u3000]+",
        text,
        maxsplit=1,
    )

    if (
        len(split) == 2
        and MUNICIPALITY_SUFFIX_PATTERN.fullmatch(split[0])
    ):
        return split[1].strip()

    return text


def split_address_candidates(
    body: str,
) -> list[str]:
    raw_candidates = [
        item.strip()
        for item in ADDRESS_SEPARATORS_PATTERN.split(body)
        if item.strip()
    ]

    result: list[str] = []
    seen: set[str] = set()

    for candidate in raw_candidates:
        normalized = normalize_name(candidate)

        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        result.append(candidate)

    return result


def longest_non_overlapping_matches(
    normalized_address: str,
    town_entries: tuple[TownEntry, ...],
) -> list[TownEntry]:
    """
    LGC別町名一覧からAddress内の候補を抽出する。

    長い名称を先に検索し、すでに採用した文字範囲と重なる
    短い名称を除外する。
    """
    accepted: list[tuple[int, int, TownEntry]] = []

    for entry in town_entries:
        name = entry.normalized

        if not name:
            continue

        start = normalized_address.find(name)

        while start >= 0:
            end = start + len(name)

            overlaps = any(
                not (
                    end <= accepted_start
                    or start >= accepted_end
                )
                for accepted_start, accepted_end, _ in accepted
            )

            if not overlaps:
                accepted.append(
                    (start, end, entry)
                )
                break

            start = normalized_address.find(
                name,
                start + 1,
            )

    accepted.sort(key=lambda item: item[0])

    return [
        entry
        for _, _, entry in accepted
    ]


class AddressResolver:
    """
    Address解析結果をキャッシュする。

    同一のLGC・Addressが繰り返される場合の再計算を避ける。
    """

    def __init__(
        self,
        municipality_names: dict[str, str],
        town_index: dict[str, tuple[TownEntry, ...]],
    ) -> None:
        self.municipality_names = municipality_names
        self.town_index = town_index
        self._cache: dict[
            tuple[str, str],
            tuple[str, str, tuple[str, ...], tuple[TownEntry, ...]],
        ] = {}

    def resolve(
        self,
        address: str,
        lgc: str,
    ) -> tuple[
        str,
        str,
        tuple[str, ...],
        tuple[TownEntry, ...],
    ]:
        key = (lgc, address)

        cached = self._cache.get(key)

        if cached is not None:
            return cached

        body = strip_municipality_name(
            address,
            lgc,
            self.municipality_names,
        )

        normalized_body = normalize_name(body)
        split_candidates = tuple(
            split_address_candidates(body)
        )

        dictionary_matches: tuple[TownEntry, ...] = ()

        # 全町名検索はフォールバック用なので、ここではまだ実施しない。
        result = (
            body,
            normalized_body,
            split_candidates,
            dictionary_matches,
        )
        self._cache[key] = result

        return result

    def dictionary_matches(
        self,
        address: str,
        lgc: str,
        normalized_body: str,
    ) -> tuple[TownEntry, ...]:
        key = (lgc, address)
        cached = self._cache.get(key)

        if cached is not None and cached[3]:
            return cached[3]

        entries = self.town_index.get(lgc, ())

        matches = tuple(
            longest_non_overlapping_matches(
                normalized_body,
                entries,
            )
        )

        if cached is None:
            body = strip_municipality_name(
                address,
                lgc,
                self.municipality_names,
            )
            split_candidates = tuple(
                split_address_candidates(body)
            )
        else:
            body, _, split_candidates, _ = cached

        self._cache[key] = (
            body,
            normalized_body,
            split_candidates,
            matches,
        )

        return matches


# ============================================================
# 空間結合
# ============================================================

def validate_lgc_overlap(
    records: pd.DataFrame,
    boundaries: gpd.GeoDataFrame,
) -> None:
    record_lgcs = {
        normalize_lgc_value(value)
        for value in records[LGC_COLUMN]
        if normalize_lgc_value(value)
    }

    boundary_lgcs = set(
        boundaries["_LGC"]
        .dropna()
        .astype(str)
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
        raise ValueError(
            "CSVと境界データのLGC共通率が低すぎます。"
        )


def create_point_geodataframe(
    records: pd.DataFrame,
) -> tuple[gpd.GeoDataFrame, pd.Series]:
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

    points = gpd.GeoDataFrame(
        valid_records[["_SOURCE_INDEX"]].copy(),
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
    started = perf_counter()

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

    matched_source_count = int(
        joined.loc[
            joined["index_right"].notna(),
            "_SOURCE_INDEX",
        ].nunique()
    )

    print(
        "空間結合: "
        f"{matched_source_count:,}／{len(points):,}ポイントを特定、"
        f"{perf_counter() - started:.2f}秒"
    )

    if matched_source_count == 0:
        raise ValueError(
            "ポイントと町丁字境界が1件も結合されませんでした。\n"
            f"ポイントCRS: {points.crs}\n"
            f"境界CRS: {boundaries.crs}\n"
            f"ポイント範囲: {points.total_bounds.tolist()}\n"
            f"境界範囲: {boundaries.total_bounds.tolist()}"
        )

    return pd.DataFrame(
        joined.drop(columns="geometry")
    )


def build_spatial_matches(
    joined: pd.DataFrame,
) -> dict[int, tuple[tuple[str, str], ...]]:
    """
    source indexごとに (LGC, Town) の一意な組を保持する。

    アンダースコア列を安全に扱うため、列名アクセスを使用する。
    """
    started = perf_counter()
    result: dict[int, tuple[tuple[str, str], ...]] = {}

    matched = joined.loc[
        joined["index_right"].notna(),
        [
            "_SOURCE_INDEX",
            "_LGC",
            "_TOWN_RAW",
        ],
    ].copy()

    matched["_LGC"] = (
        matched["_LGC"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    matched["_TOWN_RAW"] = (
        matched["_TOWN_RAW"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    matched = matched.loc[
        matched["_LGC"].ne("")
        & matched["_TOWN_RAW"].ne("")
    ].drop_duplicates()

    for source_index, group in matched.groupby(
        "_SOURCE_INDEX",
        sort=False,
    ):
        result[int(source_index)] = tuple(
            zip(
                group["_LGC"].tolist(),
                group["_TOWN_RAW"].tolist(),
            )
        )

    print(
        "空間結合結果整理: "
        f"{len(result):,}件、"
        f"{perf_counter() - started:.2f}秒"
    )

    return result


# ============================================================
# 判定
# ============================================================

def direct_town_match(
    split_candidates: tuple[str, ...],
    normalized_body: str,
    point_towns: tuple[str, ...],
) -> tuple[str, str, str]:
    """
    座標地点の町名だけを直接照合する高速経路。
    """
    normalized_candidates = tuple(
        (
            candidate,
            normalize_name(candidate),
        )
        for candidate in split_candidates
        if normalize_name(candidate)
    )

    normalized_point_towns = tuple(
        (
            town,
            normalize_name(town),
        )
        for town in point_towns
        if normalize_name(town)
    )

    # 1. 分割候補との原表記一致
    for candidate_raw, _ in normalized_candidates:
        candidate_unicode = normalize_unicode(candidate_raw)

        for town_raw, _ in normalized_point_towns:
            if candidate_unicode == normalize_unicode(town_raw):
                return "ExactMatch", candidate_raw, town_raw

    # 2. 分割候補との正規化一致
    for candidate_raw, candidate_norm in normalized_candidates:
        for town_raw, town_norm in normalized_point_towns:
            if candidate_norm == town_norm:
                return "NormalizedMatch", candidate_raw, town_raw

    # 3. Address全体に座標地点の町名が含まれる
    for town_raw, town_norm in normalized_point_towns:
        if town_norm and town_norm in normalized_body:
            return "PartialMatch", town_raw, town_raw

    # 4. 親町名一致
    candidate_norms = [
        (raw, normalized)
        for raw, normalized in normalized_candidates
    ]

    for candidate_raw, candidate_norm in candidate_norms:
        candidate_parent = get_parent_name(candidate_norm)

        for town_raw, town_norm in normalized_point_towns:
            town_parent = get_parent_name(town_norm)

            if (
                candidate_norm == town_parent
                or candidate_parent == town_norm
                or (
                    candidate_parent
                    and candidate_parent == town_parent
                )
            ):
                return "ParentNameMatch", candidate_raw, town_raw

    # 分割に失敗していても、Address全体に親町名が含まれれば許容。
    for town_raw, town_norm in normalized_point_towns:
        town_parent = get_parent_name(town_norm)

        if (
            town_parent
            and len(town_parent) >= MIN_PARTIAL_NAME_LENGTH
            and town_parent in normalized_body
        ):
            return "ParentNameMatch", town_parent, town_raw

    return "", "", ""


def alias_match(
    lgc: str,
    split_candidates: tuple[str, ...],
    normalized_body: str,
    point_towns: tuple[str, ...],
    alias_map: dict[tuple[str, str], set[str]],
) -> tuple[str, str, str]:
    if not alias_map:
        return "", "", ""

    point_lookup = {
        normalize_name(town): town
        for town in point_towns
        if normalize_name(town)
    }

    candidate_pairs = [
        (candidate, normalize_name(candidate))
        for candidate in split_candidates
        if normalize_name(candidate)
    ]

    for candidate_raw, candidate_norm in candidate_pairs:
        current_names = alias_map.get(
            (lgc, candidate_norm),
            set(),
        )

        for current_name in current_names:
            if current_name in point_lookup:
                return (
                    "AliasMatch",
                    candidate_raw,
                    point_lookup[current_name],
                )

    # 分割候補にない旧地名も、Address本文内にあれば確認する。
    for (alias_lgc, old_name), current_names in alias_map.items():
        if alias_lgc != lgc:
            continue

        if old_name not in normalized_body:
            continue

        for current_name in current_names:
            if current_name in point_lookup:
                return (
                    "AliasMatch",
                    old_name,
                    point_lookup[current_name],
                )

    return "", "", ""


def fallback_dictionary_match(
    dictionary_matches: tuple[TownEntry, ...],
    point_towns: tuple[str, ...],
) -> tuple[str, str, str]:
    """
    LGC別町丁字辞書による部分一致候補を、
    座標地点の町丁字名で最終確認する。
    """
    point_lookup = {
        normalize_name(town): town
        for town in point_towns
        if normalize_name(town)
    }

    point_parent_lookup: dict[str, str] = {}

    for town_norm, town_raw in point_lookup.items():
        parent = get_parent_name(town_norm)

        if parent:
            point_parent_lookup[parent] = town_raw

    for entry in dictionary_matches:
        if entry.normalized in point_lookup:
            return (
                "PartialMatch",
                entry.raw,
                point_lookup[entry.normalized],
            )

    for entry in dictionary_matches:
        if (
            entry.parent
            and entry.parent in point_parent_lookup
        ):
            return (
                "ParentNameMatch",
                entry.raw,
                point_parent_lookup[entry.parent],
            )

    return "", "", ""


def determine_lgc_check(
    valid_coordinate: bool,
    spatial_matches: tuple[tuple[str, str], ...],
    csv_lgc: str,
) -> str:
    if not valid_coordinate:
        return "InvalidCoordinate"

    if not spatial_matches:
        return "Unresolved"

    point_lgcs = {
        lgc
        for lgc, _ in spatial_matches
    }

    return (
        "Match"
        if csv_lgc in point_lgcs
        else "Mismatch"
    )


def determine_town_check(
    valid_coordinate: bool,
    spatial_matches: tuple[tuple[str, str], ...],
    csv_lgc: str,
    address: str,
    resolver: AddressResolver,
    alias_map: dict[tuple[str, str], set[str]],
) -> tuple[str, str, str, tuple[str, ...], tuple[TownEntry, ...]]:
    if not valid_coordinate:
        return "NotEvaluated", "", "", (), ()

    if not spatial_matches:
        return "NotEvaluated", "", "", (), ()

    (
        _body,
        normalized_body,
        split_candidates,
        _,
    ) = resolver.resolve(address, csv_lgc)

    if not normalized_body:
        return "Unresolved", "", "", (), ()

    # TownCheckはLGCCheckから独立させるため、
    # 座標地点に該当する全町名を比較対象とする。
    point_towns = tuple(dict.fromkeys(
        town
        for _, town in spatial_matches
    ))

    (
        match_result,
        matched_address,
        matched_town,
    ) = direct_town_match(
        split_candidates,
        normalized_body,
        point_towns,
    )

    if match_result:
        return (
            match_result,
            matched_address,
            matched_town,
            split_candidates,
            (),
        )

    (
        match_result,
        matched_address,
        matched_town,
    ) = alias_match(
        csv_lgc,
        split_candidates,
        normalized_body,
        point_towns,
        alias_map,
    )

    if match_result:
        return (
            match_result,
            matched_address,
            matched_town,
            split_candidates,
            (),
        )

    # 高負荷のLGC別全町名検索は未解決行にだけ実行する。
    dictionary_matches = resolver.dictionary_matches(
        address,
        csv_lgc,
        normalized_body,
    )

    (
        match_result,
        matched_address,
        matched_town,
    ) = fallback_dictionary_match(
        dictionary_matches,
        point_towns,
    )

    if match_result:
        return (
            match_result,
            matched_address,
            matched_town,
            split_candidates,
            dictionary_matches,
        )

    return (
        "Mismatch",
        "",
        "",
        split_candidates,
        dictionary_matches,
    )


def determine_overall_check(
    lgc_check: str,
    town_check: str,
) -> str:
    if (
        lgc_check == "InvalidCoordinate"
        or town_check == "InvalidCoordinate"
    ):
        return "InvalidCoordinate"

    if (
        lgc_check == "Unresolved"
        or town_check in {"Unresolved", "NotEvaluated"}
    ):
        return "Unresolved"

    lgc_ok = lgc_check == "Match"
    town_ok = town_check in NORMAL_TOWN_RESULTS

    if lgc_ok and town_ok:
        return "OK"

    if not lgc_ok and town_ok:
        return "LGCMismatch"

    if lgc_ok and not town_ok:
        return "TownMismatch"

    return "LGCAndTownMismatch"


def check_records(
    records: pd.DataFrame,
    valid_coordinate_mask: pd.Series,
    spatial_matches: dict[int, tuple[tuple[str, str], ...]],
    resolver: AddressResolver,
    alias_map: dict[tuple[str, str], set[str]],
) -> pd.DataFrame:
    started = perf_counter()
    problem_rows: list[dict[str, object]] = []
    total = len(records)

    fallback_search_count = 0

    for sequence, (source_index, row) in enumerate(
        records.iterrows(),
        start=1,
    ):
        if (
            sequence == 1
            or sequence % 1000 == 0
            or sequence == total
        ):
            print(
                f"位置確認中: {sequence:,}／{total:,}"
            )

        valid_coordinate = bool(
            valid_coordinate_mask.loc[source_index]
        )

        csv_lgc = normalize_lgc_value(
            row.get(LGC_COLUMN, "")
        )

        address = str(
            row.get(ADDRESS_COLUMN, "") or ""
        ).strip()

        matches = spatial_matches.get(
            int(source_index),
            (),
        )

        lgc_check = determine_lgc_check(
            valid_coordinate,
            matches,
            csv_lgc,
        )

        (
            town_check,
            matched_address,
            matched_town,
            split_candidates,
            dictionary_matches,
        ) = determine_town_check(
            valid_coordinate,
            matches,
            csv_lgc,
            address,
            resolver,
            alias_map,
        )

        if dictionary_matches:
            fallback_search_count += 1

        overall_check = determine_overall_check(
            lgc_check,
            town_check,
        )

        if overall_check == "OK":
            continue

        point_lgcs = tuple(dict.fromkeys(
            lgc
            for lgc, _ in matches
        ))

        point_towns = tuple(dict.fromkeys(
            town
            for _, town in matches
        ))

        output_row = row.to_dict()

        output_row.update({
            "LGCCheck": lgc_check,
            "TownCheck": town_check,
            "OverallCheck": overall_check,
            "AddressCandidates": "｜".join(split_candidates),
            "DictionaryCandidates": "｜".join(
                entry.raw
                for entry in dictionary_matches
            ),
            "PointBoundaryLGC": "｜".join(point_lgcs),
            "PointBoundaryTown": "｜".join(point_towns),
            "MatchedAddressCandidate": matched_address,
            "MatchedBoundaryTown": matched_town,
            "NormalizedLGC": csv_lgc,
            "SourceRowNumber": int(source_index) + 2,
        })

        problem_rows.append(output_row)

    print(
        "判定処理: "
        f"{perf_counter() - started:.2f}秒、"
        f"町名一覧フォールバック={fallback_search_count:,}件"
    )

    output_columns = list(records.columns) + [
        "LGCCheck",
        "TownCheck",
        "OverallCheck",
        "AddressCandidates",
        "DictionaryCandidates",
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
    total_started = perf_counter()

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
        [
            ADDRESS_COLUMN,
            LGC_COLUMN,
            LAT_COLUMN,
            LON_COLUMN,
        ],
        INPUT_CSV.name,
    )

    (
        boundaries,
        town_column,
        lgc_source,
    ) = read_town_boundaries()

    print(f"入力CSV: {INPUT_CSV}")
    print(f"町丁字境界: {TOWN_BOUNDARY_GPKG}")
    print(f"レイヤ: {TOWN_BOUNDARY_LAYER}")
    print(f"境界地物数: {len(boundaries):,}")
    print(f"境界町名列: {town_column}")
    print(f"境界LGC生成: {lgc_source}")

    validate_lgc_overlap(
        records,
        boundaries,
    )

    municipality_names = read_lgc_master()
    alias_map = read_alias_table()

    town_index = build_town_index(
        boundaries
    )

    resolver = AddressResolver(
        municipality_names,
        town_index,
    )

    points, valid_coordinate_mask = (
        create_point_geodataframe(records)
    )

    print(
        f"有効座標: {len(points):,}／{len(records):,}"
    )

    joined = spatial_join_points(
        points,
        boundaries,
    )

    matches = build_spatial_matches(joined)

    check_result = check_records(
        records,
        valid_coordinate_mask,
        matches,
        resolver,
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
    print(f"問題出力: {len(check_result):,}件")
    print(f"出力先: {OUTPUT_CSV}")
    print(
        f"総処理時間: "
        f"{perf_counter() - total_started:.2f}秒"
    )

    if not check_result.empty:
        print()
        print("OverallCheck内訳")

        for name, count in (
            check_result["OverallCheck"]
            .value_counts(dropna=False)
            .items()
        ):
            print(f"  {name}: {count:,}件")

        print()
        print("LGCCheck内訳")

        for name, count in (
            check_result["LGCCheck"]
            .value_counts(dropna=False)
            .items()
        ):
            print(f"  {name}: {count:,}件")

        print()
        print("TownCheck内訳")

        for name, count in (
            check_result["TownCheck"]
            .value_counts(dropna=False)
            .items()
        ):
            print(f"  {name}: {count:,}件")


if __name__ == "__main__":
    main()
