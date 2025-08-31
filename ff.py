#!/Users/rramdin/ff/venv/bin/python
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
import re
import urllib.request
import texteditor
import textwrap
import math
import getpass

from sleeper_wrapper import League, Players
from functools import cache
from thefuzz import process, fuzz
from rich import print
from rich.progress import track
from rich.highlighter import Highlighter
from rich.panel import Panel
from rich.table import Table
from rich.console import Console



VERBOSE = False

# Whether to use pre-draft rosters or just keepers
PRE_DRAFT = True

# Local state store notes and liked/disliked players. The editor is selected
# based on the EDITOR environment variable.
local_state = []

# During the draft, how often to poll for draft updates. Rate-limit to avoid
# overwhelming the sleeper API. When --refresh is specified, we start a second
# thread to periodically update results. When players are drafted, some info is
# printed.
REFRESH_RATE = 15  # seconds

# Files downloaded from sleeper when --refresh is specified
PICKS_FILE = "data/picks.json"
DRAFT_FILE = "data/draft.json"
ROSTERS_FILE = "data/rosters.json"
USERS_FILE = "data/users.json"
MATCHUPS_FILE = "data/2025_matchups.json"
PLAYERS_FILE = "data/nfl_players.json"

# Rotowire PPG projections
PROJECTIONS_FILE = "data/rotowire-projections.csv"

# Fantasy pros auction values based on 12 team, half PPR
# https://www.fantasypros.com/nfl/auction-values/calculator.php
DRAFT_VALUE_FILE = "data/draft_values_2025.txt"

# Draft sharks auction values
# https://www.draftsharks.com/league/draft/board/mvp?id=781007
DRAFT_VALUE_FILE = "data/2025_predraft_ds_osb.csv"
DRAFT_VALUE_FILE_GEN = "data/2025-draft-sharks-auction-values-half-ppr.csv"

# Sleeper projections
SLEEPER_PROJECTIONS_FILE = "data/2025_sleeper_auction_proj.csv"

# Offensive line and defense rankings.
OL_FILE = "data/2025_ol_rankings.txt"
DEF_FILE = "data/2025_defense_rankings.txt"

# Injury predictions
INJURIES_FILE = "data/2025_draft_shark_injury_predictions.csv"

# For loading first downs per route run
ROUTES_RUN_FILE = "data/2024_first_down_per_route_run.csv"

# COMBOS -- Moneyball!
#
# Can we make better player based on strength of schedule. The biggest bias
# here is the Bye-week, so this is a good way of finding WR3/4/5 that goes with
# your WR1 and WR2. Obviously drafting Chase and JJ is the best combo of WRs,
# but in later rounds of the draft, this can give insights of who might be
# advantageous to draft.
#
# In this analysis, we only measure the first COMBOS_MAX_WEEK, this could be
# set to the length of the regular season or to include the playoffs. We also
# only count COMBOS_NUM_GAMES, discarding the worst weeks, because we are
# trying to win games, not maximize points-per-season -- i.e. explosiveness is
# a little better than consistency.
#
# DRAFT_SETTINGS is your goal roster, e.g. QB [1,2] means you want to roster 2
# QBs and start 1 each week. Current rosters are considered, so if you've
# already drafted 2 wide receivers, only combos containing them are considered.
#
# Finally, if a --max-age is specified or MAX_AGE is set below, players over
# that age will not be included in Combos. Who wants a 32 year old WR4?
#
# When analyzing strength-of-schedule (SOS):
# - We have projected points per game (PPG)
# - We have weekly matchup strength in 1-5 "stars" from FantasyPros
# We take the forecasted total points for the season, and assume
# PERCENTAGE_POINTS_VAR varies by week. We take the total variable
# portion and divide it by the player's total stars, and give that
# many points per star to each game to get their per-game forecast.
PERCENTAGE_POINTS_VAR = 0.3
NUM_RECOMMEND = 4
COMBOS_MAX_WEEK = 13
COMBOS_NUM_GAMES = 10
# 16 players; 1 DEF, 1 K
DRAFT_SETTINGS = {
"QB": [1, 2],
"RB": [3, 6],
"WR": [2, 4],
"TE": [1, 2],
}
MAX_AGE = 100

OVERALL_TIER = "Overall"
TOP_TIERS = "TOP"
ALL_TIERS = "ALL"

CONFIG_NAME = f"{getpass.getuser()}"
try:
    CONFIG = __import__(CONFIG_NAME)
except:
    logging.error("Create a file at %s.py with sleeper"
                  " LEAGUE_ID and MY_USER_ID", CONFIG_NAME)
    logging.error("Go to https://sleeper.com/")
    logging.error("Go to your league, your league ID is in the URL")
    logging.error("Go to https://api.sleeper.app/v1/user/<user_id>"
                  " and find user_id or use get_sleeper_user.py")
    sys.exit(1)

def load_config():
    def get(s, default=None):
        global CONFIG
        if hasattr(CONFIG, s):
            return getattr(CONFIG, s)
        return default
    global STATE_FILE, LEAGUE_ID, MY_USER_ID, DRAFT_ID, KEEPERS_FILE
    STATE_FILE = get("STATE_FILE",
                     f"data/local_state_{getpass.getuser()}.json")
    LEAGUE_ID = get("LEAGUE_ID")
    MY_USER_ID = get("MY_USER_ID")
    DRAFT_ID = get("DRAFT_ID")
    KEEPERS_FILE = get("KEEPERS_FILE")

load_config()

class Matchup:
    def __init__(self, arr):
        self.is_home = arr[1].startswith("@")
        self.is_bye = arr[1].startswith("BYE")
        if self.is_bye:
            self.favor = 0
        else:
            self.opponent = arr[1].replace("@", "").strip()
            self.favor = int(arr[2])

    def __str__(self):
        if self.is_bye:
            return "BYE"
        return f"{'@' if self.is_home else ''}{self.opponent} ({self.favor})"

