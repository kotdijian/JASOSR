#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
13Tokyo_total.csv 時代フラグ付与スクリプト
============================================================

【概要】

CSVの Chronology 列を参照し、時代区分を示す0/1フラグ列を
追加して、新しいCSVとして保存する。

判定は完全一致ではなく部分一致で行う。
Chronologyセルに複数の時代が記載されている場合は、
該当するすべてのフラグ列に1を設定する。

例：

    Chronology = "縄文時代、弥生時代"

の場合：

    Jo = 1
    Ya = 1

となる。


【追加する列】

Pa : 旧石器
Jo : 縄文
Ya : 弥生
Ko : 古墳
As : 飛鳥（現在は未使用）
An : 古代（奈良または平安）
Na : 奈良
He : 平安
Me : 中世
Ka : 鎌倉（現在は未使用）
Nb : 南北朝（現在は未使用）
Mu : 室町（現在は未使用）
Se : 戦国（現在は未使用）
EM : 近世
AM : 安土桃山（現在は未使用）
Ed : 江戸（現在は未使用）
Md : 近代
Pr : 現代
Un : 不明


【実行環境】

Python 3.10以上を推奨する。

pandasがインストールされていない場合は、ターミナルで以下を実行する。

    python -m pip install pandas


【【実行方法】

1. 下記の INPUT_CSV を、実際のファイル配置に合わせて設定する。

2. ターミナル（WindowsではコマンドプロンプトまたはPowerShell）を開く。

3. このPythonファイルが保存されているフォルダへ移動する。

【macOS の例】

Pythonファイルが

    /Users/username/Documents/GitHub/MyRepository/scripts/

にある場合

    cd "/Users/username/Documents/GitHub/MyRepository/scripts"
    
【Homebrew版Pythonを利用している場合】

HomebrewでインストールしたPythonでは、
PEP 668（Externally Managed Environment）により、
システム環境へ直接 pip install を実行できない場合がある。

その場合は、このスクリプト用の仮想環境を作成して利用する。

初回のみ

    python3 -m venv .venv

仮想環境を有効化

    source .venv/bin/activate

必要なライブラリをインストール

    python -m pip install pandas

スクリプトを実行

    python 13Tokyo_Chronology.py

作業終了後

    deactivate

仮想環境（.venv）は一度作成すればよく、
以後は「有効化 → スクリプト実行」のみで利用できる。

GitHubリポジトリで管理する場合は、
.venv フォルダはコミットせず、
.gitignore に追加することを推奨する。

    .venv/

【Windows の例（コマンドプロンプト・PowerShell共通）】

Pythonファイルが

    C:/Users/username/Documents/GitHub/MyRepository/scripts/

にある場合

    cd "C:/Users/username/Documents/GitHub/MyRepository/scripts"

4. 次のコマンドを実行する。

macOS

    python3 13Tokyo_Chronology.py

Windows

    python 13Tokyo_Chronology.py

（環境によっては Windows でも python3 を使用する場合がある。）
【パス指定例】

PythonファイルとCSVが同じフォルダの場合：

    INPUT_CSV = Path("13Tokyo_total.csv")

Pythonファイルが scripts フォルダ、CSVが 13Tokyo フォルダに
ある場合：

    INPUT_CSV = Path("../13Tokyo/13Tokyo_total.csv")

絶対パスを使う場合：

    INPUT_CSV = Path(
        "/Users/username/project/13Tokyo/13Tokyo_total.csv"
    )


【文字コード】

入力CSVおよび出力CSVはUTF-8を使用する。


【出力】

既定では次のファイルを生成する。

    13Tokyo_chronology.csv

元のCSVは上書きしない。

元ファイルを直接更新する場合は、INPUT_CSVとOUTPUT_CSVに
同じパスを指定できる。ただし、最初は別名出力で内容を確認する
ことを推奨する。

