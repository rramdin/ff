#!/Library/Frameworks/Python.framework/Versions/Current/bin/python3
import bs4
from dataclasses import dataclass, field
from enum import Enum
import collections
import itertools
import argparse
import csv
import datetime
import subprocess
import urllib.request
import sys
import json
import os


VERBOSE = False

LEAGUE_ID = "1180175940712742912"

DRAFT_ID = "1252372867966832640"

def parse_args():
    parser = argparse.ArgumentParser(
        description="analyze", formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('-v', '--verbose', dest="verbose", action="store_true")
    global VERBOSE
    args = parser.parse_args()
    VERBOSE = args.verbose
    return args

def main():
    os.system(f"curl \"https://api.sleeper.app/v1/draft/{DRAFT_ID}/picks\" > data/picks.json")

if __name__ == "__main__":
    main()

