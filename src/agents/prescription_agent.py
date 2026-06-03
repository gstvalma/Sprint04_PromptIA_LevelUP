"""
Agente de Prescrição / Ferramentas do BluaDiagnostics.

Especialista na execução de FERRAMENTAS (function calling). Recebe do cérebro um
PLANO de chamadas e as executa via o dispatcher `executar_ferramenta`, fazendo o
encadeamento clínico necessário — por exemplo, primeiro consultar o histórico do
paciente e só então verificar interações usando os medicamentos reais em uso.

Restrições respeitadas (Sprint 1):
  * R2 — não prescreve: apenas verifica interações e organiza informação.
  * R3 — não age sozinho: `agendar_teleconsulta` é proposto SEM confirmação
    (a trava de human-in-the-loop está na própria ferramenta).
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.agents.cerebro import Cerebro, CerebroHeuristico
from src.graph.state import EstadoBlua, registrar_auditoria
from src.tools.clinical_tools import executar_ferramenta


class AgenteFerramentas:
    """Planeja e executa as ferramentas clínicas, encadeando suas saídas."""

    def __init__(self, cerebro: Cerebro | None = None) -> None:
        self._cerebro = cerebro or CerebroHeuristico()

    def __call__(self, estado: EstadoBlua) -> Dict[str, Any]:
        """Nó do grafo: executa o plano de ferramentas e registra os resultados."""
        plano = self._cerebro.planejar_ferramentas(estado)
        resultados: List[Dict[str, Any]] = []
        medicamentos_em_uso: List[Dict[str, Any]] = []

        for nome, args in plano:
            if nome == "consultar_historico_paciente":
                saida = executar_ferramenta(nome, args)
                resultados.append({"ferramenta": nome, "saida": saida, **saida})
                # Extrai medicamentos em uso para alimentar a verificação seguinte.
                meds = saida.get("dados", {}).get("medicamentos_em_uso", [])
                medicamentos_em_uso = self._converter_medicamentos(meds)

            elif nome == "__verificar_interacoes_com_historico__":
                # Pseudo-passo: monta a chamada real usando o histórico recuperado.
                lista = list(medicamentos_em_uso)
                lista.append({"principio_ativo": args["novo_medicamento"],
                              "dose_mg": 600, "frequencia_diaria": 3,
                              "novo_medicamento": True})
                if len(lista) >= 2:
                    saida = executar_ferramenta("verificar_interacoes_medicamentosas", {
                        "medicamentos": lista,
                        "perfil_paciente": args["perfil_paciente"],
                        "sessao_triagem_id": args["sessao_triagem_id"],
                    })
                    resultados.append({"ferramenta": "verificar_interacoes_medicamentosas",
                                       "saida": saida, **saida})

            else:
                saida = executar_ferramenta(nome, args)
                resultados.append({"ferramenta": nome, "saida": saida, **saida})

        return {
            "resultados_ferramentas": resultados,
            "historico": [{"role": "agente_ferramentas",
                           "content": f"{len(resultados)} ferramenta(s) executada(s)."}],
            "log_auditoria": [registrar_auditoria(
                "agente_ferramentas_executou",
                {"ferramentas": [r["ferramenta"] for r in resultados]})],
        }

    @staticmethod
    def _converter_medicamentos(meds_pep: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Converte medicamentos do PEP para o formato esperado pela verificação."""
        convertidos: List[Dict[str, Any]] = []
        for m in meds_pep:
            dose_txt = str(m.get("dose", "0")).lower().replace("mg", "").strip()
            try:
                dose = float(dose_txt)
            except ValueError:
                dose = 0.0
            freq_txt = str(m.get("frequencia", "1")).lower()
            freq = 2 if "2x" in freq_txt else (3 if "3x" in freq_txt else 1)
            convertidos.append({
                "principio_ativo": m.get("principio_ativo", ""),
                "dose_mg": dose, "frequencia_diaria": freq, "novo_medicamento": False,
            })
        return convertidos