@dataclass
class Team:
    name: str
    long_name: str
    emoji: str
    color: str
    ol_ranking: int = 0
    def_ranking: int = 0

    @property
    def namec(self):
        return f"[bold #{self.color}]{self.name}[/bold #{self.color}]"

    def __str__(self):
        return f"[bold #{self.color}]{self.long_name}[/bold #{self.color}]"


teams = {}

class WeekFPPGs(collections.UserList):
    def __init__(self, obj=None):
        super().__init__(obj if obj else list())
        for _ in range(18):
            self.append(0.0)
    def __str__(self):
        return ', '.join("%.2f" % f for f in self)

class PlayerName(str):
    @property
    def unf(self):
        return super().__str__()

    def __str__(self):
        return f"[bold blue]{super().__str__()}[/bold blue]"


@dataclass
class Player:
    name: PlayerName
    team_name: str
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
    keeper_cost = 0
    draft_value = 0
    actual_cost = 0
    actual_draft_pos = None
    fantasy_team = None
    is_override = False

    adp = 1000.0
    overall_tier = 15.0
    pos_tier = 15.0
    is_keeper = False
    sleeper_auction_value = 0.0
    ds_note = None

    team:Team = None

    injury_risk = "Unknown"
    career_injuries = 0
    injury_risk_per_season = 0.0
    durability = 0
    projected_games_missed = 0.0

    weeks: list[Matchup] = field(default_factory=list)
    week_fppgs: WeekFPPGs = field(default_factory=WeekFPPGs)

    routes_run = 0
    first_downs_per_route_run = 0.0
    first_downs_per_route_run_rank = 0

    def __hash__(self):
        return hash(self.sleeper_id)

    def week_fppg(self, i):
        if i >= len(self.weeks):
            return 0.0
        return self.week_fppgs[i]

    def adj_projection(self):
        return self.projection * (17 - self.projected_games_missed) / 17

    def name_str(self):
        return f"[bold blue]{self.name}[/bold blue]"

    def calc_score(self):
        stars = 0
        proj = self.adj_projection()
        for i in range(len(self.weeks)):
            stars += self.weeks[i].favor
        vari = proj * PERCENTAGE_POINTS_VAR
        fixed = (proj - vari) / (len(self.weeks) - 1)
        points_per_star = vari / stars
        for i in range(len(self.weeks)):
            if self.weeks[i].favor == 0:
                self.week_fppgs[i] = 0
            else:
                self.week_fppgs[i] = fixed + points_per_star * self.weeks[i].favor

    @property
    def picked(self):
        return self.fantasy_team and self.fantasy_team.id == MY_USER_ID

    @property
    def taken(self):
        return self.fantasy_team and self.fantasy_team.id != MY_USER_ID

    def tostr(self, unf=False, emoji=True, notes=True):
        t = 'N'
        if self.taken:
            t = 'T'
        elif self.picked:
            t = 'P'
        if self.team:
            oldef = f" OL{self.team.ol_ranking} DEF{self.team.def_ranking}"
            if emoji:
                emoji_str = f"{self.team.emoji}  "
            else:
                emoji_str = ""

            team_info  = (f"{emoji_str}"
                          f"{self.team.name if unf else self.team.namec}"
                          f" {self.depth_chart_position}{self.depth_chart_order}")
        else:
            oldef = ""
            team_info = "Free Agent"

        if notes:
            if self.sleeper_id in local_state.notes:
                  note = local_state.notes[self.sleeper_id]
                  if not note[0]:
                      color = "#d70000"
                      prefix = "[Dislike] "
                  elif note[0] == "Love":
                      color = "#5f00ff"
                      prefix = "[Draft] "
                  else:
                      color = "#5fd700"
                      prefix = f"[{note[0]}] "
                  if not unf:
                      prefix = f"[bold {color}]{prefix}[/bold {color}]"
            else:
                prefix = ""

            if self.ds_note:
                note = f"[{self.ds_note}] "
                if not unf:
                    color = "#FC6A03"
                    prefix += f"[bold {color}]{note}[/bold {color}]"
        else:
            prefix = ""

        if self.actual_cost:
            actual_cost = f" Actual: ${self.actual_cost}"
        else:
            actual_cost = ""

        if not DRAFT_ID and self.keeper_cost:
            keeper_cost = f" K: ${self.keeper_cost}"
        else:
            keeper_cost = ""

        return (f"{prefix }{self.name.unf if unf else self.name}"
                f" ({self.position}{self.positional_rank}) {team_info}"
                f" spg: {self.adj_projection()/18:.2f}{oldef}"
                f" ADP: {self.adp}"
                f"{keeper_cost}"
                f" Val: ${self.draft_value}{actual_cost}")

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
        m = {}
        for attr in dir(self):
            if attr.startswith('_'):
                continue
            value = getattr(self, attr)
            if callable(value):
                continue
            if isinstance(value, WeekFPPGs):
                value = str(value)
            if isinstance(value, list):
                value = ', '.join([str(v) for v in value])
            elif isinstance(value, dict):
                value = ', '.join([f"{k}: {v}" for k, v in value.items()])
            m[attr] = value
        print(m)

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

        if self.sleeper_id in local_state.notes:
            note = local_state.notes[self.sleeper_id]
            note_text = textwrap.fill(note[1])
            note_text = textwrap.indent(note_text, "    ")
            if note[0] == "Dislike":
                note_color = "red"
            elif note[0] == "Love":
                note_color = "green"
            else:
                note_color = "yellow"
            note_str = f"\n[{note_color}]{note_text}[/{note_color}]"
        else:
            note_str = ""
        short_name = self.name.lower().replace("'", "").replace(" ", "-")
        status_color = "green" if self.status == "Active" else "red"
        status = f"[bold {status_color}]{self.status}[/bold {status_color}]"

        injury_risk = (f"Injury: {self.injury_risk}"
                       f" - Career: {self.career_injuries}"
                       f" Risk/Season: {self.injury_risk_per_season*100:.2f}%"
                       f" Proj Missed: {self.projected_games_missed:.1f}")
        if "High" in self.injury_risk:
            inj_color = "red"
        elif "Medium" in self.injury_risk:
            inj_color = "yellow"
        elif "Unknown" in self.injury_risk:
            inj_color = "yellow"
        else:
            inj_color = "green"
        injury_risk = f"[{inj_color}]{injury_risk}[/{inj_color}]"

        fdprr = ""
        if self.routes_run:
            fdprr = (f"    Routes Run: {self.routes_run}"
                     f" First Downs/Route: {self.first_downs_per_route_run}"
                     f" Rank: {self.first_downs_per_route_run_rank}\n")

        return (f"------ {self.name} ({self.team if self.team else "[grey]Free Agent[/grey]"} #{self.number}) ------\n"
        f"    {self.tostr(notes=False)}\n"
        f"    {self.position}{self.positional_rank} Overall: {self.rank}"
                f" Status: {status}"
                f" Season: {exp}"
                f" Tier: {self.overall_tier}"
                f" Pos Tier: {self.pos_tier}\n"
        f"    Age: {self.age} Ht: {height_feet}'{height_inches}\""
                f" Wt: {self.weight} lbs\n"
        f"    {'  '.join(['%.1f' % self.week_fppg(i) for i in range(9)])}\n"
        f"    {'  '.join(['%.1f' % self.week_fppg(i) for i in range(9, 18)])}\n"
        f"    FPPG: {self.projection/17.0:.3f} Score: {self.projection:.2f} Taken: {self.taken} Picked: {self.picked}\n"
        f"    {injury_risk}\n"
        f"    sleeper: {self.sleeper_id} owner: {self.fantasy_team.namec if self.fantasy_team else 'None'}"
                f"{' OVERRIDE' if self.is_override else ''}\n"
                f"{fdprr}"
#        f"    https://fantasydata.com/nfl/{short_name}-fantasy/{self.fantasy_data_id}/\n"
        f"    https://www.rotowire.com/football/player/{short_name}-{self.rotowire_id}\n"
#        f"    https://www.fubo.tv/welcome/athlete/{self.sportradar_id}/"
        f"{note_str}"
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
                logging.debug(f"{player.name} already in players_by_name, overwriting")
                self.name[player.name] = player
            else:
                logging.debug(f"{player.name} already in players_by_name, not overwriting")
        else:
            self.name[player.name] = player

        for attr in dir(player):
            if attr.endswith('_id'):
                the_id = getattr(player, attr)
                if the_id:
                    self.id[attr][getattr(player, attr)] = player

    def find(self, token):
        if token in teams:
            return None

        p = self.sleeper.get(token)
        if p: return p

        p = self.name.get(token)
        if p: return p

        try:
            player_id = int(token)
            p = self.sleeper.get(player_id)
            if p: return p
        except ValueError:
            pass

        name = str(token)
        for suffix in [' Jr.', ' Sr.', ' III', ' II']:
            if name.endswith(suffix):
                name = name[:-len(suffix)].strip()
        p = players.name.get(name)
        if p: return p

        tokens = process.extract(name, players.name.keys(), scorer=fuzz.token_set_ratio)
        tokens.sort(key=lambda x: x[1], reverse=True)
        new_name = tokens[0][0]
        if tokens[0][1] > 80:
            name = new_name
            p = players.name.get(name)
        else:
            logging.info("Could not resolve %s -> %s", name, new_name)
        return p


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
        self.calc_score()

    def remove_player(self, player):
        self.players.remove(player)
        player.fantasy_team = None
        self.calc_score()

    def calc_score(self):
        self.score = 0.0
        for player in self.players:
            self.score += player.projection
        if self.score > 0:
            self.score = self.score / len(self.players) / 17.0

    @property
    def namec(self):
        color = "#d78700"
        if self.id == MY_USER_ID:
            color = "green"
        return f"[bold {color}]{self.name}[/bold {color}]"

    def __str__(self):
        return f"{self.name} ({len(self.players)} players, score: {self.score:.2f})"

UNKNOWN_TEAM = FantasyTeam("Unknown", "")
fantasy_teams = {
    "": UNKNOWN_TEAM,
}
fantasy_team_names = {
    UNKNOWN_TEAM.name: UNKNOWN_TEAM,
}
fantasy_team_roster_id = {
    0: UNKNOWN_TEAM,
}

def print_header(s):
    print(s)
    print("-" * len(s))

def load_2025_matchups(players):
    with open(MATCHUPS_FILE, "r") as f:
        j = json.loads(f.read())
        for player, weeks in track(j.items(), description="Loading matchups"):
            p = players.find(player)
            if not p:
                logging.info(f"Unknown player in matchups: {player}")
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
                         'RDT', 'WS', 'OG', 'RB', 'DEF', 'PR'])
    with open(PLAYERS_FILE, "r") as f:
        j = json.loads(f.read())
        for idnum, info in track(j.items(), description="Loading players"):
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

            p = Player(PlayerName(name), info.get("team"), matched_pos, 0, 0)
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

            p.sleeper_id = idnum
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

    team_map = {
        "ARI": ["Arizona Cardinals", "🔴"],
        "ATL": ["Atlanta Falcons", "🪶"],
        "BAL": ["Baltimore Ravens", "💜"],
        "BUF": ["Buffalo Bills", "🦬"],
        "CAR": ["Carolina Panthers", "🐈‍⬛"],
        "CHI": ["Chicago Bears", "🐻"],
        "CIN": ["Cincinnati Bengals", "🐅"],
        "CLE": ["Cleveland Browns", "🏈"],
        "DAL": ["Dallas Cowboys", "🤠"],
        "DEN": ["Denver Broncos", "🐴"],
        "DET": ["Detroit Lions", "🦁"],
        "GB": ["Green Bay Packers", "🧀"],
        "HOU": ["Houston Texans", "🤠"],
        "IND": ["Indianapolis Colts", "🐴"],
        "JAX": ["Jacksonville Jaguars", "🐆"],
        "KC": ["Kansas City Chiefs", "🏹"],
        "LV": ["Las Vegas Raiders", "⚔️"],
        "LAC": ["Los Angeles Chargers", "⚡️"],
        "LAR": ["Los Angeles Rams", "🐏"],
        "MIA": ["Miami Dolphins", "🐬"],
        "MIN": ["Minnesota Vikings", "🛡"],
        "NE": ["New England Patriots", "🇺🇸"],
        "NO": ["New Orleans Saints", "⚜️"],
        "NYG": ["New York Giants", "🗽"],
        "NYJ": ["New York Jets", "✈️"],
        "PHI": ["Philadelphia Eagles", "🦅"],
        "PIT": ["Pittsburgh Steelers", ": 🪙"],
        "SF": ["San Francisco 49ers", "⛏"],
        "SEA": ["Seattle Seahawks", "🦅"],
        "TB": ["Tampa Bay Buccaneers", "🏴‍☠️"],
        "TEN": ["Tennessee Titans", "☄️"],
        "WAS": ["Washington Commanders", "🏈"],
     }

    team_colors = {
         "ARI": "97233F",
         "ATL": "A71930",
         "BAL": "241773",
         "BUF": "00338D",
         "CAR": "0085CA",
         "CHI": "C83803",
         "CIN": "FB4F14",
         "CLE": "FF3C00",
         "DAL": "041E42",
         "DEN": "FB4F14",
         "DET": "0076B6",
         "GB": "203731",
         "HOU": "0080C5",
         "IND": "002C5F",
         "JAX": "006778",
         "KC": "E31837",
         "LAC": "FFC20E",
         "LAR": "FFD100",
         "LV": "A5ACAF",
         "MIA": "008E97",
         "MIN": "4F2683",
         "NE": "002244",
         "NO": "D3BC8D",
         "NYG": "A71930",
         "NYJ": "125740",
         "PHI": "2B8C4E",
         "PIT": "FFB612",
         "SEA": "69BE28",
         "SF": "B3995D",
         "TB": "A71930",
         "TEN": "4B92DB",
         "WAS": "5A1414",
     }

    for p in track(players.sleeper.values(), "Loading teams"):
        if not p.team_name:
            continue
        team_name = p.team_name
        team = teams.get(team_name)
        if not team:
            team = Team(team_name,
                        team_map[team_name][0],
                        team_map[team_name][1],
                        team_colors[team_name])
            teams[team.name] = team
            teams[team.long_name] = team
        p.team = team

    with open(SLEEPER_PROJECTIONS_FILE, "r") as f:
        reader = csv.DictReader(f)
        for r in reader:
            name = r['player']
            if name in teams:
                continue
            p = players.find(name)
            if not p:
                logging.error("Could not find %s", name)
                continue
            p.sleeper_auction_value = int(r['cost'].replace('$', ''))


