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

from sleeper_wrapper import League, Players

VERBOSE = False

LEAGUE_ID = "1180175940712742912"


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
    league = League(LEAGUE_ID)
    players = Players()
    all_players = players.get_all_players()
    nfl_players = {}
    for i, p in all_players.items():
        if p["sport"] == "nfl":
            nfl_players[i] = p
    with open("data/nfl_players.json", "w") as f:
        json.dump(nfl_players, f)

if __name__ == "__main__":
    main()

