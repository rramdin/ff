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

import texteditor  # type: ignore[import-untyped]
import textwrap
import math
import getpass
from fzf import Fzf, fzf  # type: ignore[import-untyped]

from functools import cache
from thefuzz import process, fuzz  # type: ignore[import-untyped]
from rich import print
from rich.progress import track
from rich.highlighter import Highlighter
from rich.panel import Panel
from rich.table import Table
from rich.console import Console

from typing import Any, TypeVar, Callable, Sequence

T = TypeVar("T")

VERBOSE = False

# Whether to use pre-draft rosters or just keepers
PRE_DRAFT = True

# When analyzing strength-of-schedule (SOS):
# - We have projected points per game (PPG)
# - We have weekly matchup strength in 1-5 "stars" from FantasyPros
# We take the forecasted total points for the season, and assume
# PERCENTAGE_POINTS_VAR varies by week. We take the total variable
# portion and divide it by the player's total stars, and give that
# many points per star to each game to get their per-game forecast.
PERCENTAGE_POINTS_VAR = 0.3
MAX_AGE = 100

DEFAULT_CONFIG_NAME = f"osb"

# Files downloaded from sleeper when --refresh is specified
MATCHUPS_FILE = "data/2025_matchups.json"
PLAYERS_FILE = "data/nfl_players.json"
PLAYERS_FILE = "data/nfl_players2.json"

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

OSB_ROS_FILE = "data/one-street-bowl-ros-rankings.csv"


def download_file(url: str, filename: str) -> None:
    try:
        os.system(f"curl {url} > {filename} 2>/dev/null")
        #with urllib.request.urlopen(url) as response:
        #    data = json.load(response)
        #    with open(filename, "w") as f:
        #        json.dump(data, f, indent=4)
        logging.info(f"Downloaded {filename}")
    except Exception as e:
        logging.error(f"Error downloading {filename}: {e}")


class Matchup:
    def __init__(self, arr: list[str]) -> None:
        self.is_home = arr[1].startswith("@")
        self.is_bye = arr[1].startswith("BYE")
        if self.is_bye:
            self.favor = 0
        else:
            self.opponent = arr[1].replace("@", "").strip()
            self.favor = int(arr[2])

    def __str__(self) -> str:
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
    def namec(self) -> str:
        return f"[bold #{self.color}]{self.name}[/bold #{self.color}]"

    def __str__(self) -> str:
        return f"[bold #{self.color}]{self.long_name}[/bold #{self.color}]"


class WeekFPPGs(collections.UserList[float]):
    def __init__(self, obj: Any | None = None) -> None:
        super().__init__(obj if obj else list())
        for _ in range(18):
            self.append(0.0)

    def __str__(self) -> str:
        return ", ".join("%.2f" % f for f in self)


