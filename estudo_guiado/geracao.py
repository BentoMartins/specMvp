"""Geração única de resumo, mapa mental e quiz, mais respostas do chat.

Dois provedores: LLM via API (barato) ou modo offline extrativo (sem custo, para demo/testes).
Toda saída passa por validação em código: fontes existentes, termos presentes no texto.
"""
import random
import re
from collections import Counter

from .chunking import Trecho
from .llm import ClienteLLM, ErroLLM
from .retrieval import IndiceBM25
from .texto import STOPWORDS, contem_termo, frases, normalizar, tokens

MAX_PALAVRAS_CONTEXTO = 9000
NIVEIS = ["lembrar", "compreender", "aplicar"]
SISTEMA_BASE = ("Você é um tutor que responde SOMENTE com base nos trechos fornecidos, em português do Brasil, "
                "com linguagem simples e didática. Nunca invente fatos nem termos. Responda apenas JSON válido.")

MSG_FORA_DO_MATERIAL = "Não encontrei base para responder isso no material enviado. Tente perguntar sobre um conceito que aparece nos slides/páginas."


def _rotulo(t: Trecho) -> str:
    return f"[{t.arquivo} p.{t.pagina}]"


def _contexto(trechos: list[Trecho]) -> str:
    return "\n".join(f"{_rotulo(t)} {t.texto}" for t in trechos)


def fontes_validas(trechos: list[Trecho]) -> set[tuple[str, int]]:
    return {(t.arquivo, t.pagina) for t in trechos}


def _fonte_ok(fonte, validas) -> dict | None:
    if isinstance(fonte, dict) and (fonte.get("arquivo"), fonte.get("pagina")) in validas:
        return {"arquivo": fonte["arquivo"], "pagina": fonte["pagina"]}
    return None


def _fonte_mais_proxima(texto: str, indice: IndiceBM25, fallback: Trecho) -> dict:
    achados = indice.buscar(texto, 1)
    return (achados[0][0] if achados else fallback).fonte()


# ---------------------------------------------------------------- termos-chave (offline)

def termos_candidatos(trechos: list[Trecho], n: int = 10) -> list[str]:
    """Palavras relevantes (TF x espalhamento entre páginas), preservando a grafia original."""
    grafia: dict[str, Counter] = {}
    paginas: dict[str, set] = {}
    for t in trechos:
        for palavra in re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ-]{4,}", t.texto):
            k = normalizar(palavra)
            if k in STOPWORDS:
                continue
            grafia.setdefault(k, Counter())[palavra.lower()] += 1
            paginas.setdefault(k, set()).add((t.arquivo, t.pagina))
    pontos = {k: sum(c.values()) * (1 + 0.5 * (len(paginas[k]) - 1) ** 0.5) for k, c in grafia.items() if sum(c.values()) >= 2}
    if not pontos:
        pontos = {k: sum(c.values()) for k, c in grafia.items()}
    melhores = sorted(pontos, key=lambda k: -pontos[k])[:n]
    return [grafia[k].most_common(1)[0][0] for k in melhores]


def _frase_com(termo: str, trechos: list[Trecho]) -> tuple[str, Trecho] | None:
    for t in trechos:
        for f in frases(t.texto):
            if contem_termo(f, termo):
                return f, t
    return None


def _encurtar(s: str, n: int = 170) -> str:
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------- resumo

def gerar_resumo(trechos: list[Trecho], indice: IndiceBM25, llm: ClienteLLM | None):
    """Retorna (conteudo, tokens_in, tokens_out)."""
    if llm is None:
        return _resumo_offline(trechos), 0, 0
    tin = tout = 0
    ctx_trechos = trechos
    if sum(len(t.texto.split()) for t in trechos) > MAX_PALAVRAS_CONTEXTO:
        notas, tin, tout = _condensar(trechos, llm)
        ctx = notas
    else:
        ctx = _contexto(ctx_trechos)
    r = llm.json(SISTEMA_BASE, (
        "Faça um resumo por tópicos (6 a 10) e uma lista de 6 a 10 termos-chave. Cada termo-chave deve aparecer "
        "literalmente no texto. Formato: {\"topicos\":[{\"titulo\":\"\",\"texto\":\"\",\"fonte\":{\"arquivo\":\"\",\"pagina\":0}}],"
        "\"termos\":[{\"termo\":\"\",\"definicao\":\"uma linha\"}]}\n\nTRECHOS:\n" + ctx))
    return _validar_resumo(r.dados, trechos, indice), tin + r.tokens_in, tout + r.tokens_out


