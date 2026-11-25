"""Seed build and load: listing selection, Cypher rewriting and CSV batching.

Run with: python -m pytest deploy/seed/tests (needs deploy/seed/requirements.txt installed).
"""

import csv
import json
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

SEED_DIRECTORY = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SEED_DIRECTORY.parents[1]
sys.path.insert(0, str(SEED_DIRECTORY))

import prepare_seed_input  # noqa: E402
import seed  # noqa: E402


def write_csv(path: Path, header, rows):
    """Write a small CSV fixture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


class CypherRewriteTests(unittest.TestCase):
    def test_load_csv_becomes_an_unwind_over_rows(self):
        statement = (
            "LOAD CSV WITH HEADERS FROM 'file:///real_estate_graph_ready/neo4j_ward_nodes.csv' AS row\n"
            "MERGE (n:Ward {ward_id: row.`ward_id:ID(Ward)`})"
        )
        file_name, query = seed.as_parameterized(statement)
        self.assertEqual(file_name, "neo4j_ward_nodes.csv")
        self.assertTrue(query.startswith("UNWIND $rows AS row\n"))

    def test_in_transactions_is_removed_because_each_batch_commits_alone(self):
        statement = (
            "LOAD CSV WITH HEADERS FROM 'file:///real_estate_graph_ready/edges.csv' AS row\n"
            "CALL {\n  WITH row\n  MERGE (a)-[:R]->(b)\n} IN TRANSACTIONS OF 10000 ROWS"
        )
        _, query = seed.as_parameterized(statement)
        self.assertNotIn("IN TRANSACTIONS", query)
        self.assertTrue(query.rstrip().endswith("}"))

    def test_other_statements_are_unchanged(self):
        statement = "CREATE INDEX ward_name_idx IF NOT EXISTS FOR (n:Ward) ON (n.name)"
        self.assertEqual(seed.as_parameterized(statement), (None, statement))

    def test_real_import_script_reads_only_files_the_seed_ships(self):
        cypher_text = (REPOSITORY_ROOT / "backend/scripts/neo4j/import_search_graph.cypher").read_text(encoding="utf-8")
        statements = seed.split_statements(cypher_text)
        files = {seed.as_parameterized(statement)[0] for statement in statements} - {None}
        graph_files = {path.name for path in (REPOSITORY_ROOT / "data/v3/graph").glob("*.csv")}
        self.assertEqual(files, graph_files)
        self.assertFalse(any("LOAD CSV" in seed.as_parameterized(statement)[1] for statement in statements))
        self.assertFalse(any(statement.lstrip().startswith("//") for statement in statements))


class CsvBatchTests(unittest.TestCase):
    def test_batches_split_rows_and_turn_empty_fields_into_none(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.csv"
            write_csv(path, ["id", "name"], [["1", "a"], ["2", ""], ["3", "c"]])
            batches = list(seed.csv_batches(path, batch_size=2))
        self.assertEqual([len(batch) for batch in batches], [2, 1])
        self.assertIsNone(batches[0][1]["name"])


class ContentDigestTests(unittest.TestCase):
    """The digests decide whether a run replaces a store, so only content may change them."""

    def write_files(self, directory: Path, contents: dict) -> list:
        """Write {file name: text} into directory; return the paths in the dict's order."""
        paths = []
        for name, text in contents.items():
            path = directory / name
            path.write_text(text, encoding="utf-8")
            paths.append(path)
        return paths

    def test_same_content_gives_the_same_digest_and_any_change_another(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            original = seed.content_digest(self.write_files(Path(first), {"a.csv": "id\n1\n", "b.csv": "x\n"}))
            copy = seed.content_digest(self.write_files(Path(second), {"a.csv": "id\n1\n", "b.csv": "x\n"}))
            self.assertEqual(original, copy)
            changed = seed.content_digest(self.write_files(Path(second), {"a.csv": "id\n2\n", "b.csv": "x\n"}))
            self.assertNotEqual(original, changed)

    def test_bytes_moved_between_files_change_the_digest(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            original = seed.content_digest(self.write_files(Path(first), {"a.csv": "ab", "b.csv": "c"}))
            moved = seed.content_digest(self.write_files(Path(second), {"a.csv": "a", "b.csv": "bc"}))
        self.assertNotEqual(original, moved)

    def test_graph_digest_covers_the_graph_and_import_script_but_not_the_seed_version(self):
        with tempfile.TemporaryDirectory() as directory:
            data_directory = Path(directory)
            (data_directory / "graph").mkdir()
            self.write_files(data_directory / "graph", {"b_nodes.csv": "id\n1\n", "a_edges.csv": "from,to\n"})
            self.write_files(data_directory, {"import_search_graph.cypher": "RETURN 1;\n", "SEED_VERSION": "one\n"})
            original_data_directory = seed.DATA_DIR
            seed.DATA_DIR = data_directory
            try:
                files = seed.graph_files()
                before = seed.content_digest(files)
                (data_directory / "SEED_VERSION").write_text("two\n", encoding="utf-8")
                after_new_version = seed.content_digest(seed.graph_files())
            finally:
                seed.DATA_DIR = original_data_directory
        self.assertEqual([path.name for path in files], ["a_edges.csv", "b_nodes.csv", "import_search_graph.cypher"])
        self.assertEqual(before, after_new_version)


class ListingSelectionTests(unittest.TestCase):
    PHOTO_FOLDER = "images/Căn hộ chung cư/Phường Tân Định"

    def build_source(self, directory: Path):
        """Create a three-listing dataset (one new, one both, one old) and its image manifest.

        Listing 2 ("both") lists an old-dataset photo the archive lacks, and all keys in
        master_v3.csv are NFD as v3 stores them.
        """
        nfd = lambda value: unicodedata.normalize("NFD", value)  # noqa: E731
        master_header = ["listing_id", "source", "location_link", "title", "images"]
        write_csv(directory / "master_v3.csv", master_header, [
            ["1", "new", "", "a", json.dumps([nfd(f"{self.PHOTO_FOLDER}/1/x_1_002.jpg"),
                                              nfd(f"{self.PHOTO_FOLDER}/1/x_1_001.jpg")])],
            ["2", "both", "", "b", json.dumps([nfd(f"{self.PHOTO_FOLDER}/2/x_2_001.jpg"),
                                               nfd(f"{self.PHOTO_FOLDER}/2/old-dataset_wm.jpg")])],
            ["3", "old", "https://maps?key=SECRET", "c", "[]"],
        ])
        write_csv(directory / "images_manifest.csv", ["listing_id", "object_key"], [
            ["1", f"{self.PHOTO_FOLDER}/1/x_1_002.jpg"], ["1", f"{self.PHOTO_FOLDER}/1/x_1_001.jpg"],
            ["2", f"{self.PHOTO_FOLDER}/2/x_2_001.jpg"],
        ])
        graph = directory / "graph"
        write_csv(graph / "neo4j_listing_nodes.csv", ["listing_node_id:ID(Listing)", "listing_id:string"],
                  [["1", "1"], ["2", "2"], ["3", "3"]])
        for name in ("neo4j_listing_near_amenity_relationships.csv", "neo4j_listing_in_ward_relationships.csv",
                     "neo4j_listing_in_former_area_relationships.csv"):
            write_csv(graph / name, [":START_ID(Listing)", ":END_ID(X)"], [["1", "x"], ["3", "x"]])
        write_csv(graph / "neo4j_listing_on_street_relationships.csv", [":START_ID(Listing)", ":END_ID(Street)"],
                  [["1", "street-kept"], ["3", "street-dropped"]])
        write_csv(graph / "neo4j_listing_in_cluster_relationships.csv", [":START_ID(Listing)", ":END_ID(GeoCluster)"],
                  [["2", "cluster-kept"], ["3", "cluster-dropped"]])
        write_csv(graph / "neo4j_street_nodes.csv", ["street_id:ID(Street)"], [["street-kept"], ["street-dropped"]])
        write_csv(graph / "neo4j_street_in_ward_relationships.csv", [":START_ID(Street)", ":END_ID(Ward)"],
                  [["street-kept", "w"], ["street-dropped", "w"]])
        write_csv(graph / "neo4j_geo_cluster_nodes.csv", ["cluster_id:ID(GeoCluster)"],
                  [["cluster-kept"], ["cluster-dropped"]])
        for name in ("neo4j_amenity_nodes.csv", "neo4j_ward_nodes.csv", "neo4j_former_admin_area_nodes.csv",
                     "neo4j_ward_mapped_from_former_area_relationships.csv"):
            write_csv(graph / name, ["id"], [["kept-whole-1"], ["kept-whole-2"]])

    def test_only_current_listings_and_their_streets_and_clusters_remain(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as output:
            self.build_source(Path(source))
            counts = prepare_seed_input.prepare(Path(source), Path(output), expected_count=2,
                                                images_manifest=Path(source) / "images_manifest.csv")
            master_text = (Path(output) / "v3/master_v3.csv").read_text(encoding="utf-8")
        self.assertEqual(counts["master_v3.csv"], 2)
        self.assertNotIn("SECRET", master_text)
        self.assertEqual(counts["neo4j_listing_near_amenity_relationships.csv"], 1)
        self.assertEqual(counts["neo4j_street_nodes.csv"], 1)
        self.assertEqual(counts["neo4j_street_in_ward_relationships.csv"], 1)
        self.assertEqual(counts["neo4j_geo_cluster_nodes.csv"], 1)
        self.assertEqual(counts["neo4j_amenity_nodes.csv"], 2)

    def test_wrong_listing_count_stops_the_build(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as output:
            self.build_source(Path(source))
            with self.assertRaises(ValueError):
                prepare_seed_input.prepare(Path(source), Path(output), expected_count=3857,
                                           images_manifest=Path(source) / "images_manifest.csv")

    def test_images_come_from_the_archive_in_nfc_and_photo_order(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as output:
            self.build_source(Path(source))
            counts = prepare_seed_input.prepare(Path(source), Path(output), expected_count=2,
                                                images_manifest=Path(source) / "images_manifest.csv")
            rows = {row["listing_id"]: json.loads(row["images"]) for row in csv.DictReader(
                (Path(output) / "v3/master_v3.csv").open(encoding="utf-8"))}
        self.assertEqual(rows["1"], [f"{self.PHOTO_FOLDER}/1/x_1_001.jpg", f"{self.PHOTO_FOLDER}/1/x_1_002.jpg"])
        self.assertEqual(rows["2"], [f"{self.PHOTO_FOLDER}/2/x_2_001.jpg"])
        self.assertTrue(all(key == unicodedata.normalize("NFC", key) for keys in rows.values() for key in keys))
        self.assertEqual(counts["images"], 3)

    def test_a_selected_listing_with_an_api_key_stops_the_build(self):
        rows = [{"listing_id": "1", "source": "new", "location_link": "https://maps?key=SECRET"}]
        with self.assertRaises(ValueError):
            prepare_seed_input.select_listing_ids(rows)


if __name__ == "__main__":
    unittest.main()
