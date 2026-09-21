"""A namespace-package leaf with no missing source target."""
import csv

def fields(line: str):
    return next(csv.reader([line]))
