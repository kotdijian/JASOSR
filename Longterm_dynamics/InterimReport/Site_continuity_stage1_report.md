---
title: "遺跡時期継続性分析 第1段階レポート"
subtitle: "13Tokyo chronology continuity analysis"
date: "2026-08-09"
lang: ja-JP
---

# 0. レポートの位置づけ

本レポートは、`13Tokyo_chronology.csv` を入力とする第1段階の遺跡時期継続性分析について、**実施した分析を省略せず**、分析手順、結果、空間化、行動解釈、改善課題、次期実装案をまとめたものである。本文では要約表と図を中心に示し、詳細な集計値は本文後半および付録に残す。地点単位の全レコードは、対応するCSVおよびGeoPackage (`Continuity.gpkg`) が原データとなる。

解析対象時期は `Pa → Jo → Ya → Ko → Na → He → Me`（旧石器・縄文・弥生・古墳・奈良・平安・中世）の7時期である。各時期の `0` は発掘・調査によって確定した不在ではなく、入力CSV上で当該時期の記録がない **`apparent_absence`** として扱う。

## 0.1 実装・出力の確認

確認用にアップロードされた `Site_continuity_analysis_v2(1).Rmd` は `c01` から `c22` までの構成で、前処理、run/gap、継続性分類、時期遷移、Markov、Change-point、GIS化、空間クラスタ、図化、GPKG出力、出力検証までを一連の処理として実装している。

一方、現状Rmdの `c01` には `utils::package_version("1.2-7")` という名前空間指定ミスが残っている。再実行時には `base::package_version("1.2-7")` または `utils::packageVersion("spdep") < "1.2-7"` に修正する必要がある。本レポートで用いる生成済み成果物については、`output_check(1).csv` 上で **CSV 33件、figure 12件、GPKG layer 11件の計56出力がすべて `ok`** である。


# 1. 分析手順


## 1.1 分析フェイズ

| フェイズ | 説明 |
|---|---|
| **事前準備** | 入力年代情報を7時期に統一し、EM-only地点と排他的特殊SiteType地点を主分析から除外する。複合SiteTypeと特殊機能地点は除外せずフラグとして保持する。除外過程と入力列の利用区分をCSVに記録して再現可能にする。 |
| **簡易指標** | 各地点のpresence時期数、run、gap、初出・終出、存続幅を算出する。これらから `continuity_index` と `intermittency_index` を求める。`0` は確定的不在ではなく `apparent_absence` として扱う。 |
| **継続性分類** | presence時期数、最長run、gap数に基づき7種類の `sequence_class` に分類する。さらに7時期0/1系列をJaccard距離でクラスタリングする。ルールベース分類とデータ駆動クラスタを併用する。 |
| **時期遷移** | 隣接時期ごとに `continue / drop / new / apparent_absence_continue` を判定する。active transition構成比、2状態一次Markov遷移確率、探索的Change-pointで時期間変化を評価する。Markovでは4遷移すべてを保持する。 |
| **空間クラスタ** | 継続性・断続性指標の空間自己相関をGlobal Moran's IとLocal Moran's Iで評価する。`drop / new` はLocal Join Countで局所集中を検定する。空間重みはJGD2011平面直角座標IX系上の対称8-NNで定義する。 |
| **空間化** | 地点別指標、地点×時期、時期遷移、run/gap、特殊SiteType、空間統計結果をGISレイヤ化する。CSVで数値結果、GPKGで位置情報を保持する。QGIS等による再検証を可能にする。 |
| **行動分析** | 時系列変化と空間構造を統合し、継続利用・中断・再利用・新規出現・消失・機能転換の候補を解釈する。Markov、Change-point、sequence、空間クラスタを相互参照する。`apparent_absence` を廃絶や実在的不在と断定しない。 |


![Rmd上のタスクとデータフロー](report_assets/figure_analysis_flow.png)

*図：Rmd上のタスクとデータフロー*

## 1.2 Rmd上のタスクとデータフロー

Rmdのタスクは、`c01-c04` で解析環境と分析母集団を確定し、`c05-c10` で地点別時系列と継続性を構造化、`c11-c13` で時期遷移を評価する。その後 `c14-c15` でGIS化と空間統計を行い、`c16-c20` で図化、`c21` でGPKG、`c22` で全出力を検証する。

主要データフローは以下のとおりである。

1. `13Tokyo_chronology.csv` → EM-only除外 → 特殊SiteType除外/フラグ化
2. 7時期wide/long系列 → run / gap → continuity / intermittency → sequence class
3. 7bit sequence → Jaccard clustering
4. 隣接時期系列 → active transition / Markov / Change-point
5. Lon/Lat → sf → Moran / Local Moran / Local Join Count
6. CSV・PNG・GPKG → 出力検証 → 行動解釈


# 2. 分析結果


## 2.1 事前準備・分析母集団


| 段階             |   地点数 | 説明                                        |
|:-----------------|---------:|:--------------------------------------------|
| 入力             |     6520 | 13Tokyo_chronology.csv 全行                 |
| EM-only除外      |      892 | EMのみで他年代マーカーなし                  |
| EM除外後         |     5628 | 特殊SiteType判定へ                          |
| 特殊SiteType除外 |      597 | Ko単独古墳系529、Me単独塚・墓地系68         |
| 最終分析対象     |     5031 | 時系列分析対象                              |
| 空間分析対象     |     5030 | Lon/Lat有効。1地点（13206000200）は座標欠損 |


![分析母集団の前処理](report_assets/figure_preprocessing_counts.png)

*図：分析母集団の前処理*

入力6,520地点からEM-only 892地点を除外し5,628地点とした。続いて、Ko単独かつ排他的古墳・墳墓系、またはMe単独かつ排他的塚・墓地系に該当する597地点を除外し、**5,031地点**を主分析対象とした。空間分析では有効なLon/Latを持つ5,030地点を利用しており、1地点のみ座標欠損である。

特殊SiteTypeは一律に除外していない。集落等との複合SiteTypeを含む492地点は主分析に残し、うち235地点は城館・居館・窯・生産・工房等の特殊機能カテゴリとして識別可能なフラグを保持した。


