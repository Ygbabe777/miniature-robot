"""Scoreboard degli agenti.  Uso: python scoreboard.py"""
import sys

from src.config import load_config
from src.scoreboard import build_scoreboard, render_text

if __name__ == "__main__":
    print(render_text(build_scoreboard(load_config())))
    sys.exit(0)
