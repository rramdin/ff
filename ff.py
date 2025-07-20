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
import os
import sys
import termios
import threading
import time
import logging

from sleeper_wrapper import League

VERBOSE = False
MAX_AGE = 100

# Whether to use pre-draft rosters or just keepers
PRE_DRAFT = True

MULTIPLIER_ADVANTAGE = 1.5
MULTIPLIER_TOSSUP = 1.0
MULTIPLIER_DISADVANTAGE = 0.9
MULTIPLIER_BAD = 0.5
MULTIPLIER_BYE = 0.0
local_state = []

LEAGUE_ID = "1180175940712742912"
MY_USER_ID = "1121497299739418624"

DRAFT_ID = "1252372867966832640"

REFRESH_RATE = 15  # seconds

NUM_RECOMMEND = 4

DRAFT_SETTINGS = {
"QB": [1, 2],
"TE": [1, 2],
"RB": [3, 5],
"WR": [2, 3],
"K":  [1, 1],
}

PICKS_FILE = "data/picks.json"
ROSTERS_FILE = "data/rosters.json"
USERS_FILE = "data/users.json"
MATCHUPS_FILE = "data/2025_matchups.json"
PLAYERS_FILE = "data/nfl_players.json"
STATE_FILE = "data/local_state.json"
PROJECTIONS_FILE = "data/rotowire-projections.csv"

class Matchup:
    def __init__(self, arr):
        self.is_home = arr[1].startswith("@")
        self.is_bye = arr[1].startswith("BYE")
        if self.is_bye:
            self.favor = 0
        else:
            self.opponent = arr[1].replace("@", "").strip()
            self.favor = int(arr[2])

    def get_favor(self):
        if self.favor >= 4:
            return MULTIPLIER_ADVANTAGE
        elif self.favor == 3:
            return MULTIPLIER_TOSSUP
        elif self.favor == 2:
            return MULTIPLIER_DISADVANTAGE
        elif self.favor == 1:
            return MULTIPLIER_BAD
        else:
            return MULTIPLIER_BYE

    def __str__(self):
        if self.is_bye:
            return "BYE"
        return f"{'@' if self.is_home else ''}{self.opponent} ({self.favor})"


@dataclass
class Player:
    name: str
    team: str
    position: str
    rank: int
    positional_rank: int
    score = 0.0
    fppg = 0.0
    age = 0
    height = 0
    weight = 0
    experience = None
    number = 0
    status = "UNK"
    sleeper_id = 0
    projection = 0.0
    fantasy_team = None
    is_override = False
    weeks: list[Matchup] = field(default_factory=list)
    week_fppgs: list[float] = field(default_factory=lambda: [0.0] * 18)

    def week_fppg(self, i):
        if i >= len(self.weeks):
            return 0.0
        return self.weeks[i].get_favor() * self.projection / 17.0

    def calc_score(self):
        stars = 0
        for i in range(len(self.weeks)):
            stars += self.weeks[i].favor
        points_per_star = self.projection / stars
        for i in range(len(self.weeks)):
            self.week_fppgs[i] = points_per_star * self.weeks[i].favor

    @property
    def picked(self):
        return self.fantasy_team and self.fantasy_team.id == MY_USER_ID

    @property
    def taken(self):
        return self.fantasy_team and self.fantasy_team.id != MY_USER_ID

    def tostr(self):
        t = 'N'
        if self.taken:
            t = 'T'
        elif self.picked:
            t = 'P'
        return f"{self.name} ({self.position}{self.positional_rank} {self.team}, fppg: {self.fppg:.2f} spg: {self.projection/18:.2f}) {t}"

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

    def print_all_fields(self):
        for attr in dir(self):
            if attr.startswith('_'):
                continue
            value = getattr(self, attr)
            if callable(value):
                continue
            if isinstance(value, list):
                value = ', '.join([str(v) for v in value])
            elif isinstance(value, dict):
                value = ', '.join([f"{k}: {v}" for k, v in value.items()])
            print(f"{attr}: {value}")

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
        f"    {'  '.join(['%.1f' % self.week_fppg(i) for i in range(18)])}\n"
        f"    FPPG: {self.projection/17.0:.3f} Score: {self.projection:.2f} Taken: {self.taken} Picked: {self.picked}\n"
        f"    sleeper: {self.sleeper_id} owner: {self.fantasy_team.name if self.fantasy_team else 'None'}\n"
        )


