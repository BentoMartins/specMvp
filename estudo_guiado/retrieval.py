"""Recuperação lexical BM25 (leve, local, sem download de modelos).

O índice fica atrás de uma interface simples (`buscar`), para trocar por
embeddings multilíngues + ChromaDB como previsto no spec sem mexer no resto.
"""
import math
from collections import Counter

from .chunking import Trecho
from .texto import tokens


class IndiceBM25:
    def __init__(self, trechos: list[Trecho], k1: float = 1.5, b: float = 0.75):
        self.trechos = trechos
        self.k1, self.b = k1, b
        self.docs = [Counter(tokens(t.texto)) for t in trechos]
        self.tam = [sum(d.values()) for d in self.docs]
        self.media = (sum(self.tam) / len(self.tam)) if self.tam else 0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def buscar(self, consulta: str, k: int = 4) -> list[tuple[Trecho, float]]:
        q = tokens(consulta)
        pontuados = []
        for trecho, doc, tam in zip(self.trechos, self.docs, self.tam):
            s = 0.0
            for t in q:
                f = doc.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * tam / (self.media or 1)))
            if s > 0:
                pontuados.append((trecho, s))
        pontuados.sort(key=lambda x: -x[1])
        return pontuados[:k]
