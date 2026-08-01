"""
============================================================
13Tokyo 時代・自治体別集計スクリプト
============================================================

【入力ファイル】

1. 13Tokyo_Chronology.csv

   必須列：
   LGC
   Pa, Jo, Ya, Ko, As, An, Na, He, Me,
   Ka, Nb, Mu, Se, EM, AM, Ed, Md, Pr, Un

2. LGC_13Tokyo.csv

   必須列：
   LGC
   Name


【出力ファイル】

1. 13Tokyo_SumChrono.csv

   Pa～Unの各フラグ列について、値が1のレコード数を集計する。

   出力例：

       Chronology,Count
       Pa,100
       Jo,250
       ...


2. 13Tokyo_SumMunic.csv

   LGCごとのレコード数を集計し、LGC_13Tokyo.csvのName列を付加する。

   出力列：

       LGC,Name,Count


3. 13Tokyo_SumChrMun.csv

   LGCごとにPa～Unの各フラグを集計し、
   LGC_13Tokyo.csvのName列を付加する。

   出力列：

       LGC,Name,Pa,Jo,Ya,...,Un


【実行方法】

pandasを仮想環境へインストールする。

    python -m pip install pandas

スクリプトを実行する。

macOS：

    python3 13Tokyo_Summary.py

Windows：

    python 13Tokyo_Summary.py


【文字コード】

入力CSVおよび出力CSVはUTF-8を使用する。

============================================================
"""

from pathlib import Path

import pandas as pd


# ============================================================
# 入出力パス
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent

# 必要に応じてファイル名・パスを変更する
CHRONOLOGY_CSV = (
    SCRIPT_DIR
    / "13Tokyo_Chronology.csv"
)

LGC_MASTER_CSV = (
    SCRIPT_DIR
    / "LGC_13Tokyo.csv"
)

OUTPUT_SUM_CHRONO = (
    CHRONOLOGY_CSV.parent
    / "13Tokyo_SumChrono.csv"
)

OUTPUT_SUM_MUNIC = (
    CHRONOLOGY_CSV.parent
    / "13Tokyo_SumMunic.csv"
)

OUTPUT_SUM_CHR_MUN = (
    CHRONOLOGY_CSV.parent
    / "13Tokyo_SumChrMun.csv"
)


# ============================================================
# 集計対象列
# ============================================================

CHRONOLOGY_COLUMNS = [
    "Pa",
    "Jo",
    "Ya",
    "Ko",
    "As",
    "An",
    "Na",
    "He",
    "Me",
    "Ka",
    "Nb",
    "Mu",
    "Se",
    "EM",
    "AM",
    "Ed",
    "Md",
    "Pr",
    "Un",
]


# ============================================================
# 関数
# ============================================================

def read_csv_utf8(
    path: Path,
    dtype: dict[str, str] | None = None,
) -> pd.DataFrame:
    """
    UTF-8のCSVを読み込む。
    """
    if not path.exists():
        raise FileNotFoundError(
            f"CSVファイルが見つかりません: {path}"
        )

    try:
        return pd.read_csv(
            path,
            encoding="utf-8",
            dtype=dtype,
            keep_default_na=False,
        )
    except UnicodeDecodeError as error:
        raise UnicodeError(
            f"UTF-8として読み込めませんでした: {path}"
        ) from error


def validate_columns(
    dataframe: pd.DataFrame,
    required_columns: list[str],
    filename: str,
) -> None:
    """
    必須列が存在するか確認する。
    """
    missing_columns = [
        column
        for column in required_columns
        if column not in dataframe.columns
    ]

    if missing_columns:
        raise KeyError(
            f"{filename}に必要な列がありません: "
            + ", ".join(missing_columns)
        )


def normalize_lgc(
    series: pd.Series,
) -> pd.Series:
    """
    LGCを文字列として統一する。

    CSV読込時に数値化された場合の末尾 .0 も除去する。
    """
    return (
        series
        .fillna("")
        .astype(str)
        .str.strip()
        .str.replace(
            r"\.0$",
            "",
            regex=True,
        )
    )


