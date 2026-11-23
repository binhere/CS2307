-- 1. Kiểm tra số dòng cả 3 bảng (kỳ vọng: 3.857 | 10.775 | 150.495)
SELECT 'listings' AS table_name, COUNT(*) AS row_count FROM listings
UNION ALL
SELECT 'amenities', COUNT(*) FROM amenities
UNION ALL
SELECT 'listing_amenities', COUNT(*) FROM listing_amenities;

-- 2. Tìm BĐS giá <= 5 tỷ, diện tích >= 60 m2, cách ga Metro <= 10 phút lái xe
SELECT
    l.listing_id,
    l.title,
    l.price_range,
    l.area,
    l.district,
    a.name AS metro_station,
    ROUND(la.driving_distance_km::numeric, 2) AS driving_km,
    ROUND(la.driving_duration_min::numeric, 1) AS driving_min
FROM listings l
JOIN listing_amenities la ON l.listing_id = la.listing_id
JOIN amenities a ON la.osm_id = a.osm_id
WHERE la.category = 'metro'
  AND l.price_range <= 5.0
  AND l.area >= 60.0
  AND la.driving_duration_min <= 10.0
ORDER BY la.driving_duration_min ASC
LIMIT 15;

-- 3. Tìm BĐS xung quanh 1 căn mục tiêu trong bán kính tùy chỉnh 250m (đường chim bay)
WITH target AS (
    SELECT listing_id, latitude, longitude
    FROM listings
    WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    LIMIT 1
)
SELECT
    l.listing_id,
    l.title,
    l.price_range,
    l.district,
    ROUND((
        6371000 * 2 * ASIN(SQRT(
            POWER(SIN(RADIANS(l.latitude - t.latitude) / 2), 2) +
            COS(RADIANS(t.latitude)) * COS(RADIANS(l.latitude)) *
            POWER(SIN(RADIANS(l.longitude - t.longitude) / 2), 2)
        ))
    )::numeric, 1) AS straight_line_m
FROM listings l
CROSS JOIN target t
WHERE l.listing_id <> t.listing_id
  AND l.latitude IS NOT NULL
  AND (
        6371000 * 2 * ASIN(SQRT(
            POWER(SIN(RADIANS(l.latitude - t.latitude) / 2), 2) +
            COS(RADIANS(t.latitude)) * COS(RADIANS(l.latitude)) *
            POWER(SIN(RADIANS(l.longitude - t.longitude) / 2), 2)
        ))
      ) <= 250
ORDER BY straight_line_m ASC
LIMIT 15;

-- 4. Tìm các BĐS tương đồng nhờ cùng gần chung 1 tiện ích (VD: cùng gần Bệnh viện <= 2 km)
SELECT
    la1.listing_id AS source_listing,
    la2.listing_id AS similar_listing,
    a.name AS shared_hospital,
    ROUND(la2.driving_distance_km::numeric, 2) AS other_driving_km
FROM listing_amenities la1
JOIN listing_amenities la2
  ON la1.osm_id = la2.osm_id
 AND la1.listing_id <> la2.listing_id
JOIN amenities a ON la1.osm_id = a.osm_id
WHERE la1.listing_id = (SELECT listing_id FROM listings WHERE coordinate_status = 'valid' LIMIT 1)
  AND la1.category = 'hospital'
  AND la2.driving_distance_km <= 2.0
ORDER BY la2.driving_distance_km ASC
LIMIT 15;
