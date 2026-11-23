// 1. Sanity check: count nodes by label
CALL db.labels() YIELD label
CALL apoc.cypher.run('MATCH (n:`' + label + '`) RETURN count(n) AS count', {}) YIELD value
RETURN label, value.count AS count
ORDER BY count DESC;

// (Without APOC) Count each of the 6 node types & 6 relationship types
MATCH (l:Listing) WITH count(l) AS listings
MATCH (a:Amenity) WITH listings, count(a) AS amenities
MATCH (g:GeoCluster) WITH listings, amenities, count(g) AS clusters
MATCH (w:Ward) WITH listings, amenities, clusters, count(w) AS wards
MATCH (f:FormerAdminArea) WITH listings, amenities, clusters, wards, count(f) AS former_areas
MATCH (s:Street)
RETURN listings, amenities, clusters, wards, former_areas, count(s) AS streets;

// 2. Visualize full subgraph around 1 Listing (matches the 6-node, 6-edge schema)
MATCH (l:Listing)
WHERE l.coordinate_status = 'valid'
WITH l LIMIT 1
OPTIONAL MATCH (l)-[r1:IN_WARD]->(w:Ward)
OPTIONAL MATCH (l)-[r2:IN_FORMER_AREA]->(f:FormerAdminArea)
OPTIONAL MATCH (w)-[r3:MAPPED_FROM]->(f)
OPTIONAL MATCH (l)-[r4:ON_STREET]->(s:Street)
OPTIONAL MATCH (l)-[r5:IN_CLUSTER]->(g:GeoCluster)
OPTIONAL MATCH (l)-[r6:NEAR_AMENITY]->(a:Amenity)
WHERE r6.is_nearest = true
RETURN l, w, f, s, g, a, r1, r2, r3, r4, r5, r6;

// 3. Listing <-> Amenity: apartments <= 5 ty, within 10 min driving of a metro station
MATCH (l:Listing)-[r:NEAR_AMENITY]->(a:Amenity)
WHERE a.category CONTAINS 'metro'
  AND l.price_range <= 5.0
  AND r.driving_duration_min <= 10.0
RETURN l.listing_id, l.title, l.price_range, l.district, a.name AS metro_station,
       r.driving_distance_km, r.driving_duration_min
ORDER BY r.driving_duration_min ASC
LIMIT 15;

// 4a. Listing <-> Listing (150m precomputed cluster via IN_CLUSTER)
MATCH (l1:Listing)-[:IN_CLUSTER]->(g:GeoCluster)<-[:IN_CLUSTER]-(l2:Listing)
WHERE l1.listing_id <> l2.listing_id
WITH l1, g, collect(l2.listing_id)[0..5] AS nearby_ids, g.listing_count AS cluster_size
RETURN l1.listing_id, l1.title, g.cluster_id, cluster_size, nearby_ids
ORDER BY cluster_size DESC
LIMIT 10;

// 4b. Listing <-> Listing (custom 250m straight-line radius via coordinates)
MATCH (target:Listing)
WHERE target.latitude IS NOT NULL AND target.longitude IS NOT NULL
WITH target LIMIT 1
MATCH (other:Listing)
WHERE other.listing_id <> target.listing_id
  AND other.latitude IS NOT NULL AND other.longitude IS NOT NULL
  AND point.distance(
        point({latitude: target.latitude, longitude: target.longitude}),
        point({latitude: other.latitude, longitude: other.longitude})
      ) <= 250
RETURN target.listing_id AS source_id, other.listing_id AS neighbor_id,
       other.title, other.price_range,
       round(point.distance(
         point({latitude: target.latitude, longitude: target.longitude}),
         point({latitude: other.latitude, longitude: other.longitude})
       ), 1) AS straight_line_m
ORDER BY straight_line_m ASC
LIMIT 15;

// 5. Ward <-> FormerAdminArea mapping
MATCH (w:Ward)-[r:MAPPED_FROM]->(f:FormerAdminArea)
RETURN w.name AS new_ward, f.name AS former_area, r.supporting_listing_count
ORDER BY r.supporting_listing_count DESC
LIMIT 20;
