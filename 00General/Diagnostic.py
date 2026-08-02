#!/usr/bin/env python3

"""
JASOSR Data Validator
=====================

CSVファイルの文字コード、CSV構造、列情報、NULLバイト、
改行コード、必須列、緯度・経度を検査する汎用診断ツール。

想定配置
--------

JASOSR/
├─ 00General/
│  └─ Diagnostic.py
├─ 13Tokyo/
│  └─ 13Tokyo_total.csv
├─ 14Kanagawa/
│  └─ 14Kanagawa_total.csv
└─ ...

実行例
------

Diagnostic.pyがある00Generalへ移動して実行する場合:

macOS / Linux:

    cd "/Users/username/Documents/GitHub/JASOSR/00General"
    python3 Diagnostic.py ../13Tokyo/13Tokyo_total.csv

Windows:

    cd "C:/Users/username/Documents/GitHub/JASOSR/00General"
    python Diagnostic.py ../13Tokyo/13Tokyo_total.csv

絶対パスでも指定できる:

    python3 Diagnostic.py "/Users/username/Documents/GitHub/JASOSR/13Tokyo/13Tokyo_total.csv"

必須列も検査する場合:

    python3 Diagnostic.py ../13Tokyo/13Tokyo_total.csv \
        --required LGC Address Lat Lon

緯度経度の列名を明示する場合:

    python3 Diagnostic.py ../13Tokyo/13Tokyo_total.csv \
        --lat-column Lat \
        --lon-column Lon

出力
----

入力CSVと同じフォルダへ、次の3ファイルを出力する。

    <入力ファイル名>_ErrorReport.csv
    <入力ファイル名>_ErrorSummary.txt
    <入力ファイル名>_ErrorReport.html

例:

    13Tokyo_total_ErrorReport.csv
    13Tokyo_total_ErrorSummary.txt
    13Tokyo_total_ErrorReport.html

依存ライブラリ
--------------

Python標準ライブラリだけで動作する。
pandas等の追加インストールは不要。

検査内容
--------

・UTF-8 / UTF-8 BOM / CP932 / Shift_JIS の判定
・UTF-8として不正なバイト位置
・NULLバイト
・改行コードの混在
・CSV構文エラー
・ヘッダーと異なる列数のレコード
・列名一覧
・重複列名
・空列名
・必須列の有無
・Lat / Lon列が存在する場合の数値・範囲チェック
・レコード数、列数、ファイルサイズ
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Sequence


# ============================================================
# 設定
# ============================================================

ENCODING_CANDIDATES = (
    "utf-8-sig",
    "utf-8",
    "cp932",
    "shift_jis",
)

DEFAULT_LAT_COLUMN_CANDIDATES = (
    "Lat",
    "LAT",
    "Latitude",
    "latitude",
    "緯度",
)

DEFAULT_LON_COLUMN_CANDIDATES = (
    "Lon",
    "LON",
    "Lng",
    "Longitude",
    "longitude",
    "経度",
)

MAX_CONTEXT_LENGTH = 300
HTML_MAX_ISSUES = 5000


# ============================================================
# データ型
# ============================================================

@dataclass
class Issue:
    severity: str
    category: str
    record_number: int | None
    physical_line_number: int | None
    column_name: str
    message: str
    value: str
    context: str


@dataclass
class Summary:
    input_path: str
    file_name: str
    file_size_bytes: int
    detected_encoding: str
    utf8_valid: bool
    utf8_bom: bool
    nul_byte_count: int
    line_ending_lf: int
    line_ending_crlf: int
    line_ending_cr: int
    mixed_line_endings: bool
    header_column_count: int
    data_record_count: int
    physical_line_count: int
    duplicate_column_count: int
    empty_column_name_count: int
    missing_required_columns: str
    latitude_column: str
    longitude_column: str
    issue_count: int
    error_count: int
    warning_count: int


# ============================================================
# ユーティリティ
# ============================================================

def shorten(value: object, limit: int = MAX_CONTEXT_LENGTH) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", "\\x00")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def byte_offset_to_line_number(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def normalize_header_name(value: str) -> str:
    return value.strip().lstrip("\ufeff")


def find_first_matching_column(
    columns: Sequence[str],
    preferred: str | None,
    candidates: Sequence[str],
) -> str:
    if preferred:
        return preferred if preferred in columns else ""

    for candidate in candidates:
        if candidate in columns:
            return candidate

    lower_lookup = {
        column.lower(): column
        for column in columns
    }

    for candidate in candidates:
        found = lower_lookup.get(candidate.lower())
        if found:
            return found

    return ""


def safe_float(value: str) -> float | None:
    text = value.strip()

    if text == "":
        return None

    try:
        return float(text)
    except ValueError:
        return None


# ============================================================
# 文字コード・バイト検査
# ============================================================

def detect_encoding(data: bytes) -> tuple[str, bool, list[Issue]]:
    issues: list[Issue] = []
    utf8_bom = data.startswith(b"\xef\xbb\xbf")

    try:
        data.decode("utf-8-sig" if utf8_bom else "utf-8")
        return ("utf-8-sig" if utf8_bom else "utf-8", True, issues)
    except UnicodeDecodeError as error:
        line_number = byte_offset_to_line_number(data, error.start)
        context_start = max(0, error.start - 40)
        context_end = min(len(data), error.end + 40)
        raw_context = data[context_start:context_end]

        issues.append(
            Issue(
                severity="ERROR",
                category="ENCODING",
                record_number=None,
                physical_line_number=line_number,
                column_name="",
                message=(
                    "UTF-8として解釈できないバイト列があります。"
                    f" byte offset={error.start}, reason={error.reason}"
                ),
                value=raw_context.hex(" "),
                context=raw_context.decode("utf-8", errors="replace"),
            )
        )

    for encoding in ("cp932", "shift_jis"):
        try:
            data.decode(encoding)
            return (encoding, False, issues)
        except UnicodeDecodeError:
            continue

    return ("unknown", False, issues)


def inspect_nul_bytes(data: bytes) -> list[Issue]:
    issues: list[Issue] = []
    start = 0

    while True:
        offset = data.find(b"\x00", start)
        if offset < 0:
            break

        line_number = byte_offset_to_line_number(data, offset)
        context_start = max(0, offset - 40)
        context_end = min(len(data), offset + 41)
        raw_context = data[context_start:context_end]

        issues.append(
            Issue(
                severity="ERROR",
                category="NULL_BYTE",
                record_number=None,
                physical_line_number=line_number,
                column_name="",
                message=f"NULLバイトを検出しました。byte offset={offset}",
                value="00",
                context=raw_context.decode("utf-8", errors="replace"),
            )
        )
        start = offset + 1

    return issues


def inspect_line_endings(data: bytes) -> tuple[int, int, int, bool]:
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    cr = data.count(b"\r") - crlf

    kinds_used = sum(
        count > 0
        for count in (lf, crlf, cr)
    )

    return lf, crlf, cr, kinds_used > 1


# ============================================================
# CSV検査
# ============================================================

def decode_for_csv(data: bytes, encoding: str) -> str:
    if encoding == "unknown":
        return data.decode("utf-8", errors="replace")

    return data.decode(encoding, errors="strict")


def parse_csv_records(
    text: str,
) -> tuple[list[str], list[list[str]], list[int], list[Issue]]:
    issues: list[Issue] = []
    header: list[str] = []
    rows: list[list[str]] = []
    record_line_numbers: list[int] = []

    stream = io.StringIO(text, newline="")
    reader = csv.reader(stream, strict=True)

    try:
        raw_header = next(reader)
        header = [
            normalize_header_name(value)
            for value in raw_header
        ]
    except StopIteration:
        issues.append(
            Issue(
                severity="ERROR",
                category="CSV_STRUCTURE",
                record_number=None,
                physical_line_number=1,
                column_name="",
                message="CSVが空です。",
                value="",
                context="",
            )
        )
        return header, rows, record_line_numbers, issues
    except csv.Error as error:
        issues.append(
            Issue(
                severity="ERROR",
                category="CSV_SYNTAX",
                record_number=None,
                physical_line_number=reader.line_num or 1,
                column_name="",
                message=f"ヘッダーの解析に失敗しました: {error}",
                value="",
                context="",
            )
        )
        return header, rows, record_line_numbers, issues

    expected_count = len(header)
    record_number = 1

    while True:
        start_line = reader.line_num + 1

        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error as error:
            issues.append(
                Issue(
                    severity="ERROR",
                    category="CSV_SYNTAX",
                    record_number=record_number,
                    physical_line_number=reader.line_num or start_line,
                    column_name="",
                    message=f"CSV構文エラー: {error}",
                    value="",
                    context="",
                )
            )
            break

        record_line_numbers.append(start_line)
        rows.append(row)

        if len(row) != expected_count:
            issues.append(
                Issue(
                    severity="ERROR",
                    category="COLUMN_COUNT",
                    record_number=record_number,
                    physical_line_number=start_line,
                    column_name="",
                    message=(
                        f"列数がヘッダーと一致しません。"
                        f" expected={expected_count}, actual={len(row)}"
                    ),
                    value=str(len(row)),
                    context=shorten(" | ".join(row)),
                )
            )

        record_number += 1

    return header, rows, record_line_numbers, issues


def inspect_headers(
    header: Sequence[str],
    required_columns: Sequence[str],
) -> tuple[list[Issue], list[str]]:
    issues: list[Issue] = []

    empty_names = [
        index + 1
        for index, name in enumerate(header)
        if name.strip() == ""
    ]

    for index in empty_names:
        issues.append(
            Issue(
                severity="ERROR",
                category="HEADER",
                record_number=None,
                physical_line_number=1,
                column_name="",
                message=f"{index}列目の列名が空です。",
                value="",
                context="",
            )
        )

    seen: dict[str, int] = {}

    for index, name in enumerate(header, start=1):
        if name in seen:
            issues.append(
                Issue(
                    severity="ERROR",
                    category="HEADER",
                    record_number=None,
                    physical_line_number=1,
                    column_name=name,
                    message=(
                        f"列名が重複しています。"
                        f" first={seen[name]}列目, duplicate={index}列目"
                    ),
                    value=name,
                    context="",
                )
            )
        else:
            seen[name] = index

    missing_required = [
        column
        for column in required_columns
        if column not in header
    ]

    for column in missing_required:
        issues.append(
            Issue(
                severity="ERROR",
                category="REQUIRED_COLUMN",
                record_number=None,
                physical_line_number=1,
                column_name=column,
                message=f"必須列がありません: {column}",
                value="",
                context="",
            )
        )

    return issues, missing_required


def inspect_coordinates(
    header: Sequence[str],
    rows: Sequence[Sequence[str]],
    record_line_numbers: Sequence[int],
    lat_column: str,
    lon_column: str,
) -> list[Issue]:
    issues: list[Issue] = []

    if not lat_column and not lon_column:
        return issues

    if lat_column and lat_column not in header:
        issues.append(
            Issue(
                severity="WARNING",
                category="COORDINATE",
                record_number=None,
                physical_line_number=1,
                column_name=lat_column,
                message=f"指定された緯度列がありません: {lat_column}",
                value="",
                context="",
            )
        )
        lat_column = ""

    if lon_column and lon_column not in header:
        issues.append(
            Issue(
                severity="WARNING",
                category="COORDINATE",
                record_number=None,
                physical_line_number=1,
                column_name=lon_column,
                message=f"指定された経度列がありません: {lon_column}",
                value="",
                context="",
            )
        )
        lon_column = ""

    lat_index = header.index(lat_column) if lat_column else None
    lon_index = header.index(lon_column) if lon_column else None

    for record_number, row in enumerate(rows, start=1):
        physical_line = (
            record_line_numbers[record_number - 1]
            if record_number - 1 < len(record_line_numbers)
            else None
        )

        if len(row) != len(header):
            continue

        if lat_index is not None:
            raw_lat = row[lat_index]
            lat = safe_float(raw_lat)

            if raw_lat.strip() == "":
                issues.append(
                    Issue(
                        severity="WARNING",
                        category="COORDINATE",
                        record_number=record_number,
                        physical_line_number=physical_line,
                        column_name=lat_column,
                        message="緯度が空です。",
                        value=raw_lat,
                        context="",
                    )
                )
            elif lat is None:
                issues.append(
                    Issue(
                        severity="ERROR",
                        category="COORDINATE",
                        record_number=record_number,
                        physical_line_number=physical_line,
                        column_name=lat_column,
                        message="緯度を数値として解釈できません。",
                        value=raw_lat,
                        context="",
                    )
                )
            elif not -90 <= lat <= 90:
                issues.append(
                    Issue(
                        severity="ERROR",
                        category="COORDINATE",
                        record_number=record_number,
                        physical_line_number=physical_line,
                        column_name=lat_column,
                        message="緯度が有効範囲外です。",
                        value=raw_lat,
                        context="",
                    )
                )

        if lon_index is not None:
            raw_lon = row[lon_index]
            lon = safe_float(raw_lon)

            if raw_lon.strip() == "":
                issues.append(
                    Issue(
                        severity="WARNING",
                        category="COORDINATE",
                        record_number=record_number,
                        physical_line_number=physical_line,
                        column_name=lon_column,
                        message="経度が空です。",
                        value=raw_lon,
                        context="",
                    )
                )
            elif lon is None:
                issues.append(
                    Issue(
                        severity="ERROR",
                        category="COORDINATE",
                        record_number=record_number,
                        physical_line_number=physical_line,
                        column_name=lon_column,
                        message="経度を数値として解釈できません。",
                        value=raw_lon,
                        context="",
                    )
                )
            elif not -180 <= lon <= 180:
                issues.append(
                    Issue(
                        severity="ERROR",
                        category="COORDINATE",
                        record_number=record_number,
                        physical_line_number=physical_line,
                        column_name=lon_column,
                        message="経度が有効範囲外です。",
                        value=raw_lon,
                        context="",
                    )
                )

    return issues


# ============================================================
# 出力
# ============================================================

def output_paths(input_path: Path) -> tuple[Path, Path, Path]:
    base = input_path.with_suffix("")
    report_csv = base.parent / f"{base.name}_ErrorReport.csv"
    summary_txt = base.parent / f"{base.name}_ErrorSummary.txt"
    report_html = base.parent / f"{base.name}_ErrorReport.html"

    return report_csv, summary_txt, report_html


def write_issue_csv(path: Path, issues: Sequence[Issue]) -> None:
    fieldnames = [
        "severity",
        "category",
        "record_number",
        "physical_line_number",
        "column_name",
        "message",
        "value",
        "context",
    ]

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )
        writer.writeheader()

        for issue in issues:
            writer.writerow(asdict(issue))


def format_columns(header: Sequence[str]) -> str:
    if not header:
        return "  （取得できませんでした）"

    lines = []

    for index, name in enumerate(header, start=1):
        lines.append(f"  {index:>3}: {name}")

    return "\n".join(lines)


def write_summary_txt(
    path: Path,
    summary: Summary,
    header: Sequence[str],
    issues: Sequence[Issue],
) -> None:
    category_counts: dict[str, int] = {}

    for issue in issues:
        category_counts[issue.category] = (
            category_counts.get(issue.category, 0) + 1
        )

    lines = [
        "JASOSR Data Validator 診断結果",
        "=" * 60,
        "",
        f"入力ファイル: {summary.input_path}",
        f"ファイル名: {summary.file_name}",
        f"ファイルサイズ: {summary.file_size_bytes:,} bytes",
        f"検出文字コード: {summary.detected_encoding}",
        f"UTF-8として有効: {'はい' if summary.utf8_valid else 'いいえ'}",
        f"UTF-8 BOM: {'あり' if summary.utf8_bom else 'なし'}",
        f"NULLバイト数: {summary.nul_byte_count:,}",
        "",
        "改行コード",
        "-" * 60,
        f"LF: {summary.line_ending_lf:,}",
        f"CRLF: {summary.line_ending_crlf:,}",
        f"CR: {summary.line_ending_cr:,}",
        f"混在: {'あり' if summary.mixed_line_endings else 'なし'}",
        "",
        "CSV基本情報",
        "-" * 60,
        f"ヘッダー列数: {summary.header_column_count:,}",
        f"データレコード数: {summary.data_record_count:,}",
        f"物理行数: {summary.physical_line_count:,}",
        f"重複列名数: {summary.duplicate_column_count:,}",
        f"空列名数: {summary.empty_column_name_count:,}",
        f"不足必須列: {summary.missing_required_columns or 'なし'}",
        f"緯度列: {summary.latitude_column or '未検出'}",
        f"経度列: {summary.longitude_column or '未検出'}",
        "",
        "診断結果",
        "-" * 60,
        f"問題総数: {summary.issue_count:,}",
        f"ERROR: {summary.error_count:,}",
        f"WARNING: {summary.warning_count:,}",
        "",
        "カテゴリ別件数",
        "-" * 60,
    ]

    if category_counts:
        for category, count in sorted(category_counts.items()):
            lines.append(f"{category}: {count:,}")
    else:
        lines.append("問題は検出されませんでした。")

    lines.extend([
        "",
        "列名一覧",
        "-" * 60,
        format_columns(header),
        "",
    ])

    path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def write_report_html(
    path: Path,
    summary: Summary,
    header: Sequence[str],
    issues: Sequence[Issue],
) -> None:
    issue_rows = []

    for issue in issues[:HTML_MAX_ISSUES]:
        issue_rows.append(
            "<tr>"
            f"<td>{html.escape(issue.severity)}</td>"
            f"<td>{html.escape(issue.category)}</td>"
            f"<td>{'' if issue.record_number is None else issue.record_number}</td>"
            f"<td>{'' if issue.physical_line_number is None else issue.physical_line_number}</td>"
            f"<td>{html.escape(issue.column_name)}</td>"
            f"<td>{html.escape(issue.message)}</td>"
            f"<td><code>{html.escape(shorten(issue.value, 180))}</code></td>"
            f"<td><code>{html.escape(shorten(issue.context, 300))}</code></td>"
            "</tr>"
        )

    if not issue_rows:
        issue_rows.append(
            '<tr><td colspan="8">問題は検出されませんでした。</td></tr>'
        )

    column_items = "\n".join(
        f"<li><code>{html.escape(name)}</code></li>"
        for name in header
    )

    truncated_note = ""

    if len(issues) > HTML_MAX_ISSUES:
        truncated_note = (
            f"<p>HTMLには先頭{HTML_MAX_ISSUES:,}件のみ表示しています。"
            "全件はCSVレポートを確認してください。</p>"
        )

    html_text = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JASOSR Data Validator - {html.escape(summary.file_name)}</title>
<style>
body {{
  margin: 24px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
    "Hiragino Kaku Gothic ProN", "Yu Gothic", Meiryo, sans-serif;
  color: #222;
}}
h1, h2 {{ margin-bottom: 0.4em; }}
.summary {{
  display: grid;
  grid-template-columns: minmax(180px, 260px) 1fr;
  max-width: 1000px;
  border: 1px solid #ccc;
}}
.summary dt, .summary dd {{
  margin: 0;
  padding: 7px 10px;
  border-bottom: 1px solid #ddd;
}}
.summary dt {{ font-weight: 700; background: #f5f5f5; }}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}}
th, td {{
  padding: 6px 8px;
  border: 1px solid #ccc;
  text-align: left;
  vertical-align: top;
}}
th {{ background: #f2f2f2; position: sticky; top: 0; }}
code {{ white-space: pre-wrap; word-break: break-all; }}
.error {{ color: #b00020; font-weight: 700; }}
.warning {{ color: #8a5a00; font-weight: 700; }}
</style>
</head>
<body>
<h1>JASOSR Data Validator</h1>
<h2>{html.escape(summary.file_name)}</h2>

<dl class="summary">
<dt>入力ファイル</dt><dd>{html.escape(summary.input_path)}</dd>
<dt>ファイルサイズ</dt><dd>{summary.file_size_bytes:,} bytes</dd>
<dt>検出文字コード</dt><dd>{html.escape(summary.detected_encoding)}</dd>
<dt>UTF-8として有効</dt><dd>{'はい' if summary.utf8_valid else 'いいえ'}</dd>
<dt>UTF-8 BOM</dt><dd>{'あり' if summary.utf8_bom else 'なし'}</dd>
<dt>NULLバイト数</dt><dd>{summary.nul_byte_count:,}</dd>
<dt>ヘッダー列数</dt><dd>{summary.header_column_count:,}</dd>
<dt>データレコード数</dt><dd>{summary.data_record_count:,}</dd>
<dt>物理行数</dt><dd>{summary.physical_line_count:,}</dd>
<dt>不足必須列</dt><dd>{html.escape(summary.missing_required_columns or 'なし')}</dd>
<dt>緯度列</dt><dd>{html.escape(summary.latitude_column or '未検出')}</dd>
<dt>経度列</dt><dd>{html.escape(summary.longitude_column or '未検出')}</dd>
<dt>問題総数</dt><dd>{summary.issue_count:,}</dd>
<dt>ERROR</dt><dd class="error">{summary.error_count:,}</dd>
<dt>WARNING</dt><dd class="warning">{summary.warning_count:,}</dd>
</dl>

<h2>列名</h2>
<ol>
{column_items}
</ol>

<h2>検出された問題</h2>
{truncated_note}
<table>
<thead>
<tr>
<th>severity</th>
<th>category</th>
<th>record</th>
<th>physical line</th>
<th>column</th>
<th>message</th>
<th>value</th>
<th>context</th>
</tr>
</thead>
<tbody>
{''.join(issue_rows)}
</tbody>
</table>
</body>
</html>
"""

    path.write_text(
        html_text,
        encoding="utf-8",
    )


