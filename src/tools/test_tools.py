"""
Testes unitários das ferramentas clínicas do BluaDiagnostics.

Cobertura por dimensão:
  * CONTRATO  — entradas mal-formadas levantam ToolValidationError.
  * DOMÍNIO   — paciente inexistente / consentimento inativo retornam status erro.
  * CLÍNICO   — regras de interação e alertas de dose corretos.
  * SEGURANÇA — human-in-the-loop de `agendar_teleconsulta`.
  * DISPATCH  — roteamento e tratamento de erros do `executar_ferramenta`.
  * SCHEMAS   — fidelidade dos contratos de function calling.

Execução (a partir da raiz do projeto):
    pytest -v
    # ou, sem pytest instalado, este arquivo também roda standalone:
    python src/tools/test_tools.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.tools.clinical_tools import (
    TOKEN_ANTONIO,
    TOKEN_MARIA,
    TOOL_DISPATCHER,
    TOOL_SCHEMAS,
    ToolValidationError,
    agendar_teleconsulta,
    consultar_historico_paciente,
    executar_ferramenta,
    recuperar_dados_wearable,
    verificar_interacoes_medicamentosas,
)

# Constantes válidas reutilizadas nos testes.
SESSAO = "SESS-K7M2X9ABCD"
OPERADOR = "OP-X7K2M9AB"
AGORA = datetime(2026, 6, 1, 10, 0, tzinfo=timezone(timedelta(hours=-3)))


# =========================================================================== #
# TOOL 1 — consultar_historico_paciente
# =========================================================================== #
class TestConsultarHistorico:
    def test_retorna_dados_da_maria_hipertensa_com_losartana(self):
        """Caso canônico: Maria, hipertensa, em uso contínuo de losartana 50mg."""
        r = consultar_historico_paciente(
            patient_token=TOKEN_MARIA,
            secoes_solicitadas=["comorbidades_ativas", "medicamentos_em_uso",
                                "consultas_recentes"],
            sessao_triagem_id=SESSAO,
        )
        assert r["status"] == "sucesso"
        cids = [c["cid"] for c in r["dados"]["comorbidades_ativas"]]
        assert "I10" in cids  # Hipertensão arterial essencial
        med = r["dados"]["medicamentos_em_uso"][0]
        assert med["principio_ativo"] == "Losartana" and med["dose"] == "50mg"
        assert med["uso_continuo"] is True
        # Última consulta em 03/2026.
        assert r["dados"]["consultas_recentes"][0]["data"].startswith("2026-03")

    def test_minimizacao_retorna_apenas_secoes_pedidas(self):
        r = consultar_historico_paciente(
            patient_token=TOKEN_MARIA, secoes_solicitadas=["alergias"],
            sessao_triagem_id=SESSAO,
        )
        assert set(r["dados"].keys()) == {"alergias"}

    def test_profundidade_resumo_limita_a_tres_registros(self):
        r = consultar_historico_paciente(
            patient_token=TOKEN_ANTONIO, secoes_solicitadas=["medicamentos_em_uso"],
            sessao_triagem_id=SESSAO, profundidade="resumo",
        )
        assert len(r["dados"]["medicamentos_em_uso"]) <= 3

    def test_token_invalido_levanta_erro_de_contrato(self):
        with pytest.raises(ToolValidationError):
            consultar_historico_paciente(
                patient_token="12345",  # não é UUID v4
                secoes_solicitadas=["alergias"], sessao_triagem_id=SESSAO,
            )

    def test_secao_invalida_levanta_erro(self):
        with pytest.raises(ToolValidationError):
            consultar_historico_paciente(
                patient_token=TOKEN_MARIA, secoes_solicitadas=["inexistente"],
                sessao_triagem_id=SESSAO,
            )

    def test_paciente_inexistente_retorna_status_erro(self):
        token_valido_mas_inexistente = "11111111-2222-4333-8444-555555555555"
        r = consultar_historico_paciente(
            patient_token=token_valido_mas_inexistente,
            secoes_solicitadas=["alergias"], sessao_triagem_id=SESSAO,
        )
        assert r["status"] == "erro" and r["codigo"] == "paciente_nao_encontrado"


# =========================================================================== #
# TOOL 2 — verificar_interacoes_medicamentosas
# =========================================================================== #
class TestVerificarInteracoes:
    def _med(self, nome, dose, freq, novo=False):
        return {"principio_ativo": nome, "dose_mg": dose,
                "frequencia_diaria": freq, "novo_medicamento": novo}

    def test_losartana_mais_aine_e_interacao_grave(self):
        r = verificar_interacoes_medicamentosas(
            medicamentos=[self._med("losartana", 50, 1),
                          self._med("ibuprofeno", 600, 3, novo=True)],
            perfil_paciente={"idade_anos": 34, "sexo_biologico": "feminino"},
            sessao_triagem_id=SESSAO,
        )
        assert r["status"] == "sucesso"
        assert any(i["severidade"] == "grave" for i in r["interacoes_identificadas"])
        assert "ATENÇÃO" in r["resumo_seguranca"]

    def test_alerta_de_dose_renal_para_metformina_com_tfg_baixa(self):
        r = verificar_interacoes_medicamentosas(
            medicamentos=[self._med("metformina", 850, 2),
                          self._med("losartana", 50, 1)],
            perfil_paciente={"idade_anos": 62, "sexo_biologico": "masculino",
                             "tfg_ml_min": 28},
            sessao_triagem_id=SESSAO,
        )
        tipos = [a["tipo"] for a in r["alertas_adicionais"]]
        assert "contraindicacao_renal" in tipos

    def test_par_sem_interacao_conhecida(self):
        r = verificar_interacoes_medicamentosas(
            medicamentos=[self._med("paracetamol", 750, 3),
                          self._med("dipirona", 500, 4)],
            perfil_paciente={"idade_anos": 40, "sexo_biologico": "feminino"},
            sessao_triagem_id=SESSAO,
        )
        assert r["interacoes_identificadas"] == []
        assert "Nenhuma interação" in r["resumo_seguranca"]

    def test_exige_no_minimo_dois_medicamentos(self):
        with pytest.raises(ToolValidationError):
            verificar_interacoes_medicamentosas(
                medicamentos=[self._med("losartana", 50, 1)],
                perfil_paciente={"idade_anos": 34, "sexo_biologico": "feminino"},
                sessao_triagem_id=SESSAO,
            )

    def test_perfil_sem_campos_obrigatorios_levanta_erro(self):
        with pytest.raises(ToolValidationError):
            verificar_interacoes_medicamentosas(
                medicamentos=[self._med("losartana", 50, 1),
                              self._med("ibuprofeno", 600, 3)],
                perfil_paciente={"idade_anos": 34},  # falta sexo_biologico
                sessao_triagem_id=SESSAO,
            )

    def test_normalizacao_de_acentos_no_nome_do_farmaco(self):
        """'Losartana' com caixa/acentos diferentes ainda casa a regra de classe."""
        r = verificar_interacoes_medicamentosas(
            medicamentos=[self._med("LOSARTANA", 50, 1),
                          self._med("Diclofenaco", 50, 2, novo=True)],
            perfil_paciente={"idade_anos": 50, "sexo_biologico": "masculino"},
            sessao_triagem_id=SESSAO,
        )
        assert any(i["severidade"] == "grave" for i in r["interacoes_identificadas"])


# =========================================================================== #
# TOOL 3 — agendar_teleconsulta (human-in-the-loop)
# =========================================================================== #
class TestAgendarTeleconsulta:
    def test_sem_confirmacao_nao_agenda(self):
        """Trava de human-in-the-loop: sem confirmação, não há agendamento."""
        r = agendar_teleconsulta(
            patient_token=TOKEN_MARIA, especialidade="cardiologia",
            prioridade="urgencia", motivo_consulta="Dor torácica.",
            operador_id=OPERADOR, sessao_triagem_id=SESSAO,
            confirmacao_operador=False,
        )
        assert r["status"] == "aguardando_confirmacao"
        assert "numero_agendamento" not in r

    def test_com_confirmacao_agenda_com_sucesso(self):
        r = agendar_teleconsulta(
            patient_token=TOKEN_MARIA, especialidade="cardiologia",
            prioridade="urgencia", motivo_consulta="Dor torácica.",
            operador_id=OPERADOR, sessao_triagem_id=SESSAO,
            confirmacao_operador=True, agora=AGORA,
        )
        assert r["status"] == "agendado"
        assert r["numero_agendamento"].startswith("AGD-")
        assert r["medico_alocado"]["especialidade"] == "Cardiologia"

    def test_urgencia_agenda_dentro_de_duas_horas(self):
        r = agendar_teleconsulta(
            patient_token=TOKEN_MARIA, especialidade="clinica_medica",
            prioridade="urgencia", motivo_consulta="Quadro agudo.",
            operador_id=OPERADOR, sessao_triagem_id=SESSAO,
            confirmacao_operador=True, agora=AGORA,
        )
        horario = datetime.fromisoformat(r["horario_consulta"])
        assert horario - AGORA <= timedelta(hours=2)

    def test_operador_id_invalido_levanta_erro(self):
        with pytest.raises(ToolValidationError):
            agendar_teleconsulta(
                patient_token=TOKEN_MARIA, especialidade="cardiologia",
                prioridade="rotina", motivo_consulta="Consulta.",
                operador_id="operador_errado", sessao_triagem_id=SESSAO,
                confirmacao_operador=True,
            )

    def test_motivo_acima_de_500_caracteres_levanta_erro(self):
        with pytest.raises(ToolValidationError):
            agendar_teleconsulta(
                patient_token=TOKEN_MARIA, especialidade="cardiologia",
                prioridade="rotina", motivo_consulta="x" * 501,
                operador_id=OPERADOR, sessao_triagem_id=SESSAO,
                confirmacao_operador=True,
            )


# =========================================================================== #
# TOOL 4 — recuperar_dados_wearable
# =========================================================================== #
class TestRecuperarWearable:
    def test_maria_com_consentimento_ativo_retorna_metricas(self):
        r = recuperar_dados_wearable(
            patient_token=TOKEN_MARIA,
            metricas_solicitadas=["frequencia_cardiaca_repouso", "sono_total_horas"],
            periodo={"ultimas_horas": 72}, sessao_triagem_id=SESSAO, agora=AGORA,
        )
        assert r["status"] == "sucesso"
        assert "frequencia_cardiaca_repouso" in r["metricas"]

    def test_antonio_sem_consentimento_e_bloqueado(self):
        r = recuperar_dados_wearable(
            patient_token=TOKEN_ANTONIO,
            metricas_solicitadas=["frequencia_cardiaca_repouso"],
            periodo={"ultimas_horas": 24}, sessao_triagem_id=SESSAO,
        )
        assert r["status"] == "erro" and r["codigo"] == "consentimento_inativo"

    def test_alerta_de_afib_quando_metrica_solicitada(self):
        r = recuperar_dados_wearable(
            patient_token=TOKEN_MARIA,
            metricas_solicitadas=["irregularidade_ritmo_afib"],
            periodo={"ultimas_horas": 72}, sessao_triagem_id=SESSAO,
            incluir_alertas_dispositivo=True, agora=AGORA,
        )
        assert any(a["tipo"] == "irregularidade_ritmo_cardiaco"
                   for a in r["alertas_dispositivo"])

    def test_metrica_invalida_levanta_erro(self):
        with pytest.raises(ToolValidationError):
            recuperar_dados_wearable(
                patient_token=TOKEN_MARIA, metricas_solicitadas=["batimento_alienigena"],
                periodo={"ultimas_horas": 24}, sessao_triagem_id=SESSAO,
            )

    def test_periodo_sem_campos_obrigatorios_levanta_erro(self):
        with pytest.raises(ToolValidationError):
            recuperar_dados_wearable(
                patient_token=TOKEN_MARIA,
                metricas_solicitadas=["frequencia_cardiaca_repouso"],
                periodo={}, sessao_triagem_id=SESSAO,
            )


# =========================================================================== #
# Dispatcher e schemas
# =========================================================================== #
class TestDispatcherESchemas:
    def test_dispatcher_roteia_para_a_funcao_correta(self):
        r = executar_ferramenta("consultar_historico_paciente", {
            "patient_token": TOKEN_MARIA, "secoes_solicitadas": ["alergias"],
            "sessao_triagem_id": SESSAO,
        })
        assert r["status"] == "sucesso"

    def test_dispatcher_ferramenta_desconhecida(self):
        r = executar_ferramenta("ferramenta_fantasma", {})
        assert r["status"] == "erro" and r["codigo"] == "ferramenta_desconhecida"

    def test_dispatcher_converte_erro_de_contrato_em_payload(self):
        """ToolValidationError vira payload — não derruba o laço do agente."""
        r = executar_ferramenta("consultar_historico_paciente", {
            "patient_token": "invalido", "secoes_solicitadas": ["alergias"],
            "sessao_triagem_id": SESSAO,
        })
        assert r["status"] == "erro" and r["codigo"] == "validacao"

    def test_todos_os_schemas_tem_nome_e_estao_no_dispatcher(self):
        nomes_schema = {s["name"] for s in TOOL_SCHEMAS}
        assert nomes_schema == set(TOOL_DISPATCHER.keys())
        for s in TOOL_SCHEMAS:
            assert "input_schema" in s and "required" in s["input_schema"]

    def test_agendar_exige_confirmacao_no_contrato_de_seguranca(self):
        schema = next(s for s in TOOL_SCHEMAS if s["name"] == "agendar_teleconsulta")
        assert "confirmacao_operador" in schema["input_schema"]["properties"]


# --------------------------------------------------------------------------- #
# Execução standalone (sem pytest): roda uma verificação mínima de fumaça.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    try:
        sys.exit(pytest.main([__file__, "-v"]))
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover
        print(f"Falha ao executar pytest: {exc}")
        sys.exit(1)