class PlayerLookup:
    def __init__(self):
        self.sleeper = {}
        self.name = {}
        self.id = collections.defaultdict(dict)

    def add(self, player):
        self.sleeper[player.sleeper_id] = player

        existing = self.name.get(player.name)
        if existing:
            if player.search_rank > existing.search_rank:
                logging.warning(f"{player.name} already in players_by_name, overwriting")
                self.name[player.name] = player
            else:
                logging.warning(f"{player.name} already in players_by_name, not overwriting")
        else:
            self.name[player.name] = player

        for attr in dir(player):
            if attr.endswith('_id'):
                the_id = getattr(player, attr)
                if the_id:
                    self.id[attr][getattr(player, attr)] = player

players = PlayerLookup()

class FantasyTeam:
    def __init__(self, name, team_id):
        self.name = name
        self.id = team_id
        self.players = []
        self.score = 0.0

    def add_player(self, player):
        if player.fantasy_team:
            player.fantasy_team.remove_player(player)
        self.players.append(player)
        player.fantasy_team = self

    def remove_player(self, player):
        self.players.remove(player)
        player.fantasy_team = None

    def calc_score(self):
        self.score = 0.0
        for player in self.players:
            self.score += player.projection
        if self.score > 0:
            self.score = self.score / len(self.players) / 17.0

    def __str__(self):
        return f"{self.name} ({len(self.players)} players, score: {self.score:.2f})"

UNKNOWN_TEAM = FantasyTeam("Unknown", "")
fantasy_teams = {
    "": UNKNOWN_TEAM,
}
fantasy_team_names = {
    UNKNOWN_TEAM.name: UNKNOWN_TEAM,
}

def load_2025_matchups(players):
    with open(MATCHUPS_FILE, "r") as f:
        j = json.loads(f.read())
        for player, weeks in j.items():
            p = players.name.get(player)
            if not p:
                logging.warning(f"Unknown player in matchups: {player}")
                continue
            for week in weeks:
                matchup = Matchup(week)
                p.weeks.append(matchup)
            p.calc_score()

    return players

def load_sleeper():
    position_mapping = {
        "QB": "QB",
        "FB": "RB",
        "TE": "TE",
        "K/P": "K",
        "LWR": "WR",
        "K": "K",
        "RWR": "WR",
        "WR": "WR",
        "SWR": "WR",
        "RB": "RB",
        "DEF": "DEF",
    }

    all_positions = set(['WLB', 'QB', 'SLB', 'P', 'RCB', 'FS', 'SS', 'RDE',
                         'FB', 'RILB', 'MLB', 'NB', 'TE', 'C', 'OL', 'OLB',
                         'NT', 'LILB', 'K/P', 'RT', 'ROLB', 'LWR', 'LCB', 'K',
                         'LOLB', 'LDE', 'LT', 'LEO', 'LS', 'RWR', 'LDT', 'DL',
                         'WR', 'DB', 'LB', 'LG', 'SWR', 'OT', 'RG', 'DT',
                         'RDT', 'WS', 'OG', 'RB', 'DEF'])

    with open(PLAYERS_FILE, "r") as f:
        j = json.loads(f.read())
        for idnum, info in j.items():
            name = info.get('full_name', "")
            if not name:
                continue
            if not info.get('active', False):
                continue

            matched_pos = info.get('depth_chart_position')
            if matched_pos:
                if matched_pos not in all_positions:
                    logging.warning(f"{matched_pos} is not a fantasy position, skipping {name}")
                matched_pos = position_mapping.get(matched_pos)
            if not matched_pos:
                for pos in info.get('fantasy_positions') or []:
                    if pos not in all_positions:
                        logging.warning(f"{matched_pos} is not a fantasy position, skipping {name}")
                    pos = position_mapping.get(pos)
                    if pos:
                        matched_pos = pos
                        break
            if not matched_pos:
                continue

            p = Player(name, info.get("team"), matched_pos, 0, 0)
            p.age = info.get('age', 0)
            p.weight = int(info.get('weight') or 0)
            p.experience = info.get('years_exp', None)
            p.number = info.get('number', 0)
            p.status = info.get('status', 'UNK')

            height = info.get('height', '')
            if height:
                if "'" in height:
                    feet, inches = height.split("'")
                    feet = int(feet.strip())
                    inches = int(inches.strip().replace('"', ''))
                    p.height = feet * 12 + inches
                else:
                    # Assume height is in inches if no feet/inches formatter_class
                    p.height = int(height.strip())
            else:
                p.height = 0

            p.sleeper_id = int(idnum)
            p.channel_id = info.get("channel_id")
            p.depth_chart_position = info.get("depth_chart_position")
            p.depth_chart_order = info.get("depth_chart_order")
            p.fantasy_data_id = info.get("fantasy_data_id")
            p.pandascore_id = info.get("pandascore_id")
            p.opta_id = info.get("opta_id")
            p.sportradar_id = info.get("sportradar_id")
            p.yahoo_id = info.get("yahoo_id")
            p.gsis_id = info.get("gsis_id")
            p.birth_date = info.get("birth_date")
            p.rotowire_id = info.get("rotowire_id")
            p.oddsjam_id = info.get("oddsjam_id")
            p.search_rank = info.get("search_rank")
            p.swish_id = info.get("swish_id")
            p.player_id = info.get("player_id")
            p.rotoworld_id = info.get("rotoworld_id")
            p.espn_id = info.get("espn_id")
            p.stats_id = info.get("stats_id")
            p.injury_status = info.get("injury_status")
            p.injury_notes = info.get("injury_notes")
            p.college = info.get("college")

            if p.birth_date:
                DATE=datetime.datetime(year=2024, month=12, day=1)
                bday = datetime.datetime.strptime(p.birth_date, '%Y-%m-%d')
                p.age = int((DATE - bday).days / 365.2425)
            else:
                p.age = 0

            players.add(p)

