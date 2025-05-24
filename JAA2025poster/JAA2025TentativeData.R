# データ読み込み　整形後データを読み込む
Saitama <- import("https://github.com/kotdijian/JASOSR/raw/master/11Saitama/11Saitama.csv", setclass= "tbl_df", encoding = "UTF-8") 
Tokyo <- import("https://github.com/kotdijian/JASOSR/raw/master/13Tokyo/13TokyoAge.csv", setclass= "tbl_df", encoding = "UTF-8") 
Kawasaki <- import("https://github.com/kotdijian/JASOSR/raw/master/14Kanagawa/14130KawasakiAge.csv", setclass= "tbl_df", encoding = "UTF-8") 

# 遺跡統合のためJASID、遺跡名、経度緯度、P〜M列を抽出、統合
Saitama1 <- dplyr::select(Saitama, JASID, SiteName, Lon, Lat, P, J, Y, K, N, H, M)
Tokyo1 <- dplyr::select(Tokyo, JASID, SiteName_original, Lon, Lat, P, J, Y, K, N, H, M)
Kawasaki1 <- dplyr::select(Kawasaki, JASID, SiteName, Lon, Lat, P, J, Y, K, N, H, M)
Tokyo1 <- rename(Tokyo1, SiteName = SiteName_original)

# 全遺跡結合＋NA処理、ファイル保存
Total2025 <- bind_rows(Saitama1, Tokyo1, Kawasaki1)
Total2025 <- replace_na(Total2025, list(P=0, J=0, Y=0, K=0, N=0, H=0, M=0))
write.csv(Total2025, "Total2025.csv", row.names=FALSE, fileEncoding = "UTF-8")
