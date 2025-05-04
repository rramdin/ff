#/usr/local/bin/hrt-python /usr/local/venvs/atlvenv3/bin/python
import bs4
from dataclasses import dataclass, field
from enum import Enum
import collections
import itertools
import argparse
import csv
import datetime

FPPG_DECAY = 0.8

VERBOSE = False
MAX_AGE = 100

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

    def week_fppg(self, i):
        return self.weeks[i] * self.fppg

    def tostr(self):
        return f"{self.name} ({self.team}) (#{self.positional_rank}, fppg: {self.fppg:.2f} spg: {self.fppg*self.score/18:.2f})"

    def last_name(self):
        return self.name.split()[-1]

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
        f"    FPPG: {self.fppg:.3f} Score: {self.score:.2f} Taken: {self.taken} Picked: {self.picked}"
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

    if len(picked) >= n:
        raise RuntimeError("More picked than combos")

    combos = itertools.combinations(filtered, n - len(picked))
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
        for i in range(len(play)):
            print(f"Week{i+1}: ", ", ".join([p.last_name() for p in play[i]]))
        print()

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

def parse_args():
    parser = argparse.ArgumentParser(
        description="analyze", formatter_class=argparse.RawTextHelpFormatter
    )
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
    args = parse_args()
    players = load_players()
    load_data(players)
    load_stats(players)
    if not args.no_taken:
        load_taken(players)
    if not args.no_picked:
        load_picked(players)
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
    else:
        do_combos(players, args.pos, args.num_draft, args.num_play)

if __name__ == "__main__":
    main()