def load_league(players):
    global local_state
    with draft.lock:
        with open(USERS_FILE, "r") as f:
            for j in json.loads(f.read()):
                team_name = j.get("display_name")
                fantasy_team = FantasyTeam(team_name, j.get("user_id"))
                fantasy_teams[fantasy_team.id] = fantasy_team
                fantasy_team_names[fantasy_team.name] = fantasy_team

        for p in players.sleeper.values():
            p.fantasy_team = None

        with open(ROSTERS_FILE, "r") as f:
            j = json.loads(f.read())
            for r in j:
                field = 'keepers' if PRE_DRAFT else 'players'
                if not r.get(field):
                    continue
                for ps in r[field]:
                    try:
                        if p := players.sleeper.get(int(ps)):
                            fantasy_team = fantasy_teams.get(r['owner_id'], UNKNOWN_TEAM)
                            if p.fantasy_team != fantasy_team:
                                print(f"Adding player {p.name} to team {fantasy_team.name} ({fantasy_team.id})")
                                fantasy_team.add_player(p)
                        else:
                            logging.warning(f"Player {ps} not found in players")
                    except ValueError:
                        pass

        # Amend drafted/take during the draft
        local_state = LocalState(players)

    draft.reapply_all()

    for team in fantasy_teams.values():
        team.calc_score()

    return players


class Draft:
    def __init__(self):
        self.picks = []
        self.thread = None
        self.lock = threading.RLock()
        self.load()

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while True:
            self.refresh()
            time.sleep(REFRESH_RATE)

    def refresh(self):
        logging.debug("Refreshing draft picks...")
        os.system(f"curl -s \"https://api.sleeper.app/v1/draft/{DRAFT_ID}/picks\" > {PICKS_FILE}")
        draft.load()

    def reapply_all(self):
        for owner_id, player in self.picks:
            self.apply(owner_id, player)

    def apply(self, owner_id, player):
        if not owner_id and player.fantasy_team:
            logging.warning(f"Player {player.name} has no owner_id "
                            "but has a fantasy team, not removing from team")
            return

        fantasy_team = fantasy_teams.get(owner_id, UNKNOWN_TEAM)
        with self.lock:
            if fantasy_team is not None:
                fantasy_team.add_player(player)

            print()
            print("Drafting", player.name, "for ",
                  fantasy_team.name if fantasy_team else "Unknown Team")

    def load(self):
        if not os.path.exists(PICKS_FILE):
            logging.warning("Could not load draft from %s", PICKS_FILE)
            return
        with open(PICKS_FILE, "r") as f:
            j = json.loads(f.read())
            count = 0
            for p in j:
                count += 1
                if count <= len(self.picks):
                    continue
                player = players.sleeper.get(int(p['metadata']['player_id']))
                if not player:
                    logging.warning(f"Player {p['player_id']} not found in players")
                    continue
                owner_id = p["picked_by"]
                self.picks.append((owner_id, player))
                self.apply(owner_id, player)


