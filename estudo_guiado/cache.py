import hashlib
import json
import sqlite3
import time
from pathlib import Path


def hash_arquivos(arquivos: list[tuple[str, bytes]], assinatura: str) -> str:
    h = hashlib.sha256(assinatura.encode())
    for hf in sorted(hashlib.sha256(d).hexdigest() for _, d in arquivos):
        h.update(hf.encode())
    return h.hexdigest()


class Cache:
    def __init__(self, caminho: str):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.dir_imagens = self.caminho.parent / "imagens"
        self.db = sqlite3.connect(self.caminho, check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS conteudo (hash TEXT PRIMARY KEY, json TEXT NOT NULL, criado REAL);
            CREATE TABLE IF NOT EXISTS uso (id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT, etapa TEXT,
                tokens_in INTEGER, tokens_out INTEGER, custo_brl REAL, ts REAL);
            CREATE TABLE IF NOT EXISTS jornadas (id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT, dia TEXT);
        """)

    def obter(self, h: str) -> dict | None:
        r = self.db.execute("SELECT json FROM conteudo WHERE hash=?", (h,)).fetchone()
        return json.loads(r[0]) if r else None

    def salvar(self, h: str, conteudo: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO conteudo VALUES (?,?,?)", (h, json.dumps(conteudo, ensure_ascii=False), time.time()))
        self.db.commit()

    def registrar_uso(self, h: str, etapa: str, tin: int, tout: int, custo: float) -> None:
        self.db.execute("INSERT INTO uso (hash,etapa,tokens_in,tokens_out,custo_brl,ts) VALUES (?,?,?,?,?,?)",
                        (h, etapa, tin, tout, custo, time.time()))
        self.db.commit()

    def uso_por_etapa(self, h: str) -> list[dict]:
        linhas = self.db.execute(
            "SELECT etapa, SUM(tokens_in), SUM(tokens_out), SUM(custo_brl) FROM uso WHERE hash=? GROUP BY etapa", (h,)).fetchall()
        return [{"etapa": e, "tokens_in": a or 0, "tokens_out": b or 0, "custo_brl": c or 0.0} for e, a, b, c in linhas]

    def jornadas_hoje(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM jornadas WHERE dia=date('now','localtime')").fetchone()[0]

    def registrar_jornada(self, h: str) -> None:
        self.db.execute("INSERT INTO jornadas (hash,dia) VALUES (?,date('now','localtime'))", (h,))
        self.db.commit()

    def salvar_imagem(self, h: str, arquivo: str, pagina: int, idx: int, dados: bytes) -> str:
        pasta = self.dir_imagens / h[:16]
        pasta.mkdir(parents=True, exist_ok=True)
        nome = "".join(c if c.isalnum() else "_" for c in arquivo)
        caminho = pasta / f"{nome}_p{pagina}_{idx}.img"
        caminho.write_bytes(dados)
        return str(caminho)