def _condensar(trechos: list[Trecho], llm: ClienteLLM) -> tuple[str, int, int]:
    """Resumo hierárquico: resume por seção (grupos de trechos) e depois consolida."""
    grupos, atual, n = [], [], 0
    for t in trechos:
        atual.append(t)
        n += len(t.texto.split())
        if n >= 2500:
            grupos.append(atual)
            atual, n = [], 0
    if atual:
        grupos.append(atual)
    notas, tin, tout = [], 0, 0
    for g in grupos:
        r = llm.json(SISTEMA_BASE, "Condense estes trechos em notas curtas mantendo as marcações [arquivo p.N] e os termos originais. "
                     "Formato: {\"notas\":\"...\"}\n\n" + _contexto(g))
        notas.append(str(r.dados.get("notas", "")))
        tin, tout = tin + r.tokens_in, tout + r.tokens_out
    return "\n".join(notas), tin, tout


def _validar_resumo(d: dict, trechos: list[Trecho], indice: IndiceBM25) -> dict:
    validas = fontes_validas(trechos)
    texto_total = " ".join(t.texto for t in trechos)
    topicos = []
    for tp in d.get("topicos", []):
        if not tp.get("texto"):
            continue
        fonte = _fonte_ok(tp.get("fonte"), validas) or _fonte_mais_proxima(tp["texto"], indice, trechos[0])
        topicos.append({"titulo": str(tp.get("titulo", "")), "texto": str(tp["texto"]), "fonte": fonte})
    termos = [{"termo": x["termo"], "definicao": str(x.get("definicao", ""))}
              for x in d.get("termos", []) if x.get("termo") and contem_termo(texto_total, x["termo"])]
    return {"topicos": topicos, "termos": termos}


def _resumo_offline(trechos: list[Trecho]) -> dict:
    termos_lista = termos_candidatos(trechos, 10)
    termos = []
    for termo in termos_lista:
        achado = _frase_com(termo, trechos)
        if achado:
            termos.append({"termo": termo, "definicao": _encurtar(achado[0])})
    pesos = Counter({normalizar(t): 1 for t in termos_lista})
    por_pagina: dict[tuple[str, int], list[Trecho]] = {}
    for t in trechos:
        por_pagina.setdefault((t.arquivo, t.pagina), []).append(t)
    chaves = list(por_pagina)
    if len(chaves) > 10:
        chaves = [chaves[round(i * (len(chaves) - 1) / 9)] for i in range(10)]
    topicos = []
    for arq, pg in chaves:
        texto = " ".join(t.texto for t in por_pagina[(arq, pg)])
        fs = frases(texto) or [texto[:240]]
        rank = sorted(fs, key=lambda f: -sum(pesos[tk] for tk in tokens(f)))[:2]
        escolhidas = [f for f in fs if f in rank]
        titulo = _encurtar(fs[0], 60)
        topicos.append({"titulo": titulo, "texto": _encurtar(" ".join(escolhidas), 420), "fonte": {"arquivo": arq, "pagina": pg}})
    return {"topicos": topicos, "termos": termos}


def destacar_termos(texto: str, termos: list[str]) -> str:
    """Aplica negrito (markdown) por código; só termos que existem no texto são marcados."""
    for termo in sorted(termos, key=len, reverse=True):
        texto = re.sub(rf"(?<![\w*])({re.escape(termo)})(?![\w*])", r"**\1**", texto, flags=re.I)
    return texto


# ---------------------------------------------------------------- mapa mental

def gerar_mapa(trechos: list[Trecho], resumo: dict, indice: IndiceBM25, llm: ClienteLLM | None):
    if llm is None:
        return _mapa_offline(trechos, resumo), 0, 0
    notas = "\n".join(f"- {t['titulo']}: {t['texto']} [{t['fonte']['arquivo']} p.{t['fonte']['pagina']}]" for t in resumo["topicos"])
    r = llm.json(SISTEMA_BASE, (
        "Gere um mapa mental (máx. 15 nós) com conceitos e relações. Formato: {\"nos\":[{\"id\":\"n1\",\"rotulo\":\"\","
        "\"fonte\":{\"arquivo\":\"\",\"pagina\":0}}],\"relacoes\":[{\"de\":\"n1\",\"para\":\"n2\",\"tipo\":\"contém\"}]}. "
        "O primeiro nó é o tema central.\n\nNOTAS:\n" + notas))
    return _validar_mapa(r.dados, trechos, indice), r.tokens_in, r.tokens_out


