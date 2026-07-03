import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)                       # service dir (for pipeline_common, vector_creator…)
sys.path.insert(0, os.path.join(_HERE, ".."))   # repo root (for shared/)


def load_stage(filename: str):
    """Import a numeric-prefixed stage module (e.g. '02_retrieve_sources.py') by path."""
    path = os.path.join(_HERE, filename)
    mod_name = "stage_" + os.path.splitext(filename)[0]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module
