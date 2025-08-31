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

PREV_LEAGUE_ID = "1121477093499543552"
LEAGUE_ID = "1180175940712742912"


def download_file(url, filename):
    try:
        with urllib.request.urlopen(url) as response:
            data = json.load(response)
            with open(filename, "w") as f:
                json.dump(data, f, indent=4)
            return data
    except Exception as e:
        print(f"Error downloading {filename}: {e}")


def main():
    league = League(PREV_LEAGUE_ID)
    draft_id = league.get_all_drafts()[0]["draft_id"]

    draft = download_file(
        f"https://api.sleeper.app/v1/draft/{draft_id}/picks", "data/2024_draft.json"
    )

    player = Players()
    players = player.get_all_players()
    player_costs = {}

    for p in draft:
        player = players.get(p["metadata"]["player_id"])
        if not player:
            print(f"Player {p['player_id']} not found in players")
            continue
        if player["position"] == "DEF":
            continue
        cost = int(p["metadata"].get("amount", 0))
        player_costs[player["full_name"]] = cost
    for p in sorted(player_costs, key=lambda p: player_costs[p], reverse=True):
        print(p, player_costs[p])


if __name__ == "__main__":
    main()
