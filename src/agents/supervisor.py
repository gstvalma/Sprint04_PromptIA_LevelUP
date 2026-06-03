"""
Agente Supervisor do BluaDiagnostics.

O Supervisor é o orquestrador de alto nível do sistema multiagente. Ele NÃO faz
triagem nem chama ferramentas: sua função é (1) classificar a intenção da
mensagem do operador e (2) decidir para qual agente especializado encaminhar.

A decisão de segurança (red flags / escalada) é feita pelo Motor de Guardrails
ANTES do Supervisor, no nó de entrada do grafo. O Supervisor apenas respeita o
nível de escalada já presente no estado.
"""

from __future__ import annotations

from typing import Any, Dict

from src.agents.cerebro import Cerebro, CerebroHeuristico
from src.graph.state import EstadoBlua, registrar_auditoria


class Supervisor:
    """Classifica a intenção e prepara o roteamento condicional do grafo."""

    def __init__(self, cerebro: Cerebro | None = None) -> None:
        self._cerebro = cerebro or CerebroHeuristico()

    def __call__(self, estado: EstadoBlua) -> Dict[str, Any]:
        """Nó do grafo: define `intencao` no estado e registra a decisão."""
        intencao = self._cerebro.classificar_intencao(estado["entrada_operador"])
        return {
            "intencao": intencao,
            "log_auditoria": [registrar_auditoria(
                "supervisor_classificou_intencao",
                {"intencao": intencao, "nivel_escalada": estado.get("nivel_escalada")})],
        }
