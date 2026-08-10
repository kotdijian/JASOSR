# 遺跡分布長期動態解析

## 1. このREADMEの位置づけ

本プロジェクトは、東京都の遺跡地点を対象として、旧石器時代から中世までの長期的な出現・継続・中断・再出現・機能転換を、時系列分析・空間統計・自然地理単位・activity domainの組合せによって評価するものである。

本READMEは、最終版Rmdの操作説明だけではなく、**分析手法をどのように検討し、どの方法を採用・修正・不採用としたかを記録する方法論ドキュメント**として位置づける。

したがって、以下では次の3種類を区別する。

- **採用**：現在の分析結果・解釈に用いる手法。
- **探索的に保持**：補助的・比較的には有用だが、主要な結論の根拠とはしない手法。
- **検討後に不採用・置換**：初期段階で検討・実装したが、母集団定義、解釈可能性、データ特性などを理由に最終分析では用いない手法。

最終的な基準実装は以下である。

- activity-domain前処理：`Tokyo_activity_domain_preprocess_v1_3.Rmd`
- 長期動態・空間分析：`Site_continuity_analysis_v3_4_activity_domain_local_spatial.Rmd`
- activity-domain付加済み入力：`13Tokyo_chronology_gsi_landform_activity.csv`
- 最終分析出力ディレクトリ：`Continuity_results_v3_4`

---

## 2. 分析対象と基本的な前提

### 2.1 対象

分析単位は、時代・時期区分を0/1で保持するポイント・フィーチャとしての「遺跡地点」である。

対象7時期は以下とする。

| code | 時期 |
|---|---|
| `Pa` | 旧石器時代 |
| `Jo` | 縄文時代 |
| `Ya` | 弥生時代 |
| `Ko` | 古墳時代 |
| `Na` | 奈良時代 |
| `He` | 平安時代 |
| `Me` | 中世 |

### 2.2 0の意味

`0` は「その時期に遺跡が存在しなかったことが確認された」ことを意味しない。

本分析では、

- `1 = presence`
- `0 = apparent_absence`

として扱う。

したがって、`drop` は確定的な廃絶、`new` は確定的な新規成立を意味せず、**現在の遺跡データにおける記録上の消失・出現**として解釈する。

### 2.3 分析母集団

現行分析では次の処理を行う。

- 座標不正地点を除外する。
- `EM` のみに該当し、対象7時期に出現しない地点を主分析から除外する。
- 古墳・墓・寺社・城館・窯などの特殊なSiteTypeは、原則としてall_land_useから除外しない。
- 特殊用途は後述のactivity domainとして別軸で分類する。

この方針により、**「特殊SiteTypeを除外して一般的土地利用だけを見る」のではなく、まず全土地利用を共通母集団として分析し、その内訳をactivity domainで分解する**。

---

## 3. 分析設計の発展と方針決定

### 3.1 特殊SiteTypeの除外からactivity domainへの転換

#### 初期案

初期段階では、古墳時代単独の古墳・横穴墓、中世単独の塚・墓地などを「特殊SiteType」として主分析から除外し、集落等の一般的土地利用を抽出する方法を検討した。

この方法には、時期別地点数の増減が古墳・墓制・寺社・城館・生産遺跡などの増減によって左右される問題を抑える利点があった。

#### 問題点

しかし、次の問題がある。

1. `集落・古墳` のような複合SiteTypeを一律に除外できない。
2. SiteTypeは遺跡全体の属性であり、その用途がどの時期に対応するかを自動的には決められない。
3. 特殊地点を除外すると、各時代の土地利用構造そのものを分析対象から失う。
4. 古墳→奈良のような時代転換では、古墳の減少と一般活動の展開を分けて見る方が考古学的に有益である。

#### 最終方針

特殊SiteType除外を主要手法とはせず、以下の多ラベルactivity domainを導入した。

- `all_land_use`
- `general_activity`
- `monument`
- `institutional`
- `production`

`SiteType` と `Chronology` は独立情報として扱い、SiteTypeを観測された全時期へ機械的に展開しない。

---

### 3.2 activity-domain前処理の検証

activity-domain分類では、SiteType辞書、ArchaeologicalFeatures辞書、時期対応ルールを用いる。

前処理は監査を通じて段階的に修正した。

#### v1.1で修正した点

- `[古墳時代]住居` の時期タグ「古墳」をmonumentと誤認する問題を修正。
- `貝塚` の「塚」をmonumentと誤認する問題を修正。
- `[奈良・平安時代]` 等の複数時期タグを単一時期へ誤確定しないように修正。

