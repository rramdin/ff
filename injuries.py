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

LEAGUES = [
    "1180175940712742912",
    "1268282452565049344",
    "1277095761078669312",
    "1257067166914588672",
    "1282516995866112000",
    "1265709285279539200",
    "1284260662914080768",
    "1286947284654764032",
    "1275999905017495552",
    "1282846638703902720",
    "1284013477932834816",
    "1286833972302594048",
    "1274575394938302464",
    "1277400873391128576",
    "1273867796538724352",
    "1250246602933665792",
    "1277074711418777600",
    "1278830058294743040",
]

MY_USER_ID = "1121497299739418624"

def print_injuries(players, league_id):
    league = League(league_id)
    l = league.get_league()
    rosters = league.get_rosters()
    um = {}
    problems = False
    for r in rosters:
        if r["owner_id"] != MY_USER_ID:
            continue
        active = 0
        for pid in r["starters"]:
            p = players[pid]
            if p["injury_status"]:
                problems = True
                print("*****",
                      l["name"], "-", p["first_name"], p["last_name"],"-",
                      p["injury_status"],
                      "*****")
            else:
                #print(l["name"], "-", p["first_name"], p["last_name"],"-",
                #      p["injury_status"])
                active += 1
    return problems, f'{l["name"]} - {active} / {len(r["starters"])} Good'

def main():
    player = Players()
    players = player.get_all_players()
    report = []
    problems = False

    print(datetime.datetime.now())
    print()

    for l in LEAGUES:
        p, r = print_injuries(players, l)
        if p:
            problems = True
        report.append(r)

    if problems:
        print()
    for r in report:
        print(r)




if __name__ == "__main__":
    main()
