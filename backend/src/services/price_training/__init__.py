"""Train the price model on the server, from the listings in PostgreSQL.

The steps of price_prediction/data_preparation (normalize labels, clean, remove outliers, encode)
run here on the catalog and on every listing users posted, with missing bedrooms and coordinates
filled in rather than dropped. A run trains the same three candidate models as
price_prediction/train-models.py, and the best one replaces the model being served only when it
beats it on a fixed holdout set. Runs start from a daily schedule (when enough new data arrived)
or from the "Huấn luyện ngay" button, one at a time across every backend process.
"""
