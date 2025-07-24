import json

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

load_sleeper()

