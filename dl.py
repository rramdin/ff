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
URL = "https://api.sleeper.app/v1/league/1121477093499543552/rosters"

def parse_args():
    parser = argparse.ArgumentParser(
        description="analyze", formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('-r', '--roster', dest='roster', action="store_true")
    parser.add_argument('-q', '--query', dest='query', nargs='+')
    parser.add_argument('-p', '--pos', dest="pos")
    parser.add_argument('-d', '--draft', dest="num_draft", type=int, default=1)
    parser.add_argument('-l', '--play', dest="num_play", type=int, default=1)
    parser.add_argument('-v', '--verbose', dest="verbose", action="store_true")
    parser.add_argument('--no-taken', dest="no_taken", action="store_true")
    parser.add_argument('--no-picked', dest="no_picked", action="store_true")
    parser.add_argument('--max-age', dest="max_age", type=int, default=100)
    global VERBOSE, MAX_AGE
    args = parser.parse_args()
    VERBOSE = args.verbose
    MAX_AGE = args.max_age
    return args

def main():
    sys.exec
    with urllib.request.urlopen(URL) as f:
        json = f.read().decode('utf-8')
        print(json)

if __name__ == "__main__":
    main()