# ============================================================
# 診断本体
# ============================================================

def validate_csv(
    input_path: Path,
    required_columns: Sequence[str],
    lat_column_arg: str | None,
    lon_column_arg: str | None,
) -> tuple[Summary, list[str], list[Issue]]:
    data = input_path.read_bytes()
    issues: list[Issue] = []

    detected_encoding, utf8_valid, encoding_issues = detect_encoding(data)
    issues.extend(encoding_issues)

    nul_issues = inspect_nul_bytes(data)
    issues.extend(nul_issues)

    lf, crlf, cr, mixed_line_endings = inspect_line_endings(data)

    if mixed_line_endings:
        issues.append(
            Issue(
                severity="WARNING",
                category="LINE_ENDING",
                record_number=None,
                physical_line_number=None,
                column_name="",
                message="複数種類の改行コードが混在しています。",
                value=f"LF={lf}, CRLF={crlf}, CR={cr}",
                context="",
            )
        )

    try:
        text = decode_for_csv(data, detected_encoding)
    except UnicodeDecodeError as error:
        issues.append(
            Issue(
                severity="ERROR",
                category="ENCODING",
                record_number=None,
                physical_line_number=byte_offset_to_line_number(data, error.start),
                column_name="",
                message=f"検出文字コードでのデコードに失敗しました: {error}",
                value="",
                context="",
            )
        )
        text = data.decode("utf-8", errors="replace")

    header, rows, record_line_numbers, csv_issues = parse_csv_records(text)
    issues.extend(csv_issues)

    header_issues, missing_required = inspect_headers(
        header,
        required_columns,
    )
    issues.extend(header_issues)

    lat_column = find_first_matching_column(
        header,
        lat_column_arg,
        DEFAULT_LAT_COLUMN_CANDIDATES,
    )

    lon_column = find_first_matching_column(
        header,
        lon_column_arg,
        DEFAULT_LON_COLUMN_CANDIDATES,
    )

    issues.extend(
        inspect_coordinates(
            header,
            rows,
            record_line_numbers,
            lat_column_arg or lat_column,
            lon_column_arg or lon_column,
        )
    )

    duplicate_column_count = len(header) - len(set(header))
    empty_column_name_count = sum(
        not name.strip()
        for name in header
    )

    error_count = sum(
        issue.severity == "ERROR"
        for issue in issues
    )

    warning_count = sum(
        issue.severity == "WARNING"
        for issue in issues
    )

    physical_line_count = (
        data.count(b"\n")
        + (1 if data else 0)
    )

    summary = Summary(
        input_path=str(input_path.resolve()),
        file_name=input_path.name,
        file_size_bytes=len(data),
        detected_encoding=detected_encoding,
        utf8_valid=utf8_valid,
        utf8_bom=data.startswith(b"\xef\xbb\xbf"),
        nul_byte_count=len(nul_issues),
        line_ending_lf=lf,
        line_ending_crlf=crlf,
        line_ending_cr=cr,
        mixed_line_endings=mixed_line_endings,
        header_column_count=len(header),
        data_record_count=len(rows),
        physical_line_count=physical_line_count,
        duplicate_column_count=duplicate_column_count,
        empty_column_name_count=empty_column_name_count,
        missing_required_columns=", ".join(missing_required),
        latitude_column=lat_column,
        longitude_column=lon_column,
        issue_count=len(issues),
        error_count=error_count,
        warning_count=warning_count,
    )

    return summary, header, issues


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "CSVの文字コード、構造、列名、必須列、"
            "緯度経度などを検査する汎用診断ツール。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