def load_league(players):
    global local_state, fantasy_team_roster_id
    with draft.lock:
        for p in players.sleeper.values():
            p.fantasy_team = None

        with open(USERS_FILE, "r") as f:
            for j in track(json.loads(f.read()), "Loading league"):
                team_name = j.get("display_name")
                fantasy_team = FantasyTeam(team_name, j.get("user_id"))
                fantasy_teams[fantasy_team.id] = fantasy_team
                fantasy_team_names[fantasy_team.name] = fantasy_team

        fantasy_team_roster_id = {}
        with open(ROSTERS_FILE, "r") as f:
            j = json.loads(f.read())
            for i, r in track(enumerate(j), "Loading rosters", total=12):
                user_id = r.get('owner_id', "0")
                fantasy_team = fantasy_teams.get(r['owner_id'], UNKNOWN_TEAM)
                fantasy_team_roster_id[int(r['roster_id'])] = fantasy_team
                for cowner_id in r.get('co_owners') or []:
                    fantasy_teams[cowner_id] = fantasy_team

                if DRAFT_ID:
                    continue
                field = 'keepers' if PRE_DRAFT else 'players'
                if not r.get(field):
                    continue
                for ps in r[field]:
                    try:
                        if p := players.sleeper.get(ps):
                            if p.fantasy_team != fantasy_team:
                                print(f"Adding player {p.name} to team {fantasy_team.namec} ({fantasy_team.id})")
                                fantasy_team.add_player(p)
                                if field == "keepers":
                                    p.is_keeper = True
                                    p.actual_cost = int(float(p.keeper_cost))
                                    p.actual_draft_pos = 0
                        else:
                            logging.info(f"Player {ps} not found in players")
                    except ValueError:
                        pass

        # Amend drafted/take during the draft
        local_state = LocalState(players)

    draft.reapply_all()

    for team in fantasy_team_roster_id.values():
        team.calc_score()

    return players

