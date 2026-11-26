"""Listings posted by signed-in users: lenient type conversion and validation.

Users post listings one by one through a form or many at once in a zip archive. Their values
are typed by hand or exported from spreadsheets, so this package converts the usual ways of
writing a value ("4,5 tỷ", "70m2", "2 PN") into the listing schema of docs/plan.html, and
reports every listing it cannot accept with the field and the reason.
"""
