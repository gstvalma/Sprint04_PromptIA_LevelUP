"""
Estado compartilhado do grafo multiagente do BluaDiagnostics.

No LangGraph, todos os nós (Supervisor, Agente de Triagem, Agente de
Ferramentas, Guardrails) leem e escrevem em um ÚNICO objeto de estado tipado.
Cada nó recebe o estado atual e devolve um dicionário PARCIAL com os campos que
deseja atualizar; o LangGraph faz o merge.

Campos com `Annotated[list, operator.add]` são ACUMULADORES: em vez de
sobrescrever, o LangGraph concatena o que cada nó devolve (usado para o histórico
de mensagens e para o log de auditoria imutável exigido pela Sprint 1).
"""

from __future__ import annotations

import operator
import uuid
from datetime import datetime, timezone, timedelta
from typing import Annotated, Any, Dict, List, Optional, TypedDict

_TZ_BR = timezone(timedelta(hours=-3))


# --------------------------------------------------------------------------- #
# Subestruturas
# --------------------------------------------------------------------------- #
class Paciente(TypedDict, total=False):
    """Contexto do paciente atual da sessão (pseudonimizado — LGPD)."""
    token: str               # UUID v4 pseudonimizado (nunca CPF)
    primeiro_nome: str
    idade_anos: int
    sexo_biologico: str


class ChunkRecuperado(TypedDict, total=False):
    """Trecho de evidência clínica recuperado pelo RAG."""
    texto: str
    doc_id: str
    secao: str
    score: float


# Níveis de escalada (espelham o System Prompt da Sprint 1).
NIVEL_NENHUM = "NENHUM"
NIVEL_MEDIO = "MEDIO"
NIVEL_ALTO = "ALTO"
NIVEL_CRITICO = "CRITICO"

# Intenções roteáveis pelo Supervisor.
INTENCAO_TRIAGEM = "triagem"
INTENCAO_MEDICACAO = "medicacao"
INTENCAO_AGENDAMENTO = "agendamento"
INTENCAO_FORA_ESCOPO = "fora_escopo"
INTENCAO_SEGURANCA = "seguranca"   # prompt injection / manipulação


# --------------------------------------------------------------------------- #
# Estado principal do grafo
# --------------------------------------------------------------------------- #
class EstadoBlua(TypedDict, total=False):
    """Estado compartilhado por todos os nós do grafo BluaDiagnostics."""

    # --- Identificação e entrada ---
    sessao_id: str
    operador_id: str
    entrada_operador: str                     # mensagem atual do operador

    # --- Memória conversacional (acumulador) ---
    historico: Annotated[List[Dict[str, str]], operator.add]

    # --- Contexto clínico ---
    paciente_atual: Optional[Paciente]
    contexto_clinico: List[ChunkRecuperado]   # chunks do RAG
    classificacao_triagem: Dict[str, Any]     # saída do Agente de Triagem
    resultados_ferramentas: List[Dict[str, Any]]  # saídas das tools

    # --- Roteamento e segurança ---
    intencao: str                             # definida pelo Supervisor
    nivel_escalada: str                       # NENHUM/MEDIO/ALTO/CRITICO
    red_flags: List[Dict[str, Any]]           # achados dos guardrails
    bloqueado: bool                           # corta o fluxo (injection/oos)
    motivo_bloqueio: str

    # --- Saída ---
    resposta_final: str

    # --- Auditoria (acumulador, imutável) ---
    log_auditoria: Annotated[List[Dict[str, Any]], operator.add]


# --------------------------------------------------------------------------- #
# Fábricas e utilitários de estado
# --------------------------------------------------------------------------- #
def criar_estado_inicial(
    entrada_operador: str,
    paciente_atual: Optional[Paciente] = None,
    sessao_id: Optional[str] = None,
    operador_id: str = "OP-DEMO0001",
) -> EstadoBlua:
    """Cria um estado inicial válido para uma nova interação de triagem."""
    return EstadoBlua(
        sessao_id=sessao_id or _gerar_sessao_id(),
        operador_id=operador_id,
        entrada_operador=entrada_operador,
        historico=[{"role": "operador", "content": entrada_operador}],
        paciente_atual=paciente_atual,
        contexto_clinico=[],
        classificacao_triagem={},
        resultados_ferramentas=[],
        intencao="",
        nivel_escalada=NIVEL_NENHUM,
        red_flags=[],
        bloqueado=False,
        motivo_bloqueio="",
        resposta_final="",
        log_auditoria=[],
    )


def _gerar_sessao_id() -> str:
    """Gera um sessao_id no padrão 'SESS-XXXXXXXXXX' (A-Z/0-9)."""
    bruto = uuid.uuid4().hex.upper()
    return "SESS-" + bruto[:10]


def registrar_auditoria(gatilho: str, detalhe: Any = None) -> Dict[str, Any]:
    """Monta um registro padronizado para o log de auditoria imutável."""
    return {
        "timestamp": datetime.now(_TZ_BR).isoformat(),
        "gatilho": gatilho,
        "detalhe": detalhe,
    }


def nivel_mais_severo(a: str, b: str) -> str:
    """Retorna o mais severo entre dois níveis de escalada."""
    ordem = {NIVEL_NENHUM: 0, NIVEL_MEDIO: 1, NIVEL_ALTO: 2, NIVEL_CRITICO: 3}
    return a if ordem.get(a, 0) >= ordem.get(b, 0) else b
