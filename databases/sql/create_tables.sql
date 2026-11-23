DROP TABLE IF EXISTS listing_amenities;
DROP TABLE IF EXISTS amenities;
DROP TABLE IF EXISTS listings;

CREATE TABLE listings (
    listing_id              BIGINT PRIMARY KEY,
    listing_type            TEXT,
    posted_date             DATE,
    expiry_date             DATE,
    property_type           TEXT,
    city_province           TEXT,
    district                TEXT,
    folder                  TEXT,
    title                   TEXT,
    address                 TEXT,
    old_address             TEXT,
    location_link           TEXT,
    latitude_longitude      TEXT,
    description             TEXT,
    area_price_history      DOUBLE PRECISION,
    price_range             DOUBLE PRECISION,
    area                    DOUBLE PRECISION,
    bedrooms                INTEGER,
    bathrooms               INTEGER,
    legal_status            TEXT,
    furnishing              TEXT,
    balcony_direction       TEXT,
    house_direction         TEXT,
    image_urls              JSONB,
    latitude                DOUBLE PRECISION,
    longitude               DOUBLE PRECISION,
    coordinate_status       TEXT,
    n_references            INTEGER,
    geo_cluster_150m        TEXT,
    listing_road_offset_m   DOUBLE PRECISION
);

CREATE TABLE amenities (
    osm_id                  TEXT PRIMARY KEY,
    name                    TEXT,
    category                TEXT,
    subtype                 TEXT,
    latitude                DOUBLE PRECISION,
    longitude               DOUBLE PRECISION,
    street                  TEXT,
    address                 TEXT,
    operator                TEXT,
    url                     TEXT,
    osm_timestamp           TIMESTAMPTZ,
    coordinate_method       TEXT
);

CREATE TABLE listing_amenities (
    listing_id              BIGINT NOT NULL REFERENCES listings(listing_id),
    osm_id                  TEXT NOT NULL REFERENCES amenities(osm_id),
    category                TEXT NOT NULL,
    rank                    INTEGER,
    driving_rank            INTEGER,
    straight_line_km        DOUBLE PRECISION,
    driving_distance_km     DOUBLE PRECISION,
    driving_duration_min    DOUBLE PRECISION,
    poi_road_offset_m       DOUBLE PRECISION,
    route_status            TEXT,
    PRIMARY KEY (listing_id, osm_id, category)
);
