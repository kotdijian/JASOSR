"""
============================================================
東京都遺跡地図 API 自治体別件数照合スクリプト
============================================================

pandas, requestsを使用
python -m pip install pandas requests

【概要】

1. LGC_13Tokyo.csv の Name 列にある区市町村名を順番に使い、
   東京都遺跡地図の検索APIへPOSTリクエストを送信する。

2. APIから返されるJSON配列の要素数を、各区市町村の
   検索ヒット数として集計する。

3. 13Tokyo_SumMunic.csv の自治体別集計値と比較する。

4. 次の列を持つCSVを出力する。

    LGC
    Name
    API_Count
    Difference

   Differenceは次の式で算出する。

    Difference = API_Count - Count

【集計行の扱い】

LGC_13Tokyo.csvには、通常の自治体以外に次の集計行がある。

    区部
    多摩地区
    島嶼部
    東京都計

これらはAPI検索を行わず、実自治体のAPI検索件数を合計して
算出する。

【入力ファイル】

    LGC_13Tokyo.csv

必須列：

    LGC
    Name

    13Tokyo_SumMunic.csv

必須列：

    LGC
    Name
    Count

【出力ファイル】

    13Tokyo_SumCheck.csv

【必要なライブラリ】

仮想環境を有効にしてから、次を実行する。

    python -m pip install pandas requests

【実行方法：macOS】

Pythonファイルのあるフォルダへ移動する例：

    cd "/Users/noguchiatsushi/Documents/GitHub/JASOSR/13Tokyo"

仮想環境を有効化する：

    source .venv/bin/activate

実行する：

    python 13Tokyo_SumCheck.py

【実行方法：Windows】

Pythonファイルのあるフォルダへ移動する例：

    cd "C:/Users/username/Documents/GitHub/JASOSR/13Tokyo"

仮想環境を有効化する例：

    .venv\\Scripts\\activate

実行する：

    python 13Tokyo_SumCheck.py

【文字コード】

入力CSVおよび出力CSVはUTF-8を使用する。

【注意】

東京都遺跡地図のサーバーへ短時間に多数のリクエストを
集中させないよう、各検索の間に待機時間を設けている。

============================================================
"""

from pathlib import Path
import time
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# 入出力ファイル
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent

LGC_CSV = (
    SCRIPT_DIR
    / "LGC_13Tokyo.csv"
)

SUM_MUNIC_CSV = (
    SCRIPT_DIR
    / "13Tokyo_SumMunic.csv"
)

OUTPUT_CSV = (
    SCRIPT_DIR
    / "13Tokyo_SumCheck.csv"
)


# ============================================================
# API設定
# ============================================================

API_URL = (
    "https://tokyo-iseki.metro.tokyo.lg.jp/json2.php"
)

# サーバー負荷を避けるための検索間隔（秒）
REQUEST_INTERVAL_SECONDS = 1.0

# 1回の通信のタイムアウト（秒）
REQUEST_TIMEOUT_SECONDS = 30

# 一時的な通信エラー時の再試行回数
MAX_RETRIES = 3


# ============================================================
# 列名設定
# ============================================================

LGC_COLUMN = "LGC"
NAME_COLUMN = "Name"

# 13Tokyo_SumMunic.csvにある既存集計値の列
SOURCE_COUNT_COLUMN = "Count"

# 出力列
API_COUNT_COLUMN = "API_Count"
DIFFERENCE_COLUMN = "Difference"


# ============================================================
# 集計行の定義
# ============================================================

AGGREGATE_NAMES = {
    "区部",
    "多摩地区",
    "島嶼部",
    "東京都計",
}


# ============================================================
# 共通関数
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
            f"入力CSVが見つかりません: {path}"
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

    数値として読み込まれた場合の末尾 '.0' も除去する。
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


def create_http_session() -> requests.Session:
    """
    再試行設定を持つHTTPセッションを作成する。
    """
    retry = Retry(
        total=MAX_RETRIES,
        connect=MAX_RETRIES,
        read=MAX_RETRIES,
        status=MAX_RETRIES,
        backoff_factor=1.0,
        status_forcelist=[
            429,
            500,
            502,
            503,
            504,
        ],
        allowed_methods=[
            "POST",
        ],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry
    )

    session = requests.Session()

    session.mount(
        "https://",
        adapter,
    )

    session.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": (
            "https://tokyo-iseki.metro.tokyo.lg.jp/"
            "map.html#main"
        ),
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; archaeological-site-count-check/1.0)"
        ),
    })

    return session


# ============================================================
# API検索
# ============================================================