#### v1.2で修正した点

- `temporally_ambiguous` を0に変換せず、時期未確定として保持。
- SiteTypeの括弧内token分割を修正。

#### v1.3で修正した点

- specialized activityの時期帰属が未確定の場合、その不確定性を`general_activity`にも伝播。
- `domain_presence = NA` を `temporally_unresolved` として保持。

#### 最終方針

activity-domainの時期状態は、

- `1 = present`
- `0 = resolved non-presence`
- `NA = temporally_unresolved`

の3状態で保持する。

`NA` はabsenceに変換しない。

---

### 3.3 continuity indexの検討

地点の長期継続性を1つの尺度で比較するため、以下を統合した`continuity_index`を使用する。

- 全7時期に対するpresenceの広がり
- 最長連続run
- 初出〜終出間のgapの少なさ

基準重みは以下とする。

| 構成要素 | 重み |
|---|---:|
| presence coverage | 0.35 |
| longest run | 0.40 |
| gap-free component | 0.25 |

重みを変えた感度分析では順位相関がほぼ1となり、基準重みに強く依存しないことを確認した。

#### 採用しなかった案

各時期を実年代の長さで重み付けする方法も検討対象となったが、対象7区分は年代幅が大きく異なる一方、遺跡データ自体の時間解像度も均一ではない。そのため、**絶対年代幅によるtime weightingは採用しない**。

---

### 3.4 sequence classとsequence clustering

地点ごとの0/1系列を、以下の解釈可能な`sequence_class`に分類する。

- `no_presence`
- `single_phase`
- `continuous_2phase`
- `continuous_3_4phase`
- `continuous_5_7phase`
- `intermittent_gap1`
- `intermittent_gap2plus`

`recurrent`は独立クラスではなく、gap後の再出現を含むintermittent系として扱う。

#### sequence clustering

7時期系列に対するクラスタリングも検討・実装した。

k候補、silhouette、bootstrap stability、cluster imbalance等を比較したが、最良候補でも一つのclusterが大半の地点を占め、考古学的な類型としての解像度が低かった。

そのため、

- `sequence_class` = 主要な類型化
- sequence clustering = 探索的補助分析

とする。

---

### 3.5 Transitionの母集団

隣接時期間の状態変化を以下に分類する。

- `continue = 1 → 1`
- `drop = 1 → 0`
- `new = 0 → 1`
- `apparent_absence_continue = 0 → 0`

#### 初期実装の問題

初期のTransition空間分析では、0→0地点も含む全地点上で局所統計を計算した。

その結果、例えば`new`のcoldspotの大部分が、実際には「変化がなかった0→0地点」で構成される場合があり、考古学的な解釈を誤らせる可能性があった。

#### 修正後の方針

`continue / drop / new` の構成比とTransition空間分析では、0→0を分母から除外する。

さらに空間分析では、**各phase pairごとにactive transition地点だけを抽出し、その部分集合上でkNN近隣を再構築する**。

一方、Markov分析では0→0も観測状態遷移の一部であるため保持する。

このため、

- Active transition = 変化の構成
- Markov = 前状態を条件とした4状態遷移

として役割を分ける。

---

### 3.6 Change-point分析

presence率とactive transition構成について、単一break候補をSSE改善率で探索する。

対象が7大区分しかないため、これは厳密な多重change-point推定ではない。

したがって、

- 転換候補を探索するスクリーニング
- 考古学的に注目すべき画期を比較する補助指標

として使用し、統計的に確定した「変化点」とは表現しない。

---

### 3.7 空間近隣の設定

主分析では対称k-nearest-neighborを用いる。

- k=4
- k=8
- k=12

を毎回計算し、**k=8を基準**とする。

さらにfixed-distance 1,000 / 2,000 / 5,000 mを補助的に計算し、kNNの結果が特定の近隣定義だけに依存していないか確認する。

同一座標に複数地点がある場合は、別地点として扱う分析を主とし、座標集約版を感度分析として確認する。

---

### 3.8 Global Moran / Local Moran / Local G* / Local Join Countの使い分け

#### Global Moran's I

対象変数が全体として空間的自己相関を持つかを確認する。

使用対象：

- continuity index
- run / gap / intermittency
- transition event
- activity-domain event

#### Local Moran's I

連続量について、周囲との類似・異質性を検出する。

主な分類：

