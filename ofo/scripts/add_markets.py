"""Usage: python scripts/add_markets.py [ES] [GC] [--data data]
Builds the continuous 1-minute files for the S&P 500 (ES) and gold (GC) from the day files you already downloaded:
no new download, MES and MGC use the same prices (the Lab applies the micro contract's value). Run it again after you
download more days; then restart the Lab."""
import sys

from ofo.markets import MARKETS, build_market

argv = sys.argv[1:]
data = argv[argv.index("--data") + 1] if "--data" in argv else "data"
roots = [a.upper() for a in argv if not a.startswith("--") and a != data] or ["ES", "GC"]
bad = [r for r in roots if r not in MARKETS or r == "NQ"]
if bad:
    sys.exit(f"Mercato non valido: {bad}. Usa ES e/o GC (NQ si costruisce con build_dataset.py).")
ok = 0
for r in roots:
    print(f"\n{r} ({MARKETS[r]['name']}): costruisco ... (puo' richiedere qualche minuto)")
    try:
        info = build_market(data, r)
    except Exception as e:
        print(f"  NON riuscito: {e}")
        continue
    ok += 1
    print(f"  fatto: {info['rows']:,} barre da {info['first']} a {info['last']} -> {info['path']}")
    print(f"  contratti usati: {', '.join(info['chain'])}")
print(f"\n{ok}/{len(roots)} mercati pronti. Riavvia il Lab: leggerai 'Mercati attivi' nel terminale.")