class PlayerName(str):
    @property
    def unf(self) -> str:
        return super().__str__()

    def __str__(self) -> str:
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
    sleeper_id = ""
    projection = 0.0
    keeper_cost = 0
    draft_value = 0
    actual_cost = 0
    actual_draft_pos: int | None = None
    fantasy_team: "FantasyTeam | None" = None
    is_override = False

    depth_chart_position = "UN"
    depth_chart_order = 100

    adp = 1000.0
    overall_tier = 15
    pos_tier = 15
    is_keeper = False
    sleeper_auction_value = 0.0
    ds_note: str | None = None

    team: Team | None = None

    injury_risk = "Unknown"
    career_injuries = 0
    injury_risk_per_season = 0.0
    durability = 0.0
    projected_games_missed = 0.0

    weeks: list[Matchup] = field(default_factory=list)
    week_fppgs: WeekFPPGs = field(default_factory=WeekFPPGs)

    routes_run = 0
    first_downs_per_route_run = 0.0
    first_downs_per_route_run_rank = 0

    channel_id = ""
    fantasy_data_id = ""
    pandascore_id = ""
    opta_id = ""
    sportradar_id = ""
    yahoo_id = ""
    gsis_id = ""
    birth_date = ""
    rotowire_id = ""
    oddsjam_id = ""
    search_rank = ""
    swish_id = ""
    player_id = ""
    rotoworld_id = ""
    espn_id = ""
    stats_id = ""
    injury_status = ""
    injury_notes = ""
    college = ""
    notes: list[str] = field(default_factory=list)

    def __hash__(self) -> int:
        return hash(self.sleeper_id)

    def week_fppg(self, i: int) -> float:
        if i >= len(self.weeks):
            return 0.0
        return self.week_fppgs[i]

    def adj_projection(self) -> float:
        return self.projection * (17 - self.projected_games_missed) / 17

    def name_str(self) -> str:
        return f"[bold blue]{self.name}[/bold blue]"

    def calc_score(self) -> None:
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
    def picked(self) -> bool:
        return self.fantasy_team is not None and self.fantasy_team.is_me

    @property
    def taken(self) -> bool:
        return self.fantasy_team is not None and not self.fantasy_team.is_me

    def tostr(self, unf: bool = False, emoji: bool = True, notes: bool = True) -> str:
        t = "N"
        if self.taken:
            t = "T"
        elif self.picked:
            t = "P"
        if self.team:
            oldef = f" OL{self.team.ol_ranking} DEF{self.team.def_ranking}"
            if emoji:
                emoji_str = f"{self.team.emoji}  "
            else:
                emoji_str = ""

            team_info = (
                f"{emoji_str}"
                f"{self.team.name if unf else self.team.namec}"
                f" {self.depth_chart_position}{self.depth_chart_order}"
            )
        else:
            oldef = ""
            team_info = "Free Agent"

        if notes:
            if self.sleeper_id in self.notes:
                note = self.notes[self.sleeper_id]
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
                n = f"[{self.ds_note}] "
                if not unf:
                    color = "#FC6A03"
                    prefix += f"[bold {color}]{n}[/bold {color}]"
        else:
            prefix = ""

        if self.actual_cost:
            actual_cost = f" Actual: ${self.actual_cost}"
        else:
            actual_cost = ""

        if self.keeper_cost:
            keeper_cost = f" K: ${self.keeper_cost}"
        else:
            keeper_cost = ""

        return (
            f"{prefix }{self.name.unf if unf else self.name}"
            f" ({self.position}{self.positional_rank}) {team_info}"
            f" spg: {self.adj_projection()/18:.2f}{oldef}"
            f" ADP: {self.adp}"
            f"{keeper_cost}"
            f" Val: ${self.draft_value}{actual_cost}"
        )

    def last_name(self) -> str:
        return self.name.split()[-1]

    def pos_order(self) -> int:
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

    def print_all_fields(self) -> None:
        m = {}
        for attr in dir(self):
            if attr.startswith("_"):
                continue
            value = getattr(self, attr)
            if callable(value):
                continue
            if isinstance(value, WeekFPPGs):
                value = str(value)
            if isinstance(value, list):
                value = ", ".join([str(v) for v in value])
            elif isinstance(value, dict):
                value = ", ".join([f"{k}: {v}" for k, v in value.items()])
            m[attr] = value
        print(m)

    def tostrl(self) -> str:
        height_feet = int(self.height / 12)
        height_inches = self.height - height_feet * 12
        exp = "Unknown"
        if self.experience is None:
            exp = "Unknown"
        elif int(self.experience) == 0:
            exp = "Rookie"
        else:
            exp = self.experience

        if self.sleeper_id in self.notes:
            note = self.notes[self.sleeper_id]
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

        injury_risk = (
            f"Injury: {self.injury_risk}"
            f" - Career: {self.career_injuries}"
            f" Risk/Season: {self.injury_risk_per_season*100:.2f}%"
            f" Proj Missed: {self.projected_games_missed:.1f}"
        )
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
            fdprr = (
                f"    Routes Run: {self.routes_run}"
                f" First Downs/Route: {self.first_downs_per_route_run}"
                f" Rank: {self.first_downs_per_route_run_rank}\n"
            )

        return (
            f"------ {self.name} ({self.team if self.team else "[grey]Free Agent[/grey]"} #{self.number}) ------\n"
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

class Draft:
    def __init__(self, players, draft_id, draft_file, picks_file, keepers_file) -> None:
        self.players = players
        self.draft_id = draft_id
        self.draft_file = draft_file
        self.picks_file = picks_file
        self.keepers_file = keepers_file
        self.picks: list[tuple[str, int, Player, int, int, int]] = []
        self.thread: threading.Thread | None = None
        self.lock = threading.RLock()
        self.slot_to_team: dict[int, FantasyTeam] = {}
        self.initial_load = True
        self.is_sim = False

    def start(self) -> None:
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.refresh()
        self.thread.start()

    def start_sim(self) -> None:
        self.is_sim = True
        self.refresh()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self) -> None:
        while True:
            self.refresh()
            time.sleep(REFRESH_RATE)

    def refresh(self) -> None:
        if not self.draft_id:
            logging.warning("DRAFT_ID is not set, skipping draft refresh")
            return
        logging.debug("Refreshing draft picks...")
        if not self.is_sim:
            download_file(
                f"https://api.sleeper.app/v1/draft/{self.draft_id}/picks", self.picks_file
            )
            download_file(f"https://api.sleeper.app/v1/draft/{self.draft_id}", self.draft_file)
        self.load()

    def reapply_all(self) -> None:
        for info in self.picks:
            self.apply(*info)

    def apply(
        self,
        picked_by: str,
        draft_slot: int,
        player: Player,
        amount: int,
        pick_no: int,
        draft_round: int,
    ) -> None:
        if not draft_slot and player.fantasy_team:
            logging.warning(
                f"Player {player.name} has no draft_slot "
                "but has a fantasy team, not removing from team"
            )
            return

        if picked_by:
            fantasy_team = self.players.fantasy_teams.get(picked_by, UNKNOWN_TEAM)
        else:
            fantasy_team = self.slot_to_team[draft_slot]

        with self.lock:
            if fantasy_team is not None:
                fantasy_team.add_player(player)

        player.actual_cost = int(float(amount))
        adj = 24 if self.keepers_file else 0
        player.actual_draft_pos = max(pick_no - adj, 0)
        if self.initial_load:
            print(f"{fantasy_team.namec} drafted {player.name} for ${amount}")
        else:
            print(
                Panel(
                    f"{fantasy_team.namec if fantasy_team else 'Unknown Team'}"
                    f" drafted {player.name} for ${amount}"
                    f" - Pick #{pick_no} Round {draft_round}"
                    f"\n\n{player.tostrl()}"
                    f"\n\n{fantasy_team.namec}'s team:"
                    f" {', '.join(p.name.unf for p in fantasy_team.players)}",
                    title=f"{datetime.datetime.now().time().strftime("%H:%M:%S")} Player Drafted",
                )
            )

        if not self.initial_load:
            # draft_analyze(verbose=False)
            # print_tier_info(OVERALL_TIER, player.overall_tier)
            # print_tier_info(player.position, player.pos_tier)
            pass

    def load(self) -> None:
        if os.path.exists(self.draft_file):
            with open(self.draft_file, "r") as f:
                j = json.loads(f.read())
                self.slot_to_id: dict[int, int] = {}
                for slot, roster_id in j.get("slot_to_roster_id", {}).items():
                    self.slot_to_team[int(slot)] = self.players.fantasy_team_roster_id.get(
                        int(roster_id), UNKNOWN_TEAM
                    )
        else:
            logging.warning("Could not load draft metadata from %s", self.draft_file)
            return
        if not os.path.exists(self.picks_file):
            logging.warning("Could not load draft from %s", self.picks_file)
            return
        with open(self.picks_file, "r") as f:
            j = json.loads(f.read())
            count = 0
            added = 0
            for p in j:
                count += 1
                if count <= len(self.picks):
                    continue
                player = self.players.sleeper.get(p["metadata"]["player_id"])
                if not player:
                    logging.warning(f"Player {p['player_id']} not found in players")
                    continue
                draft_slot = int(p["draft_slot"])
                picked_by = p.get("picked_by", 0)
                if "amount" in p["metadata"]:
                    amount = int(p["metadata"]["amount"])
                else:
                    amount = 0
                pick_no = int(p["pick_no"])
                draft_round = int(p["round"])
                info = (
                    picked_by,
                    int(draft_slot),
                    player,
                    amount,
                    pick_no,
                    draft_round,
                )
                self.picks.append(info)
                self.apply(*info)
                added += 1
                if self.is_sim:
                    target = 20 if self.initial_load else 1
                    if added >= target:
                        break
        self.initial_load = False


