import json, os
from pathlib import Path
from multibagger.modules import score_fundamentals
from multibagger.live_facts import normalize_facts, coverage

INPUT = Path(os.getenv("LIVE_FACTS_JSON", "reports/firecrawl_fundamentals.json"))
if not INPUT.exists():
    raise SystemExit(f"Missing normalized live facts: {INPUT}")

payload = json.loads(INPUT.read_text())
facts = normalize_facts(payload.get("facts", payload))
print(json.dumps({
    "status": "LIVE_FACTS_BRIDGE_READY",
    "coverage": coverage(facts),
    "source_policy": "Provider-neutral normalized facts; acquisition is external to scoring runtime.",
    "pit_policy": "Only records with independently verified available_at <= as_of may be used for PIT validation."
}, indent=2, default=str))
