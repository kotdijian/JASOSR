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
| **前処理** | `em_only_flag` | EMのみ地点の除外 | `EM` のみに該当し、他の年代列に記録がない地点を主分析から除外する | `c03` | `em_only_exclusion_summary.csv` に件数を記録 |
| **前処理** | `site_type_tokens` | SiteTypeカテゴリ分解 | `SiteType` を複数カテゴリに分解し、排他的特殊カテゴリか複合カテゴリかを判定する | `c04` | `古墳群+集落` などの複合SiteTypeを誤除外しない |
| **前処理** | `ko_single_phase` | 古墳時代単独地点判定 | 対象7時期のうち `Ko` のみに出現する地点を判定する | `c04` | 古墳・横穴墓等の除外条件に使用 |
| **前処理** | `me_single_phase` | 中世単独地点判定 | 対象7時期のうち `Me` のみに出現する地点を判定する | `c04` | 塚・墓地・板碑出土地等の除外条件に使用 |
| **前処理** | `exclude_special_site_type` | 排他的特殊SiteType除外 | `Ko`単独の古墳系、または`Me`単独の塚・墓地系で、SiteTypeが排他的特殊カテゴリのみの地点を除外する | `c04` | 複合SiteTypeは除外しない |
| **前処理** | `has_special_site_type_flag` | 複合特殊SiteTypeフラグ | 除外カテゴリを含む複合地点を主分析に残して識別可能にする | `c04`, `c06`, `c09` | New/Drop等の解釈用補助属性 |
| **前処理** | `has_special_function_site_type` | 特殊機能カテゴリフラグ | 城館・居館・窯・生産・工房等を除外せず識別可能にする | `c04`, `c06`, `c09` | 土地利用・機能転換の解釈に利用 |
| **前処理** | `special_category_excluded_points` | 除外地点保存 | 特殊SiteTypeにより主分析から除外した地点を保存する | `c04`, `c14`, `c21`, `c22` | CSVおよびGPKGレイヤに出力 |
| **前処理** | `special_site_type_flagged_points` | 特殊SiteType保持地点保存 | 除外せず特殊SiteTypeフラグを付けた地点を保存する | `c04`, `c14`, `c21`, `c22` | CSVおよびGPKGレイヤに出力 |
| **前処理** | `special_function_flagged_points` | 特殊機能地点保存 | 城館・居館・窯・生産・工房等の地点を保存する | `c04`, `c14`, `c21`, `c22` | CSVおよびGPKGレイヤに出力 |
| **前処理** | `chronology_phase_table` | 時期順序表 | 7時期のコード・順序・日英ラベルを作成する | `c05` | `chronology_phase_table.csv` に出力 |
| **前処理** | `place_phase_long` | 地点×時期long表 | 各地点・各時期のpresence/apparent_absenceを縦持ちで記録する | `c06` | `place_phase_long.csv` に出力 |
| **前処理** | `place_phase_sequence_wide` | 地点別時系列wide表 | 地点を1行、7時期を列として0/1系列を保持する | `c06` | `place_phase_sequence_wide.csv` に出力 |
| **前処理** | `presence_status` | 出現状態ラベル | `1 = presence`, `0 = apparent_absence` として扱う | `c06` | 0は実在の不在を意味しない |

   ### 2-2. 単純指標分析：連続性基本指標の定量化
    
   - 目的：各地点の出現期間・連続性・断続性を基本指標として定量化する
   - 達成目標：presence_phase_count` `longest_run_length` `gap_count・continuity_index` 等が全地点について算出されている
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **単純指標** | `place_run_events` | Run-length analysis | 地点ごとの連続出現区間を抽出する | `c07` | `place_run_events.csv` に出力 |
| **単純指標** | `run_count` | run数 | 地点ごとの連続出現区間数を数える | `c07`, `c09` | 連続性分類の基礎指標 |
| **単純指標** | `longest_run_length` | 最長連続出現時期数 | 地点ごとの最長runを算出する | `c07`, `c09` | 空間クラスタ分析対象にも使用 |
| **単純指標** | `place_gap_events` | Gap analysis | 出現run間に挟まるapparent_absence区間を抽出する | `c08` | `place_gap_events.csv` に出力 |
| **単純指標** | `gap_count` | gap数 | 出現run間に挟まるgapの数を算出する | `c08`, `c09` | 前後端のabsenceはgapに含めない |
| **単純指標** | `gap_phase_count` | gap総時期数 | run間gapの総時期数を算出する | `c08`, `c09` | `gap_penalty` の構成要素 |
| **単純指標** | `max_gap_length` | 最大gap長 | 地点ごとの最大gap長を算出する | `c08`, `c09` | 空間クラスタ分析対象にも使用 |
| **単純指標** | `presence_phase_count` | 出現時期数 | 7時期中のpresence時期数を算出する | `c09` | 基本的な利用幅指標 |
| **単純指標** | `first_presence_phase` | 初出時期 | 最初にpresenceとなる時期を取得する | `c09` | `first_presence_order` も保持 |
| **単純指標** | `last_presence_phase` | 終出時期 | 最後にpresenceとなる時期を取得する | `c09` | `last_presence_order` も保持 |
| **単純指標** | `observed_span_phase_count` | 記録上の存続幅 | 初出から終出までの時期幅を算出する | `c09` | 間のgapを含む |
| **単純指標** | `presence_ratio_all_phases` | 全期間出現比率 | 7時期全体に対するpresence時期数の比率を算出する | `c09` | 改訂Continuity indexの構成要素 |
| **単純指標** | `longest_run_ratio_all_phases` | 全期間最長run比率 | 7時期全体に対する最長runの比率を算出する | `c09` | 改訂Continuity indexの構成要素 |
| **単純指標** | `gap_penalty` | gap減点指標 | 初出〜終出区間に含まれるgapの割合を評価する | `c09` | `continuity_index`, `intermittency_index` に使用 |
| **単純指標** | `continuity_index` | 継続性総合指標 | 全期間出現比率、全期間最長run比率、gap-free spanを統合する | `c09`, `c15`, `c16` | 単一時期地点の過大評価を避けるよう式を改訂 |
| **単純指標** | `intermittency_index` | 断続性指標 | gapの大きさから断続性を評価する | `c09`, `c15` | 空間クラスタ分析対象 |

   ### 2-3. 継続性分類：時系列動態の類型化
   - 目的：地点ごとの時系列パターンを類型化し、継続・短期・断続・再出現を比較可能にする
   - 達成目標：`sequence_class` と `sequence_cluster_id` により各地点の利用履歴が分類されている
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **継続性分類** | `sequence_class_definition` | 分類基準表 | sequence class codeと分類条件を明示する | `c09` | `sequence_class_definition.csv` に出力 |
| **継続性分類** | `sequence_class_code` | 数値分類 | sequence classに0〜6の数値コードを付与する | `c09` | 各分類を機械的に扱うためのコード |
| **継続性分類** | `sequence_class` | 継続性分類ラベル | 出現時期数、最長run、gap数に基づいて地点を分類する | `c09` | `place_continuity_summary.csv` に出力 |
| **継続性分類** | `no_presence` | 出現なし | 対象7時期に出現記録がない地点 | `c09`, `c16` | `sequence_class_code = 0` |
| **継続性分類** | `single_phase` | 単一時期利用型 | 出現記録が1時期のみの地点 | `c09`, `c16` | `sequence_class_code = 1` |
| **継続性分類** | `continuous_2phase` | 短期連続型 | gapなし、かつ最長runが2時期の地点 | `c09`, `c16` | `sequence_class_code = 2` |
| **継続性分類** | `continuous_3_4phase` | 中期連続型 | gapなし、かつ最長runが3〜4時期の地点 | `c09`, `c16` | `sequence_class_code = 3` |
| **継続性分類** | `continuous_5_7phase` | 長期連続型 | gapなし、かつ最長runが5〜7時期の地点 | `c09`, `c16` | `sequence_class_code = 4` |
| **継続性分類** | `intermittent_gap1` | 単回断続・再出現型 | 出現runの間に1回のgapを挟む地点 | `c09`, `c16` | `sequence_class_code = 5` |
| **継続性分類** | `intermittent_gap2plus` | 複数断続・再出現型 | 出現runの間に2回以上のgapを挟む地点 | `c09`, `c16` | `sequence_class_code = 6` |
| **継続性分類** | `recurrent` | 再出現概念 | gap後に再びpresenceとなる利用履歴 | `c09` | 独立クラスではなく `intermittent_gap1` / `intermittent_gap2plus` に包含 |
| **継続性分類** | `unknown_dominated` | 不明優勢型 | unknownが多い地点を分類する想定 |  | 現行Rmdではunknown状態を設けていないため未実装 |
| **継続性分類** | `sequence_pattern_cluster` | 系列パターンクラスタ | ユニークな7時期0/1系列をJaccard距離でクラスタリングする | `c10` | 最大128種類の系列パターンを対象 |
| **継続性分類** | `sequence_cluster_id` | 地点への系列クラスタ付与 | 系列パターンクラスタを各地点へ戻して付与する | `c10` | `place_sequence_cluster.csv` に出力 |
| **継続性分類** | `cluster_phase_profile` | クラスタ別時期プロファイル | 各sequence clusterの時期別presence率を算出する | `c10`, `c18` | CSVおよび図に出力 |
| **継続性分類** | `sequence_cluster_summary` | クラスタ要約 | クラスタごとの地点数、代表系列、平均継続性指標をまとめる | `c10` | `sequence_cluster_summary.csv` に出力 |

   ### 2-4. 時期遷移：時系列動態の定量化
   - 目的：隣接時期間における継続・消失・出現の変化を定量化する
   - 達成目標：各時期ペアについて `continue` `drop` `new` の件数と構成比が算出されている
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **時期遷移** | `phase_presence_summary` | 時期別出現量 | 各時期のpresence数・率を集計する | `c11`, `c16` | `phase_presence_summary.csv` に出力 |
| **時期遷移** | `phase_transition_long` | 地点別隣接時期遷移 | 各地点について隣接時期間の状態遷移を作成する | `c11`, `c14`, `c15` | `place_phase_transition_long.csv` に出力 |
| **時期遷移** | `continue` | 継続遷移 | `1 → 1` を表す | `c11` | active transition |
| **時期遷移** | `drop` | 記録上の消失 | `1 → 0` を表す | `c11`, `c15` | Local Join Countによる空間クラスタ分析対象 |
| **時期遷移** | `new` | 記録上の出現・再出現 | `0 → 1` を表す | `c11`, `c15` | Local Join Countによる空間クラスタ分析対象 |
| **時期遷移** | `apparent_absence_continue` | 記録上の不在継続 | `0 → 0` を表す | `c11`, `c12` | active transition集計からは除外、Markovでは保持 |
| **時期遷移** | `transition_rate_active` | active transition率 | `continue / drop / new` 内で構成比を算出する | `c11`, `c13`, `c16` | `0→0` は分母から除外 |
| **時期遷移** | `markov_transition_by_phase` | 時期別Markov遷移確率 | 2状態一次Markovとして各時期ペアの条件付き遷移確率を算出する | `c12`, `c17` | `0→0` を含む全4遷移を使用 |
| **時期遷移** | `markov_transition_overall` | 全時期プールMarkov遷移 | 全時期ペアをプールした条件付き遷移確率を算出する | `c12` | 時期を順序カテゴリとして扱い、等時間幅は仮定しない |
| **時期遷移** | `change_point_candidates` | 転換候補探索 | presence率とactive transition率の単一break候補をSSE改善率で評価する | `c13`, `c17` | 7時期のため探索的スクリーニングとして実装 |
| **時期遷移** | `change_point_best` | 最有力転換候補 | 各系列で最大のchange scoreを持つ候補を抽出する | `c13` | 統計的有意性を意味しない |
| **時期遷移** | `transition` | 遷移分類 | 隣接時期間の状態変化を `continue`, `drop`, `new`, `apparent_absence_continue` に分類する | `c11` | 地点×時期ペア単位で保持 |
| **時期遷移** | `apparent_absence_continue_excluded` | 除外された不在継続数 | active transition集計から除外した `0 → 0` の地点数を記録する | `c11` | `phase_transition_summary.csv` に補足列として保持 |
| **時期遷移** | `active_total_places` | active transition分母 | `continue + drop + new` の合計地点数を各時期ペアの分母として保持する | `c11`, `c16` | `apparent_absence_continue` は含めない |

   ### 2-5. 空間クラスタ：時系列動態の空間分布把握
   - 目的：継続・断続・出現・消失が空間的に集中する領域を統計的に検出する
   - 達成目標：継続性・gap・new/drop について有意な局所クラスタと空間自己相関が識別されている
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **空間クラスタ** | `spatial_neighbor_definition` | 空間近傍定義 | 投影座標上の対称k近傍を空間重みとして定義する | `c15` | 既定 `k = 8`; CRSはEPSG:6677。近傍辺距離のmin/median/maxも記録 |
| **空間クラスタ** | `spatial_neighbor_edges` | 近傍構造の検証 | kNN空間重みの辺と距離を保存し、QGIS上で近傍関係を確認可能にする | `c15`, `c21` | `spatial_neighbor_edges.csv` および `spatial_knn_edges` レイヤ |
| **空間クラスタ** | `spatial_global_moran` | 全体空間自己相関 | `continuity_index`, run, gap, intermittencyのGlobal Moran's Iを算出する | `c15` | `spatial_global_moran.csv` |
| **空間クラスタ** | `spatial_local_lisa` | 局所空間クラスタ | permutation Local Moran's IでHigh-High, Low-Low, High-Low, Low-Highを検出する | `c15`, `c20` | 999条件付きpermutation、局所p値はBH補正 |
| **空間クラスタ** | `spatial_continuity_gap_lisa_sf` | 継続・断続クラスタGIS | LISA結果を地点geometryに結合する | `c15`, `c20`, `c21` | `spatial_continuity_gap_lisa` レイヤ |
| **空間クラスタ** | `spatial_transition_global_moran` | 遷移イベント全体自己相関 | phase pair × `continue/drop/new` の二値イベントにGlobal Moran's Iを算出する | `c15` | 全体的な空間自己相関の診断。`spatial_transition_global_moran.csv` |
| **空間クラスタ** | `spatial_transition_joincount` | 転換イベント局所集中 | phase pair × `drop/new` の二値イベントにLocal Join Countを適用し、同種イベントの局所集中を検出する | `c15`, `c20` | 999条件付きpermutation。イベント比率が0.5を超える場合は局所検定を実施しない |
| **空間クラスタ** | `joincount_class` | 転換集中域分類 | Local Join Count結果を `event_cluster / event_not_significant / non_event` 等に分類する | `c15`, `c20` | event地点のpseudo-p値をBH補正 |

   ### 2-6. 図化：解析結果の可視化
   - 目的：時系列パターンと継続性指標を視覚化し、全体傾向と特徴的な変化を把握する
   - 達成目標：時期別出現数・分類構成・継続性分布・遷移・系列パターンが図として確認できる
        
| **図化** | `figure_phase_presence_count.png` | 時期別出現数図 | 各時期のpresence地点数を表示する | `c16` |  |
| **図化** | `figure_sequence_class_count.png` | sequence class構成図 | 継続性分類ごとの地点数を表示する | `c16` |  |
| **図化** | `figure_continuity_index_histogram.png` | 継続性分布図 | `continuity_index` の分布を表示する | `c16` |  |
| **図化** | `figure_phase_transition_summary.png` | active transition図 | `continue/drop/new` の構成比を時期ペア別に表示する | `c16` | `0→0` は除外 |
| **図化** | `figure_markov_transition_probability.png` | Markov遷移確率図 | 4遷移の条件付き確率を時期ペア別に表示する | `c17` |  |
| **図化** | `figure_change_point_scores.png` | 転換候補図 | change scoreと最有力break候補を表示する | `c17` | 探索的評価 |
| **図化** | `figure_sequence_heatmap.png` | 系列ヒートマップ | 地点×時期のpresence/apparent_absence系列を表示する | `c18` |  |
| **図化** | `figure_cluster_phase_profile.png` | sequence clusterプロファイル | クラスタ別時期presence率を表示する | `c18` |  |
| **図化** | `figure_run_gap_timeline.png` | run/gap timeline | 地点ごとのrunとgapを時期軸上に表示する | `c19` |  |
| **図化** | `figure_spatial_continuity_lisa.png` | 継続性LISA図 | `continuity_index` の局所空間クラスタを表示する | `c20` | 座標がある場合のみ |
| **図化** | `figure_spatial_gap_lisa.png` | 断続性LISA図 | `intermittency_index` の局所空間クラスタを表示する | `c20` | 座標がある場合のみ |
| **図化** | `figure_spatial_transition_joincount.png` | new/drop局所クラスタ図 | 各時期ペアのnew/dropのLocal Join Countクラスタを表示する | `c20` | 座標があり、対象イベントが検定可能な場合に解釈 |

   ### 2-7. 空間化：解析結果のgpkg出力
   - 目的：継続性・断続性・特殊SiteTypeを地理的位置と結び付けて評価可能にする
   - 達成目標：地点別・時期別・run/gap別の分析結果がGISレイヤとして出力されている
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **空間化** | `chronology_place_continuity_sf` | 地点別評価レイヤ | 継続性・断続性・sequence classを地点geometryに結合する | `c14`, `c21` | `chronology_place_continuity` |
| **空間化** | `chronology_place_phase_presence_sf` | 地点×時期レイヤ | presence/apparent_absenceを地点×時期でGIS化する | `c14`, `c21` | `chronology_place_phase_presence` |
| **空間化** | `chronology_phase_transition_sf` | 地点×遷移レイヤ | phase pairごとの `continue/drop/new/0→0` をGIS化する | `c14`, `c21` | `chronology_phase_transition` |
| **空間化** | `chronology_run_events_sf` | run eventレイヤ | run単位の情報を地点geometry付きで出力する | `c14`, `c21` | `chronology_run_events` |
| **空間化** | `chronology_gap_events_sf` | gap eventレイヤ | gap単位の情報を地点geometry付きで出力する | `c14`, `c21` | `chronology_gap_events` |
| **空間化** | `spatial_knn_edges` | 空間重みレイヤ | 空間クラスタ分析に用いたkNN接続を距離属性付き線レイヤで出力する | `c15`, `c21` | 空間分析結果・長距離近傍の検証用 |
| **空間化** | `spatial_continuity_gap_lisa` | LISA結果レイヤ | 継続・gap系指標のLocal Moran結果をGIS化する | `c15`, `c21` |  |
| **空間化** | `spatial_transition_joincount` | 遷移局所クラスタレイヤ | phase pair × `drop/new` のLocal Join Count結果をGIS化する | `c15`, `c21` |  |
| **空間化** | `special_category_excluded_points_sf` | 特殊カテゴリ除外地点レイヤ | 主分析から除外した排他的特殊SiteType地点をGIS化する | `c14`, `c21` | `special_category_excluded_points` としてGPKG出力 |
| **空間化** | `special_site_type_flagged_points_sf` | 特殊SiteType保持地点レイヤ | 除外せず特殊SiteTypeフラグを付与して保持した地点をGIS化する | `c14`, `c21` | `special_site_type_flagged_points` としてGPKG出力 |
| **空間化** | `special_function_flagged_points_sf` | 特殊機能地点レイヤ | 城館・居館・窯・生産・工房等の特殊機能地点をGIS化する | `c14`, `c21` | `special_function_flagged_points` としてGPKG出力 |
| **空間化** | 10mグリッドへの集計 | 継続型分布の作成 | 分類結果を10mグリッド単位に集計する |  | 未実装 |
| **空間化** | 調査区への集計 | 調査区単位の評価 | 分類結果を調査区ポリゴンに集計する |  | 未実装 |
| **空間化** | 遺跡範囲への集計 | 遺跡単位の評価 | 分類結果を遺跡範囲ポリゴンに集計する |  | 未実装 |
| **空間化** | 継続型分布 | 分布図作成 | continuous系クラスの地点・区域を抽出して地図化する |  | 未実装 |
| **空間化** | 断続型分布 | 分布図作成 | intermittent系クラスの地点・区域を抽出して地図化する |  | 未実装 |
| **空間化** | 再出現型分布 | 分布図作成 | recurrent相当の地点・区域を抽出して地図化する |  | 未実装 |
| **空間化** | 短期利用型分布 | 分布図作成 | `single_phase` 地点・区域を抽出して地図化する |  | 未実装 |
| **空間化** | **Spatio-temporal autocorrelation** | 空間＋時間の連続性 | 近接地点が同時期または隣接時期に連続するかを評価する |  | 未実装 |
| **空間化** | **Time-sliced kernel density** | 時期別分布変化 | 各時期の密度面を作り、中心の移動・拡大・縮小を評価する |  | 未実装 |
| **空間化** | **Space-time cube** | 時空間可視化 | 地点×時期を3次元的に積み上げ、継続・断絶を確認する |  | 未実装 |

   ### 2-8. **行動分析**
   - 目的：時系列変化と空間集中を統合し、遺跡利用の継続・中断・再利用・機能転換を解釈する
   - 達成指標：継続域、断続・再利用域、新規出現域、消失域および転換時期が行動パターンとして整理されている
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **行動分析** | 継続利用集中域 | 長期的利用の空間集中 | `continuity_index` のHigh-Highを継続利用の集中候補として読む | `c15`, `c20` | 考古学的解釈は別途必要 |
| **行動分析** | 断続・再利用集中域 | 断続的利用の空間集中 | `intermittency_index` 等のHigh-Highを断続・再利用集中候補として読む | `c15`, `c20` |  |
| **行動分析** | 新規出現集中域 | 活動開始・再編成の空間集中 | `new` のLocal Join Count `event_cluster` を特定時期の新規出現集中候補として読む | `c15`, `c20` | `apparent_absence` の性格に注意 |
| **行動分析** | 消失集中域 | 活動縮小・移動の空間集中 | `drop` のLocal Join Count `event_cluster` を特定時期の消失集中候補として読む | `c15`, `c20` | `drop` は確定的廃絶を意味しない |
| **行動分析** | 遷移持続性 | 時間的な継続傾向 | Markovの `presence → presence` 等の条件付き確率から時期別持続傾向を比較する | `c12`, `c17` | 観測状態Markov model |
| **行動分析** | 転換候補時期 | 時系列レジーム変化の候補 | `change_point_best` からpresence率・遷移率が大きく変わる境界を抽出する | `c13`, `c17` | 探索的指標 |

   ### 2-7. **出力確認**
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **出力確認** | `output_check` | 出力確認 | CSV、図、GPKGレイヤが作成されたか確認する | `c18` | `output_check.csv` に出力 |


   ### 2-8. **高度分析**
        
| 分析フェイズ・カテゴリ | 手法・項目 | 用途 | 簡潔な説明 | 実装chunk番号 | 補足事項 |
|---|---|---|---|---|---|
| **高度分析** | Hidden Markov Model | 観測不完全性の補正 | 未調査・未検出を潜在状態と観測状態に分離して推定する |  | 未実装 |
| **高度分析** | Survival analysis | 存続期間モデル | 活動の継続・終了を生存時間として扱う |  | 未実装 |
| **高度分析** | Dynamic Time Warping | 時期ずれを許容した系列比較 | 少しずれた利用履歴間の類似性を評価する |  | 未実装 |
| **高度分析** | Bayesian chronological model | 年代幅の不確実性処理 | 時期区分・年代比定の不確実性を確率的に扱う |  | 未実装 |
