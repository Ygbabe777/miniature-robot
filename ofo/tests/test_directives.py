from ofo.directives import Directives, split_kind, words


def test_kind_is_read_from_the_first_words_in_italian_and_english(tmp_path):
    assert split_kind("cerca opening range NQ") == ("search", "opening range NQ")
    assert split_kind("Continua a cercare gap fill") == ("keep", "gap fill")
    assert split_kind("smetti di cercare crypto") == ("stop", "crypto")
    assert split_kind("stop mean reversion")[0] == "stop" and split_kind("evita il forex")[1] == "il forex"
    assert split_kind("preferisco strategie con stop stretti")[0] == "note"
    assert split_kind("cerca X", kind="stop")[0] == "stop"                      # an explicit choice in the UI wins


def test_search_is_used_once_keep_every_run_and_stop_blocks_papers(tmp_path):
    d = Directives(tmp_path / "d.json")
    d.add("cerca gap fill NQ"); d.add("continua opening range"); d.add("smetti di cercare crypto bitcoin")
    assert d.take_searches() == ["opening range", "gap fill NQ"]
    assert d.take_searches() == ["opening range"]                              # 'search' consumed, 'keep' stays
    assert d.blocked("Bitcoin and crypto momentum") and not d.blocked("Bitcoin only")   # all stop words must appear
    assert Directives(tmp_path / "d.json").active("keep")                      # persisted


def test_scout_follows_directives_and_researcher_prompt_carries_them(tmp_path, monkeypatch):
    import ofo.scout as scout_mod
    from ofo.scout import Scout
    d = Directives(tmp_path / "d.json")
    d.add("cerca overnight gap fill"); d.add("smetti di cercare crypto")
    papers = [{"id": "a:1", "source": "arxiv", "title": "Crypto futures momentum", "abstract": "crypto futures intraday trading strategy " * 10,
               "url": "u", "pdf_url": "", "published": "2024", "authors": []},
              {"id": "a:2", "source": "arxiv", "title": "NQ gap fill", "abstract": "nasdaq futures gap trading strategy intraday " * 10,
               "url": "u2", "pdf_url": "", "published": "2024", "authors": []}]
    monkeypatch.setattr(scout_mod, 'search_arxiv', lambda q, fetch=None: [dict(p) for p in papers])
    sc = Scout(tmp_path / "s", tmp_path / "i", None, None, fetch=object(), use_semantic_scholar=False, use_ssrn=False, directives=d)
    qs = sc.next_queries()
    assert qs[0] == "overnight gap fill"
    found = sc._candidates(qs)
    assert [p["id"] for p in found] == ["a:2"]                                   # the crypto paper was dropped
    txt = d.prompt_text()
    assert "LOOK FOR: overnight gap fill" in txt and "AVOID: crypto" in txt.replace("DO NOT PROPOSE / AVOID", "AVOID")


def test_sources_can_be_switched_off_and_the_choice_is_saved(tmp_path, monkeypatch):
    import ofo.scout as scout_mod
    from ofo.scout import Scout
    from ofo.sources import Channel
    calls = []

    class Fake(Channel):
        name = "github"
        def search(self, query, n=5):
            calls.append("github"); return []
    monkeypatch.setattr(scout_mod, 'search_arxiv', lambda q, fetch=None: (calls.append("arxiv") or []))
    monkeypatch.setattr(scout_mod, 'search_ssrn', lambda q, fetch=None: (calls.append("ssrn") or []))
    sc = Scout(tmp_path / "s", tmp_path / "i", None, None, fetch=object(), use_semantic_scholar=False, channels=[Fake()])
    assert {x["name"]: x["enabled"] for x in sc.sources()} == {"arxiv": True, "semanticscholar": False, "ssrn": True, "github": True}
    assert sc.set_source("arxiv", False) and sc.set_source("github", False) and not sc.set_source("nope", True)
    sc._candidates(["nasdaq futures"])
    assert calls == ["ssrn"]                                                       # only what is ticked runs
    assert not Scout(tmp_path / "s", tmp_path / "i", None, None, fetch=object(), channels=[Fake()]).is_on("arxiv")   # saved across restarts
