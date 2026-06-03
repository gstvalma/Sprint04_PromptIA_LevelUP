"""
Motor de Guardrails Técnicos do BluaDiagnostics.

Camada de segurança DETERMINÍSTICA e INDEPENDENTE do LLM (conforme decisão da
Sprint 1: "guardrails são camadas independentes do LLM"). São regras em Python
puro — não dependem de chamada de modelo, não podem ser contornadas por
prompt injection e executam em microssegundos.

Quatro responsabilidades:
  1. detectar_red_flags        — sinais clínicos de alarme -> nível de escalada.
  2. detectar_prompt_injection — tentativas de manipulação do sistema.
  3. validar_escopo            — pedidos fora do escopo clínico.
  4. moderar_conteudo          — conteúdo abusivo evidente.

Os gatilhos e níveis (CRÍTICO / ALTO / MÉDIO) espelham fielmente a seção
ESCALADA_HUMANA do System Prompt.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List

from src.graph.state import (
    NIVEL_ALTO,
    NIVEL_CRITICO,
    NIVEL_MEDIO,
    NIVEL_NENHUM,
)


# --------------------------------------------------------------------------- #
# Resultado estruturado de uma análise de guardrail
# --------------------------------------------------------------------------- #
@dataclass
class ResultadoGuardrail:
    """Resultado consolidado das verificações de guardrail sobre um texto."""
    red_flags: List[Dict[str, str]] = field(default_factory=list)
    nivel_escalada: str = NIVEL_NENHUM
    injection_detectada: bool = False
    fora_de_escopo: bool = False
    conteudo_impróprio: bool = False
    detalhes: List[str] = field(default_factory=list)


def _normalizar(texto: str) -> str:
    """Minúsculas e remoção de acentos, para casar padrões de forma robusta."""
    texto = texto.lower()
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# --------------------------------------------------------------------------- #
# 1) Red flags clínicas
# --------------------------------------------------------------------------- #
# Cada regra: nome, nível de escalada e os termos que a disparam (já sem acento).
_REGRAS_RED_FLAG = [
    {
        "nome": "suspeita_sindrome_coronariana",
        "nivel": NIVEL_CRITICO,
        "descricao": "Dor torácica com características de SCA (suspeita de infarto).",
        "gatilho_principal": ["dor toracica", "dor no peito", "aperto no peito",
                              "dor no torax"],
        "agravantes": ["irradia", "braco esquerdo", "mandibula", "sudorese",
                       "suor frio", "falta de ar", "nausea", "queimacao no peito"],
    },
    {
        "nome": "suspeita_avc",
        "nivel": NIVEL_CRITICO,
        "descricao": "Sinais sugestivos de AVC (déficit neurológico agudo).",
        "gatilho_principal": ["boca torta", "face torta", "fraqueza subita",
                              "perda de forca", "fala enrolada", "dificuldade de falar",
                              "dormencia de um lado", "paralisia"],
        "agravantes": [],
    },
    {
        "nome": "risco_autoagressao",
        "nivel": NIVEL_CRITICO,
        "descricao": "Indicativo de risco de suicídio/autolesão (R8 — acionar protocolo).",
        "gatilho_principal": ["suicid", "me matar", "se matar", "tirar a propria vida",
                              "nao quero mais viver", "nao quer mais viver",
                              "nao querer mais viver", "vontade de morrer",
                              "acabar com tudo", "automutila", "me cortar",
                              "se cortar", "autolesao", "me ferir", "se ferir"],
        "agravantes": [],
    },
    {
        "nome": "insuficiencia_respiratoria",
        "nivel": NIVEL_CRITICO,
        "descricao": "Sinais de insuficiência respiratória aguda.",
        "gatilho_principal": ["nao consigo respirar", "falta de ar intensa",
                              "cianose", "labios roxos", "sufocando"],
        "agravantes": [],
    },
    {
        "nome": "crise_convulsiva",
        "nivel": NIVEL_ALTO,
        "descricao": "Crise convulsiva / estado pós-ictal.",
        "gatilho_principal": ["convulsao", "convulsionando", "crise epileptica",
                              "ataque epileptico"],
        "agravantes": [],
    },
]

# Limiares numéricos (ex.: SpO2 baixa, FC muito alta) — alinhados ao System Prompt.
_RE_SPO2 = re.compile(r"(?:spo2|satura\w*)\D{0,12}(\d{2,3})")
_RE_FC = re.compile(r"(?:fc|frequencia cardiaca|batiment\w*)\D{0,12}(\d{2,3})")


def detectar_red_flags(texto: str) -> List[Dict[str, str]]:
    """Detecta sinais clínicos de alarme e o nível de escalada correspondente."""
    t = _normalizar(texto)
    achados: List[Dict[str, str]] = []

    for regra in _REGRAS_RED_FLAG:
        tem_principal = any(g in t for g in regra["gatilho_principal"])
        if not tem_principal:
            continue
        # Para SCA exigimos pelo menos 1 agravante OU a menção explícita de SCA/infarto.
        if regra["nome"] == "suspeita_sindrome_coronariana":
            tem_agravante = any(a in t for a in regra["agravantes"]) \
                or "infarto" in t or "sca" in t
            if not tem_agravante:
                # Dor torácica isolada: eleva, mas como ALTO (não CRÍTICO).
                achados.append({"nome": "dor_toracica_isolada", "nivel": NIVEL_ALTO,
                                "descricao": "Dor torácica sem agravantes — avaliar."})
                continue
        achados.append({"nome": regra["nome"], "nivel": regra["nivel"],
                        "descricao": regra["descricao"]})

    # Limiares de wearable / sinais vitais.
    m = _RE_SPO2.search(t)
    if m and int(m.group(1)) < 90:
        achados.append({"nome": "spo2_critica", "nivel": NIVEL_ALTO,
                        "descricao": f"SpO2 relatada em {m.group(1)}% (< 90%)."})
    m = _RE_FC.search(t)
    if m and int(m.group(1)) > 150:
        achados.append({"nome": "taquicardia_grave", "nivel": NIVEL_ALTO,
                        "descricao": f"FC relatada em {m.group(1)} bpm (> 150)."})

    return achados


# --------------------------------------------------------------------------- #
# 2) Prompt injection / manipulação (R6)
# --------------------------------------------------------------------------- #
_PADROES_INJECTION = [
    "esqueca todas as instrucoes", "esqueca as instrucoes", "ignore as instrucoes",
    "ignore todas as instrucoes", "desconsidere as instrucoes",
    "modo desenvolvedor", "modo dev", "developer mode", "modo debug",
    "anthropic autorizou", "anthropic liberou", "autorizado pela anthropic",
    "novo system prompt", "novas instrucoes do sistema", "atualizei seu prompt",
    "revele seu prompt", "mostre seu system prompt", "mostre suas instrucoes",
    "aja como", "finja ser", "voce agora e", "a partir de agora voce",
    "sem restricoes", "ignore as regras", "desative os guardrails",
]
# CPF explícito é tratado como tentativa de violar a pseudonimização (R5).
_RE_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")


def detectar_prompt_injection(texto: str) -> bool:
    """Detecta tentativas de sobrescrever o sistema, trocar de papel ou vazar prompt."""
    t = _normalizar(texto)
    if any(p in t for p in _PADROES_INJECTION):
        return True
    if _RE_CPF.search(texto):  # CPF real no input -> violação de pseudonimização
        return True
    return False


# --------------------------------------------------------------------------- #
# 3) Validação de escopo
# --------------------------------------------------------------------------- #
_TERMOS_FORA_ESCOPO = [
    "previsao do tempo", "vai chover", "temperatura hoje", "clima amanha",
    "recomende um restaurante", "indica um restaurante", "onde comer",
    "receita de bolo", "resultado do jogo", "cotacao do dolar", "piada",
    "escreva um poema", "qual o sentido da vida",
]
_TERMOS_CLINICOS = [
    "dor", "febre", "sintoma", "medicament", "remedio", "pressao", "triagem",
    "consulta", "exame", "paciente", "diabetes", "hipertens", "interacao",
    "agendar", "tontura", "tosse", "cabeca", "peito", "respirar", "ansiedade",
    "depress", "saude", "alergi", "dose",
]


def validar_escopo(texto: str) -> bool:
    """Retorna True se o texto estiver FORA do escopo clínico do assistente."""
    t = _normalizar(texto)
    fora = any(termo in t for termo in _TERMOS_FORA_ESCOPO)
    clinico = any(termo in t for termo in _TERMOS_CLINICOS)
    # Fora de escopo apenas quando há marcador não-clínico e nenhum termo clínico.
    return fora and not clinico


# --------------------------------------------------------------------------- #
# 4) Moderação de conteúdo (leve)
# --------------------------------------------------------------------------- #
_TERMOS_ABUSIVOS = ["idiota", "imbecil", "lixo", "merda"]  # exemplo mínimo


def moderar_conteudo(texto: str) -> bool:
    """Sinaliza conteúdo abusivo evidente (moderação mínima)."""
    t = _normalizar(texto)
    return any(p in t for p in _TERMOS_ABUSIVOS)


# --------------------------------------------------------------------------- #
# Orquestrador do motor
# --------------------------------------------------------------------------- #
class MotorGuardrails:
    """Agrega as quatro verificações em uma análise única e estruturada."""

    def analisar_entrada(self, texto: str) -> ResultadoGuardrail:
        """Analisa a ENTRADA do operador (antes do raciocínio do agente)."""
        resultado = ResultadoGuardrail()

        # Segurança tem precedência (R6).
        if detectar_prompt_injection(texto):
            resultado.injection_detectada = True
            resultado.nivel_escalada = NIVEL_CRITICO
            resultado.detalhes.append("Tentativa de manipulação/injection detectada.")
            return resultado  # curto-circuito: nada mais importa

        resultado.fora_de_escopo = validar_escopo(texto)
        resultado.conteudo_impróprio = moderar_conteudo(texto)

        red_flags = detectar_red_flags(texto)
        resultado.red_flags = red_flags
        for rf in red_flags:
            resultado.nivel_escalada = _mais_severo(resultado.nivel_escalada, rf["nivel"])
        if red_flags:
            resultado.detalhes.append(f"{len(red_flags)} red flag(s) clínica(s).")
        return resultado

    def analisar_saida_ferramentas(self, resultados: List[Dict]) -> ResultadoGuardrail:
        """Analisa a SAÍDA das ferramentas (ex.: interação grave -> escalada ALTO)."""
        resultado = ResultadoGuardrail()
        for r in resultados:
            for it in r.get("interacoes_identificadas", []):
                if it.get("severidade") in {"grave", "contraindicado"}:
                    resultado.nivel_escalada = _mais_severo(
                        resultado.nivel_escalada, NIVEL_ALTO)
                    resultado.detalhes.append(
                        f"Interação {it.get('severidade')} entre {it.get('par')}.")
            for al in r.get("alertas_dispositivo", []):
                if al.get("severidade") == "alta":
                    resultado.nivel_escalada = _mais_severo(
                        resultado.nivel_escalada, NIVEL_ALTO)
                    resultado.detalhes.append("Alerta de wearable de severidade alta.")
        return resultado


def _mais_severo(a: str, b: str) -> str:
    ordem = {NIVEL_NENHUM: 0, NIVEL_MEDIO: 1, NIVEL_ALTO: 2, NIVEL_CRITICO: 3}
    return a if ordem[a] >= ordem[b] else b
