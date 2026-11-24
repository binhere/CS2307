# Danh sách cột dư thừa cần bỏ khi build đồ thị Neo4j

**Nguồn:** `data/v3/inputs/osm_poi_graph_3857_live_osrm_complete_20261021/graph_live/listings_osm_osrm_enriched.csv`  
**Tổng cột file enrich:** 239 (gốc 24 + mới 215)  
**Đề xuất bỏ:** 181 cột | **Giữ lại trong số cột mới:** 34 cột

> Lý do chung: các cột này đã được biểu diễn dưới dạng node `Amenity`, cạnh `NEAR_AMENITY`, node `Ward`/`Street`/`GeoCluster`, hoặc chỉ là nhật ký kỹ thuật, không dùng để truy vấn.

## Nhóm A. Tiện ích dạng phẳng (152 cột) — đã có ở node `Amenity` và cạnh `NEAR_AMENITY`

| Tiện ích | Số cột | Danh sách cột |
|---|---:|---|
| `hospital` | 16 | `nearest_hospital_name`, `nearest_hospital_osm_id`, `nearest_hospital_latitude`, `nearest_hospital_longitude`, `nearest_hospital_street`, `nearest_hospital_street_source`, `nearest_hospital_address_osm`, `nearest_hospital_operator`, `hospital_straight_line_km`, `hospital_driving_distance_km`, `hospital_driving_duration_min`, `hospital_data_source`, `hospital_route_source`, `hospital_route_status`, `hospital_source_snap_m`, `hospital_destination_snap_m` |
| `school` | 17 | `nearest_school_name`, `nearest_school_osm_id`, `nearest_school_latitude`, `nearest_school_longitude`, `nearest_school_street`, `nearest_school_street_source`, `nearest_school_address_osm`, `nearest_school_operator`, `school_straight_line_km`, `school_driving_distance_km`, `school_driving_duration_min`, `school_data_source`, `school_route_source`, `within_school_2km_driving`, `school_route_status`, `school_source_snap_m`, `school_destination_snap_m` |
| `college_university` | 17 | `nearest_college_university_name`, `nearest_college_university_osm_id`, `nearest_college_university_latitude`, `nearest_college_university_longitude`, `nearest_college_university_street`, `nearest_college_university_street_source`, `nearest_college_university_address_osm`, `nearest_college_university_operator`, `college_university_straight_line_km`, `college_university_driving_distance_km`, `college_university_driving_duration_min`, `college_university_data_source`, `college_university_route_source`, `within_college_university_5km_driving`, `college_university_route_status`, `college_university_source_snap_m`, `college_university_destination_snap_m` |
| `metro` | 17 | `nearest_metro_name`, `nearest_metro_osm_id`, `nearest_metro_latitude`, `nearest_metro_longitude`, `nearest_metro_street`, `nearest_metro_street_source`, `nearest_metro_address_osm`, `nearest_metro_operator`, `metro_straight_line_km`, `metro_driving_distance_km`, `metro_driving_duration_min`, `metro_data_source`, `metro_route_source`, `within_metro_3km_driving`, `metro_route_status`, `metro_source_snap_m`, `metro_destination_snap_m` |
| `mall` | 17 | `nearest_mall_name`, `nearest_mall_osm_id`, `nearest_mall_latitude`, `nearest_mall_longitude`, `nearest_mall_street`, `nearest_mall_street_source`, `nearest_mall_address_osm`, `nearest_mall_operator`, `mall_straight_line_km`, `mall_driving_distance_km`, `mall_driving_duration_min`, `mall_data_source`, `mall_route_source`, `within_mall_5km_driving`, `mall_route_status`, `mall_source_snap_m`, `mall_destination_snap_m` |
| `market` | 17 | `nearest_market_name`, `nearest_market_osm_id`, `nearest_market_latitude`, `nearest_market_longitude`, `nearest_market_street`, `nearest_market_street_source`, `nearest_market_address_osm`, `nearest_market_operator`, `market_straight_line_km`, `market_driving_distance_km`, `market_driving_duration_min`, `market_data_source`, `market_route_source`, `within_market_2km_driving`, `market_route_status`, `market_source_snap_m`, `market_destination_snap_m` |
| `supermarket` | 17 | `nearest_supermarket_name`, `nearest_supermarket_osm_id`, `nearest_supermarket_latitude`, `nearest_supermarket_longitude`, `nearest_supermarket_street`, `nearest_supermarket_street_source`, `nearest_supermarket_address_osm`, `nearest_supermarket_operator`, `supermarket_straight_line_km`, `supermarket_driving_distance_km`, `supermarket_driving_duration_min`, `supermarket_data_source`, `supermarket_route_source`, `within_supermarket_2km_driving`, `supermarket_route_status`, `supermarket_source_snap_m`, `supermarket_destination_snap_m` |
| `park` | 17 | `nearest_park_name`, `nearest_park_osm_id`, `nearest_park_latitude`, `nearest_park_longitude`, `nearest_park_street`, `nearest_park_street_source`, `nearest_park_address_osm`, `nearest_park_operator`, `park_straight_line_km`, `park_driving_distance_km`, `park_driving_duration_min`, `park_data_source`, `park_route_source`, `within_park_5km_driving`, `park_route_status`, `park_source_snap_m`, `park_destination_snap_m` |
| `bus_stop` | 17 | `nearest_bus_stop_name`, `nearest_bus_stop_osm_id`, `nearest_bus_stop_latitude`, `nearest_bus_stop_longitude`, `nearest_bus_stop_street`, `nearest_bus_stop_street_source`, `nearest_bus_stop_address_osm`, `nearest_bus_stop_operator`, `bus_stop_straight_line_km`, `bus_stop_driving_distance_km`, `bus_stop_driving_duration_min`, `bus_stop_data_source`, `bus_stop_route_source`, `within_bus_stop_2km_driving`, `bus_stop_route_status`, `bus_stop_source_snap_m`, `bus_stop_destination_snap_m` |

