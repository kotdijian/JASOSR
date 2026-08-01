#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
13Tokyo_total.csv 時代フラグ付与・自治体別／時代別分割スクリプト
============================================================

【処理内容】

1. 13Tokyo_total.csv をUTF-8で読み込む。
2. SiteName_furigana 列に「抹消」を含む行を削除する。
3. Chronology 列を参照して Pa～Un の時代フラグを付与する。
   ・判定は部分一致。
   ・Chronology が空欄、NULL相当、または空白文字のみの場合は Un=1。
   ・Chronology に「不明」を含む場合も Un=1。
4. 全処理結果を 13Tokyo_chronology.csv に保存する。
5. LGC_13Tokyo.csv の LGC・roman 列を参照し、LGCごとに分割する。
   ・roman は最初の半角スペースより前だけを使用する。
     例："Chuo Ward" → "Chuo"
   ・ファイル名：LGC+roman.csv（例：13102Chuo.csv）
   ・出力先：byMunicipality フォルダ
6. Pa～Un の各フラグが1の行を時代別に分割する。
   ・ファイル名：13Tokyo_Pa.csv、13Tokyo_Jo.csv、…
   ・出力先：byAge フォルダ
   ・該当行が0件のフラグについても、見出しのみのCSVを出力する。

【想定ファイル構成】

13Tokyo/
├─ 13Tokyo_Chronology.py
├─ 13Tokyo_total.csv
├─ LGC_13Tokyo.csv
├─ 13Tokyo_chronology.csv          ← 実行後に作成／更新
├─ byMunicipality/                 ← 既存フォルダ
│  ├─ 13101Chiyoda.csv
│  ├─ 13102Chuo.csv
│  └─ ...
└─ byAge/                          ← 既存フォルダ
   ├─ 13Tokyo_Pa.csv
   ├─ 13Tokyo_Jo.csv
   └─ ...

【必要なライブラリ】

    python -m pip install pandas

Homebrew版Pythonで externally-managed-environment エラーが出る場合：

    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install pandas

【実行】

macOS：

    cd "/Users/username/Documents/GitHub/JASOSR/13Tokyo"
    source .venv/bin/activate
    python 13Tokyo_Chronology.py

Windows：

    cd "C:/Users/username/Documents/GitHub/JASOSR/13Tokyo"
    .venv\\Scripts\\activate
    python 13Tokyo_Chronology.py

【文字コード】

入力・出力CSVはUTF-8を使用する。
元の 13Tokyo_total.csv は変更しない。

