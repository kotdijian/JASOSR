# NNRICPdigital3-Noguchi2021-table1-3.R
# 10MAR2021 by kotdijian (https://github.com/kotdijian)
# 考古学・文化財地理空間情報のオープンデータ化、整備と活用:表1～3作成Rコード
# 論文タイトル：考古学・文化財地理空間情報のオープンデータ化、整備と活用
# 著者：野口　淳
# 収録書誌：デジタル技術による文化財情報の記録と利活用3
# シリーズ名：奈良文化財研究所研究報告第27冊
# 刊行年月日：2021年3月12日
# R-code for tables 1-3, Distributing and Utilizing Open Geospatial Data in Archaeology and Cultural Heritage Management
# title: Distributing and Utilizing Open Geospatial Data in Archaeology and Cultural Heritage Management
# author: Atsushi Noguchi
# book title: Recording and Utilization of Cultural Property Information via Digital Technologies Table of Contents Vol. 3
# series title: Research Reports of Nara National Research Institute for Cultural Properties, Vol. 27
# published date: 12MAR2021

# package installation and activation: パッケージのインストールとアクティベーション
install.packages("tidyverse")
install.packages("rio")
install.packages("formattable")
library("tidyverse")
library("rio")
library("formattable")

# data imoport: データのインポート
  # TokyoTotalAgeに原データcsvを読み込み、エンコードの指定に注意: 原データ=時代区分整備済み13TokyoAge.csv
  TokyoTotalAge <- import("https://github.com/kotdijian/JASOSR/raw/master/13Tokyo/13TokyoAge.csv", 
                          setclass= "tbl_df", encoding = "UTF-8"
                          )
  #列名変更: "/"(スラッシュ)を除去
  TokyoTotalAge <- rename(TokyoTotalAge, "遺構" = "主な遺構/概要")
  #自治体名の読み込み(more human readable)
  LGC <- import("https://github.com/kotdijian/JASOSR/raw/master/13Tokyo/LGC_13Tokyo.csv",
                setclass= "tbl_df", encoding ="UTF-8" 
                )
  LGC <- rename(LGC,区市町村名 = 名称)

# table_1 summary table of archaeological site in Tokyo by LGC and Age
  # tallying site number by LGC: 自治体別遺跡数集計
  Tokyo.summary <- TokyoTotalAge %>% 
    group_by(自治体コード) %>%  #自治体コードで集約
    count %>%                   #レコード数=遺跡数をカウント
    rename(遺跡数合計 = n) %>%  #集計結果が格納される列の名前をn→遺跡数合計に変更
    arrange(自治体コード) %>%   #自治体コードで昇順に並び替え
    left_join(dplyr::select(LGC,自治体コード,区市町村名), by = "自治体コード") %>%
    dplyr::select(自治体コード, 区市町村名, 遺跡数合計)

  # tallying site by LGC and Age: 時代別集計と追加
    # 旧石器
  Tokyo.summary_t <- TokyoTotalAge %>% 
    group_by(自治体コード, 旧石器時代)　%>%
    filter(旧石器時代 != 0) %>%
    count %>%
    ungroup %>%
    rename("旧石器" = "n") %>%
    dplyr::select(-旧石器時代)
  
  Tokyo.summary1 <- left_join(Tokyo.summary, Tokyo.summary_t, by = "自治体コード")
  
    # 縄文
  Tokyo.summary_t <- TokyoTotalAge %>% 
    group_by(自治体コード, 縄文時代)　%>%
    filter(縄文時代 != 0) %>%
    count %>% 
    ungroup %>%
    rename("縄文" = "n") %>%
    dplyr::select(-縄文時代)
  Tokyo.summary1 <- left_join(Tokyo.summary1, Tokyo.summary_t, by = "自治体コード")
  
    # 弥生
  Tokyo.summary_t <- TokyoTotalAge %>% 
    group_by(自治体コード, 弥生時代)　%>%
    filter(弥生時代 != 0) %>%
    count %>% 
    ungroup %>%
    rename("弥生" = "n") %>% 
    dplyr::select(-弥生時代)
  Tokyo.summary1 <- left_join(Tokyo.summary1, Tokyo.summary_t, by = "自治体コード")
  
    # 古墳
  Tokyo.summary_t <- TokyoTotalAge %>% 
    group_by(自治体コード, 古墳時代)　%>%
    filter(古墳時代 != 0) %>%
    count %>% 
    ungroup %>% 
    rename("古墳" = "n") %>% 
    dplyr::select(-古墳時代)
  Tokyo.summary1 <- left_join(Tokyo.summary1, Tokyo.summary_t, by = "自治体コード")
  
    # 奈良
  Tokyo.summary_t <- TokyoTotalAge %>% 
    group_by(自治体コード, 奈良時代)　%>%
    filter(奈良時代 != 0) %>% 
    count %>%
    ungroup %>%
    rename("奈良" = "n") %>% 
    dplyr::select(-奈良時代)
  Tokyo.summary1 <- left_join(Tokyo.summary1, Tokyo.summary_t, by = "自治体コード")
  
    # 平安
  Tokyo.summary_t <- TokyoTotalAge %>% 
    group_by(自治体コード, 平安時代)　%>%
    filter(平安時代 != 0) %>%
    count %>% 
    ungroup %>% 
    rename("平安" = "n") %>% 
    dplyr::select(-平安時代)
  Tokyo.summary1 <- left_join(Tokyo.summary1, Tokyo.summary_t, by = "自治体コード")
  
    # 中世
  Tokyo.summary_t <- TokyoTotalAge %>% 
    group_by(自治体コード, 中世)　%>%
    filter(中世 != 0) %>%
    count %>% 
    ungroup %>% 
    dplyr::select(-中世) %>%
    rename("中世" = "n") 
  Tokyo.summary1 <- left_join(Tokyo.summary1, Tokyo.summary_t, by = "自治体コード")
  
    # 近世
  Tokyo.summary_t <- TokyoTotalAge %>% 
    group_by(自治体コード, 近世)　%>%
    filter(近世 != 0) %>%
    count %>% 
    ungroup %>% 
    dplyr::select(-近世) %>%
    rename("近世" = "n") 
  Tokyo.summary1 <- left_join(Tokyo.summary1, Tokyo.summary_t, by = "自治体コード")
  
    # 時代不明
  Tokyo.summary_t <- TokyoTotalAge %>% 
    group_by(自治体コード, 時代不明)　%>%
    filter(時代不明 != 0) %>%
    count %>% 
    ungroup %>% 
    dplyr::select(-時代不明) %>%
    rename("不明" = "n")
  Tokyo.summary1 <- left_join(Tokyo.summary1, Tokyo.summary_t, by = "自治体コード")
  
    # NAを0に置換
  Tokyo.summary1[is.na(Tokyo.summary1)] <- 0
  
    # 一時保管オブジェクトを削除
  rm(Tokyo.summary_t)

    # visualizing the table: 表1の描画
  formattable(Tokyo.summary1)