### Cờ `near_*` và cột đếm tiện ích (10 cột)

`near_metro`, `near_bus`, `near_highway`, `near_school`, `near_hospital`, `near_mall`, `near_market`, `near_park`, `nearby_category_count`, `nearby_categories`

## Nhóm B. Nhật ký tọa độ / địa chỉ trùng lặp (15 cột)

`latitude_original`, `longitude_original`, `coordinate_source`, `coordinate_enriched_at`, `latitude_longitude_enriched`, `map_link_enriched`, `road_name_osm`, `road_name_source`, `location_graph_key`, `location_label`, `street_name`, `ward_commune`, `ward_commune_type`, `former_admin_area`, `road_snap_distance_m`

## Nhóm C. Metadata kỹ thuật (4 cột)

`travel_enriched_at`, `amenities_travel_enriched_at`, `poi_snapshot_at`, `top5_basis`

## Cột mới GIỮ LẠI (34 cột)

`pool`, `gym`, `park`, `bbq`, `kids_playground`, `sports_court`, `security_24h`, `reception`, `elevator`, `parking`, `river_view`, `park_view`, `city_view`, `balcony`, `garden`, `garage`, `terrace`, `frontage`, `access_road`, `latitude`, `longitude`, `coordinate_status`, `coordinate_confidence`, `ward_commune_name`, `geohash_6`, `geohash_7`, `geo_cluster_150m`, `geo_cluster_size`, `nearest_listing_distance_m`, `listing_density_1km`, `distance_to_ben_thanh_km`, `distance_band_ben_thanh`, `road_name_final`, `amenity_types_within_threshold_count`

## Tổng kết

| Nhóm | Số cột bỏ |
|---|---:|
| A. Tiện ích phẳng | 152 |
| A'. Cờ `near_*` + đếm | 10 |
| B. Nhật ký tọa độ/địa chỉ | 15 |
| C. Metadata | 4 |
| **Tổng** | **181** |