| 区分                      |   地点数 | 扱い                         |
|:--------------------------|---------:|:-----------------------------|
| Ko単独・排他的古墳/墳墓系 |      529 | 主分析から除外               |
| Me単独・排他的塚/墓地系   |       68 | 主分析から除外               |
| 特殊SiteTypeを含むが保持  |      492 | 複合SiteType等               |
| 特殊機能カテゴリを保持    |      235 | 城館・居館・窯・生産・工房等 |


![特殊SiteType・特殊機能地点の空間分布](report_assets/map_special_site_types.png)

*図：特殊SiteType・特殊機能地点の空間分布*

### 改善提案

- SiteType辞書を外部定義化し、語彙追加時にも再現可能な分類表として管理する。
- 主要結果について「特殊SiteType除外あり／なし」の感度分析を追加し、除外ルールが時期遷移や空間クラスタに与える影響を定量化する。
- 597除外地点のうち空間化できたのは594地点であるため、座標欠損3地点を別表で管理する。


## 2.2 簡易指標


### 2.2.1 時期別presence


| phase_code   | phase_label_ja   |   presence_count |   apparent_absence_count |   presence_rate (%) |
|:-------------|:-----------------|-----------------:|-------------------------:|--------------------:|
| Pa           | 旧石器           |              694 |                     4337 |               13.79 |
| Jo           | 縄文             |             3846 |                     1185 |               76.45 |
| Ya           | 弥生             |              647 |                     4384 |               12.86 |
| Ko           | 古墳             |             1110 |                     3921 |               22.06 |
| Na           | 奈良             |             1705 |                     3326 |               33.89 |
| He           | 平安             |             2174 |                     2857 |               43.21 |
| Me           | 中世             |              956 |                     4075 |               19    |


![時期別presence地点数](report_assets/figure_phase_presence_count.png)

*図：時期別presence地点数*

時期別presenceは縄文（3,846地点、76.45%）が突出して多い。旧石器694、弥生647、古墳1,110、奈良1,705、平安2,174、中世956地点であり、単純な時期別母数自体が大きく異なる。この差は後続するtransition、Markov、空間統計の解釈に直接影響するため、各時期のpresence率を常に併記する。


![7時期のpresence地点分布](report_assets/map_phase_presence_7panel.png)

*図：7時期のpresence地点分布*

### 2.2.2 Run / Gap


|   run_length |   n_events |
|-------------:|-----------:|
|            1 |       4301 |
|            2 |       1649 |
|            3 |        565 |
|            4 |        179 |
|            5 |         70 |
|            6 |         68 |
|            7 |         52 |


|   gap_length |   n_events |
|-------------:|-----------:|
|            1 |        736 |
|            2 |        807 |
|            3 |        352 |
|            4 |        104 |


![Run length・Gap lengthのイベント分布](report_assets/figure_run_gap_length_distribution.png)

*図：Run length・Gap lengthのイベント分布*

![地点別Run/Gap timeline](report_assets/figure_run_gap_timeline.png)

*図：地点別Run/Gap timeline*

Runイベントは6,884件で、長さ1が4,301件と最多である。Gapイベントは1,999件で、gap長2が807件、gap長1が736件を占める。timelineは各地点のrunと、そのrun間に挟まる `apparent_absence` を示し、系列の断続性を直接確認するための図である。


### 2.2.3 地点別継続性・断続性指標


| metric               |    n |   mean |   median |   min |   max |
|:---------------------|-----:|-------:|---------:|------:|------:|
| presence_phase_count | 5031 |  2.213 |    2     |     0 |   7   |
| run_count            | 5031 |  1.368 |    1     |     0 |   3   |
| longest_run_length   | 5031 |  1.765 |    1     |     0 |   7   |
| gap_count            | 5031 |  0.397 |    0     |     0 |   2   |
| gap_phase_count      | 5031 |  0.76  |    0     |     0 |   4   |
| max_gap_length       | 5031 |  0.738 |    0     |     0 |   4   |
| continuity_index     | 5031 |  0.284 |    0.211 |     0 |   1   |
| intermittency_index  | 5031 |  0.179 |    0     |     0 |   0.8 |


![Continuity indexの分布](report_assets/figure_continuity_index_histogram.png)

*図：Continuity indexの分布*

![Continuity indexの空間分布](report_assets/map_continuity_index.png)

*図：Continuity indexの空間分布*

![Intermittency indexの空間分布](report_assets/map_intermittency_index.png)

*図：Intermittency indexの空間分布*

`continuity_index` の平均は0.284、中央値0.211、最大1.0である。`intermittency_index` は中央値0で、75パーセンタイルで0.4となる。継続性指数は、全7時期に対するpresence比率、全7時期に対する最長run比率、gap-free spanをそれぞれ0.35、0.40、0.25で統合している。したがって単一時期presence地点が高得点にならない設計である。

### 改善提案

- `continuity_index` の重み（0.35 / 0.40 / 0.25）について複数セットを比較し、順位・空間パターンの頑健性を検証する。
- 現行は7時期を等間隔の順序カテゴリとして扱う。実年代幅を考慮したtime-weighted版を別指標として併設する。
- Run/GapについてSiteType別・地域別の分布比較を追加し、集落系と特殊機能系の系列差を確認する。


## 2.3 継続性分類


### 2.3.1 Sequence class


| sequence_class        |   n_places |   rate (%) |
|:----------------------|-----------:|-----------:|
| no_presence           |        146 |       2.9  |
| single_phase          |       2017 |      40.09 |
| continuous_2phase     |        601 |      11.95 |
| continuous_3_4phase   |        192 |       3.82 |
| continuous_5_7phase   |        186 |       3.7  |
| intermittent_gap1     |       1779 |      35.36 |
| intermittent_gap2plus |        110 |       2.19 |


![Sequence class別地点数](report_assets/figure_sequence_class_count.png)

*図：Sequence class別地点数*

![Sequence classの空間分布](report_assets/map_sequence_class.png)

*図：Sequence classの空間分布*

ルールベース分類では `single_phase` が2,017地点（40.09%）で最多、`intermittent_gap1` が1,779地点（35.36%）で続く。2時期以上の連続型は `continuous_2phase` 601、`continuous_3_4phase` 192、`continuous_5_7phase` 186地点である。対象7時期にpresenceがない `no_presence` は146地点で、分析対象外年代の記録やメタデータを持つ地点がここに含まれる。


### 2.3.2 Sequence clustering