# table_2 summary table of archaeological site in Tokyo by LGC and Age
  # tallying site number by LGC: 遺跡種別集計（区部）
    # 縄文貝塚
      #リスト
  Tokyo.summary_tt <- TokyoTotalAge %>% 
    filter(縄文時代 != 0) %>% 
    filter(str_detect(種別, "貝塚"))
      #集計
  Tokyo.summary_t <- Tokyo.summary_tt %>%
    group_by(自治体コード)　%>%
    count %>% 
    rename("貝塚" = "n")
  Tokyo.summary2 <- left_join(Tokyo.summary, Tokyo.summary_t, by = "自治体コード")
  
    # 方形周溝墓
      #リスト
  Tokyo.summary_tt <- TokyoTotalAge %>% 
    filter(弥生時代 != 0) %>% 
    filter(str_detect(遺構, "方形周溝墓"))
      #集計
  Tokyo.summary_t <- Tokyo.summary_tt %>% 
    group_by(自治体コード) %>% 
    count %>% 
    rename("方形周溝墓" = "n")
  Tokyo.summary2 <- left_join(Tokyo.summary2, Tokyo.summary_t, by = "自治体コード")
  
    # 古墳
      #リスト
  Tokyo.summary_tt <- TokyoTotalAge %>% 
    filter(古墳時代 != 0) %>% 
    filter(str_detect(種別, "古墳"))
      #集計
  Tokyo.summary_t <- Tokyo.summary_tt %>%
    group_by(自治体コード) %>% 
    count() %>% 
    rename("古墳" = "n")
  Tokyo.summary2 <- left_join(Tokyo.summary2, Tokyo.summary_t, by = "自治体コード")
  
    # 横穴墓
      #リスト
  Tokyo.summary_tt <- TokyoTotalAge %>% 
    filter(古墳時代 != 0) %>% 
    filter(str_detect(種別, "横穴墓"))
      #集計
  Tokyo.summary_t <- Tokyo.summary_tt %>% 
    group_by(自治体コード) %>% 
    count %>% 
    rename("横穴墓" = "n")
  Tokyo.summary2 <- left_join(Tokyo.summary2, Tokyo.summary_t, by = "自治体コード")
  
    # 中世城館
      #リスト
  Tokyo.summary_tt <- TokyoTotalAge %>% 
    filter(中世 != 0) %>% 
    filter(str_detect(種別, "城館"))
      #集計
  Tokyo.summary_t <- Tokyo.summary_tt %>% 
    group_by(自治体コード) %>% 
    count %>% 
    rename("城館" = "n")
  Tokyo.summary2 <- left_join(Tokyo.summary2, Tokyo.summary_t, by = "自治体コード")
  
    # 塚
      #リスト
  Tokyo.summary_tt <- TokyoTotalAge %>% 
    filter(str_detect(種別, "塚"))
      #集計
  Tokyo.summary_t <- Tokyo.summary_tt %>% 
    group_by(自治体コード) %>% 
    count %>% 
    rename("塚" = "n")
  Tokyo.summary2 <- left_join(Tokyo.summary2, Tokyo.summary_t, by = "自治体コード")
  
    # NAを0に置換
  Tokyo.summary2[is.na(Tokyo.summary2)] <- 0
  
    # 一時データの削除
  rm(Tokyo.summary_t, Tokyo.summary_tt)

  # visualizing the table2: 表2の描画
  Tokyo.summary22 <- filter(Tokyo.summary2, 自治体コード<13124) # 23区のみ抽出
  formattable(Tokyo.summary22)
  
  # visualizing the table3: 表3の描画
  Tokyo.summary23 <- filter(Tokyo.summary2, 自治体コード>13124) # 多摩地区のみ抽出
  formattable(Tokyo.summary23)
  