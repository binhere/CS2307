CREATE INDEX IF NOT EXISTS idx_listings_price ON listings(price_range);
CREATE INDEX IF NOT EXISTS idx_listings_area ON listings(area);
CREATE INDEX IF NOT EXISTS idx_listings_district ON listings(district);
CREATE INDEX IF NOT EXISTS idx_listings_cluster ON listings(geo_cluster_150m);
CREATE INDEX IF NOT EXISTS idx_listings_coords ON listings(latitude, longitude);

CREATE INDEX IF NOT EXISTS idx_amenities_category ON amenities(category);

CREATE INDEX IF NOT EXISTS idx_la_category ON listing_amenities(category);
CREATE INDEX IF NOT EXISTS idx_la_driving_dist ON listing_amenities(driving_distance_km);
CREATE INDEX IF NOT EXISTS idx_la_driving_dur ON listing_amenities(driving_duration_min);
CREATE INDEX IF NOT EXISTS idx_la_driving_rank ON listing_amenities(driving_rank);
CREATE INDEX IF NOT EXISTS idx_la_osm_id ON listing_amenities(osm_id);
