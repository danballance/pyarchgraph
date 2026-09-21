"""Read through a namespace package using both import spellings."""
import plugins.readers.csv_reader
from plugins.readers import csv_reader

def fields(line: str):
    return csv_reader.fields(line)
