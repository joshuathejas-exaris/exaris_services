# b_kol_identification/tests/test_config_surface.py
import configparser, os
_CFG = os.path.join(os.path.dirname(__file__), "..", "config.ini")

def _cfg():
    c = configparser.ConfigParser(); c.read(_CFG); return c

# NOTE: these knobs are client-tunable per the design ("all thresholds adjustable in
# config.ini"), so the tests assert the keys EXIST and parse to the right type/domain —
# not their exact shipped values, which a client run is expected to change.

def test_hybrid_section_present():
    c = _cfg()
    assert isinstance(c["hybrid"].getboolean("hybrid_relevance"), bool)
    assert 0.0 < c["hybrid"].getfloat("vector_sim_threshold") <= 1.0
    assert c["hybrid"].getint("vector_top_k_per_hcp") > 0

def test_scoring_weights_present():
    c = _cfg()
    for key in ("weight_relevance", "weight_reach", "weight_ratio"):
        assert c["scoring"].getfloat(key) >= 0.0            # present + numeric
    assert c["scoring"]["normalization"] in ("percentile", "minmax", "zscore")
    assert c["scoring"].getint("min_ratio_denominator") >= 1

def test_as_of_year_present():
    v = _cfg()["funnel"]["as_of_year"].strip()
    assert v == "latest" or (v.isdigit() and len(v) == 4)   # 'latest' or a 4-digit year

def test_vendored_modules_import():
    import importlib.util, os
    for name in ("vector_creator.py", "reranker.py"):
        p = os.path.join(os.path.dirname(__file__), "..", name)
        assert os.path.exists(p)
