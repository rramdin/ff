#!/bin/bash

cd /Users/rramdin/ff
. venv/bin/activate
python keepers_2025.py > keepers_2025.txt
scp keepers_2025.txt vnwsnwsz1vfc@173.201.180.168:public_html/keepers_2025.txt