draft = Draft()

def refresh_draft():
    draft.refresh()

def load_projections(players):
    with open(PROJECTIONS_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f, quotechar='"')
        header1 = next(reader)
        header2 = next(reader)
        header1 = [h.strip() for h in header1]
        header2 = [h.strip() for h in header2]

        fields = {}
        context = ""
        for i, h in enumerate(header1):
            if header1[i]:
                context = header1[i]
            if context:
                h = f"{context}-{header2[i]}"
            else:
                h = header2[i]
            fields[h] = i
        for r in reader:
            name = r[fields['Name']]
            p = players.name.get(name)
            if not p:
                logging.debug(f"skipping{name}")
                continue
            rushing_yds = int(r[fields['Rushing-YDS']])
            rushing_td = int(r[fields['Rushing-TD']])
            receiving_rec = int(r[fields['Receiving-REC']])
            receiving_yds = int(r[fields['Receiving-YDS']])
            receiving_td = int(r[fields['Receiving-TD']])
            passing_yds = int(r[fields['Passing-YDS']])
            passing_td = int(r[fields['Passing-TD']])
            proj = (
                (rushing_yds * 0.1) + (rushing_td * 6) +
                (receiving_yds * 0.1) + (receiving_td * 6) +
                (passing_yds * 0.04) + (passing_td * 6)
                + (receiving_rec * 0.5)
            )
            p.projection = proj

def do_rankings(players):
    positions = ['QB', 'RB', 'WR', 'TE', 'K']
    all_players = []
    by_position = collections.defaultdict(list)
    for p in players.sleeper.values():
        all_players.append(p)
        by_position[p.position].append(p)

    # all_players.sort(key=lambda p: (-p.fppg, p.pos_order(), p.last_name()))
    all_players.sort(key=lambda p: (-p.projection, p.pos_order(), p.last_name()))
    for i, p in enumerate(all_players):
        p.rank = i + 1

    for pos in positions:
        # by_position[pos].sort(key=lambda p: (-p.fppg, p.last_name()))
        by_position[pos].sort(key=lambda p: (-p.projection, p.last_name()))
        for i, p in enumerate(by_position[pos]):
            p.positional_rank = i + 1


def combo_score(players, n):
    wks = []
    play = []
    for i in range(18):
        players.sort(key=lambda p: -p.week_fppg(i))
        play.append(players[0:n])
        wks.append(sum(p.week_fppg(i) for p in play[-1]))
    return sum(wks), play

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
        if len(p.weeks) < 18:
            continue
        filtered.append(p)
    filtered.sort(key=lambda p:p.positional_rank)

    if len(picked) >= n:
        raise RuntimeError("More picked than combos")

    limit = 50
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
    for p in players.sleeper.values():
        by_position[p.position].append(p)
    return by_position

def print_players():
    with draft.lock:
        for p in sorted(players.sleeper.values(), key = lambda p: p.fppg):
            print(p.tostr())

def do_combos(players, pos, num_draft, num_play):
    by_position = by_pos(players)
    combos = do_combo(by_position[pos], num_draft, num_play)
    return combos

def print_roster():
    with draft.lock:
        team_count = collections.defaultdict(int)
        total_age = 0
        mine = []
        for p in players.sleeper.values():
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
        if len(mine) > 0:
            print(f"Avg Age: {total_age/len(mine):2f}")
        print()
        for p in mine:
            print(p.tostr())

def refresh_rosters():
    league = League(LEAGUE_ID)
    rosters = league.get_rosters()
    users = league.get_users()
    with open(ROSTERS_FILE, "w") as f:
        json.dump(rosters, f, indent=4)
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

