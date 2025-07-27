# Sample data for the Archaeo-GIS workshop on 2020508／研究集会「遺跡GISの現在地」ワークショップ用データ
This is sample GIS data for Hands-on Workshop in the "Current status of Archaeo-GIS" held on 19 Aug 2025 at The History of the Nara Palace Site Museum. The original data is provided by Kokubunji City and permitted to publish under MoU between the Kokubunji City Educational Board and "Integration and 3D GIS Mapping of Archaeological Big Data: Investigating Ancient Temple Site Selection, Construction, and Landscape" KAKENHI project [(JSPS 24K00142)](https://kaken.nii.ac.jp/en/grant/KAKENHI-PROJECT-24K00142)
これは2025年8月19日に奈良文化財研究所平城宮跡資料館講堂で開催される研究集会「遺跡GISの現在地」におけるハンズオンワークショップで使用されるGISおよび関連データです。原データは国分寺市教育委員会が作成したものを、同教育委員会の許可を得て日本学術振興会科研費基盤研究(B)「考古学ビッグデータの統合と3D-GIS化による古代寺院立地・造営・景観論」[(古代寺院3D-GIS科研：JSPS 24K00142）](https://kaken.nii.ac.jp/ja/grant/KAKENHI-PROJECT-24K00142)

## Usage／ご利用にあたって

1. General condition: this is just sample data for workshop. The original data will be published by Kokubunji City. We do not guarantee the accuracy or correctness of the information, therefore you are recommende to use this data only for self-learning. 
2. Basic data (**all descriptive contents are only in Japanese. English compiled version will be published in later**)  
 2-1. formatted in .csv (encoding = UTF-8, without BOM)  
* provided as following 2 files:  
* 11Saitama.csv: composed of 8 fields
    * JASID (*original*): Japanese Archaeological Site ID, 11 digit integer, UID in combination with LGC (5 digit) + site ID (3 digit, by municipality) + sub-ID (2 digit, NA=00);  
    * LGC (*original*): [Local Government Code](http://data.e-stat.go.jp/lodw/en/provdata/lodRegion), 5 digit integer (first 2 digit indicates prefecture);  
    * MunicipalityID (*SaiMap*): 2 digit integer, by Saitama Prefecture;
    * SiteID (*SaiMap*): 1 to 3 digit integer, sometime added sub-ID registered by municipality;
    * Site name (*SaiMap*): *Japanese Only*
    * Site type (*SaiMap*): shell midden, settlement, shell midden, tumulus, tunnel grave, kiln, ritual site, temple, castle and fort, etc.;  
    * Age & Period (*SaiMap*): Palaeilithic, Jomon, Yayoi, Kofun, Nara, Heian, Kamakura, Nanbokucho, Muromachi, Sengoku, Edo, Unknown
    * Coordinates (*original*): a combination of longitude and latitude of single representative point of the site, not in shape (as TokyoMETmap represents).  
    * **notification**: coordinates in this dataset are manually acquired from the original TokyoMETmap. Thus information is not originated from TokyoMETmap, and the author of this dataset doesn't give any guarantee of accuracy of those coordinates (disclaimer)  
* 11Saitama_total.csv: including unvalidated information as same as the original *SaiMap* DB

1. 基本データ  
* 基本データは.csv形式(UTF-8, BOMなし)，以下の2ファイル  
* 11Saitama.csv: JASID+自治体コード+埼玉県遺跡地図収録6項目+位置座標(経度・緯度)  
    * 内容：JASID+自治体コード+東京都遺跡地図収録6項目（市町村番号、遺跡番号、遺跡名、種別、時代）+位置座標   
    * JASID(Japan Archaeological Site ID: 11ケタ)＝自治体コード(5ケタ)+遺跡番号(自治体ごと:4ケタ)+枝番号(2ケタ、枝番ナシは00)  
    * 位置座標は、埼玉県遺跡地図を参照、目視により確認した代表点を取得した単点座標であり、ポリゴンやそれにもとづく点座標ではない  
    * したがって**位置座標は東京都遺跡地図が提供するものではなく本データセット固有の情報である**  
    * **本データセットの提供者は位置座標の正確性を保証しない**  
* 「[全国遺跡報告総覧](https://sitereports.nabunken.go.jp/ja)」の書誌情報・抄録情報との紐づけは将来的に実装する予定(したい) 
* 11Saitama_total.csv: 抹消データ、位置情報不明（東京都遺跡地図で確認不能）データを含むオリジナルデータ

2. 自治体別データ(byMunicipalityサブディレクトリ)  *準備中*  
    * 13Tokyo.csvを自治体ごとに分割したもの  
    * ファイル命名規則：ファイル名の上5ケタは[自治体コード](https://www.soumu.go.jp/denshijiti/code.html)+自治体名ローマ字表記  

3. 時代別データ(byAgeサブディレクトリ)  
    * 各時代別の一覧リスト  
    * "11Tokyo.csv"の【時代】列の入力項目により時代別に分割したもの(時代別リスト間で重複あり)  
    * ファイル命名規則："13Tokyo_total_"に各時代名を付す(palaeolithic, jomon, yayoi, kofun, nara, heian, medieval, earlymodern, unknown  

4. SJSデータ（SJSサブディレクトリ）
    * 各時代別の一覧リスト（3.時代別データと同等）をShiftJIS（CP932）で保存したもの

## Contributing／コントリビューション
Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.  
プルリクエスト歓迎。大規模な変更はイシューからどうぞ。  

## Author／作成者
Atsushi Noguchi ([kotodijian](https://github.com/kotdijian) or [@fujimicho](https://twitter.com/fujimicho)  


## Contributors／協力者  
