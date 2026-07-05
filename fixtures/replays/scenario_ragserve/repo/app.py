import numpy as np


def embed(batch):
    vecs = np.array(batch)  # planted: silent float64 (R4)
    scale = np.ones(len(batch))
    return vecs * scale[:, None]
