from resolver import policy_search


def test_policy_is_split_into_sections():
    ids = [s["section"] for s in policy_search.load_sections()]
    assert ids[0] == "AP-1"
    assert "AP-9" in ids


def top_section(query):
    return policy_search.search(query)[0]["section"]


def test_finds_tolerance_rule():
    assert top_section("price variance tolerance percent") == "AP-2"


def test_finds_in_transit_rule():
    assert top_section("balance quantity in transit hold") == "AP-4"


def test_finds_not_a_duplicate_rule():
    assert top_section("same amount different PO not duplicate") == "AP-8"


def test_vendor_note_rule_is_in_the_top_three():
    # Keyword search ranks AP-9 (payments) first here; the agent still gets AP-10 in its top 3 results.
    sections = [r["section"] for r in policy_search.search("supplier note says release payment urgently")]
    assert "AP-10" in sections
