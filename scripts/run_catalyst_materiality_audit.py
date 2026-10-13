import json, os
from datetime import datetime
from core.catalyst import NSECatalyst, assess_catalyst

# Same historical benchmark names previously used by the blind MBE audit.
SYMBOLS = {
    "STLTECH":"Sterlite Technologies", "HFCL":"HFCL", "WELCORP":"Welspun Corp",
    "DIACABS":"Diamond Power Infrastructure", "CUPID":"Cupid",
    "SIGMAADV":"Sigma Advanced Systems", "YASHO":"Yasho Industries",
    "INDOTECH":"Indo Tech Transformers", "BLISSGVS":"Bliss GVS Pharma",
    "GKENERGY":"GK Energy", "PREMIERPOLY":"Premier Polyfilm",
    "RAJOOENG":"Rajoo Engineers", "WEBELSOLAR":"Websol Energy System",
    "VIDYAWIRES":"Vidya Wires", "LENSKART":"Lenskart",
    "GVT&D":"GE Vernova T&D India", "BIRLACOT":"Birla Cotsyn (India)",
    "JTLDEFENCE":"JTL Defence", "KETOMOTORS":"Keto Motors",
    "AHLWEST":"Asian Hotels (West)", "GBLINFRA":"Global Infratech & Finance",
    "MRUGESH":"Mrugesh Trading", "RAYMOND":"Raymond",
    "TBZ":"Tribhovandas Bhimji Zaveri", "MOREPENLAB":"Morepen Laboratories",
    "SARAUTO":"SAR Auto Products", "KABRAEXTRU":"Kabra Extrusiontechnik"
}

def main():
    as_of = os.getenv("AS_OF", datetime.utcnow().strftime("%Y-%m-%d"))
    symbols = list(SYMBOLS)
    events = NSECatalyst(workers=4).batch(symbols, as_of)
    by_symbol = {}
    for item in events:
        by_symbol.setdefault(str(item.get("symbol", "")).upper(), []).append(item)
    rows = []
    for symbol, name in SYMBOLS.items():
        raw = by_symbol.get(symbol, [])
        result = assess_catalyst(raw, as_of)
        rows.append({"symbol": symbol, "name": name, "as_of": as_of,
                     "retrieved_exchange_filings": len(raw), **result})
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    report = {
        "status": "CATALYST_MATERIALITY_AUDIT_COMPLETE",
        "engine": "MBE catalyst materiality challenger",
        "method": "Current NSE primary-source announcement retrieval; positive credit requires timestamped official filing, binding commitment, >=20% of disclosed annual revenue, and explicit customer field.",
        "as_of": as_of, "benchmark_count": len(rows),
        "retrieved_filings": len(events), "status_counts": counts, "results": rows,
        "limitations": [
            "This is a current announcement audit, not historical point-in-time validation.",
            "Missing structured order value, revenue denominator, named customer, source, or timestamp is not imputed.",
            "No historical catalyst archive means old winner checkpoints cannot yet be scored without look-ahead risk."
        ]
    }
    os.makedirs("reports", exist_ok=True)
    with open("reports/catalyst-materiality-audit.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(json.dumps(report, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