- High–High
- Low–Low
- High–Low
- Low–High

主にcontinuity、run、gap、intermittencyの局所構造に用いる。

局所p値はBH補正後の値を主要判定に使用する。

#### Local Getis-Ord G*

`new / drop`等の二値イベントについて、高いevent比率・低いevent比率が局所的にまとまる位置を比較する。

ただし、二値イベントがrareな場合にはLocal G*だけでclusterを確定しない。

#### Local Join Count

rare binary eventの局所的な同種隣接を直接評価する。

最終方針は次のとおり。

| event prevalence | Local G* | Local Join Count |
|---|---|---|
| `> 0.5` | primary | 補助 |
| `<= 0.5` | 比較・図示 | primary |

なお、最終v3.4では全画期を比較するため、prevalenceにかかわらずLocal G*を図化する。ただし解釈上のprimary methodは上表に従う。

---

### 3.9 自然地理単位

自治体境界を主要分析単位とはせず、自然地理単位を主とする。

1. `watersystem`
2. `unit_basin`
3. `watershed_x_landform`

W07単位流域は、

`W07_002 × W07_006`

を識別単位とする。

自治体は補助診断にのみ用いる。

自然地理別比較における上位・下位25%は「明瞭な差」を記述するための記述的閾値であり、推測統計上の有意差を意味しない。

---

### 3.10 Local spatial analysisの最終構成

#### 第1段階：全画期all_land_use

以下の全6画期について、`new / drop`のLocal G*を同一条件で計算・図化する。

- Pa→Jo
- Jo→Ya
- Ya→Ko
- Ko→Na
- Na→He
- He→Me

目的は、activity-domainへ分解する前の共通の空間的背景を提示し、特定画期だけを事後的に選んだように見えることを避けることにある。

#### 第2段階：重点4画期のactivity-domain分析

以下は必ずactivity-domain別局所空間分析を行う。

- Ya→Ko
- Ko→Na
- Na→He
- He→Me

対象domain：

- `general_activity`
- `monument`
- `institutional`
- `production`

比較母集団は、各画期のall_land_use active transition地点とし、そのうち対象domainの前後時期がresolvedな地点だけを用いる。

event数等が少なすぎる場合は、無理に統計量を出さず`not_analyzed_*`として監査出力に残す。

---

### 3.11 Local G*局所集積域の地理的同定

統合レポートでは、Local G*の有意地点数だけでなく、地図上でまとまりを持つ局所集積域を再現可能な方法で同定する。

1. 基準スケールはk=8、局所判定はBH補正後を用いる。
2. 同一phase pair・event・activity domain・Local G classの有意地点を抽出する。
3. 実際に分析に使用したkNN edgeのうち、有意地点同士を結ぶedgeを残す。
4. そのネットワークの連結成分を「Local G*局所集積域」とする。
5. 本文で主要集積域として命名する記述基準は20地点以上とする。これは統計的有意性の閾値ではなく、地誌的解説のための規模基準である。
6. 名称はcluster内の主要河川・水系と自治体名からデータ駆動で付し、GSI地形分類・代表遺跡を併記する。
7. k=12で同じLocal G classに残る地点比率をスケール安定性の補助情報とする。k=4で消失する場合は「より広い近隣スケールで現れる地域差」と解釈する。

Local G*局所集積域は、行政界・文化圏・行動圏の境界を直接表すものではない。とくにevent prevalenceが0.5以下の場合はLocal Join Countをprimaryな局所二値検定とするため、「確定cluster」ではなく「局所集積域」「局所偏在域」と表現する。

## 4. 現行分析フロー

### 4.1 activity-domain前処理

入力：

`13Tokyo_chronology_gsi_landform.csv`

使用：

`Tokyo_activity_domain_preprocess_v1_3.Rmd`

主な出力：

- `13Tokyo_chronology_gsi_landform_activity.csv`
- `activity_preprocessing_audit.csv`
- `activity_phase_status.csv`
- `activity_audit_check.csv`
- `activity_unclassified_sitetype_tokens.csv`
- activity-domain監査summary

### 4.2 主分析

使用：

`Site_continuity_analysis_v3_4_activity_domain_local_spatial.Rmd`

主な分析段階：

