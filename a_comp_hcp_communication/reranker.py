import os

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def _find_onnx(model_dir: str) -> str:
    """Return path to first .onnx file found in model_dir."""
    for name in ("model.onnx", "model_quantized.onnx", "model_optimized.onnx"):
        p = os.path.join(model_dir, name)
        if os.path.exists(p):
            return p
    for fname in os.listdir(model_dir):
        if fname.endswith(".onnx"):
            return os.path.join(model_dir, fname)
    raise FileNotFoundError(f"No .onnx file found in {model_dir}")


class Reranker:
    """
    Cross-encoder reranker backed by the mmarco mMiniLM ONNX model.
    Scores (query, passage) pairs; higher score = more relevant.
    """

    _DEFAULT_DIR = os.path.join(_REPO_ROOT, "assets", "mmarco-reranker")

    def __init__(self, model_dir: str = None, max_length: int = 512):
        model_dir = model_dir or self._DEFAULT_DIR
        self.tokenizer = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        self.tokenizer.enable_padding()
        self.tokenizer.enable_truncation(max_length=max_length)
        model_path = _find_onnx(model_dir)
        self.session = ort.InferenceSession(model_path)
        self._input_names = {inp.name for inp in self.session.get_inputs()}

    def score(self, query: str, passages: list) -> list:
        """
        Score each (query, passage) pair.
        Returns list[float] of the same length as passages, higher = more relevant.
        """
        # tokenizers encodes as [CLS] query [SEP] passage [SEP] for BERT-style models
        pairs = self.tokenizer.encode_batch([(query, p) for p in passages])
        input_ids      = np.array([e.ids            for e in pairs], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in pairs], dtype=np.int64)
        feeds = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.array([e.type_ids for e in pairs], dtype=np.int64)
        outputs = self.session.run(None, feeds)
        logits = outputs[0]  # shape [batch, 1] or [batch, 2]
        if logits.ndim == 2 and logits.shape[1] == 2:
            return logits[:, 1].tolist()   # positive-class logit
        return logits.flatten().tolist()
