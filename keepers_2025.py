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


def get_keepers(league_id):
    league = League(league_id)
    draft_id = league.get_all_drafts()[0]["draft_id"]

    draft = download_file(
        f"https://api.sleeper.app/v1/draft/{draft_id}/picks", f"data/{league_id}_draft.json"
    )

    player = Players()
    players = player.get_all_players()
    player_costs = {}
    keepers = set()

    for p in draft:
        player = players.get(p["metadata"]["player_id"])
        if not player:
            #print(f"Player {p['player_id']} not found in players")
            continue
        if player["position"] == "DEF":
            continue
        cost = int(p["metadata"].get("amount", 0))
        pid = int(p["metadata"]["player_id"])
        player_costs[pid] = cost
        if p["is_keeper"]:
            keepers.add(pid)

    #print(json.dumps(draft, indent=4))
    for week in range(1, 13):
        #print(f"Week {week}")
        tx = league.get_transactions(week)
        for t in sorted(tx, key=lambda t:t["created"]):
            if t["status"] == "complete" and t["type"] == "waiver":
                if t["adds"]:
                    for player_id, _ in t["adds"].items():
                        p = players[player_id]
                        if p["position"] == "DEF":
                            continue
                        cost = t["settings"]["waiver_bid"]
                        player_costs[int(player_id)] = cost
                if t["drops"]:
                    for player_id, _ in t["drops"].items():
                        p = players[player_id]
                        if p["position"] == "DEF":
                            continue
                        pid = int(player_id)
                        if pid in player_costs:
                            del player_costs[pid]
    return player_costs, keepers

def print_keepers(league_id, costs):
    league = League(league_id)
    rosters = league.get_rosters()
    users = league.get_users()
    player = Players()
    players = player.get_all_players()
    um = {}
    for u in users:
        um[u["user_id"]] = u
    rosters.sort(key=lambda r: (-r["metadata"]["record"].count("W"), r["metadata"]["record"].count("L")))
    for r in rosters:
        u = um[r["owner_id"]]
        if "team_name" not in u["metadata"]:
            name = u["display_name"]
        else:
            name = f'{u["metadata"]["team_name"]} - {u["display_name"]}'
        wins = r["metadata"]["record"].count("W")
        losses = r["metadata"]["record"].count("L")
        print(name, f"({wins}-{losses})")
        print("-" * len(name))
        pc = {}
        for pid in r["players"]:
            p = players[pid]
            if p["position"] == "DEF":
                continue
            cost = costs.get(int(pid), 0)
            pc[pid] = (-cost, p["last_name"])
        for pid in sorted(r["players"], key=lambda p: pc.get(p, (0,""))):
            p = players[pid]
            if pid not in pc:
                continue
            cost = pc[pid][0]
            if not cost:
                c = "N/A"
            else:
                c = f"${-cost}"
            print(f'{p["full_name"]:24} {c}')
        print()


def main():
    costs_2024, keepers_2024 = get_keepers(PREV_LEAGUE_ID)
    costs_2025, keepers_2025 = get_keepers(LEAGUE_ID)

    for k in keepers_2024:
        if k in keepers_2025:
            if k in costs_2025:
                del costs_2025[k]

    print_keepers(LEAGUE_ID, costs_2025)

    with open("data/2025_keeper_costs.json", "w") as f:
        json.dump(costs_2025, f, indent=4)


if __name__ == "__main__":
    main()
