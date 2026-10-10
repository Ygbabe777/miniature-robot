"""Usage: python scripts/build_dataset.py DATA_DIR ROOT OUT.csv.gz
Example: python scripts/build_dataset.py data NQ data/NQ_1min.csv.gz
"""
import glob
import sys

from ofo.data import validate
from ofo.futures import build_root

data_dir, root, out = sys.argv[1:4]
paths = glob.glob(f"{data_dir}/**/*.csv.gz", recursive=True)
print(f"{len(paths)} day files")
df = build_root(paths, root)
print(validate(df[["open", "high", "low", "close", "volume"]]))
df.to_csv(out)
print("saved", out)
