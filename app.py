import copy

import streamlit as st

from estudo_guiado import geracao
from estudo_guiado.jornada import LimiteExcedido, Servico

st.set_page_config(page_title="Estudo Guiado", page_icon="📚", layout="centered")

ETAPAS = ["Envio", "Resumo", "Mapa mental", "Quiz", "Dúvidas"]


@st.cache_resource
def servico() -> Servico:
    return Servico()


svc = servico()
cfg = svc.cfg
S = st.session_state
S.setdefault("etapa", 0)
S.setdefault("jornada", None)


def reiniciar():
    for k in list(S.keys()):
        del S[k]


def citacao(f: dict) -> str:
    return f"📄 {f['arquivo']}, p. {f['pagina']}"


def trecho_da_fonte(f: dict) -> str:
    j = S.jornada
    return "\n\n".join(t.texto for t in j.trechos if t.arquivo == f["arquivo"] and t.pagina == f["pagina"])


def avancar():
    S.etapa += 1
    st.rerun()


st.title("📚 Estudo Guiado")
st.caption("Envie a aula e siga a jornada: resumo → mapa mental → quiz → dúvidas.")
st.info("Ferramenta de apoio ao estudo. Não substitui o professor nem o material oficial da disciplina.", icon="ℹ️")
st.progress(S.etapa / (len(ETAPAS) - 1), text=f"Etapa {S.etapa + 1} de {len(ETAPAS)}: {ETAPAS[min(S.etapa, 4)]}")

# ------------------------------------------------------------------ Etapa 0
if S.etapa == 0:
    lim = cfg["limites"]
    st.subheader("Envie o material da aula")
    st.write(f"Aceitamos **PDF** e **PPTX** com texto selecionável (até {lim['max_paginas_por_jornada']} páginas/slides "
             f"no total e {lim['max_tamanho_arquivo_mb']} MB por arquivo).")
    arquivos = st.file_uploader("Slides ou PDFs", type=["pdf", "pptx"], accept_multiple_files=True)
    if arquivos and st.button("Começar a estudar", type="primary"):
        pares = [(a.name, a.getvalue()) for a in arquivos]
        try:
            with st.spinner("Lendo o material e preparando sua jornada..."):
                S.jornada = svc.preparar(pares)
        except LimiteExcedido as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Não foi possível processar o material: {e}")
        else:
            S.mapa = copy.deepcopy(S.jornada.conteudo["mapa"])
            S.respostas, S.corrigido = {}, None
            S.chat, S.restantes = [], lim["perguntas_chat"]
            avancar()
    st.caption(f"Modo de geração: **{svc.modo}**" + ("" if svc.usar_api else
               " (sem chave de API: usando geração extrativa, sem custo)"))
    st.stop()

j = S.jornada
c = j.conteudo
if j.do_cache:
    st.success("Material já processado antes: conteúdo reaproveitado do cache, sem novas chamadas ao modelo.", icon="♻️")

# ------------------------------------------------------------------ Etapa 1
if S.etapa == 1:
    st.subheader("Resumo do conteúdo")
    termos = [t["termo"] for t in c["resumo"]["termos"]]
    for tp in c["resumo"]["topicos"]:
        with st.container(border=True):
            st.markdown(f"**{tp['titulo']}**")
            st.markdown(geracao.destacar_termos(tp["texto"], termos))
            st.caption(citacao(tp["fonte"]))
    st.markdown("#### Termos-chave para memorizar")
    for t in c["resumo"]["termos"]:
        st.markdown(f"- **{t['termo']}**: {t['definicao']}")
    if st.button("Avançar para o mapa mental", type="primary"):
        avancar()

