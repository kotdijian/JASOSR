# geo-spatial data of registered archaeological sites in Saitama Prefecture／埼玉県遺跡地図空間データ
This is digitized geo-spatial data of archaeological sites in Saitama, based on [Saitama Prefecture Archaeological Site Map(Japanese Only)](https://www.pref.saitama.lg.jp/isekimap/)(hereafter 'SaiMap'), publsihed by Saitama Prefectural Board of Education.  
これは埼玉県教育委員会による[埼玉県埋蔵文化財情報公開ページ](https://www.pref.saitama.lg.jp/isekimap/)（以下「埼玉県遺跡地図」）にもとづき、埼玉県の遺跡（埋蔵文化財包蔵地）情報を代表点の位置座標を付加して再利用可能な空間データ化したものです[^1][^2]。

[^1]: ただし川越市は遺跡地図をウェブ公開していない。また以下の自治体は独自のサイトで遺跡地図・情報を公開している。
    * さいたま市：[さいたま市遺跡地図](https://www.sonicweb-asp.jp/saitama/agreement?theme=th_30)
    * 熊谷市: [くまがや遺跡情報](https://www.kumagaya-iseki.jp/)
    * 戸田市: [戸田市情報ポータルサイト](https://www.city.toda.saitama.jp/site/bunkazai/kyo-syogaigaku-prop-maizo.html)
    * 蓮田市: [埼玉県蓮田市文化財情報]（https://hasuda-archive.net/containing_places)    
    * ふじみ野市: [埼玉県ふじみ野市文化財情報](https://fujiminobunkazai.jp/pages/index)
    * 白岡市: [白岡市 埋蔵文化財の取扱いについて](https://www.city.shiraoka.lg.jp/soshiki/kyouikubu/syougaigakusyuuka/36/bunnkazaihogo/1285.html)
    * 松伏町: [松伏町　埋蔵文化財の取扱いについて](https://www.town.matsubushi.lg.jp/www/contents/1331015077536/index.html)
[^2]: 2025年1月13日現在入力作業は完了していない。入力済み範囲は 11saitama.summary.csvまたは11saitama.geojsonを確認のこと。なお「埼玉県遺跡地図」はPDFとして地図・遺跡台帳が公開されているため一覧データも手動で再構成している。作業時のミスの確認漏れがあり得るため情報の正確性は保証できない。

## Usage／ご利用にあたって

1. Basic data (**all descriptive contents are only in Japanese. English compiled version will be published in later**)  
* formatted in .csv (encoding = UTF-8, without BOM)  
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
