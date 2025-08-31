import csv, re

postions = ["QB", "RB", "WR", "TE", "K"]
rows = []
for position in postions:
    with open(
        f"data/FantasyPros_Fantasy_Football_2025_{position}_Matchups.csv", "r"
    ) as file:
        reader = csv.reader(file)
        next(reader)  # Skip the header row
        for row in reader:
            rows.append(row)


def parse_name(name):
    name_maps = {}
    for token in ["Sr.", "Jr."]:
        spl = name.split(token)
        if len(spl) > 1:
            return spl[0].strip()  # + " " + token
    m = re.match(r"^[A-Z]\.[A-Z]\.", name)
    if m:
        spl = name.split(m.group(0))
        return m.group(0) + spl[1]

    m = re.match(r"^[A-Z][A-Za-z] ", name)
    if m:
        spl = name.split(m.group(0))
        return m.group(0) + spl[1]

    if name.startswith("Amon-Ra St. Brown"):
        return "Amon-Ra St. Brown"

    m = re.match(r"([-'A-Za-z ]+)[A-Z]\.", name)
    if not m:
        print(name)
        return None
    name = m.group(1)
    if name.endswith(" II"):
        name = name[:-3].strip()
    if name.endswith(" III"):
        name = name[:-4].strip()
    return name


players = {}
for r in rows:
    if len(r) < 3:
        continue
    if r[1].endswith(" FA"):
        continue
    name = parse_name(r[1])
    if not name:
        print(name)
        continue
    weeks = []
    for i in range(2, len(r)):
        week = i - 2
        c = r[i].strip()
        if c == "BYE":
            weeks.append([week, "BYE", 0])
            continue
        m = re.match("(vs.|at)\s+([A-Z]+)This is a (\d) star", c)
        if not m:
            print("Failed to parse:", c)
            print(r)
            continue
        if m.group(1) == "vs.":
            weeks.append([week, m.group(2), int(m.group(3))])
        elif m.group(1) == "at":
            weeks.append([week, "@" + m.group(2), int(m.group(3))])
    players[name] = weeks

with open("data/2025_matchups.json", "w") as f:
    import json

    json.dump(players, f, indent=2)