# ------------------------------------------------------------------ Etapa 2
elif S.etapa == 2:
    st.subheader("Mapa mental")
    mapa = S.mapa
    esc = lambda s: s.replace('"', "'")
    dot = ["digraph G { rankdir=LR; node [shape=box, style=\"rounded,filled\", fillcolor=\"#eef2ff\", color=\"#4f46e5\", fontname=Helvetica];"]
    dot += [f'{n["id"]} [label="{esc(n["rotulo"])}\\n(p. {n["fonte"]["pagina"]})"];' for n in mapa["nos"]]
    dot += [f'{r["de"]} -> {r["para"]} [label="{esc(r["tipo"])}", fontsize=9];' for r in mapa["relacoes"]]
    st.graphviz_chart("\n".join(dot) + "}", width="stretch")

    por_id = {n["id"]: n for n in mapa["nos"]}
    sel = st.selectbox("Ver o trecho de um nó", list(por_id), format_func=lambda i: por_id[i]["rotulo"]) if por_id else None
    if sel:
        f = por_id[sel]["fonte"]
        st.caption(citacao(f))
        st.text(trecho_da_fonte(f)[:1200])
        imgs = c["imagens"].get(f"{f['arquivo']}|{f['pagina']}", [])
        for p in imgs[:2]:
            st.image(p, width=260)

    with st.expander("Editar mapa (adicionar/remover nós)"):
        col1, col2 = st.columns(2)
        with col1:
            rotulo = st.text_input("Novo nó")
            pai = st.selectbox("Ligar a", list(por_id), format_func=lambda i: por_id[i]["rotulo"], key="pai") if por_id else None
            if st.button("Adicionar") and rotulo.strip():
                novo = f"u{len(mapa['nos']) + 1}_{abs(hash(rotulo)) % 9999}"
                fonte = por_id[pai]["fonte"] if pai else j.trechos[0].fonte()
                mapa["nos"].append({"id": novo, "rotulo": rotulo.strip(), "fonte": fonte})
                if pai:
                    mapa["relacoes"].append({"de": pai, "para": novo, "tipo": "relaciona-se"})
                st.rerun()
        with col2:
            rem = st.selectbox("Remover nó", list(por_id), format_func=lambda i: por_id[i]["rotulo"], key="rem") if por_id else None
            if st.button("Remover") and rem:
                mapa["nos"] = [n for n in mapa["nos"] if n["id"] != rem]
                mapa["relacoes"] = [r for r in mapa["relacoes"] if rem not in (r["de"], r["para"])]
                st.rerun()
    if st.button("Avançar para o quiz", type="primary"):
        avancar()

# ------------------------------------------------------------------ Etapa 3
elif S.etapa == 3:
    st.subheader("Quiz")
    quiz = c["quiz"]
    if not quiz["questoes"]:
        st.warning("Não foi possível gerar questões com este material (conteúdo insuficiente).")
    with st.form("quiz"):
        for i, q in enumerate(quiz["questoes"]):
            st.markdown(f"**{i + 1}.** {q['enunciado']}  \n<small>nível: {q['nivel']}</small>", unsafe_allow_html=True)
            S.respostas[i] = st.radio("Alternativas", list("ABCD"), index=None, key=f"q{i}",
                                      format_func=lambda L, q=q: f"{L}) {q['alternativas']['ABCD'.index(L)]}",
                                      label_visibility="collapsed")
        enviado = st.form_submit_button("Corrigir", type="primary", disabled=S.corrigido is not None)
    if enviado:
        S.corrigido = geracao.corrigir_quiz(quiz, S.respostas)
        st.rerun()
    if S.corrigido:
        r = S.corrigido
        st.metric("Nota", f"{r['nota']}/10", f"{r['acertos']} de {r['total']} acertos")
        for it in r["itens"]:
            if not it["acertou"]:
                with st.container(border=True):
                    st.markdown(f"**Questão {it['indice'] + 1}**: você marcou {it['marcada'] or '—'}; gabarito {it['gabarito']}.")
                    st.write(it["justificativa"])
                    st.caption(citacao(it["fonte"]))
        if st.button("Ir para as dúvidas", type="primary"):
            avancar()

# ------------------------------------------------------------------ Etapa 4
elif S.etapa == 4:
    st.subheader("Tire suas dúvidas")
    total = cfg["limites"]["perguntas_chat"]
    st.markdown(f"**{S.restantes} de {total} perguntas restantes**")
    for m in S.chat:
        with st.chat_message("user"):
            st.write(m["pergunta"])
        with st.chat_message("assistant"):
            st.write(m["resposta"])
            for f in m["fontes"]:
                st.caption(citacao(f))
    if S.restantes > 0:
        if p := st.chat_input("Pergunte sobre o material"):
            with st.spinner("Buscando no material..."):
                r = svc.perguntar(j, p)
            S.chat.append({"pergunta": p, **r})
            if not r["sem_base"] or cfg["limites"]["recusa_consome_pergunta"]:
                S.restantes -= 1
            st.rerun()
    else:
        st.success("Jornada concluída! Você usou todas as perguntas. Bons estudos!", icon="🎉")
        with st.expander("Custo estimado desta jornada"):
            uso = svc.custos(j.hash)
            st.table(uso) if uso else None
            st.write(f"Total: R$ {sum(u['custo_brl'] for u in uso):.4f}")
        if st.button("Estudar outro material"):
            reiniciar()
            st.rerun()