1. 入力・除外・metadata確認
2. 7時期presence系列作成
3. run / gap / continuity指標
4. sequence class
5. sequence clustering（探索的）
6. active transition
7. Markov
8. change-point screening
9. continuity指標のGlobal/Local Moran
10. transition Global Moran
11. 全6画期all_land_use Local G*
12. 自然地理単位比較
13. activity-domain別presence / transition / Markov / change-point
14. 重点4画期activity-domain Global Moran / Local G* / Join Count
15. k=4/8/12・fixed-distance等の感度分析
16. CSV / Figure / GPKG出力監査

---

## 5. 現行の主要指標

### 5.1 Run / Gap

| 指標 | 意味 |
|---|---|
| `presence_phase_count` | 7時期中のpresence時期数 |
| `run_count` | 連続出現区間数 |
| `longest_run_length` | 最長連続出現時期数 |
| `gap_count` | run間のgap数 |
| `gap_phase_count` | gapに含まれる総時期数 |
| `max_gap_length` | 最大gap長 |
| `observed_span_phase_count` | 初出から終出までの時期幅 |
| `continuity_index` | presence・run・gapを統合した継続性指標 |
| `intermittency_index` | 断続性指標 |

前後端の0はgapに含めない。

### 5.2 Transition

| transition | 定義 | 解釈 |
|---|---|---|
| `continue` | 1→1 | 記録上の継続 |
| `drop` | 1→0 | 記録上の消失 |
| `new` | 0→1 | 記録上の出現・再出現 |
| `apparent_absence_continue` | 0→0 | 記録上の不在継続 |

active transitionの分母は、

`continue + drop + new`

であり、0→0を含めない。

---

## 6. activity domain

### 6.1 分類

| domain | 概要 |
|---|---|
| `all_land_use` | 対象7時期の全土地利用 |
| `general_activity` | 集落・居住・一般的活動を中心とする土地利用 |
| `monument` | 古墳・横穴墓・一部の墳墓・塚等 |
| `institutional` | 官衙・社寺・城館等 |
| `production` | 窯・鍛冶・製鉄・工房等 |

墓・墓地は自動的にすべて`monument`とはしない。

### 6.2 多ラベル

activity domainは排他的分類ではない。

同一地点・同一時期に、例えば、

- `general_activity = 1`
- `monument = 1`

が同時に成立する場合がある。

したがってdomain別件数の合計はall_land_use件数と一致する必要はない。

### 6.3 時期未確定

`temporally_unresolved`は0として扱わない。

- phase presence率：その時期の分母から除外
- transition / Markov：前後どちらかがNAならpairの分母から除外
- Run / Gap / continuity：7期のいずれかがNAのplace×domain系列は系列全体を除外

---

## 7. 探索的に保持する手法

| 手法 | 現在の位置づけ | 理由 |
|---|---|---|
| sequence clustering | 探索的 | cluster imbalanceが大きく、sequence classより解釈性が低い |
| change-point | 探索的 | 7大区分しかなく、単一break screeningである |
| fixed-distance spatial weights | 感度分析 | kNNの頑健性確認用 |
| coordinate aggregation | 感度分析 | 同一座標レコードを統合した場合の影響確認 |
| Local G* for rare event | 比較・図示 | rare eventのprimary local testはJoin Count |
| municipality-based comparison | 補助診断 | 主要空間単位は自然地理単位とする |

---

## 8. 検討後に主要分析から外した方法・考え方

| 方法・指標 | 判断 | 根拠 |
|---|---|---|
| 特殊SiteTypeの一律除外 | activity domainへ置換 | 時期との対応を自動確定できず、時代固有の土地利用構造も失うため |
| SiteTypeを全観測時期へ展開 | 不採用 | SiteTypeとChronologyは独立情報であり、時期別用途を誤推定するため |
| activity-domainの時期不確定を0化 | 不採用 | false drop/newと人工的gapを生成するため |
| Transition Local analysisを全地点で計算 | 不採用 | 0→0がcoldspot等を支配し得るため |
| 単一kのみで近隣を固定 | 不採用 | 局所統計が近隣スケールに依存するため |
| 絶対年代幅によるtime weighting | 不採用 | 時期区分の時間幅とデータ時間解像度が均質でないため |
| Local G*のみでrare event clusterを確定 | 不採用 | binary rare eventではJoin Countの方が直接的なため |
| sequence clusteringを主要類型とする | 不採用 | 主要clusterへの過度な集中があり、考古学的解釈性が低いため |

---

## 9. 未実装・将来検討

以下は本分析で検討対象となったが、現段階では実装しない。