| sequence_cluster_id   |   n_places | dominant_sequence_class   |   mean_presence_phase_count |   mean_longest_run_length |   mean_gap_count |   mean_continuity_index | typical_sequence_phases   |
|:----------------------|-----------:|:--------------------------|----------------------------:|--------------------------:|-----------------:|------------------------:|:--------------------------|
| SC0_no_presence       |        146 | no_presence               |                       0     |                     0     |            0     |                   0     | none                      |
| SC1                   |       4361 | intermittent_gap1         |                       2.427 |                     1.913 |            0.457 |                   0.31  | Jo                        |
| SC2                   |         97 | single_phase              |                       1.031 |                     1     |            0.031 |                   0.145 | Ya                        |
| SC3                   |        210 | single_phase              |                       1.081 |                     1.067 |            0.014 |                   0.153 | Me                        |
| SC4                   |        188 | single_phase              |                       1.005 |                     1     |            0.005 |                   0.143 | He                        |
| SC5                   |         29 | single_phase              |                       1.034 |                     1     |            0.034 |                   0.145 | Na                        |


![Sequence clusterの時期別presence profile](report_assets/figure_cluster_phase_profile.png)

*図：Sequence clusterの時期別presence profile*

![地点×時期sequence heatmap](report_assets/figure_sequence_heatmap.png)

*図：地点×時期sequence heatmap*

ユニークな7bit系列パターンは98種類で、これらをJaccard距離・average linkageでクラスタリングし、結果を地点へ戻している。SC1に4,361地点が集中し、代表系列はJoのみである一方、SC2-SC5はそれぞれYa、Me、He、Naを中心とする小規模clusterである。SC0は対象7時期にpresenceを持たない146地点である。

### 改善提案

- `cluster_k = 5` を固定せず、kを変化させたcluster安定性、silhouette等を用いて妥当性を評価する。
- SC1が全体の大部分を占めるため、SC1内部の再クラスタリングまたはsequence頻度を重みとして扱う方法を比較する。
- CSVを表計算ソフトで開く際に7bit系列の先頭0が失われる可能性があるため、`sequence_string_7digit` または時期名列を正式な表示列とする。


## 2.4 時期遷移


### 2.4.1 Active transition


| phase_pair   | transition   |   n_places |   active_total_places |   active_rate (%) |   apparent_absence_continue_excluded |
|:-------------|:-------------|-----------:|----------------------:|------------------:|-------------------------------------:|
| Pa->Jo       | continue     |        634 |                  3906 |             16.23 |                                 1125 |
| Pa->Jo       | drop         |         60 |                  3906 |              1.54 |                                 1125 |
| Pa->Jo       | new          |       3212 |                  3906 |             82.23 |                                 1125 |
| Jo->Ya       | continue     |        549 |                  3944 |             13.92 |                                 1087 |
| Jo->Ya       | drop         |       3297 |                  3944 |             83.6  |                                 1087 |
| Jo->Ya       | new          |         98 |                  3944 |              2.48 |                                 1087 |
| Ya->Ko       | continue     |        358 |                  1399 |             25.59 |                                 3632 |
| Ya->Ko       | drop         |        289 |                  1399 |             20.66 |                                 3632 |
| Ya->Ko       | new          |        752 |                  1399 |             53.75 |                                 3632 |
| Ko->Na       | continue     |        651 |                  2164 |             30.08 |                                 2867 |
| Ko->Na       | drop         |        459 |                  2164 |             21.21 |                                 2867 |
| Ko->Na       | new          |       1054 |                  2164 |             48.71 |                                 2867 |
| Na->He       | continue     |       1485 |                  2394 |             62.03 |                                 2637 |
| Na->He       | drop         |        220 |                  2394 |              9.19 |                                 2637 |
| Na->He       | new          |        689 |                  2394 |             28.78 |                                 2637 |
| He->Me       | continue     |        571 |                  2559 |             22.31 |                                 2472 |
| He->Me       | drop         |       1603 |                  2559 |             62.64 |                                 2472 |
| He->Me       | new          |        385 |                  2559 |             15.04 |                                 2472 |


![隣接時期のactive transition構成比](report_assets/figure_phase_transition_summary.png)

*図：隣接時期のactive transition構成比*

Active transitionでは `0→0` を分母から除外し、`continue / drop / new` の構成を比較する。Pa→Joではnewが82.23%、Jo→Yaではdropが83.60%、Ya→KoとKo→Naではnewがそれぞれ53.75%、48.71%を占める。Na→Heはcontinue 62.03%が優勢で、He→Meはdrop 62.64%が優勢である。


![continueの時期ペア別空間分布](report_assets/map_transition_continue.png)

*図：continueの時期ペア別空間分布*

![dropの時期ペア別空間分布](report_assets/map_transition_drop.png)

*図：dropの時期ペア別空間分布*

![newの時期ペア別空間分布](report_assets/map_transition_new.png)

*図：newの時期ペア別空間分布*

### 2.4.2 Markov transition model


| transition_label                     |   n_transitions |   n_from_state |   probability (%) |
|:-------------------------------------|----------------:|---------------:|------------------:|
| apparent_absence -> apparent_absence |           13820 |          20010 |             69.07 |
| apparent_absence -> presence         |            6190 |          20010 |             30.93 |
| presence -> apparent_absence         |            5928 |          10176 |             58.25 |
| presence -> presence                 |            4248 |          10176 |             41.75 |


![時期ペア別Markov遷移確率](report_assets/figure_markov_transition_probability.png)

*図：時期ペア別Markov遷移確率*

全時期をpoolすると、`apparent_absence → apparent_absence` は69.07%、`apparent_absence → presence` は30.93%、`presence → apparent_absence` は58.25%、`presence → presence` は41.75%である。

時期別には、Pa→Joでpresenceの91.35%がJoでもpresenceである一方、Paでapparent_absenceだった地点の74.06%がJoでpresenceになる。Jo→Yaでは逆にpresenceの85.73%がapparent_absenceへ移行する。Ya→Koはpresence継続55.33%、Ko→Naは58.65%、Na→Heは87.10%と高く、He→Meでは26.26%まで低下する。これはactive transition図と異なり、「前状態を条件とした確率」であるため、両図は別の問いに答える。


### 2.4.3 Change-point screening


