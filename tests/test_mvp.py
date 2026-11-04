import io

import pymupdf as fitz
import pytest
from pptx import Presentation

from estudo_guiado import geracao
from estudo_guiado.cache import Cache
from estudo_guiado.config import carregar_config
from estudo_guiado.jornada import LimiteExcedido, Servico

TEXTO = [
    "A fotossíntese é o processo pelo qual as plantas convertem luz solar em energia química. A clorofila absorve a luz nos cloroplastos. "
    "A fotossíntese ocorre em duas fases: fase clara e ciclo de Calvin. Os cloroplastos contêm clorofila.",
    "A respiração celular ocorre nas mitocôndrias e libera energia na forma de ATP. A glicose é oxidada durante a respiração celular. "
    "As mitocôndrias produzem ATP a partir da glicose e do oxigênio.",
    "O ciclo de Calvin utiliza ATP e NADPH para fixar carbono e produzir glicose. A fase clara gera ATP e NADPH nos tilacoides. "
    "Os tilacoides ficam dentro dos cloroplastos e contêm clorofila.",
]


def pdf(textos=TEXTO) -> bytes:
    doc = fitz.open()
    for t in textos:
        pg = doc.new_page()
        pg.insert_textbox(fitz.Rect(50, 50, 550, 750), t, fontsize=11)
    return doc.tobytes()


def pptx(textos=TEXTO) -> bytes:
    p = Presentation()
    for t in textos:
        s = p.slides.add_slide(p.slide_layouts[1])
        s.shapes.title.text = "Slide"
        s.placeholders[1].text = t
    b = io.BytesIO()
    p.save(b)
    return b.getvalue()


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = carregar_config()
    cfg["cache"]["caminho"] = str(tmp_path / "c.sqlite")
    cfg["limites"]["max_paginas_por_jornada"] = 5
    return Servico(cfg, Cache(cfg["cache"]["caminho"]))


@pytest.mark.parametrize("nome,dados", [("a.pdf", pdf()), ("a.pptx", pptx())])
def test_jornada_completa(svc, nome, dados):
    j = svc.preparar([(nome, dados)])
    c = j.conteudo
    assert c["resumo"]["topicos"] and c["resumo"]["termos"]
    validas = {(t.arquivo, t.pagina) for t in j.trechos}
    for tp in c["resumo"]["topicos"]:
        assert (tp["fonte"]["arquivo"], tp["fonte"]["pagina"]) in validas
    texto = " ".join(t.texto for t in j.trechos)
    assert all(geracao.contem_termo(texto, t["termo"]) for t in c["resumo"]["termos"])
    ids = {n["id"] for n in c["mapa"]["nos"]}
    assert all(r["de"] in ids and r["para"] in ids for r in c["mapa"]["relacoes"])
    qs = c["quiz"]["questoes"]
    assert qs
    for q in qs:
        assert q["alternativas"]["ABCD".index(q["gabarito"])].lower() in q["justificativa"].lower()
    r = geracao.corrigir_quiz(c["quiz"], {i: q["gabarito"] for i, q in enumerate(qs)})
    assert r["nota"] == 10.0


def test_limite_antes_de_processar(svc):
    with pytest.raises(LimiteExcedido, match="máximo"):
        svc.preparar([("grande.pdf", pdf(TEXTO * 3))])


def test_cache_reaproveita(svc):
    arq = [("a.pdf", pdf())]
    assert not svc.preparar(arq).do_cache
    assert svc.preparar(arq).do_cache


def test_chat_recusa_fora_do_material(svc):
    j = svc.preparar([("a.pdf", pdf())])
    ok = svc.perguntar(j, "Onde ocorre a respiração celular?")
    assert not ok["sem_base"] and ok["fontes"]
    assert svc.perguntar(j, "Quem ganhou a copa de 1994?")["sem_base"]


def test_destaque_somente_termos_existentes():
    assert geracao.destacar_termos("A clorofila absorve luz.", ["clorofila", "inexistente"]) == "A **clorofila** absorve luz."


def test_pdf_sem_texto(svc):
    doc = fitz.open()
    doc.new_page()
    with pytest.raises(LimiteExcedido, match="texto selecionável"):
        svc.preparar([("e.pdf", doc.tobytes())])