| 手法 | 想定用途 | 現状 |
|---|---|---|
| Hidden Markov Model | 未調査・未検出を潜在状態と観測状態に分離 | 未実装 |
| Survival analysis | 活動の継続・終了を存続時間として扱う | 未実装 |
| Dynamic Time Warping | 時期ずれを許容した系列類似度 | 未実装 |
| Bayesian chronological model | 年代比定・時期境界の不確実性 | 未実装 |
| Spatio-temporal autocorrelation | 空間＋時間の自己相関 | 未実装 |
| Time-sliced kernel density | 時期別密度面の比較 | 未実装 |
| Space-time cube | 時空間3次元可視化 | 未実装 |
| 10m grid集計 | 分布面・区域分析 | 未実装 |
| 調査区・遺跡範囲ポリゴン集計 | 面単位の評価 | 未実装 |

これらは「分析上不要」と判断したものではなく、現在の7時期ポイントデータで回答する研究課題に対して、現行手法より優先度が低いものとして保留している。

---

## 10. 解釈上の原則

1. `apparent_absence`を実在のabsenceと断定しない。
2. `drop`を廃絶、`new`を成立と機械的に読み替えない。
3. Global Moranは「地域差の存在」、Local statisticsは「その地域差が現れる位置」として区別する。
4. Local MoranのHH/LLを、直ちに文化圏・行動圏とみなさない。
5. Local G*のhot/coldを固定的な考古学的領域とみなさない。
6. rare eventではLocal Join Countをprimaryとする。
7. k=4/8/12の結果が大きく異なる場合、単一の境界線として解釈しない。
8. 自然地理別上位・下位25%は記述的な「明瞭な差」であり、有意差ではない。
9. activity-domainは遺跡の本質的・排他的分類ではなく、時系列変化を分解する分析上の観測カテゴリである。
10. 統計結果はQGIS上で地形、水系、個別遺跡、調査状況と再照合して考古学的に解釈する。

---

## 11. 統合レポートで記述する方法論の流れ

統合レポートでは、最終手法だけを列挙するのではなく、次の順序で分析過程を説明する。

1. **問題設定**  
   遺跡地点の長期的な出現・継続・中断をどのように0/1時系列から記述するか。

2. **基本指標の構築**  
   Run / Gap / continuity index / sequence class。

3. **時代画期の定量化**  
   presence、active transition、Markov、change-point。

4. **方法検証**  
   continuity weight、sequence clustering、Transition denominator、空間近隣の感度。

5. **空間分析手法の選択**  
   Global Moran、Local Moran、Local G*、Local Join Countの役割分担。

6. **自然地理との対応**  
   水系・単位流域・地形による地域差。

7. **SiteType問題とactivity-domain導入**  
   特殊SiteType除外案から多ラベル・時期未確定保持へ至った判断。

8. **全画期空間スクリーニング**  
   all_land_use Local G*による6画期比較。

9. **重点4画期の用途別空間分析**  
   Ya→Ko、Ko→Na、Na→He、He→Me。

10. **総合解釈と限界**  
    長期動態、機能転換、空間スケール、記録上のabsenceの限界。

---

## 12. 出力

現行Rmdは、少なくとも以下を出力する。

### CSV

- 地点×時期presence
- Run / Gap
- continuity summary
- sequence class / clustering
- active transition
- Markov
- change-point
- 自然地理単位別集計
- activity-domain別presence / transition / Markov
- Global Moran / Local Moran
- all_land_use Local G*
- activity-domain Local G* / Join Count
- kNN / fixed-distance diagnostics
- 出力監査

### Figure

- 時期別presence
- sequence class
- continuity index
- active transition
- Markov
- change-point
- run/gap timeline
- Local Moran
- 全6画期Local G*
- activity-domain Local G*
- kNN sensitivity

### GeoPackage

QGIS上で、

- 長期継続性
- 時期別presence
- transition
- activity-domain
- Local Moran
- Local G*
- kNN edges

を個別地点へ戻って検証可能な形で保存する。

---

## 13. 現行の分析上の位置づけ

本分析の目的は、0/1時系列から単一の「継続性値」を作ることだけではない。

最終的には、

**長期的な利用履歴  
→ 時代画期ごとの変化  
→ その空間的な偏り  
→ 自然地理との対応  
→ activity-domainによる機能的分解**

という複数段階を組み合わせ、遺跡分布の長期動態を考古学的に解釈可能な形へ整理することを目的とする。

そのため、途中で検討して採用しなかった手法や、探索的に残した指標も、方法論上の判断過程を示す情報として記録する。