class PlayerLookup:
    def __init__(self) -> None:
        self.sleeper: dict[str, Player] = {}
        self.name: dict[str, Player] = {}
        self.id: dict[str, dict[str, Player]] = collections.defaultdict(dict)
        self.fantasy_teams = {
            "": UNKNOWN_TEAM,
        }
        self.fantasy_team_names = {
            UNKNOWN_TEAM.name: UNKNOWN_TEAM,
        }
        self.fantasy_team_roster_id = {
            0: UNKNOWN_TEAM,
        }
        self.teams: dict[str, Team] = {}


    def add(self, player: Player) -> None:
        self.sleeper[player.sleeper_id] = player

        existing = self.name.get(player.name.unf)
        if existing:
            if player.search_rank > existing.search_rank:
                logging.debug(f"{player.name} already in players_by_name, overwriting")
                self.name[player.name.unf] = player
            else:
                logging.debug(
                    f"{player.name} already in players_by_name, not overwriting"
                )
        else:
            self.name[player.name.unf] = player

        for attr in dir(player):
            if attr.endswith("_id"):
                the_id = getattr(player, attr)
                if the_id:
                    self.id[attr][getattr(player, attr)] = player

    def find(self, token: str) -> Player | None:
        if token in self.teams:
            return None

        p = self.sleeper.get(token)
        if p:
            return p

        p = self.name.get(token)
        if p:
            return p

        name = str(token)
        for suffix in [" Jr.", " Sr.", " III", " II"]:
            if name.endswith(suffix):
                name = name[: -len(suffix)].strip()
        p = self.name.get(name)
        if p:
            return p
        if name.startswith("Marquise"):
            p = self.name.get(name.replace("Marquise", "Hollywood"))
            if p:
                return p

        tokens = process.extract(name, self.name.keys(), scorer=fuzz.token_set_ratio)
        tokens.sort(key=lambda x: x[1], reverse=True)
        new_name = tokens[0][0]
        if tokens[0][1] > 80:
            name = new_name
            p = self.find(name)
        else:
            logging.info("Could not resolve %s -> %s", name, new_name)
        return p