def search_municipality(
    session: requests.Session,
    municipality_name: str,
) -> list[dict[str, Any]]:
    """
    指定した区市町村名で東京都遺跡地図APIを検索する。

    APIのレスポンスはJSON配列を想定する。
    """
    payload = {
        "rd_syurui": "遺跡",
        "txtname": "",
        "lst_kushityouson": municipality_name,
        "txttyotyome": "",
        "txtisekino": "",
        "txtsyubetsu": "",
        "txtjidai": "",
    }

    response = session.post(
        API_URL,
        data=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    try:
        data = response.json()

    except requests.JSONDecodeError as error:
        preview = response.text[:500]

        raise ValueError(
            f"{municipality_name}のレスポンスをJSONとして"
            "解析できませんでした。\n"
            f"レスポンス先頭: {preview}"
        ) from error

    if not isinstance(data, list):
        raise TypeError(
            f"{municipality_name}のレスポンスが"
            "JSON配列ではありません。"
        )

    return data


def collect_api_counts(
    lgc_dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    各実自治体をAPI検索し、ヒット数を取得する。

    区部、多摩地区、島嶼部、東京都計はここでは検索しない。
    """
    session = create_http_session()

    results: list[dict[str, object]] = []

    search_targets = lgc_dataframe.loc[
        ~lgc_dataframe[NAME_COLUMN].isin(
            AGGREGATE_NAMES
        ),
        [LGC_COLUMN, NAME_COLUMN],
    ]

    total_targets = len(search_targets)

    for sequence, row in enumerate(
        search_targets.itertuples(index=False),
        start=1,
    ):
        lgc = str(
            getattr(row, LGC_COLUMN)
        )

        name = str(
            getattr(row, NAME_COLUMN)
        ).strip()

        print(
            f"[{sequence:02d}/{total_targets:02d}] "
            f"{name}を検索中...",
            end=" ",
            flush=True,
        )

        try:
            records = search_municipality(
                session,
                name,
            )

            api_count = len(records)

            print(
                f"{api_count:,}件"
            )

            results.append({
                LGC_COLUMN: lgc,
                NAME_COLUMN: name,
                API_COUNT_COLUMN: api_count,
                "API_Error": "",
            })

        except Exception as error:
            print(
                f"エラー: {error}"
            )

            results.append({
                LGC_COLUMN: lgc,
                NAME_COLUMN: name,
                API_COUNT_COLUMN: pd.NA,
                "API_Error": str(error),
            })

        # 最後の検索後には待機しない
        if sequence < total_targets:
            time.sleep(
                REQUEST_INTERVAL_SECONDS
            )

    session.close()

    return pd.DataFrame(results)


# ============================================================
# 集計行の算出
# ============================================================

def sum_api_counts_by_lgc(
    dataframe: pd.DataFrame,
    lgc_values: list[str],
) -> int | pd.NA:
    """
    指定したLGC群のAPI件数を合計する。

    いずれかに取得失敗がある場合はpd.NAを返す。
    """
    selected = dataframe.loc[
        dataframe[LGC_COLUMN].isin(lgc_values),
        API_COUNT_COLUMN,
    ]

    if selected.empty:
        return pd.NA

    if selected.isna().any():
        return pd.NA

    return int(
        selected.astype("int64").sum()
    )


def add_aggregate_counts(
    api_counts: pd.DataFrame,
    lgc_master: pd.DataFrame,
) -> pd.DataFrame:
    """
    区部、多摩地区、島嶼部、東京都計を算出する。
    """
    result = api_counts.copy()

    actual_lgc_values = set(
        result[LGC_COLUMN].tolist()
    )

    ward_lgc = [
        f"131{number:02d}"
        for number in range(1, 24)
    ]

    tama_city_lgc = [
        f"132{number:02d}"
        for number in [
            1, 2, 3, 4, 5, 6, 7, 8, 9,
            10, 11, 12, 13, 14, 15,
            18, 19, 20, 21, 22, 23, 24,
            25, 27, 28, 29,
        ]
    ]

    nishitama_lgc = [
        "13303",
        "13305",
        "13307",
        "13308",
    ]

    island_lgc = [
        "13361",
        "13362",
        "13363",
        "13364",
        "13381",
        "13382",
        "13401",
        "13402",
        "13421",
    ]

    aggregate_members = {
        "区部": ward_lgc,

        "多摩地区": (
            tama_city_lgc
            + nishitama_lgc
        ),

        "島嶼部": island_lgc,

        "東京都計": sorted(
            actual_lgc_values
        ),
    }

    aggregate_rows: list[dict[str, object]] = []

    for aggregate_name, members in aggregate_members.items():
        master_match = lgc_master.loc[
            lgc_master[NAME_COLUMN]
            == aggregate_name
        ]

        if master_match.empty:
            continue

        aggregate_lgc = master_match.iloc[0][
            LGC_COLUMN
        ]

        count = sum_api_counts_by_lgc(
            result,
            members,
        )

        aggregate_rows.append({
            LGC_COLUMN: aggregate_lgc,
            NAME_COLUMN: aggregate_name,
            API_COUNT_COLUMN: count,
            "API_Error": "",
        })

    if aggregate_rows:
        result = pd.concat(
            [
                result,
                pd.DataFrame(aggregate_rows),
            ],
            ignore_index=True,
        )

    return result


# ============================================================
# 既存集計値との比較
# ============================================================

def create_check_table(
    lgc_master: pd.DataFrame,
    sum_munic: pd.DataFrame,
    api_counts: pd.DataFrame,
) -> pd.DataFrame:
    """
    LGCマスター、既存集計、API集計を連結し、差分を算出する。
    """
    result = (
        lgc_master[
            [LGC_COLUMN, NAME_COLUMN]
        ]
        .merge(
            sum_munic[
                [
                    LGC_COLUMN,
                    SOURCE_COUNT_COLUMN,
                ]
            ],
            on=LGC_COLUMN,
            how="left",
            validate="one_to_one",
        )
        .merge(
            api_counts[
                [
                    LGC_COLUMN,
                    API_COUNT_COLUMN,
                    "API_Error",
                ]
            ],
            on=LGC_COLUMN,
            how="left",
            validate="one_to_one",
        )
    )

    result[SOURCE_COUNT_COLUMN] = (
        pd.to_numeric(
            result[SOURCE_COUNT_COLUMN],
            errors="coerce",
        )
        .astype("Int64")
    )

    result[API_COUNT_COLUMN] = (
        pd.to_numeric(
            result[API_COUNT_COLUMN],
            errors="coerce",
        )
        .astype("Int64")
    )

    result[DIFFERENCE_COLUMN] = (
        result[API_COUNT_COLUMN]
        - result[SOURCE_COUNT_COLUMN]
    ).astype("Int64")

    # ユーザー指定の主要4列
    return result[
        [
            LGC_COLUMN,
            NAME_COLUMN,
            API_COUNT_COLUMN,
            DIFFERENCE_COLUMN,
        ]
    ]


def write_csv_utf8(
    dataframe: pd.DataFrame,
    path: Path,
) -> None:
    """
    UTF-8のCSVとして保存する。
    """
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
    print(
        f"LGCマスター: {LGC_CSV.resolve()}"
    )

    print(
        f"既存集計: {SUM_MUNIC_CSV.resolve()}"
    )

    print()

    lgc_master = read_csv_utf8(
        LGC_CSV,
        dtype={
            LGC_COLUMN: str,
            NAME_COLUMN: str,
        },
    )

    sum_munic = read_csv_utf8(
        SUM_MUNIC_CSV,
        dtype={
            LGC_COLUMN: str,
        },
    )

    validate_columns(
        lgc_master,
        [
            LGC_COLUMN,
            NAME_COLUMN,
        ],
        LGC_CSV.name,
    )

    validate_columns(
        sum_munic,
        [
            LGC_COLUMN,
            SOURCE_COUNT_COLUMN,
        ],
        SUM_MUNIC_CSV.name,
    )

    lgc_master[LGC_COLUMN] = normalize_lgc(
        lgc_master[LGC_COLUMN]
    )

    lgc_master[NAME_COLUMN] = (
        lgc_master[NAME_COLUMN]
        .astype(str)
        .str.strip()
    )

    sum_munic[LGC_COLUMN] = normalize_lgc(
        sum_munic[LGC_COLUMN]
    )

    # LGC重複チェック
    if lgc_master[LGC_COLUMN].duplicated().any():
        duplicated = (
            lgc_master.loc[
                lgc_master[LGC_COLUMN].duplicated(
                    keep=False
                ),
                LGC_COLUMN,
            ]
            .unique()
            .tolist()
        )

        raise ValueError(
            "LGC_13Tokyo.csvに重複するLGCがあります: "
            + ", ".join(duplicated)
        )

    if sum_munic[LGC_COLUMN].duplicated().any():
        duplicated = (
            sum_munic.loc[
                sum_munic[LGC_COLUMN].duplicated(
                    keep=False
                ),
                LGC_COLUMN,
            ]
            .unique()
            .tolist()
        )

        raise ValueError(
            "13Tokyo_SumMunic.csvに重複するLGCがあります: "
            + ", ".join(duplicated)
        )

    # --------------------------------------------------------
    # API検索
    # --------------------------------------------------------

    api_counts = collect_api_counts(
        lgc_master
    )

    # 集計行を追加
    api_counts = add_aggregate_counts(
        api_counts,
        lgc_master,
    )

    # --------------------------------------------------------
    # 既存集計値との比較
    # --------------------------------------------------------

    check_table = create_check_table(
        lgc_master,
        sum_munic,
        api_counts,
    )

    write_csv_utf8(
        check_table,
        OUTPUT_CSV,
    )

    # --------------------------------------------------------
    # 実行結果
    # --------------------------------------------------------

    print()
    print("照合が完了しました。")
    print(
        f"出力先: {OUTPUT_CSV.resolve()}"
    )

    successful = int(
        check_table[API_COUNT_COLUMN]
        .notna()
        .sum()
    )

    failed = int(
        check_table[API_COUNT_COLUMN]
        .isna()
        .sum()
    )

    differences = int(
        (
            check_table[DIFFERENCE_COLUMN]
            .fillna(0)
            != 0
        ).sum()
    )

    print(
        f"API件数取得済み: {successful:,}行"
    )

    print(
        f"API件数未取得: {failed:,}行"
    )

    print(
        f"差分あり: {differences:,}行"
    )


# ============================================================
# 実行
# ============================================================

if __name__ == "__main__":
    main()