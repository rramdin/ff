with open("matchups.txt", "r") as file:
    lines = file.readlines()

i = 0
teams = {}
while i < len(lines):
    team = lines[i].strip()
    i += 1
    while i < len(lines):
        line = lines[i].strip()
        if line == "1":
            break
        print("discarding", team, line)
        i += 1
    keep = []
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if line.startswith("Week 1 Opponent"):
            break
        keep.append(line)
        print(team, keep)

    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if line.startswith("#"):
            break
    team = team.split(" ")[1]
    teams[team] = keep
    if i >= len(lines):
        break
    i -= 1


def process(keep):
    matchups = []
    for i in range(0, 18):
        idx = i * 3
        matchups.append(keep[idx : idx + 3])
    return matchups


team_names = [
    "CLE",
    "BUF",
    "DET",
    "LAR",
    "HOU",
    "TEN",
    "KC",
    "PIT",
    "ARI",
    "NYJ",
    "CAR",
    "SEA",
    "GB",
    "LV",
    "NYG",
    "BAL",
    "NO",
    "OAK",
    "DEN",
    "MIA",
    "ATL",
    "NE",
    "LAC",
    "IND",
    "TB",
    "WAS",
    "CHI",
    "MIN",
    "JAX",
    "PHI",
    "CIN",
    "SF",
    "DAL",
]

team_map = {
    "Cardinals": "ARI",
    "Texans": "HOU",
    "Jets": "NYJ",
    "Panthers": "CAR",
    "Titans": "TEN",
    "Rams": "LAR",
    "Seahawks": "SEA",
    "Jaguars": "JAX",
    "49ers": "SF",
    "Chiefs": "KC",
    "Falcons": "ATL",
    "Dolphins": "MIA",
    "Saints": "NO",
    "Buccaneers": "TB",
    "Colts": "IND",
    "Eagles": "PHI",
    "Broncos": "DEN",
    "Steelers": "PIT",
    "Packers": "GB",
    "Bears": "CHI",
    "Patriots": "NE",
    "Chargers": "LAC",
    "Browns": "CLE",
    "Bills": "BUF",
    "Raiders": "LV",  # Note: LV is used for Las Vegas Raiders
    "Ravens": "BAL",
    "Lions": "DET",
    "Commanders": "WAS",  # Note: WAS is used for Washington Commanders
    "Vikings": "MIN",
    "Bengals": "CIN",
    "Giants": "NYG",
    "Cowboys": "DAL",
}

matchups = {}
for team, keep in teams.items():
    matchups[team_map[team]] = process(keep)

with open("data/2024_matchups.json", "w") as f:
    import json

    json.dump(matchups, f, indent=2)
