#!/bin/bash

cd /Users/rramdin/ff
. venv/bin/activate
python injuries.py > injuries.txt
scp injuries.txt vnwsnwsz1vfc@173.201.180.168:public_html/injuries.txt