def load_draft_values(players):
    with open(DRAFT_VALUE_FILE, "r") as f:
        reader = csv.DictReader(f, quotechar='"')
        for r in reader:
            name = r["Player"]
            for suffix in [' Jr.', ' Sr.', ' III', ' II']:
                if name.endswith(suffix):
                    name = name[:-len(suffix)].strip()
            if name in teams:
                continue
            p = players.find(name)
            if not p:
                logging.info("Could not lookup %s", name)
                continue

            p.draft_value = int(float(r["Auction $"].replace("$","")))
            p.projection = float(r["DS Proj"])
            p.adp = float(r["ADP"])
            p.overall_tier = int(float(r["Overall Tier"]))
            p.pos_tier = int(float(r["Pos. Tier"]))
            p.ds_note = r["Note"]


def load_draft_values_gen(players):
    with open(DRAFT_VALUE_FILE_GEN, "r") as f:
        reader = csv.DictReader(f, quotechar='"')
        for r in reader:
            name = r["Player"]
            for suffix in [' Jr.', ' Sr.', ' III', ' II']:
                if name.endswith(suffix):
                    name = name[:-len(suffix)].strip()
            if name in teams:
                continue
            p = players.find(name)
            if not p:
                logging.error("Could not lookup %s", name)
                continue

            p.draft_value = int(float(r["DS AuctionValue"].replace("$","")))
            p.projection = float(r["DS Proj"])


