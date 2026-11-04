"""Cliente LLM compatível com a API OpenAI (chave só no servidor). Sem dependências extras."""
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass


class ErroLLM(Exception):
    pass


@dataclass
class Resposta:
    dados: dict
    tokens_in: int
    tokens_out: int


class ClienteLLM:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.chave = os.environ.get(cfg["chave_env"], "")

    @staticmethod
    def disponivel(cfg: dict) -> bool:
        if cfg["provider"] == "offline":
            return False
        return bool(os.environ.get(cfg["chave_env"]))

    def custo_brl(self, tin: int, tout: int) -> float:
        c = self.cfg
        usd = tin / 1e6 * c["preco_entrada_por_milhao"] + tout / 1e6 * c["preco_saida_por_milhao"]
        return round(usd * c["cambio_brl"], 6)

    def json(self, sistema: str, usuario: str) -> Resposta:
        corpo = {
            "model": self.cfg["modelo"],
            "temperature": self.cfg["temperatura"],
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
        }
        req = urllib.request.Request(
            self.cfg["base_url"].rstrip("/") + "/chat/completions",
            data=json.dumps(corpo).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.chave}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                dados = json.load(r)
        except (urllib.error.URLError, TimeoutError) as e:
            raise ErroLLM(f"Falha ao chamar o modelo: {e}") from e
        texto = dados["choices"][0]["message"]["content"]
        texto = re.sub(r"^```(?:json)?|```$", "", texto.strip(), flags=re.M).strip()
        try:
            obj = json.loads(texto)
        except json.JSONDecodeError as e:
            raise ErroLLM("O modelo devolveu um JSON inválido.") from e
        uso = dados.get("usage", {})
        return Resposta(obj, uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0))
