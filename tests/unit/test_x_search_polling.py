from safe_monitor.sources.x_search_polling import _pack_handles_into_query_batches

# The 46 X handles monitored as of this fix (config.yaml `sources.x.handles`).
# The old greedy packer budgeted only the joined "from:.. OR .." string and
# ignored the "(...) since_time:<unix>" wrapper TwitterApiIoClient adds, so
# batch 1's real query ran ~524 chars — over X advanced_search's 512-char
# limit — and the API silently returned nothing for those 26 accounts.
_MONITORED_HANDLES = [
    "samczsun", "zachxbt", "pcaversaccio", "tayvano_", "evilcos",
    "Mudit__Gupta", "frangio_", "PatrickAlphaC", "pashov", "FrankResearcher",
    "0xfoobar", "0xQuit", "DanielVF", "patrickd_",
    "PeckShieldAlert", "SlowMist_Team", "CertiKAlert", "Cyvers_",
    "BlockSecTeam", "HypernativeLabs", "BeosinAlert", "realScamSniffer",
    "GoPlusSecurity", "dedaub", "FortaNetwork", "_SEAL_Org", "Hexens",
    "OtterSec", "NumenCyber",
    "whale_alert", "ArkhamIntel", "lookonchain", "spotonchain", "nansen_ai",
    "TheBlock_", "DLNews", "CoinDesk", "decryptmedia", "Bankless",
    "WuBlockchain", "ForesightNews", "PANews", "ChainCatcher_", "OdailyChina",
    "BlockBeats", "hackenclub",
]


def _full_query_len(batch: list[str]) -> int:
    """Mirror TwitterApiIoClient.search_tweets: "(<joined>) since_time:<unix>"."""
    joined = " OR ".join(f"from:{h}" for h in batch)
    return len(f"({joined}) since_time:1778726668")


def test_packed_batch_full_query_stays_within_budget():
    budget = 500  # below X advanced_search's 512-char query limit
    batches = _pack_handles_into_query_batches(_MONITORED_HANDLES, budget)

    assert batches, "expected at least one batch"
    for batch in batches:
        assert _full_query_len(batch) <= budget, (
            f"batch full query is {_full_query_len(batch)} chars, exceeds {budget}"
        )


def test_packing_preserves_every_handle_in_order():
    # Regression guard: the wrapper-overhead fix must not drop or reorder
    # handles — every monitored account stays covered.
    batches = _pack_handles_into_query_batches(_MONITORED_HANDLES, 500)
    flat = [h for batch in batches for h in batch]
    assert flat == _MONITORED_HANDLES