class FantasyTeam:
    def __init__(self, name: str, team_id: str, is_me:bool) -> None:
        self.name = name
        self.id = team_id
        self.players: list[Player] = []
        self.score = 0.0
        self.is_me = is_me

    def add_player(self, player: Player) -> None:
        if player.fantasy_team:
            player.fantasy_team.remove_player(player)
        self.players.append(player)
        player.fantasy_team = self
        self.calc_score()

    def remove_player(self, player: Player) -> None:
        self.players.remove(player)
        player.fantasy_team = None
        self.calc_score()

    def calc_score(self) -> None:
        self.score = 0.0
        for player in self.players:
            self.score += player.projection
        if self.score > 0:
            self.score = self.score / len(self.players) / 17.0

    @property
    def namec(self) -> str:
        color = "#d78700"
        if self.is_me:
            color = "green"
        return f"[bold {color}]{self.name}[/bold {color}]"

    def __str__(self) -> str:
        return f"{self.name} ({len(self.players)} players, score: {self.score:.2f})"

UNKNOWN_TEAM = FantasyTeam("Unknown", "", False)

class LocalState:
    def __init__(self, players: PlayerLookup, draft, state_file) -> None:
        self.players = players
        self.draft = draft
        self.overrides: dict[str, str] = {}
        self.notes: dict[str, tuple[str, str]] = {}
        self.state_file = state_file

    def load(self) -> None:
        if os.path.exists(self.state_file):
            with open(self.state_file, "r") as f:
                j = json.loads(f.read())
                overrides = j.get("overrides", {})
                for player_id, user_id in j.get("overrides", {}).items():
                    if user_id is None:
                        continue
                    self.overrides[player_id] = user_id
                self.notes = j.get("notes", {})
                for pid, n in self.notes.items():
                    p = self.players.sleeper[pid]
                    if p:
                        p.notes = n

        self.apply()

    def apply(self) -> None:
        for player_id, user_id in self.overrides.items():
            p = self.players.sleeper.get(player_id)
            if p:
                self.players.fantasy_teams.get(user_id, UNKNOWN_TEAM).add_player(p)
                p.is_override = True

    def save(self) -> None:
        with open(self.state_file, "w") as f:
            json.dump(
                {
                    "overrides": self.overrides,
                    "notes": self.notes,
                },
                f,
                indent=4,
            )

    def pick(self, player: Player) -> None:
        with self.draft.lock:
            fantasy_team = self.players.fantasy_teams.get(my_user_id, UNKNOWN_TEAM)
            fantasy_team.add_player(player)
            self.overrides[player.sleeper_id] = fantasy_team.id
            player.is_override = True
            self.save()

    def take(self, player: Player) -> None:
        with self.draft.lock:
            UNKNOWN_TEAM.add_player(player)
            assert player.fantasy_team
            self.overrides[player.sleeper_id] = player.fantasy_team.id
            player.is_override = True
            self.save()

    def unpick(self, player: Player) -> None:
        with self.draft.lock:
            if player.fantasy_team:
                player.fantasy_team.remove_player(player)
            if player.sleeper_id in self.overrides:
                del self.overrides[player.sleeper_id]
            player.is_override = True
            self.save()

    def untake(self, player: Player) -> None:
        with self.draft.lock:
            if player.fantasy_team:
                player.fantasy_team.remove_player(player)
            if player.sleeper_id in self.overrides:
                del self.overrides[player.sleeper_id]
            player.is_override = True
            self.save()

    def clear(self, player: Player) -> None:
        with self.draft.lock:
            if player.sleeper_id in self.overrides:
                del self.overrides[player.sleeper_id]
            if player.fantasy_team:
                player.fantasy_team.remove_player(player)
            player.is_override = False
            self.save()
        self.apply()

    def clear_notes(self, player: Player) -> None:
        if player.sleeper_id in self.notes:
            del self.notes[player.sleeper_id]
            player.notes = None
            self.save()

    def note(self, action: str, player: Player) -> None:
        n = texteditor.open(self.notes.get(player.sleeper_id, [None, ""])[1])
        if not n:
            n = f"Player {action.lower()}"
        self.notes[player.sleeper_id] = (action, str(n))
        player.notes = (action, str(n))
        self.save()

    def love_player(self, player: Player) -> None:
        self.note("Love", player)

    def like_player(self, player: Player) -> None:
        self.note("Like", player)

    def dislike_player(self, player: Player) -> None:
        self.note("Dislike", player)

    def set_team(self, player: Player, fantasy_team: FantasyTeam) -> None:
        with self.draft.lock:
            fantasy_team.add_player(player)
            self.overrides[player.sleeper_id] = fantasy_team.id
            player.is_override = False
            self.save()


