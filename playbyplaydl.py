import urllib
import urllib.request
import os

FORCE = False
START_YEAR = 0
END_YEAR = 2024


def download(url, dest=None):
    if not dest:
        dest = os.path.basename(url)
    if not FORCE and os.path.exists(dest):
        print(dest, "already exists")
        return

    print(dest, url)
    urllib.request.urlretrieve(url, dest)


for year in range(1999, END_YEAR):
    if year < START_YEAR:
        continue
    if year >= 1999:
        for part in ("post", "reg", "regpost"):
            download(
                f"https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_{part}_{year}.csv"
            )
            download(
                f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_{part}_{year}.csv"
            )
    if year >= 2022:
        download(
            f"https://github.com/nflverse/nflverse-data/releases/download/ftn_charting/ftn_charting_{year}.csv"
        )
    if year >= 2002:
        download(
            f"https://github.com/nflverse/nflverse-data/releases/download/weekly_rosters/roster_weekly_{year}.csv"
        )
    if year >= 2016:
        for part in ("passing", "receiving", "rushing"):
            download(
                f"https://github.com/nflverse/nflverse-data/releases/download/nextgen_stats/ngs_{year}_{part}.csv.gz"
            )
    if year >= 2009:
        download(
            f"https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{year}.csv"
        )
    if year >= 1999:
        download(
            f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{year}.csv"
        )

download(
    "https://github.com/nflverse/nflverse-data/releases/download/players_components/players.csv",
    "component_players.csv",
)
download(
    "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
)
download(
    "https://github.com/nflverse/nflverse-data/releases/download/contracts/historical_contracts.csv.gz"
)
