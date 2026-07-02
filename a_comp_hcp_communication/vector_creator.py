import os

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


class VectorCreator:
    """Embeds text using the GTE multilingual ONNX model."""

    _ASSETS = os.path.join(os.path.dirname(__file__), "..", "assets")
    _DEFAULT_MODEL = os.path.join(_ASSETS, "gte_multilang_model_quantized.onnx")
    _DEFAULT_TOKENIZER = os.path.join(_ASSETS, "tokenizer.json")

    def __init__(self, model_path: str = None, tokenizer_path: str = None, max_length: int = 512):
        model_path = model_path or self._DEFAULT_MODEL
        tokenizer_path = tokenizer_path or self._DEFAULT_TOKENIZER

        self.tokenizer = Tokenizer.from_file(tokenizer_path)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        self.tokenizer.enable_truncation(max_length=max_length)

        self.session = ort.InferenceSession(model_path)
        self._input_names = {inp.name for inp in self.session.get_inputs()}
        self._output_names = [out.name for out in self.session.get_outputs()]

    def get_vector_from_list(self, texts: list) -> np.ndarray:
        encoded = self.tokenizer.encode_batch(texts)

        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)

        feeds = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)

        outputs = self.session.run(None, feeds)

        if "sentence_embedding" in self._output_names:
            embeddings = outputs[self._output_names.index("sentence_embedding")]
        else:
            # Mean pooling over last_hidden_state weighted by attention mask
            last_hidden = outputs[0]  # [batch, seq_len, hidden_dim]
            mask = attention_mask[:, :, np.newaxis].astype(np.float32)
            embeddings = (last_hidden * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1e-9)

        # L2 normalise so cosine similarity = dot product
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = embeddings / np.maximum(norms, 1e-9)

        return embeddings[0]  # caller passes a single-item list, return 1-D vector