def load_draft_values_old(players):
    #   1.  Ja'Marr Chase (CIN - WR)  $62
    with open(DRAFT_VALUE_FILE, "r") as f:
        for line in track(f, "Loading draft values", total=150):
            line = line.strip()
            if not line:
                continue
            parts = line.split('$')
            if len(parts) != 2:
                logging.warning(f"Invalid draft value line: {line}")
                continue
            value = int(parts[1].strip())

            name = parts[0].strip()
            name = '.'.join(name.split('(')[0].split(".")[1:]).strip()
            p = players.find(name)
            if p:
                p.draft_value = value
            else:
                logging.info(f"Player {name} not found in players")

def load_ol_def_rankings(players):
    def fix_team(s):
        s = s.strip().upper()
        return {
            "TAMPA BAY BUCCANEERS": "TB",
            "LOS ANGELES RAMS": "LAR",
            "LOS ANGELES CHARGERS": "LAC",
            "GREEN BAY PACKERS": "GB",
            "SAN FRANCISCO 49ERS": "SF",
            "KANSAS CITY CHIEFS": "KC",
            "JACKSONVILLE JAGUARS": "JAX",
            "LAS VEGAS RAIDERS": "LV",
            "NEW ENGLAND PATRIOTS": "NE",
            "NEW YORK GIANTS": "NYG",
            "NEW YORK JETS": "NYJ",
            "NEW ORLEANS SAINTS": "NO",
        }.get(s, s)[0:3]

    with open(OL_FILE, "r") as f:
        for line in track(f, description="Loading O-Line rankings", total=32):
            line = line.strip()
            if not line:
                continue
            m = re.match(r'(\d+)\.\s+(.*) [–-].*2024 PRESEASON RANKING', line)
            if not m:
                continue
            short_name = fix_team(m.group(2))
            if short_name in teams:
                team = teams[short_name]
                team.ol_ranking = int(m.group(1))
            else:
                print(m.group(2))
        for team in teams.values():
            if not team.ol_ranking:
                logger.warning("No O-Line ranking for team", team.name)
    with open(DEF_FILE, "r") as f:
        for line in track(f, description="Loading defense rankings", total=32):
            line = line.strip()
            if not line:
                continue
            m = re.match(r'(\d+)\.\s+(.*)$', line)
            if not m:
                continue
            short_name = fix_team(m.group(2))
            if short_name in teams:
                team = teams[short_name]
                team.def_ranking = int(m.group(1))
            else:
                print(m.group(2))
        for team in teams.values():
            if not hasattr(team, 'def_ranking'):
                logger.warning("No Defense ranking for team", team.name)

def load_keeper_costs(players):
    if not KEEPERS_FILE:
        return
    with open(KEEPERS_FILE, "r") as f:
        j = json.loads(f.read())
        for player_id, cost in track(j.items(), "Loading keeper costs"):
            player = players.sleeper.get(player_id)
            if not player:
                logging.warning(f"Player {p['player_id']} not found in players")
                continue
            player.keeper_cost = cost

def load_extra(players):
    with open(INJURIES_FILE, "r") as f:
        reader = csv.DictReader(f)
        for r in track(reader, description="Loading injury predictions", total=200):
            match = re.match(r"([ \S]+([a-z\.]| I*))[A-Z]+ \d+$", r["player"])
            name = match.group(1)
            p = players.find(name)
            if not p:
                logging.info("Could not look up player for injury: %s", name)
                continue
            p.career_injuries = int(r['career_injuries'])
            p.injury_risk = r['injury_risk'].strip()
            p.injury_risk_per_season = float(r['injury_risk_per_season'].replace("%",""))/100
            p.durability = float(r['durability'])
            p.projected_games_missed = float(r['projected_games_missed'])
    with open(ROUTES_RUN_FILE, "r") as f:
        reader = csv.DictReader(f)
        for r in track(reader, description="Loading first downs/route stats", total=50):
            p = players.find(r["name"])
            if not p:
                logging.warning("Could not lookup player for routes run for '%s'",
                                r["name"])
                continue
            p.routes_run = r['routes']
            p.first_downs_per_route_run = r['first_downs_per_route_run']
            p.first_downs_per_route_run_rank = r['rank']



def download_file(url, filename):
    try:
        with urllib.request.urlopen(url) as response:
            data = json.load(response)
            with open(filename, "w") as f:
                json.dump(data, f, indent=4)
            logging.info(f"Downloaded {filename}")
    except Exception as e:
        logging.error(f"Error downloading {filename}: {e}")

