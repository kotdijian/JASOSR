# データ読み込み　整形後データを読み込む
Saitama <- import("https://github.com/kotdijian/JASOSR/raw/master/11Saitama/11Saitama.csv", setclass= "tbl_df", encoding = "UTF-8") 
Tokyo <- import("https://github.com/kotdijian/JASOSR/raw/master/13Tokyo/13Tokyo.csv", setclass= "tbl_df", encoding = "UTF-8") 
Kawasaki <- import("https://github.com/kotdijian/JASOSR/raw/master/14Kanagawa/14130Kawasaki.csv", setclass= "tbl_df", encoding = "UTF-8") 

#　全遺跡統合のためJASID、遺跡名、経度緯度列のみを抽出、列名を変更、統合
Saitama1 <- dplyr::select(Saitama, JASID, SiteName, Lon, Lat)
Tokyo1 <- dplyr::select(Tokyo, JASID, 遺跡名, 経度, 緯度)
Kawasaki1 <- dplyr::select(Kawasaki, JASID, 遺跡名, 経度, 緯度)
Tokyo1 <- rename(Tokyo1, SiteName=遺跡名, Lon=経度, Lat=緯度)
Kawasaki1 <- rename(Kawasaki1, SiteName=遺跡名, Lon=経度, Lat=緯度)
Total2025 <- bind_rows(Saitama1, Tokyo1, Kawasaki1)
write.csv(Total2025, "Total2025.csv", row.names=FALSE, fileEncoding = "UTF-8")