class Loader:
    def load_2025_matchups(self) -> None:
        with open(self.matchups_file, "r") as f:
            j = json.loads(f.read())
            for player, weeks in track(j.items(), description="Loading matchups"):
                p = self.players.find(player)
                if not p:
                    logging.info(f"Unknown player in matchups: {player}")
                    continue
                for week in weeks:
                    matchup = Matchup(week)
                    p.weeks.append(matchup)
                p.calc_score()


    def load_sleeper(self) -> None:
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

        all_positions = set(
            [
                "WLB",
                "QB",
                "SLB",
                "P",
                "RCB",
                "FS",
                "SS",
                "RDE",
                "FB",
                "RILB",
                "MLB",
                "NB",
                "TE",
                "C",
                "OL",
                "OLB",
                "NT",
                "LILB",
                "K/P",
                "RT",
                "ROLB",
                "LWR",
                "LCB",
                "K",
                "LOLB",
                "LDE",
                "LT",
                "LEO",
                "LS",
                "RWR",
                "LDT",
                "DL",
                "WR",
                "DB",
                "LB",
                "LG",
                "SWR",
                "OT",
                "RG",
                "DT",
                "RDT",
                "WS",
                "OG",
                "RB",
                "DEF",
                "PR",
            ]
        )
        with open(self.players_file, "r") as f:
            j = json.loads(f.read())
            for idnum, info in track(j.items(), description="Loading players"):
                name = info.get("full_name", "")
                if not name:
                    continue
                if not info.get("active", False):
                    continue

                matched_pos = info.get("depth_chart_position")
                if matched_pos:
                    if matched_pos not in all_positions:
                        logging.warning(
                            f"{matched_pos} is not a fantasy position, skipping {name}"
                        )
                    matched_pos = position_mapping.get(matched_pos)
                if not matched_pos:
                    for pos in info.get("fantasy_positions") or []:
                        if pos not in all_positions:
                            logging.warning(
                                f"{matched_pos} is not a fantasy position, skipping {name}"
                            )
                        pos = position_mapping.get(pos)
                        if pos:
                            matched_pos = pos
                            break
                if not matched_pos:
                    continue

                p = Player(PlayerName(name), info.get("team"), matched_pos, 0, 0)
                p.age = info.get("age", 0)
                p.weight = int(info.get("weight") or 0)
                p.experience = info.get("years_exp", None)
                p.number = info.get("number", 0)
                p.status = info.get("status", "UNK")

                height = info.get("height", "")
                if height:
                    if "'" in height:
                        feet, inches = height.split("'")
                        feet = int(feet.strip())
                        inches = int(inches.strip().replace('"', ""))
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
                p.rotoworld_id = info.get("rotoworld_id")
                p.espn_id = info.get("espn_id")
                p.stats_id = info.get("stats_id")
                p.injury_status = info.get("injury_status")
                p.injury_notes = info.get("injury_notes")
                p.college = info.get("college")

                if p.birth_date:
                    DATE = datetime.datetime(year=2024, month=12, day=1)
                    bday = datetime.datetime.strptime(p.birth_date, "%Y-%m-%d")
                    p.age = int((DATE - bday).days / 365.2425)
                else:
                    p.age = 0

                self.players.add(p)

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

        for p in track(self.players.sleeper.values(), "Loading teams"):
            if not p.team_name:
                continue
            team_name = p.team_name
            team = self.players.teams.get(team_name)
            if not team:
                team = Team(
                    team_name,
                    team_map[team_name][0],
                    team_map[team_name][1],
                    team_colors[team_name],
                )
                self.players.teams[team.name] = team
                self.players.teams[team.long_name] = team
            p.team = team

        with open(self.sleeper_projections_file, "r") as f:
            reader = csv.DictReader(f)
            for r in reader:
                name = r["player"]
                if name in self.players.teams:
                    continue
                pl = self.players.find(name)
                if not pl:
                    logging.info("Could not find %s", name)
                    continue
                pl.sleeper_auction_value = int(r["cost"].replace("$", ""))


    def load_league(self) -> None:
        with self.draft.lock:
            for pl in self.players.sleeper.values():
                pl.fantasy_team = None

            with open(self.users_file, "r") as f:
                for j in track(json.loads(f.read()), "Loading league"):
                    team_name = j.get("display_name")
                    fantasy_team = FantasyTeam(team_name, j.get("user_id"),
                                               j.get("user_id") == self.my_user_id)
                    self.players.fantasy_teams[fantasy_team.id] = fantasy_team
                    self.players.fantasy_team_names[fantasy_team.name] = fantasy_team

            self.players.fantasy_team_roster_id = {}
            with open(self.rosters_file, "r") as f:
                j = json.loads(f.read())
                for i, r in track(enumerate(j), "Loading rosters", total=12):
                    user_id = r.get("owner_id", "0")
                    fantasy_team = self.players.fantasy_teams.get(r["owner_id"], UNKNOWN_TEAM)
                    self.players.fantasy_team_roster_id[int(r["roster_id"])] = fantasy_team
                    for cowner_id in r.get("co_owners") or []:
                        self.players.fantasy_teams[cowner_id] = fantasy_team

                    if self.draft_id:
                        continue
                    field = "keepers" if PRE_DRAFT else "players"
                    if not r.get(field):
                        continue
                    for ps in r[field]:
                        try:
                            if p := self.players.sleeper.get(ps):
                                if p.fantasy_team != fantasy_team:
                                    print(
                                        f"Adding player {p.name} to team {fantasy_team.namec} ({fantasy_team.id})"
                                    )
                                    fantasy_team.add_player(p)
                                    if field == "keepers":
                                        p.is_keeper = True
                                        p.actual_cost = int(float(p.keeper_cost))
                                        p.actual_draft_pos = 0
                            else:
                                logging.info(f"Player {ps} not found in players")
                        except ValueError:
                            pass

        self.draft.reapply_all()

        for team in self.players.fantasy_team_roster_id.values():
            team.calc_score()


    def load_draft_values(self) -> None:
        with open(self.draft_value_file, "r") as f:
            reader = csv.DictReader(f, quotechar='"')
            for r in reader:
                name = r["Player"]
                for suffix in [" Jr.", " Sr.", " III", " II"]:
                    if name.endswith(suffix):
                        name = name[: -len(suffix)].strip()
                if name in self.players.teams:
                    continue
                p = self.players.find(name)
                if not p:
                    logging.info("Could not lookup %s", name)
                    continue

                if "Auction $" in r:
                    p.draft_value = int(float(r["Auction $"].replace("$", "")))
                p.projection = float(r["DS Proj"])
                p.adp = float(r["ADP"])
                p.overall_tier = int(float(r["Overall Tier"]))
                p.pos_tier = int(float(r["Pos. Tier"]))
                p.ds_note = r["Note"]


    def load_draft_values_gen(self) -> None:
        with open(self.draft_value_file_gen, "r") as f:
            reader = csv.DictReader(f, quotechar='"')
            for r in reader:
                name = r["Player"]
                for suffix in [" Jr.", " Sr.", " III", " II"]:
                    if name.endswith(suffix):
                        name = name[: -len(suffix)].strip()
                if name in self.players.teams:
                    continue
                p = self.players.find(name)
                if not p:
                    logging.info("Could not lookup %s", name)
                    continue

                if "DS AuctionValue" in r:
                    p.draft_value = int(float(r["DS AuctionValue"].replace("$", "")))
                p.projection = float(r["DS Proj"])


    def load_ol_def_rankings(self) -> None:
        def fix_team(s: str) -> str:
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

        with open(self.ol_file, "r") as f:
            for line in track(f, description="Loading O-Line rankings", total=32):
                line = line.strip()
                if not line:
                    continue
                m = re.match(r"(\d+)\.\s+(.*) [–-].*2024 PRESEASON RANKING", line)
                if not m:
                    continue
                short_name = fix_team(m.group(2))
                if short_name in self.players.teams:
                    team = self.players.teams[short_name]
                    team.ol_ranking = int(m.group(1))
                else:
                    print(m.group(2))
            for team in self.players.teams.values():
                if not team.ol_ranking:
                    logging.warning("No O-Line ranking for team", team.name)
        with open(self.def_file, "r") as f:
            for line in track(f, description="Loading defense rankings", total=32):
                line = line.strip()
                if not line:
                    continue
                m = re.match(r"(\d+)\.\s+(.*)$", line)
                if not m:
                    continue
                short_name = fix_team(m.group(2))
                if short_name in self.players.teams:
                    team = self.players.teams[short_name]
                    team.def_ranking = int(m.group(1))
                else:
                    print(m.group(2))
            for team in self.players.teams.values():
                if not hasattr(team, "def_ranking"):
                    logging.warning("No Defense ranking for team", team.name)


    def load_keeper_costs(self) -> None:
        if not os.path.exists(self.keepers_file):
            return
        with open(self.keepers_file, "r") as f:
            j = json.loads(f.read())
            for player_id, cost in track(j.items(), "Loading keeper costs"):
                player = self.players.sleeper.get(player_id)
                if not player:
                    logging.warning(f"Player {player_id} not found in players")
                    continue
                player.keeper_cost = cost


    def load_extra(self) -> None:
        with open(self.injuries_file, "r") as f:
            reader = csv.DictReader(f)
            for r in track(reader, description="Loading injury predictions", total=200):
                match = re.match(r"([ \S]+([a-z\.]| I*))[A-Z]+ \d+$", r["player"])
                assert match
                name = match.group(1)
                p = self.players.find(name)
                if not p:
                    logging.info("Could not look up player for injury: %s", name)
                    continue
                p.career_injuries = int(r["career_injuries"])
                p.injury_risk = r["injury_risk"].strip()
                p.injury_risk_per_season = (
                    float(r["injury_risk_per_season"].replace("%", "")) / 100
                )
                p.durability = float(r["durability"])
                p.projected_games_missed = float(r["projected_games_missed"])
        with open(self.routes_run_file, "r") as f:
            reader = csv.DictReader(f)
            for r in track(reader, description="Loading first downs/route stats", total=50):
                p = self.players.find(r["name"])
                if not p:
                    logging.warning(
                        "Could not lookup player for routes run for '%s'", r["name"]
                    )
                    continue
                p.routes_run = int(r["routes"])
                p.first_downs_per_route_run = float(r["first_downs_per_route_run"])
                p.first_downs_per_route_run_rank = int(r["rank"])


    def do_rankings(self) -> None:
        positions = ["QB", "RB", "WR", "TE", "K"]
        all_players = []
        by_position = collections.defaultdict(list)
        for p in self.players.sleeper.values():
            all_players.append(p)
            by_position[p.position].append(p)

        all_players.sort(key=lambda p: (-p.adj_projection(), p.pos_order(), p.last_name()))
        for i, p in enumerate(all_players):
            p.rank = i + 1

        for pos in positions:
            by_position[pos].sort(key=lambda p: (-p.adj_projection(), p.last_name()))
            for i, p in enumerate(by_position[pos]):
                p.positional_rank = i + 1


    def refresh_rosters(self) -> None:
        download_file(f"https://api.sleeper.app/v1/league/{self.league_id}/users", self.users_file)
        download_file(f"https://api.sleeper.app/v1/league/{self.league_id}/rosters", self.rosters_file)


    def download_nfl_players(self) -> None:
        download_file(f"https://api.sleeper.app/v1/players/nfl", self.players_file)

    def refresh_draft(self) -> None:
        self.draft.refresh()


    def load_config(self, config_name) -> None:
        try:
            self.config = __import__(config_name)
        except:
            logging.error(
                "Create a file at %s.py with sleeper" " LEAGUE_ID and MY_USER_ID", config_name
            )
            logging.error("Go to https://sleeper.com/")
            logging.error("Go to your league, your league ID is in the URL")
            logging.error(
                "Go to https://api.sleeper.app/v1/user/<user_id>"
                " and find user_id or use get_sleeper_user.py"
            )
            sys.exit(1)

        def get(s: str, default_fn: Callable[[], T] = str, required:bool = True) -> T:
            if hasattr(self.config, s):
                return getattr(self.config, s)
            else:
                if required:
                    raise RuntimeError(f"Missing required config '{s}'")
            return default_fn()

        self.state_file = get("STATE_FILE", required=False)
        if not self.state_file:
            self.state_file =  f"data/local_state_{getpass.getuser()}.json"
        self.league_id = get("LEAGUE_ID")
        self.my_user_id = get("MY_USER_ID")
        self.draft_id = get("DRAFT_ID", required=False)

        file_infix = get("FILE_INFIX")

        if get("INCLUDE_KEEPERS", required=False):
            self.keepers_file = f"data/2024_{file_infix}_keeper_costs.json"
        else:
            self.keepers_file = ""
        self.picks_file = f"data/{file_infix}_picks.json"
        self.draft_file = f"data/{file_infix}_draft.json"
        self.rosters_file = f"data/{file_infix}_rosters.json"
        self.users_file = f"data/{file_infix}_users.json"
        self.draft_value_file = f"data/2025_predraft_ds_{file_infix}.csv"
        self.draft_value_file_gen = get("DRAFT_VALUE_FILE_GEN")

        draft_settings = get("DRAFT_SETTINGS", default_fn=dict, required=False)
        if draft_settings:
            self.draft_settings = draft_settings

        # Files downloaded from sleeper when --refresh is specified
        self.matchups_file = MATCHUPS_FILE
        self.players_file = PLAYERS_FILE
        self.players_file = PLAYERS_FILE

        # Rotowire PPG projections
        self.projections_file = PROJECTIONS_FILE

        # Fantasy pros auction values based on 12 team, half PPR
        # https://www.fantasypros.com/nfl/auction-values/calculator.php
        self.draft_value_file = DRAFT_VALUE_FILE

        # Draft sharks auction values
        # https://www.draftsharks.com/league/draft/board/mvp?id=781007
        self.draft_value_file = DRAFT_VALUE_FILE
        self.draft_value_file_gen = DRAFT_VALUE_FILE_GEN

        # Sleeper projections
        self.sleeper_projections_file = SLEEPER_PROJECTIONS_FILE

        # Offensive line and defense rankings.
        self.ol_file = OL_FILE
        self.def_file = DEF_FILE

        # Injury predictions
        self.injuries_file = INJURIES_FILE

        # For loading first downs per route run
        self.routes_run_file = ROUTES_RUN_FILE

        self.ros_file = OSB_ROS_FILE


    def __init__(self, config_name, should_refresh, is_sim):
        self.config = self.load_config(config_name)
        self.players = PlayerLookup()
        self.draft = Draft(self.players,
                           self.draft_id, self.draft_file, self.picks_file, self.keepers_file)
        self.local_state = LocalState(self.players, self.draft, self.state_file)
        self.should_refresh = should_refresh
        self.is_sim = is_sim


    def load(self):
        if self.should_refresh:
            self.download_nfl_players()

        self.load_sleeper()
        self.load_draft_values()
        self.local_state.load()

        # TODO
        self.load_draft_values_gen()

        self.load_ol_def_rankings()
        self.load_keeper_costs()
        self.load_extra()

        self.load_2025_matchups()
        self.do_rankings()

        if self.should_refresh:
            self.refresh_rosters()
        self.load_league()

        if self.draft_id and PRE_DRAFT:
            if self.should_refresh:
                self.draft.refresh()
                self.draft.start()
            elif self.is_sim:
                REFRESH_RATE = 5
                self.draft.start_sim()
            else:
                self.draft.load()

def load(args):
    l = Loader(args.config, args.refresh, args.sim)
    l.load()
    return l.players

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="analyze", formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("-s", "--sim", dest="sim", action="store_true")
    parser.add_argument("-c", "--config", dest="config", default=DEFAULT_CONFIG_NAME)
    parser.add_argument("-v", "--verbose", dest="verbose", action="store_true")
    parser.add_argument("--max-age", dest="max_age", type=int, default=100)
    parser.add_argument("--refresh", dest="refresh", action="store_true")
    global VERBOSE, MAX_AGE
    args = parser.parse_args()
    VERBOSE = args.verbose
    MAX_AGE = args.max_age
    return args


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    load(args)