例:
  python3 Diagnostic.py ../13Tokyo/13Tokyo_total.csv

  python3 Diagnostic.py ../13Tokyo/13Tokyo_total.csv \
      --required LGC Address Lat Lon

  python3 Diagnostic.py ../13Tokyo/13Tokyo_total.csv \
      --lat-column Lat --lon-column Lon
""",
    )

    parser.add_argument(
        "csv_path",
        type=Path,
        help="検査対象CSVのパス",
    )

    parser.add_argument(
        "--required",
        nargs="*",
        default=[],
        metavar="COLUMN",
        help="存在を確認する必須列名",
    )

    parser.add_argument(
        "--lat-column",
        default=None,
        help="緯度列名。省略時は候補名から自動検出",
    )

    parser.add_argument(
        "--lon-column",
        default=None,
        help="経度列名。省略時は候補名から自動検出",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    input_path = args.csv_path.expanduser()

    if not input_path.is_absolute():
        input_path = (
            Path.cwd()
            / input_path
        ).resolve()

    if not input_path.exists():
        parser.error(
            f"入力CSVが見つかりません: {input_path}"
        )

    if not input_path.is_file():
        parser.error(
            f"指定されたパスはファイルではありません: {input_path}"
        )

    if input_path.suffix.lower() != ".csv":
        parser.error(
            f"CSVファイルを指定してください: {input_path}"
        )

    report_csv, summary_txt, report_html = output_paths(input_path)

    summary, header, issues = validate_csv(
        input_path=input_path,
        required_columns=args.required,
        lat_column_arg=args.lat_column,
        lon_column_arg=args.lon_column,
    )

    write_issue_csv(
        report_csv,
        issues,
    )

    write_summary_txt(
        summary_txt,
        summary,
        header,
        issues,
    )

    write_report_html(
        report_html,
        summary,
        header,
        issues,
    )

    print("JASOSR Data Validator")
    print("=" * 60)
    print(f"入力: {input_path}")
    print(f"文字コード: {summary.detected_encoding}")
    print(f"UTF-8として有効: {'はい' if summary.utf8_valid else 'いいえ'}")
    print(f"レコード数: {summary.data_record_count:,}")
    print(f"列数: {summary.header_column_count:,}")
    print(f"ERROR: {summary.error_count:,}")
    print(f"WARNING: {summary.warning_count:,}")
    print()
    print(f"CSVレポート: {report_csv}")
    print(f"概要: {summary_txt}")
    print(f"HTMLレポート: {report_html}")

    return 1 if summary.error_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