| metric               | change_label    |   left_mean |   right_mean |   delta_mean |   change_score |
|:---------------------|:----------------|------------:|-------------:|-------------:|---------------:|
| continue_rate_active | Ya->Ko | Ko->Na |       0.186 |        0.381 |        0.196 |          0.374 |
| new_rate_active      | Ko->Na | Na->He |       0.468 |        0.219 |       -0.249 |          0.197 |
| presence_rate        | Jo | Ya         |       0.451 |        0.262 |       -0.189 |          0.167 |
| drop_rate_active     | Jo->Ya | Ya->Ko |       0.426 |        0.284 |       -0.141 |          0.05  |


![探索的single-break Change-point score](report_assets/figure_change_point_scores.png)

*図：探索的single-break Change-point score*

探索的single-break評価では、最も高いchange scoreは `continue_rate_active` の Ya→Ko | Ko→Na（0.374）である。`new_rate_active` は Ko→Na | Na→He（0.197）、`presence_rate` は Jo | Ya（0.167）、`drop_rate_active` は Jo→Ya | Ya→Ko（0.050）が各系列のbest candidateとなった。いずれも統計的有意な変化点ではなく、SSE改善率に基づく探索的転換候補である。

### 改善提案

- Active transition構成比とMarkov条件付き確率をレポート上で明確に区別する。
- 時期区分の実年代幅を考慮したtransition rateを別途検討する。
- 7点系列では本格的なChange-point推測統計は制約が大きいため、現段階では候補抽出に留める。
- 将来、より細分化した年代情報が得られる場合に多重変化点モデルへ拡張する。


## 2.5 空間クラスタ


### 2.5.1 空間近傍の定義と診断


|   n_points |   requested_k |   effective_k | symmetric_knn   |   analysis_crs |   local_significance_alpha | local_p_adjustment   |   local_permutations |   min_edge_distance_m |   median_edge_distance_m |   max_edge_distance_m |
|-----------:|--------------:|--------------:|:----------------|---------------:|---------------------------:|:---------------------|---------------------:|----------------------:|-------------------------:|----------------------:|
|       5030 |             8 |             8 | True            |           6677 |                       0.05 | BH                   |                  999 |                     0 |                  391.823 |               5225.48 |


![8-NN近傍エッジ距離分布](report_assets/figure_knn_edge_distance_hist.png)

*図：8-NN近傍エッジ距離分布*

![対称8-NN空間重みネットワーク](report_assets/map_knn_network.png)

*図：対称8-NN空間重みネットワーク*

空間分析は5,030地点を対象とし、EPSG:6677上の対称8-nearest-neighborを使用した。近傍エッジ距離の中央値は391.8m、最大5,225.5mである。距離0mのエッジが3本あり、同一座標を共有する地点が6地点・3位置存在する。疎な地域では数kmの近傍接続が生じるため、空間統計結果はこの近傍定義に依存する。


### 2.5.2 継続性・Gap指標のGlobal Moran's I


| metric              |   n_points |   moran_i |   statistic |    p_value |
|:--------------------|-----------:|----------:|------------:|-----------:|
| continuity_index    |       5030 |     0.207 |      32.134 | 7.419e-227 |
| longest_run_length  |       5030 |     0.181 |      28.053 | 1.848e-173 |
| gap_count           |       5030 |     0.182 |      28.255 | 6.22e-176  |
| max_gap_length      |       5030 |     0.156 |      24.265 | 2.291e-130 |
| intermittency_index |       5030 |     0.153 |      23.671 | 3.559e-124 |