class Draft:
    def __init__(self):
        self.picks = []
        self.thread = None
        self.lock = threading.RLock()
        self.slot_to_team = {}
        self.initial_load = True
        self.is_sim = False

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.refresh()
        self.thread.start()

    def start_sim(self):
        self.is_sim = True
        self.refresh()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while True:
            self.refresh()
            time.sleep(REFRESH_RATE)

    def refresh(self):
        if not DRAFT_ID:
            logging.warning("DRAFT_ID is not set, skipping draft refresh")
            return
        logging.debug("Refreshing draft picks...")
        if not self.is_sim:
            download_file(f"https://api.sleeper.app/v1/draft/{DRAFT_ID}/picks", PICKS_FILE)
            download_file(f"https://api.sleeper.app/v1/draft/{DRAFT_ID}", DRAFT_FILE)
        self.load()

    def reapply_all(self):
        for info in self.picks:
            self.apply(*info)

    def apply(self, picked_by, draft_slot, player,
              amount, pick_no, draft_round):
        if not draft_slot and player.fantasy_team:
            logging.warning(f"Player {player.name} has no draft_slot "
                            "but has a fantasy team, not removing from team")
            return

        if picked_by:
            fantasy_team = fantasy_teams.get(picked_by, UNKNOWN_TEAM)
        else:
            fantasy_team = self.slot_to_team[draft_slot]

        with self.lock:
            if fantasy_team is not None:
                fantasy_team.add_player(player)

        player.actual_cost = int(float(amount))
        player.actual_draft_pos = max(pick_no-24, 0)
        if self.initial_load:
            print(f"{fantasy_team.namec} drafted {player.name} for ${amount}")
        else:
            print(Panel(f"{fantasy_team.namec if fantasy_team else 'Unknown Team'}"
                        f" drafted {player.name} for ${amount}"
                        f" - Pick #{pick_no} Round {draft_round}"
                        f"\n\n{player.tostrl()}"
                        f"\n\n{fantasy_team.namec}'s team:"
                        f" {', '.join(p.name.unf for p in fantasy_team.players)}",
                        title=f"{datetime.datetime.now().time().strftime("%H:%M:%S")} Player Drafted"))


        if not self.initial_load:
            draft_analyze(verbose=False)
            print_tier_info(OVERALL_TIER, player.overall_tier)
            print_tier_info(player.position, player.pos_tier)

    def load(self):
        if os.path.exists(DRAFT_FILE):
            with open(DRAFT_FILE, "r") as f:
                j = json.loads(f.read())
                self.slot_to_id = {}
                for slot, roster_id in j.get('slot_to_roster_id', {}).items():
                    self.slot_to_team[int(slot)] = fantasy_team_roster_id.get(int(roster_id), UNKNOWN_TEAM)
        else:
            logging.warning("Could not load draft metadata from %s", DRAFT_FILE)
            return
        if not os.path.exists(PICKS_FILE):
            logging.warning("Could not load draft from %s", PICKS_FILE)
            return
        with open(PICKS_FILE, "r") as f:
            j = json.loads(f.read())
            count = 0
            added = 0
            for p in j:
                count += 1
                if count <= len(self.picks):
                    continue
                player = players.sleeper.get(p['metadata']['player_id'])
                if not player:
                    logging.warning(f"Player {p['player_id']} not found in players")
                    continue
                draft_slot = p["draft_slot"]
                picked_by = p.get("picked_by")
                amount = p['metadata']['amount']
                pick_no = p['pick_no']
                draft_round = p['round']
                info = [picked_by, int(draft_slot), player, amount, pick_no, draft_round]
                self.picks.append(info)
                self.apply(*info)
                added += 1
                if self.is_sim:
                    target = 20 if self.initial_load else 1
                    if added >= target:
                        break
        self.initial_load = False

draft = Draft()

def refresh_draft():
    draft.refresh()

def load_projections_old(players):
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
        for r in track(reader, description="Loading projections", total=300):
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

    all_players.sort(key=lambda p: (-p.adj_projection(), p.pos_order(), p.last_name()))
    for i, p in enumerate(all_players):
        p.rank = i + 1

    for pos in positions:
        by_position[pos].sort(key=lambda p: (-p.adj_projection(), p.last_name()))
        for i, p in enumerate(by_position[pos]):
            p.positional_rank = i + 1


def combo_score(players, n):
    wks = []
    play = []
    for i in range(COMBOS_NUM_GAMES):
        players.sort(key=lambda p: -p.week_fppg(i))
        play.append(players[0:n])
        wks.append(sum(p.week_fppg(i) for p in play[-1]))
    if len(wks) > COMBOS_MAX_WEEK:
        wks = wks.sort(reverse=True)[:COMBOS_MAX_WEEK]
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

    num_combos = math.comb(min(len(filtered), n), n - len(picked))
    combos = itertools.combinations(filtered[:limit], n - len(picked))
    ret = []
    for c in track(combos, description="Processing combinations", total=num_combos):
        c = list(c) + picked
        score, play = combo_score(c, m)
        ret.append((c, score, play))
    ret.sort(key=lambda c: c[1])
    return ret

def print_combos(combos):
    NUM_PRINT = 500

    rank = min(len(combos), NUM_PRINT)
    for players, score, play in combos[-NUM_PRINT:]:
        pstr = " - ".join([p.tostr(notes=False) for p in players])
        rstr = f"#{rank}/{len(combos)} - {score/18:.2f} - "
        pad = len(rstr) * " "
        print(f"{rstr} {players[0].tostr(notes=False)}")
        for i in range(1, len(players)):
            print(f"{pad} {players[i].tostr(notes=False)}")
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
        for p in sorted(players.sleeper.values(), key = lambda p: p.adj_projection()):
            print(p.tostr())

def print_keeper_costs():
    with draft.lock:
        for ft in fantasy_team_roster_id.values():
            to_print = [p for p in ft.players if p.keeper_cost > 0]
            to_print.sort(key = lambda p: p.keeper_cost)
            print(ft.name)
            print("-" * len(ft.name))
            for p in to_print:
                print(f"{p.name:25s} | ${p.keeper_cost}")
            print()

