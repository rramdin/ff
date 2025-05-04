#!/Library/Frameworks/Python.framework/Versions/Current/bin/python3
import bs4
from dataclasses import dataclass, field
from enum import Enum
import collections
import itertools
import argparse
import csv
import datetime
import json
import os, sys

FPPG_DECAY = 0.8

VERBOSE = False
MAX_AGE = 100

NO_TAKEN = False
NO_PICKED = False

PRE_DRAFT = False

MULTIPLIER_ADVANTAGE = 1.5
MULTIPLIER_TOSSUP = 1.0
MULTIPLIER_DISADVANTAGE = 0.9
MULTIPLIER_BAD = 0.5
MULTIPLIER_BYE = 0.0

@dataclass
class Player:
    name: str
    team: str
    position: str
    rank: int
    positional_rank: int
    weeks: list[int] = field(default_factory=lambda: [])
    fppg: dict[int, float] = field(default_factory=lambda:{})
    score = 0.0
    fppg = 0.0
    picked = False
    taken = False
    age = 0
    height = 0
    weight = 0
    experience = None
    number = 0
    status = "UNK"
    sleeper_id = 0

    def week_fppg(self, i):
        return self.weeks[i] * self.fppg

    def tostr(self):
        t = 'N'
        if self.taken:
            t = 'T'
        elif self.picked:
            t = 'P'
        return f"{self.name} ({self.position}{self.positional_rank} {self.team}, fppg: {self.fppg:.2f} spg: {self.fppg*self.score/18:.2f}) {t}"

    def last_name(self):
        return self.name.split()[-1]

    def pos_order(self):
        if self.position == "QB":
            return 1
        elif self.position == "RB":
            return 2
        elif self.position == "WR":
            return 3
        elif self.position == "TE":
            return 4
        elif self.position == "K":
            return 5
        else:
            return 10

    def tostrl(self):
        height_feet = int(self.height/12)
        height_inches = self.height - height_feet*12
        exp = "Unknown"
        if self.experience is None:
            exp = 'Unknown'
        elif int(self.experience) == 0:
            exp = 'Rookie'
        else:
            exp = self.experience
        return (f"------ {self.name} ({self.team} #{self.number}) ------\n"
        f"    {self.position}{self.positional_rank} Overall: {self.rank} Status: {self.status} Season: {exp}\n"
        f"    Age: {self.age} Ht: {height_feet}'{height_inches}\""
                f" Wt: {self.weight} lbs\n"
        f"    {'  '.join(['%.1f' % w for w in self.weeks])}\n"
        f"    FPPG: {self.fppg:.3f} Score: {self.score:.2f} Taken: {self.taken} Picked: {self.picked}\n"
        f"    sleeper: {self.sleeper_id}"
        )

def load_players():
    players = {}
    with open("raw.html", "r") as f:
        parsed = bs4.BeautifulSoup(f, features="lxml")
        hd = parsed.find('head')
        rows = hd.findChildren("tr", recursive=False)
        for row in rows:
            cells = []
            for cell in row.findChildren('td', recursive=False):
                cells.append(cell)
            rank = int(cells[0].text)
            position = ''.join(filter(lambda c: not c.isdigit(), cells[1].text))
            positional_rank = int(cells[1].text.replace(position, ""))
            name = ' '.join(cells[2].text.split())
            team = ' '.join(cells[3].text.split())
            player = Player(name, team, position, rank, positional_rank)

            for i in range(4, len(cells)):
                bgcolor = cells[i]['bgcolor']
                if bgcolor == "#00B050": #green
                    player.weeks.append(MULTIPLIER_ADVANTAGE)
                elif bgcolor == "#FFFFFF": #white
                    player.weeks.append(MULTIPLIER_TOSSUP)
                elif bgcolor == "#FFFF00": #yellow
                    player.weeks.append(MULTIPLIER_DISADVANTAGE)
                elif bgcolor == "#FF0000": #red
                    player.weeks.append(MULTIPLIER_BAD)
                elif bgcolor == "#000000": # bye
                    player.weeks.append(MULTIPLIER_BYE)
                else:
                    raise("Unknown color")
            player.score = score(player.weeks)
            players[name] = player
    return players

def load_sleeper(players):
    sids = {}
    with open("sleeper_players.json", "r") as f:
        j = json.loads(f.read())
        for idnum, info in j.items():
            name = info.get('full_name')
            if not name:
                continue
            p = players.get(name)
            if p:
                p.sleeper_id = idnum
                sids[idnum] = p
            else:
                if VERBOSE:
                    print("sleeper with unknown player", name, idnum)
    with open("rosters.json", "r") as f:
        j = json.loads(f.read())
        for r in j:
            field = 'keepers' if PRE_DRAFT else 'players'
            for ps in r[field]:
                if p := sids.get(ps):
                    p.owner = r['owner_id']
                    if r['owner_id'] == '1121497299739418624':
                        if not NO_PICKED:
                            p.picked = True
                    else:
                        if not NO_TAKEN:
                            p.taken= True


def load_stats(players):
    with open(f"players.csv", "r") as f:
        reader = csv.DictReader(f)
        for r in reader:
            name = r['display_name']
            player = players.get(name)
            if player:
                bday = r['birth_date']
                if bday:
                    DATE=datetime.datetime(year=2024, month=12, day=1)
                    bday = datetime.datetime.strptime(bday, '%Y-%m-%d')
                    player.age = int((DATE - bday).days / 365.2425)
                player.height = int(r['height' or 0])
                player.weight = int(r['weight' or 0])
                player.experience = r['years_of_experience']
                player.number = int(r['jersey_number'] or 0)
                player.status = r['status']
            elif r['status'] != 'RET':
                if VERBOSE:
                    print("Stats for unknown player:", name)

