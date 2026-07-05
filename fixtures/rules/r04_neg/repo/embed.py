import numpy as np


def embed(batch):
    vecs = np.array(batch, dtype=np.float32)
    buf = np.zeros((len(batch), 768), dtype=np.float32)
    return vecs @ buf.T
