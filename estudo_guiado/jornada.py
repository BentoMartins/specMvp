"""Orquestra a jornada: validação de limites, cache por hash, indexação e geração única."""
from dataclasses import dataclass

from . import extracao, geracao
from .cache import Cache, hash_arquivos
from .chunking import Trecho, dividir
from .config import carregar_config
from .llm import ClienteLLM
from .retrieval import IndiceBM25


class LimiteExcedido(Exception):
    pass


@dataclass
class Jornada:
    hash: str
    conteudo: dict
    trechos: list[Trecho]
    indice: IndiceBM25
    do_cache: bool
    modo: str


class Servico:
    def __init__(self, cfg: dict | None = None, cache: Cache | None = None):
        self.cfg = cfg or carregar_config()
        self.cache = cache or Cache(self.cfg["cache"]["caminho"])
        self.usar_api = ClienteLLM.disponivel(self.cfg["llm"])
        self.llm = ClienteLLM(self.cfg["llm"]) if self.usar_api else None

    @property
    def modo(self) -> str:
        return f"api:{self.cfg['llm']['modelo']}" if self.usar_api else "offline"

    def validar(self, arquivos: list[tuple[str, bytes]]) -> int:
        """Valida limites ANTES de qualquer processamento ou chamada ao LLM. Retorna total de páginas."""
        lim = self.cfg["limites"]
        if not arquivos:
            raise LimiteExcedido("Envie ao menos um arquivo PDF ou PPTX.")
        if self.cache.jornadas_hoje() >= lim["jornadas_por_dia"]:
            raise LimiteExcedido(f"Limite de {lim['jornadas_por_dia']} jornadas por dia atingido. Volte amanhã.")
        total = 0
        for nome, dados in arquivos:
            mb = len(dados) / 1_048_576
            if mb > lim["max_tamanho_arquivo_mb"]:
                raise LimiteExcedido(f"'{nome}' tem {mb:.1f} MB; o máximo é {lim['max_tamanho_arquivo_mb']} MB.")
            try:
                total += extracao.contar_paginas(nome, dados)
            except extracao.ArquivoInvalido as e:
                raise LimiteExcedido(str(e)) from e
        if total > lim["max_paginas_por_jornada"]:
            raise LimiteExcedido(
                f"O material tem {total} páginas/slides; o máximo é {lim['max_paginas_por_jornada']}. "
                "Selecione o trecho da aula que quer estudar e envie novamente.")
        return total

    def _assinatura(self) -> str:
        c, lim = self.cfg["chunking"], self.cfg["limites"]
        return f"v1|{self.modo}|{lim['perguntas_quiz']}|{c['tamanho_alvo_palavras']}|{c['sobreposicao_palavras']}"

    def preparar(self, arquivos: list[tuple[str, bytes]]) -> Jornada:
        self.validar(arquivos)
        h = hash_arquivos(arquivos, self._assinatura())
        self.cache.registrar_jornada(h)
        cache = self.cache.obter(h)
        if cache and self.cfg["cache"]["compartilhado"]:
            trechos = [Trecho(**t) for t in cache["trechos"]]
            return Jornada(h, cache["conteudo"], trechos, IndiceBM25(trechos), True, self.modo)
        return self._processar(h, arquivos)

    def _processar(self, h: str, arquivos: list[tuple[str, bytes]]) -> Jornada:
        ch = self.cfg["chunking"]
        paginas = [p for nome, dados in arquivos for p in extracao.extrair(nome, dados)]
        trechos = dividir(paginas, ch["tamanho_alvo_palavras"], ch["sobreposicao_palavras"])
        if sum(len(t.texto.split()) for t in trechos) < 30:
            raise LimiteExcedido("Quase não há texto selecionável no material (PDF escaneado?). Esta versão não suporta esse caso.")
        indice = IndiceBM25(trechos)
        imagens = {}
        for p in paginas:
            for i, img in enumerate(p.imagens):
                imagens.setdefault(f"{p.arquivo}|{p.numero}", []).append(self.cache.salvar_imagem(h, p.arquivo, p.numero, i, img))

        resumo, tin, tout = geracao.gerar_resumo(trechos, indice, self.llm)
        self._uso(h, "resumo", tin, tout)
        mapa, tin, tout = geracao.gerar_mapa(trechos, resumo, indice, self.llm)
        self._uso(h, "mapa", tin, tout)
        quiz, tin, tout = geracao.gerar_quiz(trechos, resumo, indice, self.llm, self.cfg["limites"]["perguntas_quiz"])
        self._uso(h, "quiz", tin, tout)

        conteudo = {"resumo": resumo, "mapa": mapa, "quiz": quiz, "imagens": imagens}
        self.cache.salvar(h, {"conteudo": conteudo, "trechos": [t.__dict__ for t in trechos]})
        return Jornada(h, conteudo, trechos, indice, False, self.modo)

    def perguntar(self, j: Jornada, pergunta: str) -> dict:
        c = self.cfg["chunking"]
        r = geracao.responder(pergunta, j.indice, self.llm, c["trechos_recuperados"], c["score_minimo"])
        self._uso(j.hash, "chat", r["tokens_in"], r["tokens_out"])
        return r

    def _uso(self, h: str, etapa: str, tin: int, tout: int) -> None:
        custo = self.llm.custo_brl(tin, tout) if self.llm else 0.0
        self.cache.registrar_uso(h, etapa, tin, tout, custo)

    def custos(self, h: str) -> list[dict]:
        return self.cache.uso_por_etapa(h)
