"""Usage: python scripts/build_dataset.py DATA_DIR ROOT OUT.csv.gz
Example: python scripts/build_dataset.py data NQ data/NQ_1min.csv.gz
"""
import glob
import re
import sys

from ofo.data import validate
from ofo.futures import build_root

data_dir, root, out = sys.argv[1:4]
# only the daily source files (YYYY-MM-DD.csv.gz): never our own output or other data
paths = [p for p in glob.glob(f"{data_dir}/**/*.csv.gz", recursive=True)
         if re.search(r"\d{4}-\d{2}-\d{2}\.csv\.gz$", p)]
print(f"{len(paths)} day files")
df = build_root(paths, root)
print("contratti usati:", df.attrs.get("chain"))
print("roll:", *df.attrs.get("rolls", []), sep="\n  ")
print(validate(df[["open", "high", "low", "close", "volume"]]))
df.to_csv(out)
print("saved", out)