def normalize_flag_columns(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    Pa～Un列を数値化する。

    空欄や数値化できない値は0とする。
    1以外の正の値が存在しても、その値をそのまま集計する。
    """
    result = dataframe.copy()

    for column in CHRONOLOGY_COLUMNS:
        result[column] = (
            pd.to_numeric(
                result[column],
                errors="coerce",
            )
            .fillna(0)
            .astype("int64")
        )

    return result


def create_sum_chronology(
    chronology_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Pa～Un列の列合計を作成する。
    """
    totals = (
        chronology_df[CHRONOLOGY_COLUMNS]
        .sum(axis=0)
        .reindex(CHRONOLOGY_COLUMNS)
    )

    return pd.DataFrame({
        "Chronology": totals.index,
        "Count": totals.values.astype("int64"),
    })


def create_sum_municipality(
    chronology_df: pd.DataFrame,
    lgc_master_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    LGCごとのレコード数を集計し、Nameを付加する。
    """
    municipality_counts = (
        chronology_df
        .groupby(
            "LGC",
            dropna=False,
        )
        .size()
        .reset_index(name="Count")
    )

    result = lgc_master_df[
        ["LGC", "Name"]
    ].merge(
        municipality_counts,
        on="LGC",
        how="left",
        validate="one_to_one",
    )

    result["Count"] = (
        result["Count"]
        .fillna(0)
        .astype("int64")
    )

    return result[
        ["LGC", "Name", "Count"]
    ]


def create_sum_chronology_municipality(
    chronology_df: pd.DataFrame,
    lgc_master_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    LGC×Pa～Unのクロス集計を作成し、Nameを付加する。
    """
    chronology_by_municipality = (
        chronology_df
        .groupby(
            "LGC",
            dropna=False,
        )[CHRONOLOGY_COLUMNS]
        .sum()
        .reset_index()
    )

    result = lgc_master_df[
        ["LGC", "Name"]
    ].merge(
        chronology_by_municipality,
        on="LGC",
        how="left",
        validate="one_to_one",
    )

    result[CHRONOLOGY_COLUMNS] = (
        result[CHRONOLOGY_COLUMNS]
        .fillna(0)
        .astype("int64")
    )

    return result[
        ["LGC", "Name"] + CHRONOLOGY_COLUMNS
    ]


def write_csv(
    dataframe: pd.DataFrame,
    path: Path,
) -> None:
    """
    DataFrameをUTF-8のCSVとして保存する。
    """
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dataframe.to_csv(
        path,
        index=False,
        encoding="utf-8",
        lineterminator="\n",
    )


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    print(f"時代フラグCSV: {CHRONOLOGY_CSV.resolve()}")
    print(f"LGCマスター: {LGC_MASTER_CSV.resolve()}")

    chronology_df = read_csv_utf8(
        CHRONOLOGY_CSV,
        dtype={
            "LGC": str,
        },
    )

    lgc_master_df = read_csv_utf8(
        LGC_MASTER_CSV,
        dtype={
            "LGC": str,
            "Name": str,
        },
    )

    validate_columns(
        chronology_df,
        ["LGC"] + CHRONOLOGY_COLUMNS,
        CHRONOLOGY_CSV.name,
    )

    validate_columns(
        lgc_master_df,
        ["LGC", "Name"],
        LGC_MASTER_CSV.name,
    )

    chronology_df["LGC"] = normalize_lgc(
        chronology_df["LGC"]
    )

    lgc_master_df["LGC"] = normalize_lgc(
        lgc_master_df["LGC"]
    )

    # LGCマスターの重複確認
    duplicated_lgc = (
        lgc_master_df["LGC"]
        .duplicated(keep=False)
    )

    if duplicated_lgc.any():
        duplicate_values = (
            lgc_master_df.loc[
                duplicated_lgc,
                "LGC",
            ]
            .unique()
            .tolist()
        )

        raise ValueError(
            "LGC_13Tokyo.csvに重複するLGCがあります: "
            + ", ".join(duplicate_values)
        )

    chronology_df = normalize_flag_columns(
        chronology_df
    )

    # --------------------------------------------------------
    # 1. 時代別集計
    # --------------------------------------------------------

    sum_chronology = create_sum_chronology(
        chronology_df
    )

    write_csv(
        sum_chronology,
        OUTPUT_SUM_CHRONO,
    )

    # --------------------------------------------------------
    # 2. 自治体別レコード数
    # --------------------------------------------------------

    sum_municipality = create_sum_municipality(
        chronology_df,
        lgc_master_df,
    )

    write_csv(
        sum_municipality,
        OUTPUT_SUM_MUNIC,
    )

    # --------------------------------------------------------
    # 3. 自治体×時代クロス集計
    # --------------------------------------------------------

    sum_chronology_municipality = (
        create_sum_chronology_municipality(
            chronology_df,
            lgc_master_df,
        )
    )

    write_csv(
        sum_chronology_municipality,
        OUTPUT_SUM_CHR_MUN,
    )

    # --------------------------------------------------------
    # 結果表示
    # --------------------------------------------------------

    print()
    print("集計が完了しました。")
    print(
        f"時代別集計: "
        f"{OUTPUT_SUM_CHRONO.resolve()}"
    )
    print(
        f"自治体別集計: "
        f"{OUTPUT_SUM_MUNIC.resolve()}"
    )
    print(
        f"自治体×時代集計: "
        f"{OUTPUT_SUM_CHR_MUN.resolve()}"
    )

    print()
    print(f"入力レコード数: {len(chronology_df):,}件")
    print(
        f"LGCマスター件数: "
        f"{len(lgc_master_df):,}件"
    )


# ============================================================
# 実行
# ============================================================

if __name__ == "__main__":
    main()