def load_data(players):
    for year in (2023, 2022, 2021):
        with open(f"fppg_{year}.csv", "r") as f:
            reader = csv.DictReader(f, quotechar='"')
            for r in reader:
                name = r['NAME']
                fppg = float(r['FPPG'])
                player = players.get(name)
                if not player:
                    if VERBOSE:
                        print("skipping", name)
                    continue
                if player.fppg == 0.0:
                    player.fppg = fppg
                else:
                    player.fppg = (player.fppg*FPPG_DECAY) + (fppg*(1.0-FPPG_DECAY))

def load_taken(players):
    with open("taken.txt", "r") as taken:
        for l in taken:
            l = l.strip()
            if not l:
                continue
            players[l].taken = True

def load_picked(players):
    with open("picked.txt", "r") as picked:
        for l in picked:
            l = l.strip()
            if not l:
                continue
            players[l].picked = True

def score(wks):
    return sum(wks)

def combo_score(players, n):
    wks = []
    play = []
    for i in range(len(players[0].weeks)):
        players.sort(key=lambda p: -p.week_fppg(i))
        play.append(players[0:n])
        wks.append(sum(p.week_fppg(i) for p in play[-1]))
    return score(wks), play

def do_combo(players, n, m):
    picked = []
    filtered = []
    for p in players:
        if p.picked:
            picked.append(p)
            continue
        if p.taken:
            continue
        if p.age > MAX_AGE:
            continue
        filtered.append(p)
    filtered.sort(key=lambda p:p.positional_rank)

    if len(picked) >= n:
        raise RuntimeError("More picked than combos")

    limit = 500
    if n > 4:
        limit = 30
    elif n > 3:
        limit = 50

    combos = itertools.combinations(filtered[:limit], n - len(picked))
    ret = []
    for c in combos:
        c = list(c) + picked
        score, play = combo_score(c, m)
        ret.append((c, score, play))
    ret.sort(key=lambda c: c[1])
    return ret

def print_combos(combos):
    NUM_PRINT = 500

    rank = min(len(combos), NUM_PRINT)
    for players, score, play in combos[-NUM_PRINT:]:
        pstr = " - ".join([p.tostr() for p in players])
        print(f"#{rank}/{len(combos)} {pstr} -> {score/18:.2f}")
        rank -= 1
        # for i in range(len(play)):
        #     print(f"Week{i+1}: ", ", ".join([p.last_name() for p in play[i]]))
        # print()

def by_pos(players):
    by_position = collections.defaultdict(list)
    for p in players.values():
        by_position[p.position].append(p)
    return by_position

def print_players(players):
    for p in sorted(players.values(), key = lambda p: p.fppg):
        print(p.tostr())

def do_combos(players, pos, num_draft, num_play):
    by_position = by_pos(players)
    combos = do_combo(by_position[pos], num_draft, num_play)
    print_combos(combos)

def print_roster(players):
    team_count = collections.defaultdict(int)
    total_age = 0
    mine = []
    for p in players.values():
        if p.picked:
            mine.append(p)
            team_count[p.team] += 1
            total_age += p.age
    mine.sort(key=lambda p: (p.pos_order(), p.last_name()))

    for p in mine:
        print(p.tostrl())
    print()
    teams = sorted([t for t in team_count], key=lambda t: (team_count[t], t))
    for t in teams:
        print(t, team_count[t])
    print(f"Avg Age: {total_age/len(mine):2f}")
    print()
    for p in mine:
        print(p.tostr())

def refresh_rosters():
    os.system("curl https://api.sleeper.app/v1/league/1121477093499543552/rosters > rosters.json 2>/dev/null")

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
    parser.add_argument('--no-refresh', dest="no_refresh", action="store_true")
    global VERBOSE, MAX_AGE, NO_PICKED, NO_TAKEN
    args = parser.parse_args()
    VERBOSE = args.verbose
    MAX_AGE = args.max_age
    NO_PICKED = args.no_picked
    NO_TAKEN = args.no_taken
    return args

def query_player():
    from fzf import Fzf, fzf
    chooser = Fzf()
    choices = chooser.prompt(
        [p.tostr() for p in players.values()],
        prompt="Choose player",
        multi=True,
        height=20,
        no_sort=True,
    )
def prompt():
    print("Make choice:")
    print(" (p) query player")

    c = input()
    if c == 'p':
        query_player()
    elif c == 'q':
        sys.exit(0)

def main():
    global args, players
    args = parse_args()
    players = load_players()
    load_data(players)
    load_stats(players)
    if not args.no_refresh:
        refresh_rosters()
    load_sleeper(players)
    if not args.no_taken:
        load_taken(players)
    if not args.no_picked:
        load_picked(players)
    while True:
        choice = prompt()

        choice = input()
    if VERBOSE:
        print_players(players)
    if args.query:
        for name, player in players.items():
            match = True
            for q in args.query:
                if q.lower() not in name.lower():
                    match = False
                    break
            if match:
                print(player.tostrl())
    elif args.roster:
        print_roster(players)
    else:
        pos_max = {
            "QB": 1,
            "RB": 3,
            "WR": 3,
            "TE": 2,
            "K": 1}.get(args.pos, 0)

        if args.num_play > pos_max:
            raise RuntimeError(f"Cannot play more than {pos_max} {args.pos}s")

        do_combos(players, args.pos, args.num_draft, args.num_play)

if __name__ == "__main__":
    main()