============================================================
"""

from pathlib import Path
import re

import pandas as pd


# ============================================================
# 入出力設定
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent

INPUT_CSV = SCRIPT_DIR / "13Tokyo_total.csv"
LGC_MASTER_CSV = SCRIPT_DIR / "LGC_13Tokyo.csv"
OUTPUT_CSV = SCRIPT_DIR / "13Tokyo_chronology.csv"

BY_MUNICIPALITY_DIR = SCRIPT_DIR / "byMunicipality"
BY_AGE_DIR = SCRIPT_DIR / "byAge"


# ============================================================
# 列名設定
# ============================================================

SOURCE_COLUMN = "Chronology"
FURIGANA_COLUMN = "SiteName_furigana"
LGC_COLUMN = "LGC"
ROMAN_COLUMN = "roman"


# ============================================================
# 時代フラグ設定
# ============================================================

FLAG_COLUMN_ORDER = [
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

# 空リストの区分は現在未使用で、通常は全行0となる。
# Unだけは別途、空欄／NULL相当も1にする。
FLAG_DEFINITIONS: dict[str, list[str]] = {
    "Pa": ["旧石器"],
    "Jo": ["縄文"],
    "Ya": ["弥生"],
    "Ko": ["古墳"],
    "As": [],
    "An": ["奈良", "平安"],
    "Na": ["奈良"],
    "He": ["平安"],
    "Me": ["中世"],
    "Ka": [],
    "Nb": [],
    "Mu": [],
    "Se": [],
    "EM": ["近世"],
    "AM": [],
    "Ed": [],
    "Md": ["近代"],
    "Pr": ["現代"],
    "Un": ["不明"],
}

FLAG_DESCRIPTIONS: dict[str, str] = {
    "Pa": "旧石器",
    "Jo": "縄文",
    "Ya": "弥生",
    "Ko": "古墳",
    "As": "飛鳥（未使用）",
    "An": "古代（奈良または平安）",
    "Na": "奈良",
    "He": "平安",
    "Me": "中世",
    "Ka": "鎌倉（未使用）",
    "Nb": "南北朝（未使用）",
    "Mu": "室町（未使用）",
    "Se": "戦国（未使用）",
    "EM": "近世",
    "AM": "安土桃山（未使用）",
    "Ed": "江戸（未使用）",
    "Md": "近代",
    "Pr": "現代",
    "Un": "不明または時代空欄",
}


# ============================================================
# 共通関数
# ============================================================


def normalize_lgc(series: pd.Series) -> pd.Series:
    """LGCを文字列として正規化し、末尾の .0 を除去する。"""
    return (
        series
        .fillna("")
        .astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def contains_any(series: pd.Series, keywords: list[str]) -> pd.Series:
    """各セルが指定キーワードのいずれかを含むか判定する。"""
    if not keywords:
        return pd.Series(False, index=series.index, dtype=bool)

    pattern = "|".join(re.escape(keyword) for keyword in keywords)

    return (
        series
        .fillna("")
        .astype(str)
        .str.contains(pattern, regex=True, na=False)
    )


def validate_required_columns(
    dataframe: pd.DataFrame,
    required_columns: list[str],
    filename: str,
) -> None:
    """必須列の存在を確認する。"""
    missing_columns = [
        column
        for column in required_columns
        if column not in dataframe.columns
    ]

    if missing_columns:
        raise KeyError(
            f"{filename} に必要な列がありません: "
            + ", ".join(missing_columns)
        )


def validate_definitions() -> None:
    """フラグ列順と定義辞書の整合性を確認する。"""
    ordered = set(FLAG_COLUMN_ORDER)
    defined = set(FLAG_DEFINITIONS)

    missing = ordered - defined
    extra = defined - ordered

    if missing:
        raise ValueError(
            "定義が存在しないフラグ列があります: "
            + ", ".join(sorted(missing))
        )

    if extra:
        raise ValueError(
            "FLAG_COLUMN_ORDERに存在しない定義があります: "
            + ", ".join(sorted(extra))
        )


def read_input_csv(path: Path) -> pd.DataFrame:
    """入力CSVをUTF-8で読み込む。"""
    if not path.is_file():
        raise FileNotFoundError(f"入力CSVが見つかりません: {path}")

    try:
        return pd.read_csv(
            path,
            dtype=str,
            encoding="utf-8",
            keep_default_na=False,
        )
    except UnicodeDecodeError as error:
        raise UnicodeError(
            f"UTF-8として読み込めませんでした: {path}"
        ) from error


def remove_deleted_records(dataframe: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """SiteName_furiganaに「抹消」を含む行を削除する。"""
    deleted_mask = (
        dataframe[FURIGANA_COLUMN]
        .fillna("")
        .astype(str)
        .str.contains("抹消", regex=False, na=False)
    )

    removed_count = int(deleted_mask.sum())
    result = dataframe.loc[~deleted_mask].copy()
    result.reset_index(drop=True, inplace=True)

    return result, removed_count


def add_chronology_flags(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Chronology列を参照し、Pa～Unを再計算する。"""
    result = dataframe.copy()

    chronology = (
        result[SOURCE_COLUMN]
        .fillna("")
        .astype(str)
    )

    for column in FLAG_COLUMN_ORDER:
        result[column] = (
            contains_any(chronology, FLAG_DEFINITIONS[column])
            .astype("int8")
        )

    # Chronologyが空欄・NULL相当・空白のみの場合にもUn=1とする。
    blank_chronology = chronology.str.strip().eq("")
    result["Un"] = (
        result["Un"].astype(bool) | blank_chronology
    ).astype("int8")

    return result


