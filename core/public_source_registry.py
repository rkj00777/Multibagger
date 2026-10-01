"""Public-source registry and safe fallback policy for MBE.

This module is intentionally metadata-only. It does not change MBE scores or gates.
Adapters may be added later behind explicit validation and source-health checks.
"""

PUBLIC_SOURCES = {
    "pit_xbrl": {
        "primary": "NSE_XBRL_PIT_STORE",
        "references": [
            "dhananjaym182/india-xbrl-filings",
            "rejinreeza/trading_tool",
        ],
        "authority": "NSE_XBRL_PIT_STORE",
        "can_promote": True,
        "can_replace_pit": False,
    },
    "market_history": {
        "primary": "tejhq/indian-markets",
        "references": [
            "tradevectorsrobots/nse-data-resources",
            "devsubham-python/growfin",
            "sharadaswalepi-art/india-market-data",
            "kcodweb/india-market-mcp",
        ],
        "authority": "TEJHQ_MARKET_HISTORY",
        "can_promote": False,
        "can_replace_pit": False,
    },
    "technical_discovery": {
        "primary": "Chartink",
        "references": ["pkjmesra/PKScreener", "kcodweb/india-market-mcp"],
        "authority": "DISCOVERY_ONLY",
        "can_promote": False,
        "can_replace_pit": False,
    },
    "corporate_actions": {
        "primary": "NSE_CORPORATE_ACTIONS",
        "references": ["devsubham-python/growfin", "kcodweb/india-market-mcp"],
        "authority": "NSE_CORPORATE_ACTIONS",
        "can_promote": False,
        "can_replace_pit": False,
    },
    "governance": {
        "primary": "NSE_CORPORATE_DISCLOSURES",
        "references": ["anandbaid/promoter-watch", "kcodweb/india-market-mcp"],
        "authority": "NSE_CORPORATE_DISCLOSURES",
        "can_promote": False,
        "can_replace_pit": False,
    },
}

SAFETY_RULES = (
    "Public repositories are independent references/fallbacks, not silent score replacements.",
    "No fallback can lower an MBE threshold.",
    "No current fundamental can be substituted for a historical PIT observation.",
    "Raw filings/revisions should be retained when an adapter is implemented.",
    "Conflicting sources are recorded as discrepancies rather than averaged.",
    "Technical sources are discovery inputs only.",
    "Promotion requires the existing PIT verification gate.",
)

def source_policy(area: str) -> dict:
    if area not in PUBLIC_SOURCES:
        raise KeyError(area)
    return PUBLIC_SOURCES[area].copy()
