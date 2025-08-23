This is my tool for tracking NFL Fantasy Football information throughout the
year. The basic functionality is to provide a quick interface to get basic
information and stats about a player and their situation. Each player can
liked, disliked, or draft-targeted, and notes can be associated with each
player. This information is stored in a local_state JSON file.

# Getting Started

Checkout the code:
```
git clone git@github.com:rramdin/ff.git
```

Setup a Python virtual environment with required libraries:
```
python -m venv venv
. ./venv/bin/activate
pip install -r requirements.txt
```

Now you should be able to run the program:
```
./ff.py
or
python ff.py
```

# Some basics
You must create a config file at `<user>.py` where sleeper LEAGUE_ID and
MY_USER_ID are specified. The program gives instructions if these are missing.

When the program starts up, you're presented with some options, which can be
selected by pressing the letter indicated. `q` goes back a level or quits the
program at the top level. For example, when the program starts, you can press
`p` and start typing in a player name.

The program can be run with `--refresh`. In this mode, it will refresh the NFL
players list. If a DRAFT_ID is specified in your config file and PRE_DRAFT mode
is set to True, it will start a thread that polls your draft to keep rosters
up-to-date as players are drafted. When not in PRE_DRAFT mode, rosters are
loaded once at the start of the program.

There is a moneyball style analyzer which is described in comments in ff.py.