def move_flag_columns_to_end(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Pa～Unを指定順でCSV末尾へ配置する。"""
    non_flag_columns = [
        column
        for column in dataframe.columns
        if column not in FLAG_COLUMN_ORDER
    ]

    return dataframe[non_flag_columns + FLAG_COLUMN_ORDER]


def write_csv(dataframe: pd.DataFrame, output_path: Path) -> None:
    """DataFrameをUTF-8 CSVとして保存する。"""
    dataframe.to_csv(
        output_path,
        index=False,
        encoding="utf-8",
        lineterminator="\n",
    )


def extract_roman_prefix(value: object) -> str:
    """
    roman列の最初の半角スペースより前だけを返す。

    例：
        "Chuo Ward" → "Chuo"
        "Hachioji City" → "Hachioji"
    """
    text = str(value).strip()

    if not text:
        return ""

    return text.split(" ", maxsplit=1)[0]


def validate_filename_part(value: str, lgc: str) -> None:
    """roman由来の文字列がファイル名として安全か確認する。"""
    if not value:
        raise ValueError(
            f"LGC={lgc} の roman 列が空です。"
        )

    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError(
            f"LGC={lgc} の roman から得たファイル名要素が不正です: {value}"
        )


def load_lgc_master(path: Path) -> pd.DataFrame:
    """LGC_13Tokyo.csvを読み込み、分割出力用マスターを作る。"""
    master = read_input_csv(path)

    validate_required_columns(
        master,
        [LGC_COLUMN, ROMAN_COLUMN],
        path.name,
    )

    master = master.copy()
    master[LGC_COLUMN] = normalize_lgc(master[LGC_COLUMN])
    master["_roman_prefix"] = master[ROMAN_COLUMN].map(extract_roman_prefix)

    duplicated = master[LGC_COLUMN].duplicated(keep=False)
    if duplicated.any():
        values = sorted(master.loc[duplicated, LGC_COLUMN].unique())
        raise ValueError(
            "LGC_13Tokyo.csv に重複するLGCがあります: "
            + ", ".join(values)
        )

    return master


# ============================================================
# 分割出力
# ============================================================


def export_by_municipality(
    dataframe: pd.DataFrame,
    lgc_master: pd.DataFrame,
) -> tuple[int, int]:
    """
    LGCごとに分割し、byMunicipalityへ LGC+roman.csv として保存する。

    戻り値：
        (出力ファイル数, 出力レコード総数)
    """
    if not BY_MUNICIPALITY_DIR.is_dir():
        raise FileNotFoundError(
            f"出力フォルダが見つかりません: {BY_MUNICIPALITY_DIR}"
        )

    data = dataframe.copy()
    data[LGC_COLUMN] = normalize_lgc(data[LGC_COLUMN])

    master_lookup = lgc_master.set_index(LGC_COLUMN)["_roman_prefix"]

    data_lgcs = sorted(
        lgc for lgc in data[LGC_COLUMN].unique()
        if lgc
    )

    missing_lgcs = [
        lgc for lgc in data_lgcs
        if lgc not in master_lookup.index
    ]

    if missing_lgcs:
        raise ValueError(
            "LGC_13Tokyo.csvに対応するLGCがありません: "
            + ", ".join(missing_lgcs)
        )

    file_count = 0
    record_count = 0

    for lgc in data_lgcs:
        roman_prefix = str(master_lookup.loc[lgc])
        validate_filename_part(roman_prefix, lgc)

        subset = data.loc[data[LGC_COLUMN] == lgc].copy()
        output_path = BY_MUNICIPALITY_DIR / f"{lgc}{roman_prefix}.csv"

        write_csv(subset, output_path)

        file_count += 1
        record_count += len(subset)

    return file_count, record_count


def export_by_age(dataframe: pd.DataFrame) -> dict[str, int]:
    """Pa～Unの各フラグが1の行をbyAgeへ分割出力する。"""
    if not BY_AGE_DIR.is_dir():
        raise FileNotFoundError(
            f"出力フォルダが見つかりません: {BY_AGE_DIR}"
        )

    counts: dict[str, int] = {}

    for flag in FLAG_COLUMN_ORDER:
        flag_values = pd.to_numeric(
            dataframe[flag],
            errors="coerce",
        ).fillna(0)

        subset = dataframe.loc[flag_values.eq(1)].copy()
        output_path = BY_AGE_DIR / f"13Tokyo_{flag}.csv"

        # 0件の場合も列見出しを持つ空CSVを書き出す。
        write_csv(subset, output_path)
        counts[flag] = len(subset)

    return counts


# ============================================================
# 集計表示
# ============================================================


def print_flag_summary(dataframe: pd.DataFrame) -> None:
    """各フラグの該当件数を表示する。"""
    print()
    print("時代フラグ集計")
    print("-" * 58)

    for column in FLAG_COLUMN_ORDER:
        count = int(pd.to_numeric(dataframe[column]).sum())
        description = FLAG_DESCRIPTIONS.get(column, "")

        print(
            f"{column:>2}  "
            f"{description:<24} "
            f"{count:>7,}件"
        )


# ============================================================
# メイン処理
# ============================================================


def main() -> None:
    validate_definitions()

    print(f"入力CSV: {INPUT_CSV.resolve()}")
    print(f"LGCマスター: {LGC_MASTER_CSV.resolve()}")
    print(f"全体出力: {OUTPUT_CSV.resolve()}")
    print()

    dataframe = read_input_csv(INPUT_CSV)

    validate_required_columns(
        dataframe,
        [
            SOURCE_COLUMN,
            FURIGANA_COLUMN,
            LGC_COLUMN,
        ],
        INPUT_CSV.name,
    )

    input_count = len(dataframe)

    # 1. 「抹消」を含む行を削除
    filtered, removed_count = remove_deleted_records(dataframe)

    # 2. Pa～Unを再計算
    processed = add_chronology_flags(filtered)
    processed[LGC_COLUMN] = normalize_lgc(processed[LGC_COLUMN])
    processed = move_flag_columns_to_end(processed)

    # 3. 全体CSVを書き出し
    write_csv(processed, OUTPUT_CSV)

    # 4. LGCマスターを読み込み、自治体別に分割
    lgc_master = load_lgc_master(LGC_MASTER_CSV)
    municipality_file_count, municipality_record_count = (
        export_by_municipality(processed, lgc_master)
    )

    # 5. 時代別に分割
    age_counts = export_by_age(processed)

    # 6. 実行結果
    print("処理が完了しました。")
    print(f"入力レコード数: {input_count:,}件")
    print(f"『抹消』により削除: {removed_count:,}件")
    print(f"出力レコード数: {len(processed):,}件")
    print(f"全体CSV: {OUTPUT_CSV.resolve()}")
    print()
    print(
        "自治体別出力: "
        f"{municipality_file_count:,}ファイル／"
        f"{municipality_record_count:,}件"
    )
    print(f"出力先: {BY_MUNICIPALITY_DIR.resolve()}")
    print()
    print("時代別出力")
    for flag in FLAG_COLUMN_ORDER:
        print(
            f"  13Tokyo_{flag}.csv: "
            f"{age_counts[flag]:,}件"
        )
    print(f"出力先: {BY_AGE_DIR.resolve()}")

    print_flag_summary(processed)


if __name__ == "__main__":
    main()
