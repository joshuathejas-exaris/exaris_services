import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "pipeline_common.py")
_spec = importlib.util.spec_from_file_location("pc", _S)
pc = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(pc)

def test_strip_json_fences_removes_backticks():
    assert pc.strip_json_fences('```json\n{"a":1}\n```') .strip() == '{"a":1}'

def test_parse_json_object_parses_fenced():
    assert pc.parse_json_object('```json\n{"a":1}\n```') == {"a": 1}

def test_name_matches_last_and_first_initial():
    assert pc.name_matches("Prof. Anna Berg", "Anna", "Berg") is True
    assert pc.name_matches("Karl Neu", "Anna", "Berg") is False