def _validar_mapa(d: dict, trechos: list[Trecho], indice: IndiceBM25) -> dict:
    validas = fontes_validas(trechos)
    nos, ids = [], set()
    for n in d.get("nos", []):
        if not n.get("id") or not n.get("rotulo") or n["id"] in ids:
            continue
        fonte = _fonte_ok(n.get("fonte"), validas) or _fonte_mais_proxima(n["rotulo"], indice, trechos[0])
        nos.append({"id": str(n["id"]), "rotulo": str(n["rotulo"]), "fonte": fonte})
        ids.add(str(n["id"]))
    rels = [{"de": r["de"], "para": r["para"], "tipo": str(r.get("tipo", "relaciona"))}
            for r in d.get("relacoes", []) if r.get("de") in ids and r.get("para") in ids and r["de"] != r["para"]]
    return {"nos": nos, "relacoes": rels}


def _mapa_offline(trechos: list[Trecho], resumo: dict) -> dict:
    primeiro = trechos[0]
    nos = [{"id": "n1", "rotulo": primeiro.arquivo, "fonte": primeiro.fonte()}]
    rels, paginas = [], {}
    for i, tm in enumerate(resumo["termos"], start=2):
        achado = _frase_com(tm["termo"], trechos)
        fonte = achado[1].fonte() if achado else primeiro.fonte()
        nos.append({"id": f"n{i}", "rotulo": tm["termo"], "fonte": fonte})
        rels.append({"de": "n1", "para": f"n{i}", "tipo": "contém"})
        paginas.setdefault((fonte["arquivo"], fonte["pagina"]), []).append(f"n{i}")
    for ids in paginas.values():
        for a, b in zip(ids, ids[1:]):
            rels.append({"de": a, "para": b, "tipo": "relaciona-se"})
    return {"nos": nos, "relacoes": rels}


# ---------------------------------------------------------------- quiz

def gerar_quiz(trechos: list[Trecho], resumo: dict, indice: IndiceBM25, llm: ClienteLLM | None, n: int):
    if llm is None:
        return _quiz_offline(trechos, resumo, n), 0, 0
    notas = "\n".join(f"{_rotulo(t)} {t.texto}" for t in _amostra(trechos, 12))
    r = llm.json(SISTEMA_BASE, (
        f"Crie {n} questões de múltipla escolha (4 alternativas com prefixo 'A ', 'B ', 'C ', 'D ') com níveis variados da "
        "taxonomia de Bloom revisada (lembrar, compreender, aplicar). Distratores plausíveis. Formato: {\"questoes\":[{\"enunciado\":\"\","
        "\"alternativas\":[],\"gabarito\":\"B\",\"justificativa\":\"\",\"fonte\":{\"arquivo\":\"\",\"pagina\":0},\"nivel\":\"\"}]}\n\nTRECHOS:\n" + notas))
    return _validar_quiz(r.dados, trechos, indice, n), r.tokens_in, r.tokens_out


def _amostra(trechos: list[Trecho], k: int) -> list[Trecho]:
    if len(trechos) <= k:
        return trechos
    return [trechos[round(i * (len(trechos) - 1) / (k - 1))] for i in range(k)]


def _validar_quiz(d: dict, trechos: list[Trecho], indice: IndiceBM25, n: int) -> dict:
    validas = fontes_validas(trechos)
    questoes = []
    for q in d.get("questoes", []):
        alts = q.get("alternativas", [])
        letra = str(q.get("gabarito", "")).strip().upper()[:1]
        if len(alts) != 4 or letra not in "ABCD" or not letra or not q.get("enunciado"):
            continue
        alts = [re.sub(r"^[A-Da-d][\).:]?\s+", "", str(a)) for a in alts]
        fonte = _fonte_ok(q.get("fonte"), validas) or _fonte_mais_proxima(q["enunciado"], indice, trechos[0])
        nivel = q.get("nivel") if q.get("nivel") in NIVEIS else "compreender"
        questoes.append({"enunciado": str(q["enunciado"]), "alternativas": alts, "gabarito": letra,
                         "justificativa": str(q.get("justificativa", "")), "fonte": fonte, "nivel": nivel})
    return {"questoes": questoes[:n]}


