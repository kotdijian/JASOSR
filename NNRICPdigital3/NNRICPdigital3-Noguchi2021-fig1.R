# NNRICPdigital3-Noguchi2021-fig1.R
# 10MAR2021 by kotdijian (https://github.com/kotdijian)
# 考古学・文化財地理空間情報のオープンデータ化、整備と活用:図7作成Rコード
# 論文タイトル：考古学・文化財地理空間情報のオープンデータ化、整備と活用
# 著者：野口　淳
# 収録書誌：デジタル技術による文化財情報の記録と利活用3
# シリーズ名：奈良文化財研究所研究報告第27冊
# 刊行年月日：2021年3月12日
# R-code for fig 7, Distributing and Utilizing Open Geospatial Data in Archaeology and Cultural Heritage Management
# title: Distributing and Utilizing Open Geospatial Data in Archaeology and Cultural Heritage Management
# author: Atsushi Noguchi
# book title: Recording and Utilization of Cultural Property Information via Digital Technologies Table of Contents Vol. 3
# series title: Research Reports of Nara National Research Institute for Cultural Properties, Vol. 27
# published date: 12MAR2021

# package installation and activation: パッケージのインストールとアクティベーション
  install.packages("tidyverse")
  install.packages("purrr")
  install.packages("rio")
  install.packages("leaflet")
  install.packages("jpndistrict")
  install.packages("sf")
  library("tidyverse")
  library("purrr")
  library("rio")
  library("leaflet")
  library("jpndistrict")
  library("sf")

# data imoport: データのインポート
  # TokyoTotalAgeに原データcsvを読み込み、エンコードの指定に注意: 原データ=13Tokyo_total.csv
  TokyoTotal <- import("https://github.com/kotdijian/JASOSR/raw/master/13Tokyo/13Tokyo_total.csv",
                       setclass= "tbl_df", encoding = "UTF-8"
                       )

  Tokyo.sf  <- st_as_sf(drop_na(TokyoTotal),              # 座標値空白データは変換できないため`drop_na()`で除外しておく
                     coords = c("経度", "緯度"),
                     crs = 6668)                          # EPSG:6668=JGD2011
  Tokyo.sf <- rename(Tokyo.sf, "遺構" = "主な遺構/概要")  # 列名から/を除去

# jpndistrictによる東京都行政区画ポリゴンの取得と座標参照系(JGD2011)の設定
  tokyo.bnd <- dplyr::filter(jpn_pref(admin_name = "東京都"), city_code < 13360) #島嶼部を除く
  tokyo.bnd <- st_set_crs(tokyo.bnd, 6668)  #JGD2011に変更
  tokyo     <- tokyo.bnd %>% 
                st_union() %>%
                st_cast("POLYGON") %>%
                map(~ .x[1]) %>%
                st_multipolygon() %>%       #区市町村のポリゴンを統合
                st_sfc() %>%
                st_sf(crs = 6668)           #JGD2011に変更

# leafletによる図7の描画（国立市緑川東遺跡） 
  #対象遺跡の指定とデータの抽出
  focal.site <- filter(Tokyo.sf,  遺跡名 == "緑川東遺跡") # 遺跡名で指定した遺跡を中心に描画する
  coord <- st_coordinates(focal.site)                     # 座標の抽出
  focal.site <- cbind(focal.site, coord)                  # 抽出した座標をデータに連結
  
  #対象遺跡の所在自治体ポリゴンの抽出
  city.bnd <- filter(tokyo.bnd, city_code == focal.site$自治体コード)
  
  # 出典表記
  atr2 <- paste0("背景地図:国土地理院淡色地図　",
                 "<a href='http://maps.gsi.go.jp/development/ichiran.html' target='_blank'>",
                 "地理院タイル",
                 "</a>"
                 )
  
  #地理院地図（淡色地図）上での表示
  leaflet(options = leafletOptions(zoomControl = FALSE)) %>%              # ズームボタン非表示
    addTiles("https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", # タイル地図参照URLにより背景地図の変更可能
             attribution = atr2) %>%
    addCircleMarkers(data = Tokyo.sf,
                     radius = 1, color = "#09f", weight = 5) %>%
    setView(lng = focal.site$X, lat = focal.site$Y, zoom = 13) %>%
  #遺跡所在自治体の描画
    addPolygons(data = city.bnd,
                color = "red",
                fillOpacity = 0,
                weight = 3) %>%
  #遺跡位置の描画
    addCircleMarkers(data = focal.site,
                     radius = 3, color = "#f00", weight = 10, opacity = 100, #マーカー描画の指定
                     label = focal.site$遺跡名,
                     labelOptions = labelOptions(noHide = T, textOnly = TRUE,
                                                 direction = "right", textsize = "20px",                    #キャプションと表示位置の指定
                                                 style = list("font-weight" = "bold",                       #太字
                                                              "margin" = "0px 0px 20px 15px",               #表示位置の微調整
                                                              "padding" = "0px 4px 0px 4px",                #白抜き範囲指定
                                                              "background-color" = "rgba(255,255,255,0.5)") #文字背景色指定
                                                 )
                     ) %>%
  #経緯線・スケールバーの追加
    addGraticule(interval = 0.0833333, style = list(color = "#555", weight = 1)) %>% #経緯線の設定(経緯線=5'、補助線=1'間隔)
    addGraticule(interval = 0.1666666, style = list(color = "#000", weight = 2)) %>%
    addScaleBar(position="bottomright",
                options = scaleBarOptions(imperial = FALSE, maxWidth = 500)
                )