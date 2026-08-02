"""
============================================================
東京都遺跡位置・町丁目境界チェック
============================================================

【概要】

13Tokyo_total.csv の Address 列から町丁目名を抽出し、
TownBoundary.gpkg の町丁目ポリゴンと照合する。

CSVの Lat・Lon から作成したポイントが、Addressに対応する
町丁目ポリゴンの範囲内にあるかを確認する。

境界範囲内のレコードは出力しない。
次のレコードだけを 13Tokyo_CheckAddress.csv に出力する。

1. ポイントが対応する町丁目ポリゴンの外にあるレコード
2. Addressから対応する町丁目ポリゴンを特定できなかったレコード
3. Lat・Lonが欠損または不正なレコード


【想定ディレクトリ】

JASOSR/
├─ 13Tokyo/
│  ├─ 13Tokyo_total.csv
│  ├─ 13Tokyo_CheckAddress.py
│  └─ 13Tokyo_CheckAddress.csv
│
└─ 00General/
   └─ TownBoundary.gpkg
      └─ 13Tokyo レイヤ


【必要なライブラリ】

Homebrew版Pythonでは仮想環境を使用する。

初回のみ：

    cd "/Users/noguchiatsushi/Documents/GitHub/JASOSR/13Tokyo"

    python3 -m venv .venv

    source .venv/bin/activate

    python -m pip install pandas geopandas pyogrio shapely

2回目以降：

    cd "/Users/noguchiatsushi/Documents/GitHub/JASOSR/13Tokyo"

    source .venv/bin/activate


【実行】

    python 13Tokyo_CheckAddress.py


【入力CSVの必須列】

    Address
    LGC
    Lat
    Lon


【出力】

    13Tokyo_CheckAddress.csv

元の13Tokyo_total.csvは変更しない。


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
from shapely.geometry import Point


# ============================================================
# 入出力パス
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_DIR = SCRIPT_DIR.parent

INPUT_CSV = (
    SCRIPT_DIR
    / "13Tokyo_total.csv"
)

TOWN_BOUNDARY_GPKG = (
    REPOSITORY_DIR
    / "00General"
    / "TownBoundary.gpkg"
)

TOWN_BOUNDARY_LAYER = "13Tokyo"

OUTPUT_CSV = (
    SCRIPT_DIR
    / "13Tokyo_CheckAddress.csv"
)


# ============================================================
# CSV列名
# ============================================================

ADDRESS_COLUMN = "Address"
LGC_COLUMN = "LGC"
LAT_COLUMN = "Lat"
LON_COLUMN = "Lon"


# ============================================================
# 町丁目ポリゴン属性設定
# ============================================================

# GeoPackageの実際の列名が分かっている場合は、
# 自動検出ではなく、ここへ直接指定できる。
#
# 例：
# BOUNDARY_LGC_COLUMN = "CITY"
# BOUNDARY_TOWN_COLUMN = "S_NAME"

BOUNDARY_LGC_COLUMN: str | None = None
BOUNDARY_TOWN_COLUMN: str | None = None


# 自動検出する自治体コード列の候補
BOUNDARY_LGC_COLUMN_CANDIDATES = [
    "LGC",
    "CITY",
    "CITY_CODE",
    "CITYCODE",
    "MUNICIPALITY_CODE",
    "N03_007",
    "KEY_CODE",
]

# 自動検出する町丁目名列の候補
BOUNDARY_TOWN_COLUMN_CANDIDATES = [
    "Town",
    "TOWN",
    "TownName",
    "TOWN_NAME",
    "S_NAME",
    "MOJI",
    "NAME",
    "町丁目名",
    "町丁字名",
]


# ============================================================
# 動作設定
# ============================================================

# 町丁目境界を特定できなかったレコードも確認用CSVへ出力する。
INCLUDE_UNMATCHED_ADDRESS = True

# 座標が欠損・不正なレコードも確認用CSVへ出力する。
INCLUDE_INVALID_COORDINATES = True

# 出力CSVの文字コード
OUTPUT_ENCODING = "utf-8"


# ============================================================
# 正規化関数
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


def normalize_text(value: object) -> str:
    """
    住所・町丁目名の比較用文字列を作成する。

    ・全角／半角を統一
    ・空白、改行、読点等を除去
    ・丁目に使われる漢数字を算用数字へ変換
    """
    if value is None:
        return ""

    text = str(value).strip()

    if not text:
        return ""

    text = unicodedata.normalize(
        "NFKC",
        text,
    )

    text = text.translate(
        KANJI_DIGIT_MAP
    )

    text = re.sub(
        r"[\s\u3000\r\n\t、,・;/／]+",
        "",
        text,
    )

    return text


def normalize_lgc_value(
    value: object,
) -> str:
    """
    自治体コードを5桁文字列へ正規化する。

    KEY_CODE等の長いコードの場合も先頭5桁を使用する。
    """
    if value is None:
        return ""

    text = str(value).strip()

    text = re.sub(
        r"\.0$",
        "",
        text,
    )

    digits = re.sub(
        r"\D",
        "",
        text,
    )

    if len(digits) >= 5:
        return digits[:5]

    return digits.zfill(5) if digits else ""


# ============================================================
# 列検出
# ============================================================

def detect_column(
    columns: pd.Index,
    candidates: list[str],
    description: str,
) -> str:
    """
    候補リストから実際に存在する列名を検出する。
    """
    column_lookup = {
        str(column).lower(): str(column)
        for column in columns
    }

    for candidate in candidates:
        actual = column_lookup.get(
            candidate.lower()
        )

        if actual is not None:
            return actual

    raise KeyError(
        f"{description}を表す列を特定できませんでした。\n"
        f"現在の列: {', '.join(map(str, columns))}\n"
        f"候補列: {', '.join(candidates)}"
    )


# ============================================================
# 入力確認
# ============================================================

def validate_input_files() -> None:
    """
    入力ファイルが存在するか確認する。
    """
    if not INPUT_CSV.exists():
        raise FileNotFoundError(
            "入力CSVが見つかりません。\n"
            f"{INPUT_CSV}"
        )

    if not TOWN_BOUNDARY_GPKG.exists():
        raise FileNotFoundError(
            "町丁目GeoPackageが見つかりません。\n"
            f"{TOWN_BOUNDARY_GPKG}"
        )


def validate_csv_columns(
    dataframe: pd.DataFrame,
) -> None:
    """
    CSVの必須列を確認する。
    """
    required_columns = [
        ADDRESS_COLUMN,
        LGC_COLUMN,
        LAT_COLUMN,
        LON_COLUMN,
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in dataframe.columns
    ]

    if missing_columns:
        raise KeyError(
            "13Tokyo_total.csvに必要な列がありません: "
            + ", ".join(missing_columns)
        )


# ============================================================
# 町丁目データ読み込み
# ============================================================

def read_town_boundaries() -> tuple[
    gpd.GeoDataFrame,
    str,
    str,
]:
    """
    GeoPackageから東京都町丁目レイヤを読み込む。
    """
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
            "町丁目ポリゴンにCRSが設定されていません。"
        )

    if BOUNDARY_LGC_COLUMN is None:
        boundary_lgc_column = detect_column(
            boundaries.columns,
            BOUNDARY_LGC_COLUMN_CANDIDATES,
            "自治体コード",
        )
    else:
        boundary_lgc_column = (
            BOUNDARY_LGC_COLUMN
        )

    if BOUNDARY_TOWN_COLUMN is None:
        boundary_town_column = detect_column(
            boundaries.columns,
            BOUNDARY_TOWN_COLUMN_CANDIDATES,
            "町丁目名",
        )
    else:
        boundary_town_column = (
            BOUNDARY_TOWN_COLUMN
        )

    if (
        boundary_lgc_column
        not in boundaries.columns
    ):
        raise KeyError(
            "指定された自治体コード列がありません: "
            f"{boundary_lgc_column}"
        )

    if (
        boundary_town_column
        not in boundaries.columns
    ):
        raise KeyError(
            "指定された町丁目名列がありません: "
            f"{boundary_town_column}"
        )

    # CSVの緯度経度と同じ座標系へ統一
    boundaries = boundaries.to_crs(
        "EPSG:4326"
    )

    boundaries = boundaries.loc[
        boundaries.geometry.notna()
    ].copy()

    boundaries = boundaries.loc[
        ~boundaries.geometry.is_empty
    ].copy()

    boundaries["_LGC_NORMALIZED"] = (
        boundaries[boundary_lgc_column]
        .map(normalize_lgc_value)
    )

    boundaries["_TOWN_NORMALIZED"] = (
        boundaries[boundary_town_column]
        .map(normalize_text)
    )

    # 町丁目名またはLGCが空の地物は照合対象外
    boundaries = boundaries.loc[
        (
            boundaries["_LGC_NORMALIZED"]
            != ""
        )
        & (
            boundaries["_TOWN_NORMALIZED"]
            != ""
        )
    ].copy()

    return (
        boundaries,
        boundary_lgc_column,
        boundary_town_column,
    )


# ============================================================
# 住所と町丁目ポリゴンの照合
# ============================================================

def find_candidate_boundaries(
    address: str,
    lgc: str,
    boundaries_by_lgc: dict[
        str,
        gpd.GeoDataFrame,
    ],
) -> gpd.GeoDataFrame | None:
    """
    Addressに町丁目名が含まれる境界ポリゴンを取得する。

    同一LGC内で検索するため、別自治体に同名町丁目が
    存在しても混同しない。

    Addressに複数の町丁目名が含まれる場合は、
    複数のポリゴンが候補として返る。
    """
    municipality_boundaries = (
        boundaries_by_lgc.get(lgc)
    )

    if municipality_boundaries is None:
        return None

    normalized_address = normalize_text(
        address
    )

    if not normalized_address:
        return None

    matched_mask = (
        municipality_boundaries[
            "_TOWN_NORMALIZED"
        ]
        .map(
            lambda town_name:
                bool(town_name)
                and town_name
                in normalized_address
        )
    )

    matched = municipality_boundaries.loc[
        matched_mask
    ]

    if matched.empty:
        return None

    return matched


def point_is_covered(
    point: Point,
    candidate_boundaries: gpd.GeoDataFrame,
) -> bool:
    """
    ポイントが候補町丁目ポリゴンのいずれかに含まれるか判定する。

    covers()を使用するため、ポリゴン境界線上のポイントも
    範囲内として扱う。
    """
    return bool(
        candidate_boundaries.geometry
        .map(
            lambda geometry:
                geometry.covers(point)
        )
        .any()
    )


# ============================================================
# メインチェック処理
# ============================================================

def check_addresses(
    records: pd.DataFrame,
    boundaries: gpd.GeoDataFrame,
    boundary_town_column: str,
) -> pd.DataFrame:
    """
    全レコードを確認し、範囲外または確認不能のものだけ返す。
    """
    boundaries_by_lgc = {
        lgc: group.copy()
        for lgc, group in boundaries.groupby(
            "_LGC_NORMALIZED"
        )
    }

    output_records: list[dict[str, object]] = []

    total = len(records)

    for sequence, (
        original_index,
        row,
    ) in enumerate(
        records.iterrows(),
        start=1,
    ):
        if (
            sequence == 1
            or sequence % 500 == 0
            or sequence == total
        ):
            print(
                f"位置確認中: "
                f"{sequence:,}／{total:,}"
            )

        address = str(
            row.get(
                ADDRESS_COLUMN,
                "",
            )
        ).strip()

        lgc = normalize_lgc_value(
            row.get(
                LGC_COLUMN,
                "",
            )
        )

        latitude = pd.to_numeric(
            row.get(
                LAT_COLUMN,
                None,
            ),
            errors="coerce",
        )

        longitude = pd.to_numeric(
            row.get(
                LON_COLUMN,
                None,
            ),
            errors="coerce",
        )

        result_status = ""
        matched_towns = ""
        candidate_count = 0

        # ----------------------------------------------------
        # 座標チェック
        # ----------------------------------------------------

        if (
            pd.isna(latitude)
            or pd.isna(longitude)
            or not (
                -90 <= float(latitude) <= 90
            )
            or not (
                -180 <= float(longitude) <= 180
            )
        ):
            result_status = "座標不正"

            if not INCLUDE_INVALID_COORDINATES:
                continue

        else:
            point = Point(
                float(longitude),
                float(latitude),
            )

            candidates = find_candidate_boundaries(
                address,
                lgc,
                boundaries_by_lgc,
            )

            # ------------------------------------------------
            # 町丁目境界を特定できない
            # ------------------------------------------------

            if candidates is None:
                result_status = (
                    "町丁目境界未特定"
                )

                if not INCLUDE_UNMATCHED_ADDRESS:
                    continue

            else:
                candidate_count = len(
                    candidates
                )

                matched_towns = "｜".join(
                    candidates[
                        boundary_town_column
                    ]
                    .fillna("")
                    .astype(str)
                    .drop_duplicates()
                    .tolist()
                )

                # --------------------------------------------
                # ポイント包含判定
                # --------------------------------------------

                if point_is_covered(
                    point,
                    candidates,
                ):
                    # 範囲内なので出力しない
                    continue

                result_status = (
                    "町丁目境界範囲外"
                )

        # ----------------------------------------------------
        # 元CSVの全列に確認結果を追加
        # ----------------------------------------------------

        output_row = row.to_dict()

        output_row.update({
            "CheckResult": result_status,
            "MatchedTown": matched_towns,
            "CandidatePolygonCount": (
                candidate_count
            ),
            "NormalizedLGC": lgc,
            "SourceRowNumber": (
                int(original_index) + 2
            ),
        })

        output_records.append(
            output_row
        )

    if not output_records:
        return pd.DataFrame(
            columns=(
                list(records.columns)
                + [
                    "CheckResult",
                    "MatchedTown",
                    "CandidatePolygonCount",
                    "NormalizedLGC",
                    "SourceRowNumber",
                ]
            )
        )

    return pd.DataFrame(
        output_records
    )


# ============================================================
# 実行
# ============================================================

def main() -> None:
    validate_input_files()

    print(
        f"入力CSV: {INPUT_CSV}"
    )

    print(
        f"町丁目境界: {TOWN_BOUNDARY_GPKG}"
    )

    print(
        f"レイヤ: {TOWN_BOUNDARY_LAYER}"
    )

    records = pd.read_csv(
        INPUT_CSV,
        encoding="utf-8",
        dtype={
            LGC_COLUMN: str,
        },
        keep_default_na=False,
    )

    validate_csv_columns(
        records
    )

    (
        boundaries,
        boundary_lgc_column,
        boundary_town_column,
    ) = read_town_boundaries()

    print()
    print(
        f"町丁目ポリゴン数: "
        f"{len(boundaries):,}件"
    )

    print(
        f"自治体コード列: "
        f"{boundary_lgc_column}"
    )

    print(
        f"町丁目名列: "
        f"{boundary_town_column}"
    )

    print()

    check_result = check_addresses(
        records,
        boundaries,
        boundary_town_column,
    )

    check_result.to_csv(
        OUTPUT_CSV,
        index=False,
        encoding=OUTPUT_ENCODING,
        lineterminator="\n",
    )

    print()
    print("位置確認が完了しました。")

    print(
        f"入力件数: "
        f"{len(records):,}件"
    )

    print(
        f"確認対象として出力: "
        f"{len(check_result):,}件"
    )

    print(
        f"出力先: {OUTPUT_CSV}"
    )

    if not check_result.empty:
        print()
        print("確認結果内訳")

        summary = (
            check_result[
                "CheckResult"
            ]
            .value_counts(
                dropna=False
            )
        )

        for result_name, count in (
            summary.items()
        ):
            print(
                f"  {result_name}: "
                f"{count:,}件"
            )


if __name__ == "__main__":
    main()
