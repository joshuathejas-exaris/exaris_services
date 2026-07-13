import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "06_backtest_compare.py")
_spec = importlib.util.spec_from_file_location("bt", _S); bt = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(bt)

def test_rising_star_becomes_kol():
    earlier = {"anchor_year": 2021, "hcps": [
        {"s_customer_id": "1", "name": "A", "tier": "C", "rising_star": True, "kol_score": 0.2}]}
    later = {"anchor_year": 2026, "hcps": [
        {"s_customer_id": "1", "name": "A", "tier": "A", "rising_star": False, "kol_score": 0.9}]}
    out = bt.compare_runs(earlier, later)
    assert out["rising_to_kol"] == [{"s_customer_id": "1", "name": "A", "from_tier": "C", "to_tier": "A"}]
    assert {"s_customer_id": "1", "name": "A", "from_tier": "C", "to_tier": "A"} in out["tier_moves"]

def test_new_kol_absent_earlier():
    earlier = {"anchor_year": 2021, "hcps": []}
    later = {"anchor_year": 2026, "hcps": [{"s_customer_id": "2", "name": "B", "tier": "A", "rising_star": False, "kol_score": 0.8}]}
    out = bt.compare_runs(earlier, later)
    assert out["new_kols"] == [{"s_customer_id": "2", "name": "B", "to_tier": "A"}]
