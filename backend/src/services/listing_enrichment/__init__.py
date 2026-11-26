"""Enrich the listings users post so search and the graph treat them like any other listing.

Listings come only from users (the form and zip uploads); nothing computes their surroundings
offline any more. Once a listing has coordinates (given, or found later by the geocoding job),
the server finds its nearest amenities per category from a committed OpenStreetMap reference
(amenities.py), stores them where search filters and ranks by them (listing_amenity_distances),
and adds the listing to the Neo4j graph with its ward, former district, street, 150 m location
cell and amenities (graph.py). service.py runs this after posting and on a schedule.
"""