============================================================
"""

from pathlib import Path
import re

import pandas as pd


# ============================================================
# 入出力ファイル
# ============================================================

# このPythonファイルの場所を基準にしたディレクトリ
SCRIPT_DIR = Path(__file__).resolve().parent

# 必要に応じてパスを変更する。
#
# 例：
# INPUT_CSV = SCRIPT_DIR / "13Tokyo_total.csv"
#
# Pythonファイルが scripts/、CSVが 13Tokyo/ にある場合：
# INPUT_CSV = SCRIPT_DIR.parent / "13Tokyo" / "13Tokyo_total.csv"

INPUT_CSV = SCRIPT_DIR / "13Tokyo_total.csv"

OUTPUT_CSV = SCRIPT_DIR / "13Tokyo_chronology.csv"


# ============================================================
# 基本設定
# ============================================================

SOURCE_COLUMN = "Chronology"

# 最終的なフラグ列の並び順
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


# ============================================================
# 時代フラグ定義
# ============================================================

# 各列について、Chronologyにいずれかの文字列が含まれていれば1にする。
#
# 空のリストは現在未使用の区分を表し、全レコードを0にする。
#
# Anは「奈良」または「平安」を含む場合に1となる。
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


# 説明表示用
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
    "Un": "不明",
}


# ============================================================
# 関数
# ============================================================

def contains_any(
    series: pd.Series,
    keywords: list[str],
) -> pd.Series:
    """
    各セルに、指定されたキーワードのいずれかが含まれるかを判定する。

    Parameters
    ----------
    series
        判定対象のpandas Series。

    keywords
        部分一致で検索する文字列のリスト。

    Returns
    -------
    pandas.Series
        該当する場合はTrue、該当しない場合はFalse。

    Notes
    -----
    keywordsが空の場合は、全行Falseを返す。
    キーワードは正規表現ではなく、通常の文字列として扱う。
    """
    if not keywords:
        return pd.Series(
            False,
            index=series.index,
            dtype=bool,
        )

    escaped_keywords = [
        re.escape(keyword)
        for keyword in keywords
    ]

    pattern = "|".join(escaped_keywords)

    return (
        series
        .fillna("")
        .astype(str)
        .str.contains(
            pattern,
            regex=True,
            na=False,
        )
    )


def validate_definitions() -> None:
    """
    フラグ列の並び順と定義辞書の整合性を確認する。
    """
    ordered_columns = set(FLAG_COLUMN_ORDER)
    defined_columns = set(FLAG_DEFINITIONS)

    missing_definitions = (
        ordered_columns - defined_columns
    )

    extra_definitions = (
        defined_columns - ordered_columns
    )

    if missing_definitions:
        raise ValueError(
            "定義が存在しないフラグ列があります: "
            + ", ".join(
                sorted(missing_definitions)
            )
        )

    if extra_definitions:
        raise ValueError(
            "FLAG_COLUMN_ORDERに存在しない定義があります: "
            + ", ".join(
                sorted(extra_definitions)
            )
        )


def read_input_csv(
    input_path: Path,
) -> pd.DataFrame:
    """
    入力CSVをUTF-8で読み込む。
    """
    if not input_path.exists():
        raise FileNotFoundError(
            "入力CSVが見つかりません。\n"
            f"指定されたパス: {input_path}"
        )

    if not input_path.is_file():
        raise FileNotFoundError(
            "入力パスがファイルではありません。\n"
            f"指定されたパス: {input_path}"
        )

    try:
        dataframe = pd.read_csv(
            input_path,
            dtype=str,
            encoding="utf-8",
            keep_default_na=False,
        )
    except UnicodeDecodeError as error:
        raise UnicodeError(
            "入力CSVをUTF-8として読み込めませんでした。\n"
            "CSVの文字コードをUTF-8に変換してください。"
        ) from error

    if SOURCE_COLUMN not in dataframe.columns:
        raise KeyError(
            f"{SOURCE_COLUMN}列が存在しません。\n"
            "現在の列名: "
            + ", ".join(
                map(str, dataframe.columns)
            )
        )

    return dataframe


def add_chronology_flags(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    Chronology列を参照して時代フラグ列を追加する。

    既存の同名フラグ列がある場合は、新しい判定結果で上書きする。
    """
    result = dataframe.copy()

    chronology = (
        result[SOURCE_COLUMN]
        .fillna("")
        .astype(str)
    )

    for column in FLAG_COLUMN_ORDER:
        keywords = FLAG_DEFINITIONS[column]

        result[column] = (
            contains_any(
                chronology,
                keywords,
            )
            .astype("int8")
        )

    return result


def move_flag_columns_to_end(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    フラグ列を指定順でCSV末尾に配置する。

    入力CSVに既存のフラグ列が含まれていても重複させない。
    """
    non_flag_columns = [
        column
        for column in dataframe.columns
        if column not in FLAG_COLUMN_ORDER
    ]

    final_columns = (
        non_flag_columns
        + FLAG_COLUMN_ORDER
    )

    return dataframe[final_columns]


def write_output_csv(
    dataframe: pd.DataFrame,
    output_path: Path,
) -> None:
    """
    処理済みCSVをUTF-8で保存する。
    """
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dataframe.to_csv(
        output_path,
        index=False,
        encoding="utf-8",
        lineterminator="\n",
    )


def print_summary(
    dataframe: pd.DataFrame,
) -> None:
    """
    各フラグの該当件数を表示する。
    """
    print()
    print("時代フラグ集計")
    print("-" * 50)

    for column in FLAG_COLUMN_ORDER:
        count = int(
            dataframe[column].sum()
        )

        description = FLAG_DESCRIPTIONS.get(
            column,
            "",
        )

        print(
            f"{column:>2}  "
            f"{description:<18} "
            f"{count:>6,}件"
        )


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    """
    CSVの読込、フラグ生成、保存を実行する。
    """
    validate_definitions()

    input_path = INPUT_CSV.resolve()
    output_path = OUTPUT_CSV.resolve()

    print(
        f"入力CSV: {input_path}"
    )

    print(
        f"出力CSV: {output_path}"
    )

    dataframe = read_input_csv(
        input_path
    )

    processed = add_chronology_flags(
        dataframe
    )

    processed = move_flag_columns_to_end(
        processed
    )

    write_output_csv(
        processed,
        output_path,
    )

    print()
    print(
        f"保存しました: {output_path}"
    )

    print(
        f"レコード数: {len(processed):,}件"
    )

    print_summary(processed)


# ============================================================
# 実行
# ============================================================

if __name__ == "__main__":
    main()