![5指標のGlobal Moran's I](report_assets/figure_global_moran_metrics.png)

*図：5指標のGlobal Moran's I*

5指標すべてでGlobal Moran's Iは正で、非常に小さいp値を示す。最も高いのは `continuity_index` 0.207、次いで `gap_count` 0.182、`longest_run_length` 0.181である。したがって分析地域全体としては、類似した継続性・断続性を持つ地点が空間的に近接する傾向が認められる。


### 2.5.3 Local Moran's I


| metric              |   not_significant |
|:--------------------|------------------:|
| continuity_index    |              5030 |
| gap_count           |              5030 |
| intermittency_index |              5030 |
| longest_run_length  |              5030 |
| max_gap_length      |              5030 |


![Continuity indexのLocal Moran's I](report_assets/figure_spatial_continuity_lisa.png)

*図：Continuity indexのLocal Moran's I*

![Intermittency indexのLocal Moran's I](report_assets/figure_spatial_gap_lisa.png)

*図：Intermittency indexのLocal Moran's I*

![5指標のLocal Moran's I局所クラス](report_assets/map_lisa_all_metrics.png)

*図：5指標のLocal Moran's I局所クラス*

999回のconditional permutationとBH多重比較補正を適用したLocal Moran's Iでは、5指標すべてについて5,030地点が `not_significant` となり、BH補正後のHH/LL/HL/LH局所クラスタは検出されなかった。Global Moranでは強い全体自己相関がある一方、個別地点で多重比較補正後に有意な局所クラスタが残らないという結果である。


### 2.5.4 時期遷移イベントのGlobal Moran's I


| phase_pair   | event_type   |   event_count |   event_prevalence (%) |   moran_i |    p_value |
|:-------------|:-------------|--------------:|-----------------------:|----------:|-----------:|
| Pa->Jo       | continue     |           634 |                 12.604 |     0.152 | 3.888e-124 |
| Pa->Jo       | drop         |            60 |                  1.193 |     0.068 | 7.816e-27  |
| Pa->Jo       | new          |          3212 |                 63.857 |     0.185 | 8.303e-181 |
| Jo->Ya       | continue     |           549 |                 10.915 |     0.151 | 1.008e-121 |
| Jo->Ya       | drop         |          3297 |                 65.547 |     0.252 | 0          |
| Jo->Ya       | new          |            98 |                  1.948 |     0.088 | 2.841e-43  |
| Ya->Ko       | continue     |           358 |                  7.117 |     0.143 | 1.568e-109 |
| Ya->Ko       | drop         |           289 |                  5.746 |     0.081 | 2.15e-36   |
| Ya->Ko       | new          |           752 |                 14.95  |     0.16  | 1.251e-135 |
| Ko->Na       | continue     |           651 |                 12.942 |     0.185 | 5.366e-181 |
| Ko->Na       | drop         |           459 |                  9.125 |     0.133 | 1.002e-95  |
| Ko->Na       | new          |          1054 |                 20.954 |     0.228 | 1.655e-274 |
| Na->He       | continue     |          1485 |                 29.523 |     0.276 | 0          |
| Na->He       | drop         |           220 |                  4.374 |     0.16  | 6.153e-137 |
| Na->He       | new          |           689 |                 13.698 |     0.28  | 0          |
| He->Me       | continue     |           571 |                 11.352 |     0.138 | 1.185e-102 |
| He->Me       | drop         |          1603 |                 31.869 |     0.234 | 3.56e-288  |
| He->Me       | new          |           385 |                  7.654 |     0.078 | 2.291e-34  |


![時期遷移イベントのGlobal Moran's I](report_assets/figure_transition_global_moran_heatmap.png)

*図：時期遷移イベントのGlobal Moran's I*

`continue / drop / new` の18組すべてでGlobal Moran's Iは正で統計的に非常に有意である。特にNa→Heのnew (I=0.280)・continue (I=0.276)、Jo→Yaのdrop (I=0.252)、He→Meのdrop (I=0.234)、Ko→Naのnew (I=0.228)が相対的に高い。これは「イベントの地点分布が完全にランダムではない」ことを示すが、局所的な有意クラスタの存在とは同義ではない。


### 2.5.5 Local Join Count: new / drop


| phase_pair   | event_type   | test_status                        | joincount_class       |   n_places |
|:-------------|:-------------|:-----------------------------------|:----------------------|-----------:|
| He->Me       | drop         | tested_local_join_count            | event_not_significant |       1603 |
| He->Me       | drop         | tested_local_join_count            | non_event             |       3427 |
| He->Me       | new          | tested_local_join_count            | event_not_significant |        385 |
| He->Me       | new          | tested_local_join_count            | non_event             |       4645 |
| Jo->Ya       | drop         | not_tested_event_prevalence_gt_0.5 | event_not_tested      |       3297 |
| Jo->Ya       | drop         | not_tested_event_prevalence_gt_0.5 | non_event             |       1733 |
| Jo->Ya       | new          | tested_local_join_count            | event_not_significant |         98 |
| Jo->Ya       | new          | tested_local_join_count            | non_event             |       4932 |
| Ko->Na       | drop         | tested_local_join_count            | event_not_significant |        459 |
| Ko->Na       | drop         | tested_local_join_count            | non_event             |       4571 |
| Ko->Na       | new          | tested_local_join_count            | event_not_significant |       1054 |
| Ko->Na       | new          | tested_local_join_count            | non_event             |       3976 |
| Na->He       | drop         | tested_local_join_count            | event_not_significant |        220 |
| Na->He       | drop         | tested_local_join_count            | non_event             |       4810 |
| Na->He       | new          | tested_local_join_count            | event_not_significant |        689 |
| Na->He       | new          | tested_local_join_count            | non_event             |       4341 |
| Pa->Jo       | drop         | tested_local_join_count            | event_not_significant |         60 |
| Pa->Jo       | drop         | tested_local_join_count            | non_event             |       4970 |
| Pa->Jo       | new          | not_tested_event_prevalence_gt_0.5 | event_not_tested      |       3212 |
| Pa->Jo       | new          | not_tested_event_prevalence_gt_0.5 | non_event             |       1818 |
| Ya->Ko       | drop         | tested_local_join_count            | event_not_significant |        289 |
| Ya->Ko       | drop         | tested_local_join_count            | non_event             |       4741 |
| Ya->Ko       | new          | tested_local_join_count            | event_not_significant |        752 |
| Ya->Ko       | new          | tested_local_join_count            | non_event             |       4278 |


![new/dropのLocal Join Count局所集中](report_assets/figure_spatial_transition_joincount.png)

*図：new/dropのLocal Join Count局所集中*

Local Join Countでは、検定可能なphase pair × eventのすべてでBH補正後の `event_cluster` は検出されなかった。Pa→Joのnew（event prevalence 63.86%）とJo→Yaのdrop（65.55%）は、rare-event向けの実装条件（event prevalence > 0.5）により `not_tested_event_prevalence_gt_0.5` とされた。したがって、「転換イベントは全体として空間自己相関を持つが、現行8-NN・BH補正下では局所有意clusterとして確定しない」というのが第1段階の結果である。

### 改善提案

- k=4 / 8 / 12などのkNN感度分析を必須とする。
- 固定距離bandとkNNを比較し、疎地域での5km超の長距離edgeが結果を歪めていないか検証する。
- 同一座標地点を集約する場合／別地点のまま扱う場合の差を確認する。
- BH補正前・後のLocal Moran結果を診断用に併記する。ただし結論は補正後を基準とする。
- event prevalence > 0.5でLocal Join Countを適用しないケースについて、common-eventとして別の局所二値空間統計を検討する。


## 2.6 空間化


| layer                            |   n_features | crs       |   n_attributes |
|:---------------------------------|-------------:|:----------|---------------:|
| chronology_place_continuity      |         5030 | EPSG:4326 |             55 |
| chronology_place_phase_presence  |        35210 | EPSG:4326 |             34 |
| chronology_phase_transition      |        30180 | EPSG:4326 |             12 |
| chronology_run_events            |         6884 | EPSG:4326 |             10 |
| chronology_gap_events            |         1999 | EPSG:4326 |             12 |
| special_category_excluded_points |          594 | EPSG:4326 |             14 |
| special_site_type_flagged_points |          492 | EPSG:4326 |             16 |
| special_function_flagged_points  |          235 | EPSG:4326 |             16 |
| spatial_knn_edges                |        24274 | EPSG:4326 |              5 |
| spatial_continuity_gap_lisa      |         5030 | EPSG:4326 |             31 |
| spatial_transition_joincount     |        60360 | EPSG:4326 |             11 |


`Continuity.gpkg` には11レイヤが出力されている。地点別継続性、地点×時期presence、地点×時期遷移、run/gap、前処理上の除外・フラグ地点、空間重みネットワーク、LISA、Local Join Countが一つのGPKGにまとめられており、CSV集計結果と空間結果を相互参照できる。

空間化そのものは新しい統計判定を加えるフェイズではなく、前段で算出した属性を位置と結合して検証可能にする工程である。本レポートでは、時期別presence、continue/drop/new、continuity/intermittency、sequence class、特殊SiteType、kNN、LISA、Join Countを地図化して提示した。

### 改善提案

- レポート用地図では東京都境界・主要河川等の最小限の参照地物を追加し、位置解釈を容易にする。
- GPKGのメタデータとして分析日時、Rmdバージョン、パラメータセットを保存する。
- 地点が重なる場合の表示・統計処理方針を明文化する。


## 2.7 行動分析


| 時期ペア   | Active transition            | Markov                                          | 空間証拠                                                            | 行動解釈候補                                                       | 注意                               |
|:-----------|:-----------------------------|:------------------------------------------------|:--------------------------------------------------------------------|:-------------------------------------------------------------------|:-----------------------------------|
| Pa→Jo      | Joが大幅増。active new 82.2% | presence→presence 91.4%、absence→presence 74.1% | newのGlobal Moran I=0.185。Local Join Countはprevalence>0.5で未検定 | 旧石器から縄文への単純継続だけでなく、縄文で広範な地点出現が生じる | Pa/Joの検出率・遺跡定義差に注意    |
| Jo→Ya      | active drop 83.6%            | presence→absence 85.7%                          | drop Global Moran I=0.252。Local Join Countはprevalence>0.5で未検定 | 縄文から弥生で記録地点集合が大幅縮小・再編                         | 年代別母数差が大きい               |
| Ya→Ko      | active new 53.8%             | presence継続55.3%、drop44.7%                    | new I=0.160、drop I=0.081。局所有意clusterなし                      | 弥生から古墳で新規出現と既存地点の再編が併存                       | 古墳系単独地点529を除外した条件下  |
| Ko→Na      | active new 48.7%             | presence継続58.6%、drop41.4%                    | new I=0.228、drop I=0.133。局所有意clusterなし                      | 古墳から奈良への地点再編・新規展開                                 | 特殊SiteType除外の影響を感度分析要 |
| Na→He      | active continue 62.0%        | presence継続87.1%                               | continue I=0.276、new I=0.280。局所有意clusterなし                  | 奈良から平安で高い地点継続性                                       | 全体自己相関と局所非有意の差に注意 |
| He→Me      | active drop 62.6%            | presence→absence73.7%                           | drop I=0.234。局所有意clusterなし                                   | 平安から中世で大きな地点構成再編                                   | Meの特殊SiteType除外条件に注意     |


第1段階の行動分析では、**縄文期の大規模な地点出現、縄文→弥生の大規模drop、弥生→古墳→奈良の再編、奈良→平安の高い継続、平安→中世の再編**という時間構造が明瞭である。一方、Global Moran's Iでは各指標・transitionに空間自己相関が確認されるものの、BH補正後のLocal Moran / Local Join Countでは有意な局所clusterは確定していない。したがって現段階では「地域全体に空間構造がある」ことと、「特定地点群を局所有意clusterとして画定できる」ことを分けて記述する必要がある。

また、`drop` と `new` は人間行動の消滅・創出そのものではなく、入力CSVにおける時期記録の状態変化である。したがって、行動解釈は地形・水系・交通・集落機能・調査密度等の外部変数との照合を経て検証する必要がある。

### 改善提案

- transition clusterを地形・水系・旧河道・段丘・交通路・都市化/調査密度と重ね合わせる。
- 特殊機能フラグ地点を用い、「地点継続」と「機能転換」を分離する。
- 調査機会・発掘密度を説明変数として導入し、`apparent_absence` の観測バイアスを評価する。


# 3. 次期実装提案


## 3.1 優先度A：第1段階結果の頑健性検証

1. **空間近傍感度分析**：k=4/8/12、固定距離band、adaptive distanceを比較する。
2. **Continuity index感度分析**：重みセットとtime-weighted版を比較する。
3. **Sequence clustering安定性評価**：cluster数、距離、linkageの感度を評価する。
4. **特殊SiteType除外感度分析**：除外あり／なしでtransition・Moran・clusterを比較する。
5. **同一座標・座標欠損処理**：重複地点と欠損地点の扱いを明文化する。

## 3.2 優先度B：空間・時空間分析の拡張

- 10m grid、調査区、遺跡範囲への集計
- Time-sliced kernel density
- 時期別密度重心・分布域の移動量
- 本格的なspatio-temporal autocorrelation
- Space-time cube
- transition別hotspot比較
- 地形・水系・交通・土地利用等の説明変数との統合

## 3.3 優先度C：観測不確実性・年代モデル

- **Hidden Markov Model**：観測状態（presence/apparent_absence）と潜在的利用状態を分離する。
- **Survival analysis**：地点利用の継続・終了を存続時間として扱う。
- **Bayesian chronological model**：時期幅と年代比定の不確実性を確率的に扱う。
- **Dynamic Time Warping**：必要な場合に、時期ずれを許容した系列類似度を検討する。

次期実装では、まず新しい高度モデルを増やすよりも、**第1段階で得られたGlobal/Local空間統計の差と、パラメータ依存性を検証することを優先**する。とくに8-NNの距離分布とLocal cluster非検出の頑健性確認が重要である。

# 4. 第1段階のまとめ

第1段階では、6,520地点の入力から分析母集団を5,031地点へ整理し、7時期のpresence/apparent_absence系列からrun、gap、continuity、intermittency、7分類のsequence class、sequence clustering、隣接時期transition、Markov transition、探索的Change-pointを一貫して算出した。さらに5,030地点を対象に空間重みを構築し、継続性・断続性のGlobal/Local Moran's Iと、new/dropのGlobal Moran / Local Join Countを実施した。

時間方向では時期ごとに明確な地点構成変化が確認された。空間方向ではGlobal Moran's Iが一貫して正で強い自己相関を示す一方、BH補正後のLocal Moran's IとLocal Join Countでは局所有意clusterが残らなかった。この差は第2段階で近傍定義・多重比較・重複座標・母数差を含めて重点的に検証する。


# 付録A. 詳細集計表


## A.1 Sequence class定義


|   sequence_class_code | sequence_class        | criterion                                       | interpretation                               |
|----------------------:|:----------------------|:------------------------------------------------|:---------------------------------------------|
|                     0 | no_presence           | presence_phase_count == 0                       | 対象7時期に出現記録なし                      |
|                     1 | single_phase          | presence_phase_count == 1                       | 1時期のみ出現                                |
|                     2 | continuous_2phase     | gap_count == 0 and longest_run_length == 2      | 2時期連続                                    |
|                     3 | continuous_3_4phase   | gap_count == 0 and longest_run_length is 3 or 4 | 3〜4時期連続                                 |
|                     4 | continuous_5_7phase   | gap_count == 0 and longest_run_length is 5 to 7 | 5〜7時期連続                                 |
|                     5 | intermittent_gap1     | gap_count == 1                                  | 出現runの間に1回のapparent_absenceを挟む     |
|                     6 | intermittent_gap2plus | gap_count >= 2                                  | 出現runの間に2回以上のapparent_absenceを挟む |


## A.2 Markov transition（時期ペア別）


| phase_pair   | transition_label                     |   n_transitions |   n_from_state |   probability (%) |
|:-------------|:-------------------------------------|----------------:|---------------:|------------------:|
| Pa->Jo       | apparent_absence -> apparent_absence |            1125 |           4337 |             25.94 |
| Pa->Jo       | apparent_absence -> presence         |            3212 |           4337 |             74.06 |
| Pa->Jo       | presence -> apparent_absence         |              60 |            694 |              8.65 |
| Pa->Jo       | presence -> presence                 |             634 |            694 |             91.35 |
| Jo->Ya       | apparent_absence -> apparent_absence |            1087 |           1185 |             91.73 |
| Jo->Ya       | apparent_absence -> presence         |              98 |           1185 |              8.27 |
| Jo->Ya       | presence -> apparent_absence         |            3297 |           3846 |             85.73 |
| Jo->Ya       | presence -> presence                 |             549 |           3846 |             14.27 |
| Ya->Ko       | apparent_absence -> apparent_absence |            3632 |           4384 |             82.85 |
| Ya->Ko       | apparent_absence -> presence         |             752 |           4384 |             17.15 |
| Ya->Ko       | presence -> apparent_absence         |             289 |            647 |             44.67 |
| Ya->Ko       | presence -> presence                 |             358 |            647 |             55.33 |
| Ko->Na       | apparent_absence -> apparent_absence |            2867 |           3921 |             73.12 |
| Ko->Na       | apparent_absence -> presence         |            1054 |           3921 |             26.88 |
| Ko->Na       | presence -> apparent_absence         |             459 |           1110 |             41.35 |
| Ko->Na       | presence -> presence                 |             651 |           1110 |             58.65 |
| Na->He       | apparent_absence -> apparent_absence |            2637 |           3326 |             79.28 |
| Na->He       | apparent_absence -> presence         |             689 |           3326 |             20.72 |
| Na->He       | presence -> apparent_absence         |             220 |           1705 |             12.9  |
| Na->He       | presence -> presence                 |            1485 |           1705 |             87.1  |
| He->Me       | apparent_absence -> apparent_absence |            2472 |           2857 |             86.52 |
| He->Me       | apparent_absence -> presence         |             385 |           2857 |             13.48 |
| He->Me       | presence -> apparent_absence         |            1603 |           2174 |             73.74 |
| He->Me       | presence -> presence                 |             571 |           2174 |             26.26 |


## A.3 Change-point candidate 全結果


| metric               | change_label    |   left_mean |   right_mean |   delta_mean |   change_score | best_candidate   |
|:---------------------|:----------------|------------:|-------------:|-------------:|---------------:|:-----------------|
| presence_rate        | Jo | Ya         |       0.451 |        0.262 |       -0.189 |          0.167 | True             |
| presence_rate        | Ya | Ko         |       0.344 |        0.295 |       -0.048 |          0.013 | False            |
| presence_rate        | Ko | Na         |       0.313 |        0.32  |        0.007 |          0     | False            |
| presence_rate        | Na | He         |       0.318 |        0.311 |       -0.007 |          0     | False            |
| continue_rate_active | Jo->Ya | Ya->Ko |       0.151 |        0.35  |        0.199 |          0.345 | False            |
| continue_rate_active | Ya->Ko | Ko->Na |       0.186 |        0.381 |        0.196 |          0.374 | True             |
| continue_rate_active | Ko->Na | Na->He |       0.215 |        0.422 |        0.207 |          0.372 | False            |
| drop_rate_active     | Jo->Ya | Ya->Ko |       0.426 |        0.284 |       -0.141 |          0.05  | True             |
| drop_rate_active     | Ya->Ko | Ko->Na |       0.353 |        0.31  |       -0.042 |          0.005 | False            |
| drop_rate_active     | Ko->Na | Na->He |       0.317 |        0.359 |        0.042 |          0.004 | False            |
| new_rate_active      | Jo->Ya | Ya->Ko |       0.424 |        0.366 |       -0.058 |          0.011 | False            |
| new_rate_active      | Ya->Ko | Ko->Na |       0.462 |        0.308 |       -0.153 |          0.084 | False            |
| new_rate_active      | Ko->Na | Na->He |       0.468 |        0.219 |       -0.249 |          0.197 | True             |


## A.4 時期遷移Global Moran's I 全結果


| phase_pair   | event_type   |   event_count |   event_prevalence (%) |   moran_i |    p_value |
|:-------------|:-------------|--------------:|-----------------------:|----------:|-----------:|
| Pa->Jo       | continue     |           634 |                 12.604 |     0.152 | 3.888e-124 |
| Pa->Jo       | drop         |            60 |                  1.193 |     0.068 | 7.816e-27  |
| Pa->Jo       | new          |          3212 |                 63.857 |     0.185 | 8.303e-181 |
| Jo->Ya       | continue     |           549 |                 10.915 |     0.151 | 1.008e-121 |
| Jo->Ya       | drop         |          3297 |                 65.547 |     0.252 | 0          |
| Jo->Ya       | new          |            98 |                  1.948 |     0.088 | 2.841e-43  |
| Ya->Ko       | continue     |           358 |                  7.117 |     0.143 | 1.568e-109 |
| Ya->Ko       | drop         |           289 |                  5.746 |     0.081 | 2.15e-36   |
| Ya->Ko       | new          |           752 |                 14.95  |     0.16  | 1.251e-135 |
| Ko->Na       | continue     |           651 |                 12.942 |     0.185 | 5.366e-181 |
| Ko->Na       | drop         |           459 |                  9.125 |     0.133 | 1.002e-95  |
| Ko->Na       | new          |          1054 |                 20.954 |     0.228 | 1.655e-274 |
| Na->He       | continue     |          1485 |                 29.523 |     0.276 | 0          |
| Na->He       | drop         |           220 |                  4.374 |     0.16  | 6.153e-137 |
| Na->He       | new          |           689 |                 13.698 |     0.28  | 0          |
| He->Me       | continue     |           571 |                 11.352 |     0.138 | 1.185e-102 |
| He->Me       | drop         |          1603 |                 31.869 |     0.234 | 3.56e-288  |
| He->Me       | new          |           385 |                  7.654 |     0.078 | 2.291e-34  |


## A.5 Local Join Count 集計


| phase_pair   | event_type   | test_status                        | joincount_class       |   n_places |
|:-------------|:-------------|:-----------------------------------|:----------------------|-----------:|
| He->Me       | drop         | tested_local_join_count            | event_not_significant |       1603 |
| He->Me       | drop         | tested_local_join_count            | non_event             |       3427 |
| He->Me       | new          | tested_local_join_count            | event_not_significant |        385 |
| He->Me       | new          | tested_local_join_count            | non_event             |       4645 |
| Jo->Ya       | drop         | not_tested_event_prevalence_gt_0.5 | event_not_tested      |       3297 |
| Jo->Ya       | drop         | not_tested_event_prevalence_gt_0.5 | non_event             |       1733 |
| Jo->Ya       | new          | tested_local_join_count            | event_not_significant |         98 |
| Jo->Ya       | new          | tested_local_join_count            | non_event             |       4932 |
| Ko->Na       | drop         | tested_local_join_count            | event_not_significant |        459 |
| Ko->Na       | drop         | tested_local_join_count            | non_event             |       4571 |
| Ko->Na       | new          | tested_local_join_count            | event_not_significant |       1054 |
| Ko->Na       | new          | tested_local_join_count            | non_event             |       3976 |
| Na->He       | drop         | tested_local_join_count            | event_not_significant |        220 |
| Na->He       | drop         | tested_local_join_count            | non_event             |       4810 |
| Na->He       | new          | tested_local_join_count            | event_not_significant |        689 |
| Na->He       | new          | tested_local_join_count            | non_event             |       4341 |
| Pa->Jo       | drop         | tested_local_join_count            | event_not_significant |         60 |
| Pa->Jo       | drop         | tested_local_join_count            | non_event             |       4970 |
| Pa->Jo       | new          | not_tested_event_prevalence_gt_0.5 | event_not_tested      |       3212 |
| Pa->Jo       | new          | not_tested_event_prevalence_gt_0.5 | non_event             |       1818 |
| Ya->Ko       | drop         | tested_local_join_count            | event_not_significant |        289 |
| Ya->Ko       | drop         | tested_local_join_count            | non_event             |       4741 |
| Ya->Ko       | new          | tested_local_join_count            | event_not_significant |        752 |
| Ya->Ko       | new          | tested_local_join_count            | non_event             |       4278 |


# 付録B. 出力ファイル・レイヤ


## B.1 GPKGレイヤ一覧


| layer                            |   n_features | crs       |   n_attributes |
|:---------------------------------|-------------:|:----------|---------------:|
| chronology_place_continuity      |         5030 | EPSG:4326 |             55 |
| chronology_place_phase_presence  |        35210 | EPSG:4326 |             34 |
| chronology_phase_transition      |        30180 | EPSG:4326 |             12 |
| chronology_run_events            |         6884 | EPSG:4326 |             10 |
| chronology_gap_events            |         1999 | EPSG:4326 |             12 |
| special_category_excluded_points |          594 | EPSG:4326 |             14 |
| special_site_type_flagged_points |          492 | EPSG:4326 |             16 |
| special_function_flagged_points  |          235 | EPSG:4326 |             16 |
| spatial_knn_edges                |        24274 | EPSG:4326 |              5 |
| spatial_continuity_gap_lisa      |         5030 | EPSG:4326 |             31 |
| spatial_transition_joincount     |        60360 | EPSG:4326 |             11 |


## B.2 主要CSV

- `em_only_exclusion_summary.csv`
- `input_column_usage_summary.csv`
- `special_category_exclusion_summary.csv`
- `special_category_excluded_points.csv`
- `special_site_type_flag_summary.csv`
- `special_site_type_flagged_points.csv`
- `special_function_flag_summary.csv`
- `special_function_flagged_points.csv`
- `chronology_phase_table.csv`
- `place_phase_long.csv`
- `place_phase_sequence_wide.csv`
- `place_run_events.csv`
- `place_gap_events.csv`
- `place_continuity_summary.csv`
- `sequence_class_definition.csv`
- `sequence_pattern_cluster.csv`
- `place_sequence_cluster.csv`
- `cluster_phase_profile.csv`
- `sequence_cluster_summary.csv`
- `phase_presence_summary.csv`
- `place_phase_transition_long.csv`
- `phase_transition_counts_all.csv`
- `phase_transition_summary.csv`
- `markov_transition_by_phase.csv`
- `markov_transition_overall.csv`
- `change_point_candidates.csv`
- `change_point_best.csv`
- `spatial_neighbor_definition.csv`
- `spatial_neighbor_edges.csv`
- `spatial_global_moran.csv`
- `spatial_local_lisa.csv`
- `spatial_transition_global_moran.csv`
- `spatial_transition_joincount.csv`
- `output_check.csv`

## B.3 Rmd chunk構成

| Chunk | 処理 |
|---|---|
| c01 | setup |
| c02 | paths-and-parameters |
| c03 | read-input-csv-and-exclude-em-only |
| c04 | special-sitetype-exclusions-and-flags |
| c05 | build-phase-table |
| c06 | build-place-phase-tables |
| c07 | run-length-analysis |
| c08 | gap-analysis |
| c09 | continuity-index-and-sequence-class |
| c10 | sequence-clustering |
| c11 | phase-summaries |
| c12 | markov-transition-model |
| c13 | change-point-screening |
| c14 | build-spatial-layers |
| c15 | spatial-cluster-analysis |
| c16 | figures-phase-and-index |
| c17 | figures-markov-and-change-point |
| c18 | figures-sequence-heatmap |
| c19 | figures-run-gap-timeline |
| c20 | figures-spatial-clusters |
| c21 | export-gpkg |
| c22 | check-outputs |

