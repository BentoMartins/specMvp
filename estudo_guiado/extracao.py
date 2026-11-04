"""Extração de texto por página/slide (PDF e PPTX) e de imagens, sem IA."""
import io
import re
from dataclasses import dataclass, field

import pymupdf as fitz
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


class ArquivoInvalido(Exception):
    pass


@dataclass
class Pagina:
    arquivo: str
    numero: int
    texto: str
    imagens: list[bytes] = field(default_factory=list)


def limpar_texto(texto: str) -> str:
    texto = texto.replace("\u00ad", "").replace("\x00", "")
    texto = re.sub(r"-\n(?=[a-zà-ú])", "", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def contar_paginas(nome: str, dados: bytes) -> int:
    ext = nome.lower().rsplit(".", 1)[-1]
    try:
        if ext == "pdf":
            with fitz.open(stream=dados, filetype="pdf") as doc:
                return doc.page_count
        if ext == "pptx":
            return len(Presentation(io.BytesIO(dados)).slides)
    except Exception as e:
        raise ArquivoInvalido(f"Não foi possível ler '{nome}': arquivo corrompido ou protegido.") from e
    raise ArquivoInvalido(f"Formato não suportado em '{nome}'. Envie PDF ou PPTX.")


def extrair(nome: str, dados: bytes, max_imagens_por_pagina: int = 3) -> list[Pagina]:
    ext = nome.lower().rsplit(".", 1)[-1]
    return _extrair_pdf(nome, dados, max_imagens_por_pagina) if ext == "pdf" else _extrair_pptx(nome, dados, max_imagens_por_pagina)


def _extrair_pdf(nome: str, dados: bytes, max_img: int) -> list[Pagina]:
    paginas = []
    with fitz.open(stream=dados, filetype="pdf") as doc:
        for i, pg in enumerate(doc, start=1):
            imagens = []
            for info in pg.get_images(full=True)[:max_img]:
                try:
                    imagens.append(doc.extract_image(info[0])["image"])
                except Exception:
                    continue
            paginas.append(Pagina(nome, i, limpar_texto(pg.get_text("text")), imagens))
    return paginas


def _extrair_pptx(nome: str, dados: bytes, max_img: int) -> list[Pagina]:
    paginas = []
    for i, slide in enumerate(Presentation(io.BytesIO(dados)).slides, start=1):
        partes, imagens = [], []
        for shape in slide.shapes:
            if shape.has_text_frame:
                partes.append(shape.text_frame.text)
            elif getattr(shape, "has_table", False) and shape.has_table:
                for linha in shape.table.rows:
                    partes.append(" | ".join(c.text for c in linha.cells))
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE and len(imagens) < max_img:
                imagens.append(shape.image.blob)
        if slide.has_notes_slide:
            partes.append(slide.notes_slide.notes_text_frame.text)
        paginas.append(Pagina(nome, i, limpar_texto("\n".join(partes)), imagens))
    return paginas
