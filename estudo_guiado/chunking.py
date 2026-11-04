from dataclasses import dataclass

from .extracao import Pagina


@dataclass
class Trecho:
    id: int
    arquivo: str
    pagina: int
    texto: str

    def fonte(self) -> dict:
        return {"arquivo": self.arquivo, "pagina": self.pagina}


def dividir(paginas: list[Pagina], alvo: int = 450, sobreposicao: int = 60) -> list[Trecho]:
    """Janelas de palavras com sobreposição, sem cruzar fronteiras de página/slide."""
    trechos: list[Trecho] = []
    passo = max(1, alvo - sobreposicao)
    for pg in paginas:
        palavras = pg.texto.split()
        if len(palavras) < 3:
            continue
        for ini in range(0, len(palavras), passo):
            janela = palavras[ini:ini + alvo]
            if ini > 0 and len(janela) <= sobreposicao:
                break
            trechos.append(Trecho(len(trechos), pg.arquivo, pg.numero, " ".join(janela)))
            if ini + alvo >= len(palavras):
                break
    return trechos
