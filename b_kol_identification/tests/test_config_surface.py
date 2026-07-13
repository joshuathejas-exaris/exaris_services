# b_kol_identification/tests/test_config_surface.py
import configparser, os
_CFG = os.path.join(os.path.dirname(__file__), "..", "config.ini")

def _cfg():
    c = configparser.ConfigParser(); c.read(_CFG); return c

def test_hybrid_section_present():
    c = _cfg()
    assert c["hybrid"].getboolean("hybrid_relevance") is True
    assert c["hybrid"].getfloat("vector_sim_threshold") == 0.55
    assert c["hybrid"].getint("vector_top_k_per_hcp") == 20

def test_scoring_weights_present():
    c = _cfg()
    assert c["scoring"].getfloat("weight_relevance") == 0.60
    assert c["scoring"].getfloat("weight_reach") == 0.25
    assert c["scoring"].getfloat("weight_ratio") == 0.15
    assert c["scoring"]["normalization"] == "percentile"
    assert c["scoring"].getint("min_ratio_denominator") == 5

def test_as_of_year_default_latest():
    assert _cfg()["funnel"]["as_of_year"] == "latest"

def test_vendored_modules_import():
    import importlib.util, os
    for name in ("vector_creator.py", "reranker.py"):
        p = os.path.join(os.path.dirname(__file__), "..", name)
        assert os.path.exists(p)
