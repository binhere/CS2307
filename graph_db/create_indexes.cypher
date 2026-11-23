// Unique constraints for all 6 node types
CREATE CONSTRAINT listing_id_unique IF NOT EXISTS FOR (n:Listing) REQUIRE n.listing_id IS UNIQUE;
CREATE CONSTRAINT amenity_id_unique IF NOT EXISTS FOR (n:Amenity) REQUIRE n.osm_id IS UNIQUE;
CREATE CONSTRAINT cluster_id_unique IF NOT EXISTS FOR (n:GeoCluster) REQUIRE n.cluster_id IS UNIQUE;
CREATE CONSTRAINT ward_id_unique IF NOT EXISTS FOR (n:Ward) REQUIRE n.ward_id IS UNIQUE;
CREATE CONSTRAINT former_area_id_unique IF NOT EXISTS FOR (n:FormerAdminArea) REQUIRE n.former_area_id IS UNIQUE;
CREATE CONSTRAINT street_id_unique IF NOT EXISTS FOR (n:Street) REQUIRE n.street_id IS UNIQUE;

// Useful property indexes for filtering
CREATE INDEX listing_price_idx IF NOT EXISTS FOR (n:Listing) ON (n.price_range);
CREATE INDEX listing_area_idx IF NOT EXISTS FOR (n:Listing) ON (n.area);
CREATE INDEX listing_district_idx IF NOT EXISTS FOR (n:Listing) ON (n.district);
CREATE INDEX amenity_category_idx IF NOT EXISTS FOR (n:Amenity) ON (n.category);
CREATE INDEX near_amenity_dist_idx IF NOT EXISTS FOR ()-[r:NEAR_AMENITY]-() ON (r.driving_distance_km);
CREATE INDEX near_amenity_dur_idx IF NOT EXISTS FOR ()-[r:NEAR_AMENITY]-() ON (r.driving_duration_min);