def print_prospects():
    with draft.lock:
        pros = []

        for sleeper_id in local_state.notes:
            p = players.sleeper.get(sleeper_id)
            if not p:
                logging.error(f"Unknonw player in notes: {sleeper_id}")
                continue
            if p.fantasy_team:
                continue
            pros.append(p)

        pros.sort(key=lambda p: p.rank)

        print()
        for p in reversed(pros):
            a, n = local_state.notes.get(p.sleeper_id)
            if a not in ("Like", "Love"):
                continue
            print()
            print(p.tostr())
            print("    ", n.strip())



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
                team_count[p.team.name] += 1
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
            print(p.tostr(notes=False))

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
        self.notes = {}
        self.load()

    def load(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                j = json.loads(f.read())
                overrides = j.get('overrides', {})
                for player_id, user_id in j.get('overrides', {}).items():
                    if user_id is None:
                        continue
                    self.overrides[player_id] = user_id
                self.notes = j.get('notes', {})
        self.apply()

    def apply(self):
        for player_id, user_id in self.overrides.items():
            p = self.players.sleeper.get(player_id)
            if p:
                fantasy_teams.get(user_id, UNKNOWN_TEAM).add_player(p)
                p.is_override = True


    def save(self):
        with open(STATE_FILE, "w") as f:
            json.dump({
                'overrides': self.overrides,
                'notes': self.notes,
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
        self.apply()

    def clear_notes(self, player):
        if player.sleeper_id in self.notes:
            del self.notes[player.sleeper_id]
            self.save()

    def note(self, action, player):
        n = texteditor.open(self.notes.get(player.sleeper_id, [None, ""])[1])
        if not n:
            n = f"Player {action.lower()}"
        self.notes[player.sleeper_id] = [action, n]
        self.save()

    def love_player(self, player):
        self.note("Love", player)

    def like_player(self, player):
        self.note("Like", player)

    def dislike_player(self, player):
        self.note("Dislike", player)

    def set_team(self, player, fantasy_team):
        with draft.lock:
            fantasy_team.add_player(player)
            self.overrides[player.sleeper_id] = fantasy_team.id
            player.is_override = False
            self.save()

def prompt_set_team(player):
    teams = [t for t in fantasy_team_roster_id.values()]
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

    player_lookup = {p.tostr(unf=True): p for p in players.sleeper.values()}
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
    like = lambda: local_state.like_player(p)
    love = lambda: local_state.love_player(p)
    dislike = lambda: local_state.dislike_player(p)
    clear_notes = lambda: local_state.clear_notes(p)

    prompts = [
        ("i", "Info", info),
        ("d", "Draft", draft),
        ("u", "Undraft", undraft),
        ("t", "Take", take),
        ("n", "Untake", untake),
        ("c", "Clear local state", clear),
        ("s", "Set team", set_team),
        ("l", "Like player", like),
        ("L", "Love player", love),
        ("x", "Dislike player", dislike),
        ("X", "Clear notes", clear_notes),
        ("q", "Quit", lambda: True),
    ]
    while not prompt(prompts, lambda: print(p.tostrl())):
        pass

def draft_analyze(verbose=True):
    team = fantasy_teams[MY_USER_ID]
    needed = {}
    for pos, conf in DRAFT_SETTINGS.items():
        needed[pos] = conf[1]
    for p in team.players:
        if p.position in needed:
            needed[p.position] -= 1
    print("Needed:", ','.join([f"{p}: {n}" for p, n in needed.items()]))

    combos = []
    to_print = collections.defaultdict(list)
    for pos, n in needed.items():
        if n < 0:
            print(f"Drafted too many {pos}s, should reconfigure draft settings")
        if n <= 0:
            continue
        print("Analyzing", pos)
        with draft.lock:
            try:
                cs = do_combos(players, pos, DRAFT_SETTINGS[pos][1], DRAFT_SETTINGS[pos][0])
            except RuntimeError as e:
                logging.error(f"Error running combos for {pos}: {e}")
                continue

        cs.sort(key=lambda c: -c[1])
        i = 0
        num_print = max(NUM_RECOMMEND, n*2)
        for c in cs:
            ps, score, play = c
            for p in ps:
                if p.fantasy_team:
                    continue
                if p.sleeper_id in to_print[pos]:
                    continue
                combos.append(c)
                if p.sleeper_id not in to_print[pos]:
                    to_print[pos].append(p.sleeper_id)
                i += 1
                if i >= num_print:
                    break
            if i >= num_print:
                break
    if verbose:
        print_combos(list(reversed(combos)))
    for pos, top in to_print.items():
        print()
        print_header(f"Top {len(top)} {pos}s")
        for i, sleeper_id in enumerate(list(top)):
            print(f"{i+1}.", players.sleeper[sleeper_id].tostr())


def sleeper_auctions():
    ps = sorted(players.sleeper.values(),
                key = lambda p: p.draft_value - p.sleeper_auction_value)

    table = Table(title=f"Sleeper Auction Comp")
    table.add_column("Value", justify="right", style="cyan")
    table.add_column("Sleeper Proj", style="magenta", justify="right")
    table.add_column("Savings", style="green", justify="right")
    table.add_column("Player")

    for p in ps:
        if not p.sleeper_auction_value:
            continue
        diff = p.draft_value - p.sleeper_auction_value
        if diff <= 0:
            continue
        table.add_row(
            f"${p.draft_value}",
            f"${p.sleeper_auction_value}",
            f"${diff}",
            p.tostr(emoji=False, notes=False))

    console = Console()
    console.print(table)


def print_tier_info(pos, tier):
    ps = []

    num_drafted = 0
    num_remaining = 0
    num_keeper = 0
    first_price = None
    last_price = None
    total_price = 0

    for p in players.sleeper.values():
        if pos == OVERALL_TIER:
            if p.overall_tier != tier:
                continue
        elif p.position != pos or p.pos_tier != tier:
            continue
        ps.append(p)
        if p.actual_draft_pos is not None:
            if p.actual_draft_pos == 0:
                num_keeper += 1
            else:
                num_drafted += 1
                if first_price is None:
                    first_price = p.actual_cost
                last_price = p.actual_cost
                total_price += p.actual_cost
        else:
            num_remaining += 1

    ps.sort(key=lambda p: (
        p.actual_draft_pos if p.actual_draft_pos is not None else 1000,
        p.adp or 1000))

    table = Table(title=f"Position: {pos} Tier: {tier}")
    table.add_column("Draft", justify="right", style="cyan")
    table.add_column("Cost", style="magenta", justify="right")
    table.add_column("Player")

    for p in ps:
        if p.actual_draft_pos is not None:
            draft = str(p.actual_draft_pos) if p.actual_draft_pos else "K"
            cost = f"${p.actual_cost}"
        else:
            draft = ""
            cost = ""
        table.add_row(
            draft,
            cost,
            p.tostr(emoji=False, notes=False))
    console = Console()
    console.print(table)
    print()
    print(num_keeper, "Kept,",
          num_drafted, "Drafted,",
          num_remaining, "Remaining")
    if num_drafted > 0:
        print()
        print(f"First: ${first_price}"
              f" Last: ${last_price}"
              f" Avg: ${total_price/num_drafted}")



def input_tiers():
    positions = {"QB", "RB", "WR", "TE"}
    pos = None
    while not pos:
        print("Position: ", end="")
        pos = input()
        if not pos:
            return
        if pos.lower() == "all":
            pos = "all"
            break
        if "overall".startswith(pos.lower()):
            pos = OVERALL_TIER
            break
        for p in positions:
            if p.startswith(pos.upper()):
                pos = p
        if pos not in positions:
            print(f"Invalid position: {pos}")
            pos = None

    tier = None
    while not tier:
        print("Tier: ", end="")
        tier = input()
        if not tier:
            return
        if "all".startswith(tier.lower()):
            tier = ALL_TIERS
            break
        if "top".startswith(tier.lower()):
            tier = TOP_TIERS
            break
        try:
            tier = int(tier)
        except:
            print(f"Invalid tier: {tier}")
            tier = None
        if tier > 15:
            print("Tier must be 1 - 15")
            tier = None

    tiers = []
    if tier == TOP_TIERS:
        tiers = reversed(range(1,15))
    elif tier == ALL_TIERS:
        tiers = reversed(range(1,16))
    else:
        tiers = [tier]

    print("tiers", tiers)

    for t in tiers:
        if pos == "all":
            for pos in positions:
                print_tier_info(pos, t)
        else:
            print_tier_info(pos, t)



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
        for p in pos_max:
            if p.startswith(pos.upper()):
                pos = p
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
        teams = [t for t in fantasy_team_roster_id.values()]
        teams.sort(key=lambda t: (t.score, t.name), reverse=True)
        for i, team in enumerate(teams):
            if team == UNKNOWN_TEAM and not team.players:
                continue
            print(f"#{i+1}", team)
            for p in team.players:
                print(f"  {p.tostr()}")
            print()

def print_prompt(prompts):
    print("\nMake choice:")
    for p in prompts:
        if p[1] == "Quit":
            color = "red"
        else:
            color = "#5fffaf"
        print(f" [bold]({p[0]})[/bold]"
              f" [bold {color}]{p[1]}[/bold {color}]")

def handle_input(prompts, c):
    print()
    for p in prompts:
        if c == p[0]:
            print(p[1])
            return p[2]
    return lambda: False

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

        should_stop = False
        while not should_stop:
            termios.tcsetattr(fd, termios.TCSAFLUSH, new)
            if before_fn:
                before_fn()
            print_prompt(prompts)
            fn = handle_input(prompts, sys.stdin.read(1))
            termios.tcsetattr(fd, termios.TCSAFLUSH, orig)
            should_stop = fn()
    finally:
        # reset terminal parameters to their original values
        termios.tcsetattr(fd, termios.TCSAFLUSH, orig)

    return True

def download_nfl_players():
    players = Players()
    all_players = players.get_all_players()
    nfl_players = {}
    for i, p in all_players.items():
        if p["sport"] == "nfl":
            nfl_players[i] = p
    with open("data/nfl_players.json", "w") as f:
        json.dump(nfl_players, f)


def main():
    global players, local_state, REFRESH_RATE

    if args.refresh:
        download_nfl_players()

    load_sleeper()
    load_draft_values(players)

    #TODO
    load_draft_values_gen(players)


    load_ol_def_rankings(players)
    load_keeper_costs(players)
    load_extra(players)

    load_2025_matchups(players)
    do_rankings(players)

    if args.refresh:
        refresh_rosters()
    load_league(players)

    if DRAFT_ID and PRE_DRAFT:
        if args.refresh:
            draft.refresh()
            draft.start()
        elif args.sim:
            REFRESH_RATE = 5
            draft.start_sim()
        else:
            draft.load()

    prompts = [
        ("p", "Query player", query_player),
        ("r", "Print roster", print_roster),
        ("v", "Print players", print_players),
        ("d", "Draft combo", input_combos),
        ("t", "Print teams", print_rosters),
        ("k", "Print keeper costs", print_keeper_costs),
        ("x", "Refresh draft", refresh_draft),
        ("P", "Print remaining prospects", print_prospects),
        ("a", "Analyze", draft_analyze),
        ("T", "Tier Progress", input_tiers),
        ("V", "Sleeper auction values", sleeper_auctions),
        ("D", "Debug", breakpoint),
        ("q", "Quit", lambda: sys.exit(0)),
    ]
    while prompt(prompts):
        pass

def parse_args():
    parser = argparse.ArgumentParser(
        description="analyze", formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('-r', '--roster', dest='roster', action="store_true")
    parser.add_argument('-q', '--query', dest='query', nargs='+')
    parser.add_argument('-p', '--pos', dest="pos")
    parser.add_argument('-d', '--draft', dest="num_draft", type=int, default=1)
    parser.add_argument('-l', '--play', dest="num_play", type=int, default=1)
    parser.add_argument('-s', '--sim', dest="sim", action="store_true")
    parser.add_argument('-v', '--verbose', dest="verbose", action="store_true")
    parser.add_argument('--max-age', dest="max_age", type=int, default=100)
    parser.add_argument('--refresh', dest="refresh", action="store_true")
    global VERBOSE, MAX_AGE
    args = parser.parse_args()
    VERBOSE = args.verbose
    MAX_AGE = args.max_age
    return args

if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(format=format,
                        level=logging.DEBUG if args.verbose else logging.INFO,
                        datefmt="%Y-%m-%d %H:%M:%S")

    main()

