# Sample data for the Archaeo-GIS workshop on 2020508／研究集会「遺跡GISの現在地」ワークショップ用データ
This is sample GIS data for Hands-on Workshop in the "Current status of Archaeo-GIS" held on 19 Aug 2025 at The History of the Nara Palace Site Museum. The original data is provided by Kokubunji City and permitted to publish under MoU between the Kokubunji City Educational Board and "Integration and 3D GIS Mapping of Archaeological Big Data: Investigating Ancient Temple Site Selection, Construction, and Landscape" KAKENHI project [(JSPS 24K00142)](https://kaken.nii.ac.jp/en/grant/KAKENHI-PROJECT-24K00142)


これは2025年8月19日に奈良文化財研究所平城宮跡資料館講堂で開催される研究集会「遺跡GISの現在地」におけるハンズオンワークショップで使用されるGISおよび関連データです。原データは国分寺市教育委員会が作成したものを、同教育委員会の許可を得て日本学術振興会科研費基盤研究(B)「考古学ビッグデータの統合と3D-GIS化による古代寺院立地・造営・景観論」[(古代寺院3D-GIS科研：JSPS 24K00142）](https://kaken.nii.ac.jp/ja/grant/KAKENHI-PROJECT-24K00142)

## Usage／ご利用にあたって

1. General condition: This is just sample data for the workshop. The original data will be published by Kokubunji City. We don't guarantee the accuracy or correctness of the information, so you should use this data only for your self-learning purposes.

2. Data set (**all descriptive contents are only in Japanese.**)  
 2-1. SiteArea: registered archaeological site area, polygon.
   * SiteAreaDB.gpkg: Geopackage Database file
   * KokubunjiSiteArea.kml: KML file
   * Both files contain the same information (only in Japanese)  
    * fid: (data management)
    * OBJECTID: (data management)
    * SITEName: English (roman) translation of SiteName_original
    * SiteName_original: Site name in Japanese (registered in TokyoMetMap)
    * JASID: Japanese Archaeological Site ID, 11-digit integer, UID in combination with LGC (5 digit) + site ID (3 digit, by municipality) + sub-ID (2 digit, NA=00);  
     * LGC: [Local Government Code](http://data.e-stat.go.jp/lodw/en/provdata/lodRegion), 5-digit integer (first 2 digits indicates prefecture);  
     * SiteID: 3-digit integer (registered in TokyoMetMap);
     * Sub-ID: registered by municipality;
    * Chronology: As described in TokyoMetMap
    * SiteType: As described in TokyoMetMap  
    * ArchaeologicalFeatures: As described in TokyoMetMap
    * MajorArtefacts: As described in TokyoMetMap
    * **only .gpkg file contains Boolean fields of chronology**  

1. 前提事項：ここで提供するのはあくまでワークショップ用のサンプルデータです。オリジナルデータは今後、国分寺市教育委員会より公開される予定でです。私たちは本データの正しさと精確さを保証しませんので、個人的な用途（自習）にのみご利用ください。
   
2.  基本データ
 2-1. 遺跡範囲： 遺跡地図に登録されている遺跡（周知の埋蔵文化財包蔵地）の範囲、ポリゴン
   * SiteAreaDB.gpkg: ジオパッケージ・データベースファイル
   * KokubunjiSiteArea.kml: KMLファイル
   * ２つのファイルの属性情報は同じです 
    * fid: (データ管理用)
    * OBJECTID: (データ管理用)
    * SITEName: SiteName_originalのローマ字版
    * SiteName_original: Site name in Japanese (registered in TokyoMetMap)
    * JASID: Japanese Archaeological Site ID, 11-digit integer, UID in combination with LGC (5 digit) + site ID (3 digit, by municipality) + sub-ID (2 digit, NA=00);  
     * LGC: [Local Government Code](http://data.e-stat.go.jp/lodw/en/provdata/lodRegion), 5-digit integer (first 2 digits indicates prefecture);  
     * SiteID: 3-digit integer (registered in TokyoMetMap);
     * Sub-ID: registered by municipality;
    * Chronology: As described in TokyoMetMap
    * SiteType: As described in TokyoMetMap  
    * ArchaeologicalFeatures: As described in TokyoMetMap
    * MajorArtefacts: As described in TokyoMetMap
    * **only .gpkg file contains Boolean fields of chronology**    


## Author／作成者
Atsushi Noguchi ([kotodijian](https://github.com/kotdijian) or [@fujimicho](https://twitter.com/fujimicho)  