class LocalState:
    def __init__(self, players):
        self.players = players
        self.overrides = {}
        self.load()

    def load(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                j = json.loads(f.read())
                overrides = j.get('overrides', {})
                for player_id, user_id in j.get('overrides', {}).items():
                    if user_id is None:
                        continue
                    self.overrides[int(player_id)] = user_id
        self.apply()

    def apply(self):
        for player, user_id in self.overrides.items():
            p = self.players.sleeper.get(int(player))
            if p:
                fantasy_teams.get(user_id, UNKNOWN_TEAM).add_player(p)
                p.is_override = True


    def save(self):
        with open(STATE_FILE, "w") as f:
            json.dump({
                'overrides': self.overrides,
            }, f, indent=4)

    def pick(self, player):
        with draft.lock:
            fantasy_team = fantasy_teams.get(MY_USER_ID, UNKNOWN_TEAM)
            fantasy_team.add_player(player)
            self.overrides[player.sleeper_id] = fantasy_team.id
            player.is_override = True
            self.save()
    def take(self, player):
        with draft.lock:
            UNKNOWN_TEAM.add_player(player)
            self.overrides[player.sleeper_id] = player.fantasy_team.id
            player.is_override = True
            self.save()
    def unpick(self, player):
        with draft.lock:
            if player.fantasy_team:
                player.fantasy_team.remove_player(player)
            self.overrides[player.sleeper_id] = None
            player.is_override = True
            self.save()
    def untake(self, player):
        with draft.lock:
            if player.fantasy_team:
                player.fantasy_team.remove_player(player)
            self.overrides[player.sleeper_id] = None
            player.is_override = True
            self.save()
    def clear(self, player):
        with draft.lock:
            if player.sleeper_id in self.overrides:
                del self.overrides[player.sleeper_id]
            if player.fantasy_team:
                player.fantasy_team.remove_player(player)
            player.is_override = False
            self.save()
        load_league(self.players)
        self.apply()
    def set_team(self, player, fantasy_team):
        with draft.lock:
            fantasy_team.add_player(player)
            self.overrides[player.sleeper_id] = fantasy_team.id
            player.is_override = False
            self.save()

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
    parser.add_argument('--max-age', dest="max_age", type=int, default=100)
    parser.add_argument('--refresh', dest="refresh", action="store_true")
    global VERBOSE, MAX_AGE
    args = parser.parse_args()
    VERBOSE = args.verbose
    MAX_AGE = args.max_age
    return args

def prompt_set_team(player):
    teams = [t for t in fantasy_teams.values()]
    prompts = []
    def set_team(team):
        team.add_player(player)
    sel = "123456789ABCDEFHIJKLMNOPQRSTUVWXYZ"
    for i, team in enumerate(teams):
        prompts.append((sel[i], team.name, lambda t=team: local_state.set_team(player, t)))
    prompts.append(("c", "Clear local state", lambda: local_state.clear(player)))
    prompts.append(("q", "Quit", lambda: True))

    while not prompt(prompts, lambda: print(player.tostrl())):
        pass

def query_player():
    from fzf import Fzf, fzf
    chooser = Fzf()

    player_lookup = {p.tostr(): p for p in players.sleeper.values()}
    choice = chooser.prompt(
        player_lookup,
        prompt="Choose player",
    )[0]
    p = player_lookup[choice]
    draft = lambda: local_state.pick(p)
    undraft = lambda: local_state.unpick(p)
    take = lambda: local_state.take(p)
    untake = lambda: local_state.untake(p)
    clear = lambda: local_state.clear(p)
    info = lambda: p.print_all_fields()
    set_team = lambda: prompt_set_team(p)

    prompts = [
        ("i", "Info", info),
        ("d", "Draft", draft),
        ("u", "Undraft", undraft),
        ("t", "Take", take),
        ("n", "Untake", untake),
        ("c", "Clear local state", clear),
        ("s", "Set team", set_team),
        ("q", "Quit", lambda: True),
    ]
    while not prompt(prompts, lambda: print(p.tostrl())):
        pass

def draft_analyze():
    team = fantasy_teams[MY_USER_ID]
    needed = {}
    for pos, conf in DRAFT_SETTINGS.items():
        needed[pos] = conf[1]
    for p in team.players:
        if p.position in needed:
            needed[p.position] -= 1
    print("Needed:", ','.join([f"{p}: {n}" for p, n in needed.items()]))

    combos = []
    to_print = collections.defaultdict(set)
    for pos, n in needed.items():
        if n < 0:
            print(f"Drafted too many {pos}s, should reconfigure draft settings")
        if n <= 0:
            continue
        print("Analyzing", pos)
        with draft.lock:
            cs = do_combos(players, pos, DRAFT_SETTINGS[pos][1], DRAFT_SETTINGS[pos][0])

        i = 0
        for c in reversed(cs):
            ps, score, play = c
            for p in ps:
                if p.fantasy_team:
                    continue
                if p.sleeper_id in to_print[pos]:
                    continue
                combos.append(c)
                print("adding", pos, p.sleeper_id)
                to_print[pos].add(p.sleeper_id)
                i += 1
                if i >= NUM_RECOMMEND:
                    break
            if i >= NUM_RECOMMEND:
                break
    print_combos(combos)
    for pos, top in to_print.items():
        print()
        print(f"Top {NUM_RECOMMEND} {pos}s")
        for sleeper_id in top:
            print(players.sleeper[sleeper_id].tostr())



def input_combos():
    pos_max = {
        "QB": 1,
        "RB": 3,
        "WR": 3,
        "TE": 2,
        "K": 1}
    pos = None
    while not pos:
        print("Position: ", end="")
        pos = input()
        if not pos:
            return
        if pos not in pos_max:
            print(f"Invalid position: {pos}")
            pos = None

    num_play = None
    while not num_play:
        print("Number to play: ", end="")
        num_play = input()
        if not num_play:
            return
        try:
            num_play = int(num_play)
            if num_play < pos_max[pos]:
                print(f"Must play at least {pos_max[pos]} {pos}s")
                num_play = None
        except ValueError:
            print(f"Invalid number: {num_play}")
            num_play = None

    num_draft = None
    while not num_draft:
        print("Number to draft: ", end="")
        num_draft = input()
        if not num_draft:
            return
        try:
            num_draft = int(num_draft)
            if num_draft < num_play:
                print(f"Must draft at least {num_play} {pos}s")
                num_draft = None
        except ValueError:
            print(f"Invalid number: {num_draft}")
            num_draft = None

    with draft.lock:
        combos = do_combos(players, pos, num_draft, num_play)
    print_combos(combos)


def print_rosters():
    with draft.lock:
        teams = [t for t in fantasy_teams.values()]
        teams.sort(key=lambda t: (t.score, t.name), reverse=True)
        for i, team in enumerate(teams):
            print(f"#{i+1}", team)
            for p in team.players:
                print(f"  {p.tostr()}")
            print()

def print_prompt(prompts):
    print("\nMake choice:")
    for p in prompts:
        print(f" ({p[0]}) {p[1]}")

def handle_input(prompts, c):
    print()
    for p in prompts:
        if c == p[0]:
            return p[2]()

def prompt(prompts, before_fn=None):
    try:
        # get stdin and save current terminal parameters
        fd = sys.stdin.fileno()
        orig = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)

        # arrange things so we can get one character at a time
        new[3] = new[3] & ~termios.ICANON
        new[6][termios.VMIN] = 1
        new[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSAFLUSH, new)

        should_stop = False
        while not should_stop:
            if before_fn:
                before_fn()
            print_prompt(prompts)
            should_stop = handle_input(prompts, sys.stdin.read(1))
    finally:
        # reset terminal parameters to their original values
        termios.tcsetattr(fd, termios.TCSAFLUSH, orig)

    return True

def main():
    global players, local_state

    load_sleeper()
    load_projections(players)
    do_rankings(players)
    load_2025_matchups(players)

    if args.refresh:
        refresh_rosters()
    load_league(players)

    if PRE_DRAFT and args.refresh:
        draft.start()

    prompts = [
        ("p", "Query player", query_player),
        ("r", "Print roster", print_roster),
        ("v", "Print players", print_players),
        ("d", "Draft combo", input_combos),
        ("t", "Print teams", print_rosters),
        ("x", "Refresh draft", refresh_draft),
        ("a", "Analyze", draft_analyze),
        ("q", "Quit", lambda: sys.exit(0)),
    ]
    while prompt(prompts):
        pass

if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(format=format,
                        level=logging.DEBUG if args.verbose else logging.INFO,
                        datefmt="%Y-%m-%d %H:%M:%S")

    main()

