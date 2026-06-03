"""
Agente de Triagem do BluaDiagnostics.

Especialista em classificação de risco. Seu fluxo:
  1. Recupera evidência clínica relevante via RAG (ClinicalRetriever em produção,
     ou o fallback por palavra-chave offline).
  2. Delega ao cérebro a SÍNTESE da classificação de triagem a partir da queixa +
     evidência recuperada.
  3. Registra contexto e classificação no estado, sem nunca emitir diagnóstico
     (Restrição R1 da Sprint 1).

O recuperador é injetado (inversão de dependência): aceita qualquer objeto com o
método `.buscar(consulta, top_k)` que devolva itens com `.texto`, `.metadados` e
`.score` — tanto o RAG real quanto o fallback.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.agents.cerebro import Cerebro, CerebroHeuristico, RecuperadorPorPalavraChave
from src.graph.state import EstadoBlua, registrar_auditoria


class AgenteTriagem:
    """Recupera contexto clínico (RAG) e sintetiza a classificação de triagem."""

    def __init__(self, cerebro: Cerebro | None = None, recuperador: Any = None,
                 top_k: int = 3) -> None:
        self._cerebro = cerebro or CerebroHeuristico()
        # Recuperador injetável; padrão = fallback offline por palavra-chave.
        self._recuperador = recuperador or RecuperadorPorPalavraChave()
        self._top_k = top_k

    def __call__(self, estado: EstadoBlua) -> Dict[str, Any]:
        """Nó do grafo: enriquece o estado com contexto RAG e a triagem."""
        consulta = estado["entrada_operador"]

        # 1) Recuperação aumentada (RAG).
        resultados = self._recuperador.buscar(consulta, top_k=self._top_k)
        contexto: List[Dict[str, Any]] = [
            {
                "texto": r.texto,
                "doc_id": r.metadados.get("doc_id", "?"),
                "secao": r.metadados.get("secao", "?"),
                "score": round(float(r.score), 4),
            }
            for r in resultados
        ]

        # 2) Síntese da triagem (sem diagnóstico — R1).
        classificacao = self._cerebro.sintetizar_triagem(consulta, contexto)

        return {
            "contexto_clinico": contexto,
            "classificacao_triagem": classificacao,
            "historico": [{"role": "agente_triagem",
                           "content": classificacao.get("raciocinio", "")}],
            "log_auditoria": [registrar_auditoria(
                "agente_triagem_executou",
                {"cor": classificacao.get("classificacao_manchester"),
                 "fontes": classificacao.get("fontes_rag")})],
        }