def _quiz_offline(trechos: list[Trecho], resumo: dict, n: int) -> dict:
    """Questões de completar lacuna: gabarito correto por construção (termo removido da frase-fonte)."""
    rng = random.Random(42)
    termos = [t["termo"] for t in resumo["termos"]]
    usadas, questoes = set(), []
    for termo in termos:
        achado = _frase_com(termo, trechos)
        if not achado or achado[0] in usadas or len(termos) < 4:
            continue
        frase, trecho = achado
        usadas.add(frase)
        lacuna = re.sub(re.escape(termo), "_____", frase, count=1, flags=re.I)
        original = re.search(re.escape(termo), frase, flags=re.I).group(0)
        distratores = rng.sample([t for t in termos if t != termo], 3)
        opcoes = [original] + distratores
        rng.shuffle(opcoes)
        questoes.append({
            "enunciado": f"Complete a frase do material: \"{_encurtar(lacuna, 260)}\"",
            "alternativas": opcoes, "gabarito": "ABCD"[opcoes.index(original)],
            "justificativa": f"No material: \"{_encurtar(frase, 260)}\"",
            "fonte": trecho.fonte(), "nivel": "lembrar"})
        if len(questoes) >= n:
            break
    return {"questoes": questoes}


def corrigir_quiz(quiz: dict, respostas: dict[int, str]) -> dict:
    """Correção por código, sem LLM."""
    itens, acertos = [], 0
    for i, q in enumerate(quiz["questoes"]):
        marcada = respostas.get(i)
        ok = marcada == q["gabarito"]
        acertos += ok
        itens.append({"indice": i, "acertou": ok, "marcada": marcada, "gabarito": q["gabarito"],
                      "justificativa": q["justificativa"], "fonte": q["fonte"]})
    total = len(quiz["questoes"])
    return {"acertos": acertos, "total": total, "nota": round(10 * acertos / total, 1) if total else 0.0, "itens": itens}


# ---------------------------------------------------------------- chat

def responder(pergunta: str, indice: IndiceBM25, llm: ClienteLLM | None, k: int, score_min: float) -> dict:
    """Retorna {resposta, fontes, sem_base, tokens_in, tokens_out}."""
    achados = indice.buscar(pergunta, k)
    base = {"resposta": MSG_FORA_DO_MATERIAL, "fontes": [], "sem_base": True, "tokens_in": 0, "tokens_out": 0}
    if not achados or achados[0][1] < score_min:
        return base
    trechos = [t for t, _ in achados]
    if llm is None:
        return _resposta_offline(pergunta, trechos) | {"tokens_in": 0, "tokens_out": 0}
    r = llm.json(SISTEMA_BASE, (
        "Responda a pergunta usando só os trechos. Se não houver base, devolva sem_base=true. Cite as fontes usadas. "
        "Formato: {\"resposta\":\"\",\"fontes\":[{\"arquivo\":\"\",\"pagina\":0}],\"sem_base\":false}\n\n"
        f"PERGUNTA: {pergunta}\n\nTRECHOS:\n{_contexto(trechos)}"))
    validas = fontes_validas(trechos)
    fontes = [f for f in (_fonte_ok(x, validas) for x in r.dados.get("fontes", [])) if f]
    if r.dados.get("sem_base") or not fontes or not r.dados.get("resposta"):
        return base | {"tokens_in": r.tokens_in, "tokens_out": r.tokens_out}
    return {"resposta": str(r.dados["resposta"]), "fontes": fontes, "sem_base": False,
            "tokens_in": r.tokens_in, "tokens_out": r.tokens_out}


def _resposta_offline(pergunta: str, trechos: list[Trecho]) -> dict:
    q = set(tokens(pergunta))
    candidatas = []
    for t in trechos:
        for f in frases(t.texto):
            s = len(q & set(tokens(f)))
            if s:
                candidatas.append((s, f, t))
    if not candidatas:
        return {"resposta": MSG_FORA_DO_MATERIAL, "fontes": [], "sem_base": True}
    candidatas.sort(key=lambda x: -x[0])
    top = candidatas[:3]
    resposta = " ".join(f"{_encurtar(f, 300)} ({t.arquivo}, p.{t.pagina})" for _, f, t in top)
    fontes = list({(t.arquivo, t.pagina): t.fonte() for _, _, t in top}.values())
    return {"resposta": resposta, "fontes": fontes, "sem_base": False}
