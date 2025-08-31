import json
import re
import csv

def load_sleeper():
    pos = set()
    team = set()
    with open("data/nfl_players.json", "r") as f:
        j = json.loads(f.read())
        for idnum, info in j.items():
            name = info.get('full_name', "")
            if "Stefon" in name:
                print(json.dumps(info, indent=2))
            for positions in info.get("fantasy_positions") or []:
                pos.add(positions)
            if info.get("depth_chart_position"):
                pos.add(info.get("depth_chart_position"))
            if info.get("team"):
                team.add(info.get("team"))

    print("Positions:", pos)
    print("Teams:", team)

#load_sleeper()

INJURIES_FILE = "data/2025_draft_shark_injury_predictions.csv"

def load_injury_predictions():
    with open(INJURIES_FILE, "r") as f:
        reader = csv.DictReader(f)
        for r in reader:
            print(r)
            match = re.match(r"([ \S]+([a-z\.]| I*))[A-Z]+ \d+$", r["player"])
            name = match.group(1)
            career_injuries = int(r['career_injuries'])
            injury_risk = r['injury_risk']
            injury_risk_per_season = r['injury_risk_per_season']
            durability = r['durability']
            projected_games_missed = r['projected_games_missed']

#load_injury_predictions()

import questionary

# questionary.rawselect("which items",
#                    choices=["one", "two", "three"]).ask() # , use_shortcuts=True).ask()

DRAFT_SHARKS_FILE = "data/2025_predraft_ds_osb.csv"
def load_draft_sharks(players):
    with open(DRAFT_SHARKS_FILE, "r") as f:
        reader = csv.DictReader(f, quotechar='"')
        for r in reader:
            print(r)
            print(r["Player"])
            print(float(r["ADP"]))
            print(int(float(r["Overall Tier"])))
            print(int(float(r["Pos. Tier"])))
            print(float(r["DS Proj"]))
            print(float(r["Auction $"].replace("$","")))

load_draft_sharks(None)
