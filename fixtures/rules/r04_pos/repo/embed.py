import numpy as np


def embed(batch):
    vecs = np.array(batch)  # silent float64
    buf = np.zeros((len(batch), 768))
    return vecs @ buf.T
