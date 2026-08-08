# 遺跡分布長期動態解析
## 1. 基本方針
   - 0-1ベクトル化した時代・時期区分データを持つポイント・フューチャーとしての「遺跡」を対象とする
   
## 2. 分析手順
   
   ### 2-1. 前処理：分析データの定義と整形
      - 目的：分析対象と除外条件を統一し、比較可能な時系列データを整える
      - 達成指標：対象7時期・除外対象・特殊SiteTypeが一貫したルールで整理されている

| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **前処理** | `input_csv` | 入力CSV指定 | `13Tokyo_chronology.csv` を入力データとして指定する | `c02` | 入力GPKGは使用しない |
| **前処理** | `phase_cols` | 解析対象時期列の限定 | `Pa → Jo → Ya → Ko → Na → He → Me` の7列だけを連続性判定に使用する | `c02`, `c06` | その他の年代列は連続性判定には使用しない |
| **前処理** | `chronology_marker_cols` | EMのみ除外用年代列 | `Pa`〜`Un` など、EMのみ地点の判定に使う年代マーカー列を整理する | `c03` | 連続性判定とは別用途 |
| **前処理** | `em_only_flag` | EMのみ地点の除外 | `EM` のみに該当し、他の年代列に記録がない地点を主分析から除外する | `c03` | 除外数のみ `em_only_exclusion_summary.csv` に記録 |
| **前処理** | `em_only_exclusion_summary` | EM除外数の記録 | 入力行数、EMのみ除外数、分析対象行数を記録する | `c03`, `c18` | `em_only_exclusion_summary.csv` に出力 |
| **前処理** | `input_column_summary` | 入力列の使用区分確認 | 各列が連続性判定、EM除外、メタデータ、不使用のどれに該当するかを整理する | `c03`, `c18` | `input_column_usage_summary.csv` に出力 |
| **前処理** | `site_type_tokens` | SiteTypeカテゴリ分解 | `SiteType` を複数カテゴリに分解し、除外カテゴリのみで構成されるか判定できる形にする | `c04` | `古墳群+集落`、`塚+集落` などの複合SiteTypeを誤除外しないための処理 |
| **前処理** | `ko_single_phase` | 古墳時代単独地点判定 | 対象7時期のうち `Ko` のみに出現する地点を判定する | `c04` | 古墳・横穴墓等の除外条件に使用 |
| **前処理** | `me_single_phase` | 中近世単独地点判定 | 対象7時期のうち `Me` のみに出現する地点を判定する | `c04` | 塚・墓地・板碑出土地等の除外条件に使用 |
| **前処理** | `has_kofun_mortuary_site_type` | 古墳系SiteType判定 | `古墳`、`古墳群`、`横穴墓`、`方形周溝墓`、`墳墓` などを含む地点を判定する | `c04` | 複合SiteTypeの場合は除外せずフラグ保持 |
| **前処理** | `has_me_mound_burial_site_type` | 塚・墓地系SiteType判定 | `塚`、`経塚`、`墓地`、`板碑出土地`、`単独出土地` などを含む地点を判定する | `c04` | `貝塚` は塚カテゴリに含めない |
| **前処理** | `exclusive_exclusion_site_type` | 排他的特殊SiteType判定 | SiteTypeが除外カテゴリのみで構成されるかを判定する | `c04` | `古墳群+集落` のような複合遺跡は除外しない |
| **前処理** | `exclude_special_site_type` | 排他的特殊SiteType除外 | `Ko`単独の古墳系、または`Me`単独の塚・墓地系で、かつSiteTypeが排他的特殊カテゴリのみの地点を除外する | `c04` | 主分析の母集団から除外 |
| **前処理** | `special_site_type_exclusion_reason` | 特殊SiteType除外理由 | 除外対象地点に除外理由を付与する | `c04` | `ko_single_phase_exclusive_mortuary_site_type` など |
| **前処理** | `has_special_site_type_flag` | 複合特殊SiteTypeフラグ | 除外カテゴリを含むが、集落・包蔵地・散布地なども含む地点を主分析に残してフラグ付けする | `c04`, `c06`, `c10` | New/Drop解釈時の補助属性 |
| **前処理** | `has_special_function_site_type` | 特殊機能カテゴリフラグ | 城館・居館・窯・生産・工房系などを、除外せず後続分析で抽出可能にする | `c04`, `c06`, `c10` | 土地利用変化の特徴量として保持 |
| **前処理** | `special_site_type_class` | 特殊SiteType分類 | 特殊カテゴリの種類を `kofun_mortuary`、`mound_burial_or_isolated`、`special_function` などで記録する | `c04`, `c06`, `c10` | `place_continuity_summary.csv` にも保持 |
| **前処理** | `special_category_exclusion_summary` | 特殊カテゴリ除外数の記録 | 特殊SiteTypeにより除外された地点数を集計する | `c04`, `c18` | `special_category_exclusion_summary.csv` に出力 |
| **前処理** | `special_category_excluded_points` | 特殊カテゴリ除外地点の保存 | 主分析から除外した地点のID、SiteType、Chronology、除外理由を保存する | `c04`, `c16`, `c17`, `c18` | CSVおよびGPKGレイヤに出力 |
| **前処理** | `special_function_flagged_points` | 特殊機能フラグ地点の保存 | 主分析に残すが、特殊機能を持つ地点を保存する | `c04`, `c16`, `c17`, `c18` | CSVおよびGPKGレイヤに出力 |
| **前処理** | `chronology_phase_table` | 時期順序表 | `Pa → Jo → Ya → Ko → Na → He → Me` の順序表を作成する | `c05` | `chronology_phase_table.csv` に出力 |
| **前処理** | `phase_value_table` | 解析用wide表の前処理 | 対象7列を数値化し、欠損を0に置換する | `c06` | `0` は `apparent_absence` として扱う |
| **前処理** | `place_phase_long` | 地点×時期long表 | 各地点・各時期について `presence` / `apparent_absence` を縦持ちで記録する | `c06`, `c18` | `place_phase_long.csv` に出力 |
| **前処理** | `place_phase_sequence_wide` | 地点別sequence wide表 | 地点を1行、`Pa`〜`Me`を列として、時系列パターンを横持ちで記録する | `c06`, `c18` | `place_phase_sequence_wide.csv` に出力 |
| **前処理** | `presence_status` | 出現状態ラベル | `presence == 1` を `presence`、それ以外を `apparent_absence` とする | `c06`, `c14` | 実在の不在確認ではなく、記録上の不在 |
| **前処理** | `sequence_string` | 7時期系列文字列 | `Pa`〜`Me` のpresence/apparent_absence系列を7桁の0/1文字列として表す | `c06`, `c11` | 例：`0100000` |

    ### 2-2. **単純指標分析**：連続性基本指標の定量化
        - 目的：各地点の出現期間・連続性・断続性を基本指標として定量化する
        - 達成目標：presence_phase_count` `longest_run_length` `gap_count・continuity_index` 等が全地点について算出されている
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **単純指標** | `place_run_events` | Run-length analysis | 地点ごとの連続出現区間を抽出する | `c07`, `c10`, `c15`, `c18` | `place_run_events.csv` に出力 |
| **単純指標** | `run_count` | run数 | 各地点にいくつの連続出現区間があるかを数える | `c07`, `c10` | 連続性分類の基礎指標 |
| **単純指標** | `longest_run_length` | 最長連続出現時期数 | 各地点における最長の連続出現時期数を算出する | `c07`, `c10` | `continuous_2phase` などの判定に使用 |
| **単純指標** | `mean_run_length` | 平均run長 | 各地点のrun長の平均を算出する | `c07`, `c10` | 補助指標 |
| **単純指標** | `place_gap_events` | Gap analysis | 出現run間に挟まる `apparent_absence` 区間を抽出する | `c09`, `c10`, `c15`, `c18` | `place_gap_events.csv` に出力 |
| **単純指標** | `gap_count` | gap数 | 出現runの間に挟まるgapの数を数える | `c09`, `c10` | 単独の前後空白はgapに含めない |
| **単純指標** | `gap_phase_count` | gap総時期数 | 出現run間に挟まるgapの総時期数を算出する | `c09`, `c10` | `gap_penalty` の計算に使用 |
| **単純指標** | `max_gap_length` | 最大gap長 | 出現run間に挟まる最大gap長を算出する | `c09`, `c10` | gapがない地点は0 |
| **単純指標** | `mean_gap_length` | 平均gap長 | 各地点のgap長の平均を算出する | `c09`, `c10` | 補助指標 |
| **単純指標** | `presence_phase_count` | 出現時期数 | 各地点について、7時期中いくつの時期に出現記録があるかを数える | `c10` | 最も基本的な出現量指標 |
| **単純指標** | `first_presence_phase` | 初出時期 | 最初に出現記録がある時期を取得する | `c10` | `first_presence_order` も併記 |
| **単純指標** | `last_presence_phase` | 終出時期 | 最後に出現記録がある時期を取得する | `c10` | `last_presence_order` も併記 |
| **単純指標** | `observed_span_phase_count` | 記録上の存続幅 | 初出時期から終出時期までの時期幅を算出する | `c10` | 間にgapを含む |
| **単純指標** | `presence_density_in_span` | 存続幅内の出現密度 | 初出〜終出の範囲内で、どの程度連続的に出現しているかを評価する | `c10` | `presence_phase_count / observed_span_phase_count` |
| **単純指標** | `longest_run_ratio_in_span` | 存続幅内の最長run比率 | 存続幅に対する最長runの割合を算出する | `c10` | `longest_run_length / observed_span_phase_count` |
| **単純指標** | `gap_penalty` | gapによる減点指標 | 存続幅内に占めるgapの大きさを評価する | `c10` | `continuity_index` の構成要素 |
| **単純指標** | `continuity_index` | 継続性総合指標 | 出現時期数、最長run比率、gapペナルティから継続性スコアを作る | `c10`, `c13`, `c18` | `place_continuity_summary.csv` とヒストグラムに出力 |
| **単純指標** | `intermittency_index` | 断続性指標 | gapの大きさに基づいて断続性を評価する | `c10` | `presence_phase_count <= 1` では0 |

    2-3. **継続性分類**：時系列動態の類型化
        - 目的：地点ごとの時系列パターンを類型化し、継続・短期・断続・再出現を比較可能にする
        - 達成目標：`sequence_class` と `sequence_cluster_id` により各地点の利用履歴が分類されている
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **継続性分類** | `sequence_class_definition` | 分類基準表 | sequence class codeと分類条件を明示する | `c10`, `c18` | `sequence_class_definition.csv` に出力 |
| **継続性分類** | `sequence_class_code` | 数値分類 | sequence classに数値コードを付与する | `c10` | 0〜6 |
| **継続性分類** | `sequence_class` | 継続性分類ラベル | 出現時期数、最長run、gap数に基づいて地点を分類する | `c10`, `c13`, `c18` | `place_continuity_summary.csv` に出力 |
| **継続性分類** | `no_presence` | 出現なし | 対象7時期に出現記録がない地点 | `c10`, `c13` | `sequence_class_code = 0` |
| **継続性分類** | `single_phase` | 短期利用型 | 出現記録が1時期のみの地点 | `c10`, `c13` | `sequence_class_code = 1` |
| **継続性分類** | `continuous_2phase` | 短期連続型 | gapなし、かつ最長runが2時期 | `c10`, `c13` | `sequence_class_code = 2` |
| **継続性分類** | `continuous_3_4phase` | 中期連続型 | gapなし、かつ最長runが3〜4時期 | `c10`, `c13` | `sequence_class_code = 3` |
| **継続性分類** | `continuous_5_7phase` | 長期連続型 | gapなし、かつ最長runが5〜7時期 | `c10`, `c13` | `sequence_class_code = 4` |
| **継続性分類** | `intermittent_gap1` | 断続型 | 出現runの間に1回のgapを挟む | `c10`, `c13` | `sequence_class_code = 5` |
| **継続性分類** | `intermittent_gap2plus` | 複数断続型 | 出現runの間に2回以上のgapを挟む | `c10`, `c13` | `sequence_class_code = 6` |
| **継続性分類** | `recurrent` | 再出現型 | 出現後にgapを挟み、再び出現する地点を分類する | `c10` | 現行では `intermittent_gap1` / `intermittent_gap2plus` として表現 |
| **継続性分類** | `unknown_dominated` | 不明優勢型 | unknownが多い地点を分類する |  | 現行Rmdでは `unknown` 状態を使わないため未実装 |
| **継続性分類** | `sequence_dist` | Jaccard距離 | 各地点の出現時期の重なり具合を距離として計算する | `c11` | `stats::dist(..., method = "binary")` |
| **継続性分類** | `sequence_cluster_id` | Sequence clustering | 7時期のpresence/apparent_absence系列をJaccard距離でクラスタリングする | `c11`, `c14`, `c18` | `place_sequence_cluster.csv` に出力 |
| **継続性分類** | `sequence_string_7digit` | 7桁系列文字列 | 先頭0を補った7桁の系列文字列 | `c11` | 表計算ソフトでの表示確認用 |
| **継続性分類** | `sequence_phases` | 出現時期ラベル | `sequence_string_7digit` のうち、1に該当する時期をラベル化する | `c11` | 例：`Jo-Ya` |
| **継続性分類** | `cluster_phase_profile` | クラスタ別時期プロファイル | 各クラスタについて、時期別presence率を算出する | `c11`, `c14`, `c18` | `cluster_phase_profile.csv` と `figure_cluster_phase_profile.png` に出力 |
| **継続性分類** | `sequence_cluster_summary` | クラスタ要約 | クラスタごとの地点数、代表系列、平均継続性指標をまとめる | `c11`, `c18` | `sequence_cluster_summary.csv` に出力 |

    2-4. **時期遷移**：時系列動態の定量化
        - 目的：隣接時期間における継続・消失・出現の変化を定量化する
        - 達成目標：各時期ペアについて `continue` `drop` `new` の件数と構成比が算出されている
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **時期遷移** | `phase_presence_summary` | 時期別出現数 | 各時期のpresence地点数とpresence率を集計する | `c12`, `c13`, `c18` | `phase_presence_summary.csv` と棒グラフに出力 |
| **時期遷移** | `phase_transition_long` | 隣接時期間の遷移long表 | 各地点について、隣接する時期間の `1→1`, `1→0`, `0→1`, `0→0` を作成する | `c12` | 集計前の中間データ |
| **時期遷移** | `transition` | 遷移分類 | 隣接時期間の状態変化を `continue`, `drop`, `new`, `apparent_absence_continue` に分類する | `c12`, `c13` | `phase_transition_summary.csv` ではactive transitionのみ表示 |
| **時期遷移** | `continue` | 継続遷移 | 前時期も次時期もpresenceである状態 | `c12`, `c13` | `1 → 1` |
| **時期遷移** | `drop` | 記録上の消失 | 前時期がpresence、次時期がapparent_absenceである状態 | `c12`, `c13` | `1 → 0` |
| **時期遷移** | `new` | 記録上の出現・再出現 | 前時期がapparent_absence、次時期がpresenceである状態 | `c12`, `c13` | `0 → 1` |
| **時期遷移** | `apparent_absence_continue` | 記録上の不在継続 | 前後時期ともapparent_absenceである状態 | `c12` | `phase_transition_summary.csv` では構成比から除外 |
| **時期遷移** | `apparent_absence_continue_excluded` | 除外された不在継続数 | active transition集計から除外した `0→0` 件数を保持する | `c12` | `phase_transition_summary.csv` に補足列として保持 |
| **時期遷移** | `active_total_places` | active transition分母 | `continue + drop + new` の合計を分母として記録する | `c12`, `c13` | `apparent_absence_continue` は分母から除外 |
| **時期遷移** | `transition_rate_active` | active transition率 | `continue / drop / new` の構成比を、active transition内で算出する | `c12`, `c13` | `figure_phase_transition_summary.png` に使用 |
| **時期遷移** | **Markov transition model** | 時期遷移確率 | ある時期に存在した地点が次時期も継続する確率を推定する |  | 未実装 |
| **時期遷移** | **Change-point detection** | 大きな転換期の検出 | ある地点で利用様式が変わる時期を検出する |  | 未実装 |

    2-5. **図化**：解析結果の可視化
        - 目的：時系列パターンと継続性指標を視覚化し、全体傾向と特徴的な変化を把握する
        - 達成目標：時期別出現数・分類構成・継続性分布・遷移・系列パターンが図として確認できる
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **図化** | `figure_phase_presence_count.png` | 時期別出現数図 | `Pa`〜`Me` 各時期のpresence地点数を棒グラフ化する | `c13`, `c18` | `figure/` に出力 |
| **図化** | `figure_sequence_class_count.png` | sequence class構成図 | `sequence_class` ごとの地点数を棒グラフ化する | `c13`, `c18` | 分類基準は `sequence_class_definition.csv` を参照 |
| **図化** | `figure_continuity_index_histogram.png` | 継続性指標分布 | `continuity_index` の分布をヒストグラムで確認する | `c13`, `c18` | `figure/` に出力 |
| **図化** | `figure_phase_transition_summary.png` | active transition図 | `continue / drop / new` のactive構成比を隣接時期間ごとに表示する | `c13`, `c18` | `apparent_absence_continue` は除外 |
| **図化** | `figure_sequence_heatmap.png` | sequence heatmap | 地点×時期のpresence/apparent_absence系列をヒートマップで表示する | `c14`, `c18` | 地点はクラスタ・分類・継続性順に並べる |
| **図化** | `figure_cluster_phase_profile.png` | クラスタ別時期プロファイル図 | 各sequence clusterの時期別presence率を折れ線で表示する | `c14`, `c18` | facetラベルに代表的な出現時期を表示 |
| **図化** | `figure_run_gap_timeline.png` | run/gap timeline | 各地点のrunとgapを時期軸上に表示する | `c15`, `c18` | gapは出現run間の `apparent_absence` |

    2-6. **空間化**：解析結果のgpkg出力
        - 目的：継続性・断続性・特殊SiteTypeを地理的位置と結び付けて評価可能にする
        - 達成目標：地点別・時期別・run/gap別の分析結果がGISレイヤとして出力されている
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **空間化** | `chronology_place_continuity_sf` | 地点別評価レイヤ | `Lon`, `Lat` から地点レイヤを作成し、継続性指標と特殊SiteTypeフラグを付与する | `c16`, `c17`, `c18` | `chronology_place_continuity` としてGPKG出力 |
| **空間化** | `chronology_place_phase_presence_sf` | 地点×時期レイヤ | 地点×時期のpresence/apparent_absenceをgeometry付きで出力する | `c16`, `c17`, `c18` | `chronology_place_phase_presence` としてGPKG出力 |
| **空間化** | `chronology_run_events_sf` | run eventレイヤ | run単位の情報を地点geometry付きで出力する | `c16`, `c17`, `c18` | `chronology_run_events` としてGPKG出力 |
| **空間化** | `chronology_gap_events_sf` | gap eventレイヤ | gap単位の情報を地点geometry付きで出力する | `c16`, `c17`, `c18` | `chronology_gap_events` としてGPKG出力 |
| **空間化** | `special_category_excluded_points_sf` | 特殊カテゴリ除外地点レイヤ | 主分析から除外した特殊SiteType地点を地点レイヤとして出力する | `c16`, `c17`, `c18` | `special_category_excluded_points` としてGPKG出力 |
| **空間化** | `special_function_flagged_points_sf` | 特殊機能フラグ地点レイヤ | 主分析に残した特殊機能カテゴリ地点を地点レイヤとして出力する | `c16`, `c17`, `c18` | `special_function_flagged_points` としてGPKG出力 |
| **空間化** | 10mグリッドへの集計 | 継続型分布の作成 | 分類結果を10mグリッド単位に集計する |  | 未実装 |
| **空間化** | 調査区への集計 | 調査区単位の評価 | 分類結果を調査区ポリゴンに集計する |  | 未実装 |
| **空間化** | 遺跡範囲への集計 | 遺跡単位の評価 | 分類結果を遺跡範囲ポリゴンに集計する |  | 未実装 |
| **空間化** | 継続型分布 | 分布図作成 | continuous系クラスの地点・区域を抽出して地図化する |  | 未実装 |
| **空間化** | 断続型分布 | 分布図作成 | intermittent系クラスの地点・区域を抽出して地図化する |  | 未実装 |
| **空間化** | 再出現型分布 | 分布図作成 | recurrent相当の地点・区域を抽出して地図化する |  | 未実装 |
| **空間化** | 短期利用型分布 | 分布図作成 | single_phase地点・区域を抽出して地図化する |  | 未実装 |
| **空間化** | **Spatio-temporal autocorrelation** | 空間＋時間の連続性 | 近接地点が同時期または隣接時期に連続するかを見る |  | 未実装 |
| **空間化** | **Time-sliced kernel density** | 時期別分布変化 | 各時期の密度面を作り、中心の移動・拡大・縮小を見る |  | 未実装 |
| **空間化** | **Space-time cube** | 時空間可視化 | 地点×時期を3次元的に積み上げて継続・断絶を確認する |  | 未実装 |

    2-7. **出力確認**
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **出力確認** | `output_check` | 出力確認 | CSV、図、GPKGレイヤが作成されたか確認する | `c18` | `output_check.csv` に出力 |


    2-8. **高度分析**
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **高度分析** | **Hidden Markov Model** | 観測不完全性の補正 | 未調査・未検出を考慮して、潜在的な継続状態を推定する |  | 未実装 |
| **高度分析** | **Survival analysis** | 遺跡の存続期間 | 遺跡・活動痕跡がいつまで継続するかを生存時間として扱う |  | 未実装 |
| **高度分析** | **Dynamic Time Warping** | 類似履歴の比較 | 少し時期がずれた地点間の類似した利用パターンを比較する |  | 未実装 |
| **高度分析** | **Bayesian chronological model** | 年代幅の不確実性処理 | 各時期幅や年代比定の曖昧さを確率的に扱う |  | 未実装 |
