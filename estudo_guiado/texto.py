import re
import unicodedata

STOPWORDS = set("""a o as os um uma uns umas de do da dos das em no na nos nas por para com sem sob sobre entre e ou mas
que se como mais menos muito muita ao aos à às é são foi ser ter tem há ja já nao não seu sua seus suas este esta
estes estas esse essa isso isto aquele aquela quando onde qual quais quem cada outro outra pode podem deve devem
the of and to in is are for on with by an be this that""".split())


def normalizar(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def tokens(s: str, remover_stop: bool = True) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", normalizar(s))
    return [t for t in toks if len(t) > 1 and not (remover_stop and t in STOPWORDS)]


def frases(s: str) -> list[str]:
    s = re.sub(r"\s+", " ", s)
    return [f.strip() for f in re.split(r"(?<=[.!?])\s+", s) if len(f.strip()) > 25]


def contem_termo(texto: str, termo: str) -> bool:
    return normalizar(termo) in normalizar(texto)
