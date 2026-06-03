"""
Ferramentas clínicas (Function Calling) do BluaDiagnostics — Care Plus.

Este módulo implementa as 4 ferramentas especificadas na Sprint 1, no padrão de
function calling Anthropic/OpenAI:

    1. consultar_historico_paciente        (somente leitura)
    2. verificar_interacoes_medicamentosas (somente leitura)
    3. agendar_teleconsulta                (AÇÃO — exige confirmação do operador)
    4. recuperar_dados_wearable            (somente leitura, bonus)

Princípios de engenharia adotados
----------------------------------
* CONTRATO vs. DOMÍNIO:
    - Violações de contrato (token mal-formado, sessão fora do padrão, faltam
      campos obrigatórios) levantam `ToolValidationError`. São erros de quem
      CHAMA a ferramenta.
    - Situações de domínio (paciente inexistente, consentimento inativo)
      retornam um payload estruturado `{"status": "erro", ...}`, pois o LLM
      precisa observar isso como resultado da ferramenta (tool_result).

* HUMAN-IN-THE-LOOP NA PRÓPRIA FERRAMENTA:
    `agendar_teleconsulta` é a única ação com efeito no mundo real. Conforme a
    matriz de autorização da Sprint 1, ela NÃO executa sem `confirmacao_operador`
    explícita — devolvendo `status="aguardando_confirmacao"`. A trava não vive
    apenas na orquestração; ela é defensiva e local.

* DETERMINISMO:
    Horários e identificadores derivam de um relógio injetável (`agora`) e de
    hashes estáveis, tornando o módulo 100% testável de forma determinística.

IMPORTANTE: todos os dados retornados aqui são SIMULADOS (mock). Esta camada
substitui, na PoC, as integrações reais (PEP, base farmacológica, agendamento,
APIs de wearables). Dados de wearable são indicativos e jamais substituem
avaliação clínica formal.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

# Fuso horário de Brasília (residência de dados LGPD — região sa-east-1).
_TZ_BR = timezone(timedelta(hours=-3))


# --------------------------------------------------------------------------- #
# Exceções
# --------------------------------------------------------------------------- #
class ToolValidationError(ValueError):
    """Erro de CONTRATO: a chamada à ferramenta violou o schema/formato."""


# --------------------------------------------------------------------------- #
# Validadores de formato (espelham os `pattern` dos JSON Schemas da Sprint 1)
# --------------------------------------------------------------------------- #
_RE_PATIENT_TOKEN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_RE_SESSAO = re.compile(r"^SESS-[A-Z0-9]{10}$")
_RE_OPERADOR = re.compile(r"^OP-[A-Z0-9]{8}$")


def _exigir(condicao: bool, mensagem: str) -> None:
    """Levanta ToolValidationError se a condição de contrato não for satisfeita."""
    if not condicao:
        raise ToolValidationError(mensagem)


def _validar_patient_token(token: Any) -> str:
    _exigir(isinstance(token, str) and bool(_RE_PATIENT_TOKEN.match(token)),
            "patient_token inválido: deve ser um UUID v4 pseudonimizado "
            "(ex.: '550e8400-e29b-41d4-a716-446655440000'). NUNCA usar CPF real.")
    return token


def _validar_sessao(sessao: Any) -> str:
    _exigir(isinstance(sessao, str) and bool(_RE_SESSAO.match(sessao)),
            "sessao_triagem_id inválido: esperado padrão 'SESS-XXXXXXXXXX' "
            "(SESS- seguido de 10 caracteres A-Z/0-9).")
    return sessao


def _validar_operador(operador: Any) -> str:
    _exigir(isinstance(operador, str) and bool(_RE_OPERADOR.match(operador)),
            "operador_id inválido: esperado padrão 'OP-XXXXXXXX' "
            "(OP- seguido de 8 caracteres A-Z/0-9).")
    return operador


# --------------------------------------------------------------------------- #
# CAMADA DE MOCK DATA — substitui as integrações reais na PoC
# --------------------------------------------------------------------------- #

# Tokens canônicos usados na documentação e nos testes.
TOKEN_MARIA = "a3f1c2d4-5b6e-4f7a-8c9d-0123456789ab"   # caso canônico do enunciado
TOKEN_ANTONIO = "550e8400-e29b-41d4-a716-446655440000"  # caso do exemplo da spec

# Prontuário Eletrônico do Paciente (PEP) simulado, indexado por patient_token.
_MOCK_PEP: Dict[str, Dict[str, Any]] = {
    # ----- Maria, 34 anos, hipertensa, losartana 50mg de uso contínuo -------
    TOKEN_MARIA: {
        "ultima_atualizacao_pep": "2026-03-18T10:30:00-03:00",
        "demografia": {"primeiro_nome": "Maria", "idade_anos": 34,
                       "sexo_biologico": "feminino"},
        "secoes": {
            "alergias": [
                {"substancia": "Ácido acetilsalicílico (AAS)",
                 "reacao": "Urticária", "severidade": "leve",
                 "confirmada_em": "2021-08-02"},
            ],
            "medicamentos_em_uso": [
                {"principio_ativo": "Losartana", "dose": "50mg",
                 "frequencia": "1x/dia", "uso_continuo": True,
                 "prescrito_em": "2026-03-18", "prescrito_por": "MÉDICO-CRM-SP-45678"},
            ],
            "comorbidades_ativas": [
                {"cid": "I10", "descricao": "Hipertensão arterial essencial",
                 "desde": "2022"},
            ],
            "consultas_recentes": [
                {"data": "2026-03-18", "especialidade": "clinica_medica",
                 "motivo": "Acompanhamento de hipertensão arterial",
                 "conduta": "Mantida losartana 50mg/dia; PA controlada (128/82)."},
                {"data": "2025-12-10", "especialidade": "clinica_medica",
                 "motivo": "Renovação de receita de uso contínuo",
                 "conduta": "Exames de rotina solicitados."},
            ],
            "dados_antropometricos": [
                {"data": "2026-03-18", "peso_kg": 68.0, "altura_cm": 165,
                 "imc": 24.98, "tfg_ml_min": 95.0},
            ],
            "historico_internacoes": [],
        },
    },

    # ----- Antônio, 62 anos, DM2 + HAS (alinhado ao exemplo da spec) --------
    TOKEN_ANTONIO: {
        "ultima_atualizacao_pep": "2026-05-10T09:15:00-03:00",
        "demografia": {"primeiro_nome": "Antônio", "idade_anos": 62,
                       "sexo_biologico": "masculino"},
        "secoes": {
            "alergias": [
                {"substancia": "Penicilina", "reacao": "Anafilaxia",
                 "severidade": "grave", "confirmada_em": "2019-03-15"},
            ],
            "medicamentos_em_uso": [
                {"principio_ativo": "Metformina", "dose": "850mg",
                 "frequencia": "2x/dia", "uso_continuo": True,
                 "prescrito_em": "2025-11-20", "prescrito_por": "MÉDICO-CRM12345"},
                {"principio_ativo": "Losartana", "dose": "50mg",
                 "frequencia": "1x/dia", "uso_continuo": True,
                 "prescrito_em": "2025-11-20", "prescrito_por": "MÉDICO-CRM12345"},
            ],
            "comorbidades_ativas": [
                {"cid": "E11", "descricao": "Diabetes mellitus tipo 2",
                 "desde": "2019"},
                {"cid": "I10", "descricao": "Hipertensão arterial essencial",
                 "desde": "2018"},
            ],
            "consultas_recentes": [
                {"data": "2026-05-10", "especialidade": "endocrinologia",
                 "motivo": "Controle glicêmico", "conduta": "Mantida metformina."},
            ],
            "dados_antropometricos": [
                {"data": "2026-05-10", "peso_kg": 78.0, "altura_cm": 172,
                 "imc": 26.4, "tfg_ml_min": 55.0},
            ],
            "historico_internacoes": [],
        },
    },
}

# Seções válidas do prontuário (enum do schema da Tool 1).
_SECOES_VALIDAS = {
    "alergias", "medicamentos_em_uso", "comorbidades_ativas",
    "historico_internacoes", "consultas_recentes", "exames_laboratoriais",
    "exames_imagem", "vacinas", "cirurgias_procedimentos",
    "historico_familiar", "habitos_vida", "dados_antropometricos",
}

# Classe dos AINEs (anti-inflamatórios não esteroidais) para regras de interação.
_AINES = {"ibuprofeno", "naproxeno", "diclofenaco", "cetoprofeno",
          "acido acetilsalicilico", "aas", "nimesulida", "piroxicam"}

# Base de interações conhecidas (mock), indexada por par ordenado de fármacos.
# Espelha o Compêndio de Interações da knowledge base (DOC-RX-INTERACTIONS-CP).
_BASE_INTERACOES: Dict[frozenset, Dict[str, str]] = {
    frozenset({"losartana", "ibuprofeno"}): {
        "severidade": "grave",
        "mecanismo": ("AINEs reduzem o efeito anti-hipertensivo dos BRA e podem "
                      "precipitar insuficiência renal aguda, sobretudo com TFGe "
                      "comprometida."),
        "conduta_recomendada": ("Evitar combinação. Considerar paracetamol como "
                                "alternativa. Se imprescindível, monitorar função "
                                "renal e PA."),
        "referencia": "Micromedex 2026 — Nível de Evidência: Excelente",
    },
    frozenset({"metformina", "ibuprofeno"}): {
        "severidade": "moderado",
        "mecanismo": ("AINEs podem reduzir a TFG e aumentar o risco de acidose "
                      "láctica em uso de metformina com função renal limítrofe."),
        "conduta_recomendada": ("Monitorar creatinina se uso > 3 dias; cautela "
                                "adicional em TFGe reduzida."),
        "referencia": "ANVISA — Bula metformina cloridrato (2025)",
    },
    frozenset({"varfarina", "ibuprofeno"}): {
        "severidade": "grave",
        "mecanismo": "Aumento expressivo do risco hemorrágico (gastrointestinal).",
        "conduta_recomendada": "Evitar associação; preferir analgésico alternativo.",
        "referencia": "Micromedex 2026",
    },
}

# Consentimento de wearable por paciente (mock). Antônio não consentiu.
_CONSENTIMENTO_WEARABLE: Dict[str, bool] = {
    TOKEN_MARIA: True,
    TOKEN_ANTONIO: False,
}


# --------------------------------------------------------------------------- #
# TOOL 1 — consultar_historico_paciente  (somente leitura)
# --------------------------------------------------------------------------- #
def consultar_historico_paciente(
    patient_token: str,
    secoes_solicitadas: List[str],
    sessao_triagem_id: str,
    profundidade: str = "resumo",
    periodo_consulta: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Recupera seções do PEP de um beneficiário (mock).

    Aplica o princípio da minimização de dados (LGPD Art. 6º, III): retorna
    apenas as `secoes_solicitadas`. Em profundidade 'resumo', limita cada seção
    aos 3 registros mais recentes.

    Args:
        patient_token: UUID v4 pseudonimizado (nunca CPF).
        secoes_solicitadas: subconjunto não vazio das seções válidas do PEP.
        sessao_triagem_id: identificador da sessão (auditoria).
        profundidade: 'resumo' (3 últimos por seção) ou 'completo'.
        periodo_consulta: filtro de período (aceito, simplificado no mock).

    Returns:
        Dict com status, metadados de proveniência e os dados solicitados.

    Raises:
        ToolValidationError: em violação de contrato (formato/campos).
    """
    _validar_patient_token(patient_token)
    _validar_sessao(sessao_triagem_id)
    _exigir(isinstance(secoes_solicitadas, list) and len(secoes_solicitadas) >= 1,
            "secoes_solicitadas deve conter ao menos 1 seção.")
    invalidas = set(secoes_solicitadas) - _SECOES_VALIDAS
    _exigir(not invalidas, f"Seções inválidas: {sorted(invalidas)}.")
    _exigir(profundidade in {"resumo", "completo"},
            "profundidade deve ser 'resumo' ou 'completo'.")

    paciente = _MOCK_PEP.get(patient_token)
    if paciente is None:
        return {"status": "erro", "codigo": "paciente_nao_encontrado",
                "mensagem": "Nenhum beneficiário associado ao token informado.",
                "patient_token": patient_token}

    dados: Dict[str, Any] = {}
    for secao in secoes_solicitadas:
        registros = paciente["secoes"].get(secao, [])
        if profundidade == "resumo" and isinstance(registros, list):
            registros = registros[:3]
        dados[secao] = registros

    return {
        "status": "sucesso",
        "patient_token": patient_token,
        "sessao_triagem_id": sessao_triagem_id,
        "ultima_atualizacao_pep": paciente["ultima_atualizacao_pep"],
        "profundidade": profundidade,
        "dados": dados,
    }


# --------------------------------------------------------------------------- #
# TOOL 2 — verificar_interacoes_medicamentosas  (somente leitura)
# --------------------------------------------------------------------------- #
def verificar_interacoes_medicamentosas(
    medicamentos: List[Dict[str, Any]],
    perfil_paciente: Dict[str, Any],
    sessao_triagem_id: str,
    nivel_detalhe_retorno: str = "detalhado",
) -> Dict[str, Any]:
    """Verifica interações entre fármacos e adequação de dose ao perfil (mock).

    Args:
        medicamentos: lista (mín. 2) de dicts com 'principio_ativo', 'dose_mg',
            'frequencia_diaria' e 'novo_medicamento'.
        perfil_paciente: dict com ao menos 'idade_anos' e 'sexo_biologico';
            campos como 'tfg_ml_min' habilitam alertas de dose renal.
        sessao_triagem_id: identificador da sessão (auditoria).
        nivel_detalhe_retorno: 'resumido' | 'detalhado' | 'completo'.

    Returns:
        Dict com interações identificadas, alertas adicionais e resumo de
        segurança.

    Raises:
        ToolValidationError: em violação de contrato.
    """
    _validar_sessao(sessao_triagem_id)
    _exigir(isinstance(medicamentos, list) and len(medicamentos) >= 2,
            "Verificação de interação exige no mínimo 2 medicamentos.")
    for m in medicamentos:
        _exigir(isinstance(m, dict) and "principio_ativo" in m,
                "Cada medicamento deve conter ao menos 'principio_ativo'.")
        _exigir("dose_mg" in m and "frequencia_diaria" in m,
                "Cada medicamento deve conter 'dose_mg' e 'frequencia_diaria'.")
    _exigir(isinstance(perfil_paciente, dict)
            and "idade_anos" in perfil_paciente
            and "sexo_biologico" in perfil_paciente,
            "perfil_paciente deve conter 'idade_anos' e 'sexo_biologico'.")

    nomes = [_normalizar(m["principio_ativo"]) for m in medicamentos]

    # 1) Interações par a par.
    interacoes: List[Dict[str, Any]] = []
    for i in range(len(nomes)):
        for j in range(i + 1, len(nomes)):
            achado = _consultar_interacao(nomes[i], nomes[j])
            if achado:
                achado = dict(achado)
                achado["par"] = [nomes[i], nomes[j]]
                interacoes.append(achado)

    # 2) Alertas de adequação de dose (ex.: metformina em função renal reduzida).
    alertas = _alertas_dose(medicamentos, perfil_paciente)

    # 3) Resumo de segurança.
    n_grave = sum(1 for x in interacoes if x["severidade"] in {"grave", "contraindicado"})
    if not interacoes and not alertas:
        resumo = "Nenhuma interação relevante conhecida para os fármacos informados."
    elif n_grave:
        resumo = (f"ATENÇÃO — {len(interacoes)} interação(ões) e {len(alertas)} "
                  f"alerta(s) de dose. Há interação grave/contraindicada: revisar "
                  f"com o médico antes de prosseguir.")
    else:
        resumo = (f"{len(interacoes)} interação(ões) e {len(alertas)} alerta(s) "
                  f"de dose identificados (nenhum grave). Avaliação médica recomendada.")

    resposta: Dict[str, Any] = {
        "status": "sucesso",
        "sessao_triagem_id": sessao_triagem_id,
        "interacoes_identificadas": interacoes,
        "alertas_adicionais": alertas,
        "resumo_seguranca": resumo,
    }
    if nivel_detalhe_retorno == "resumido":
        # Modo enxuto: omite mecanismo/referência para reduzir tokens.
        for it in resposta["interacoes_identificadas"]:
            it.pop("mecanismo", None)
            it.pop("referencia", None)
    return resposta


def _consultar_interacao(a: str, b: str) -> Optional[Dict[str, str]]:
    """Busca um par na base; trata a classe AINE de forma genérica."""
    direto = _BASE_INTERACOES.get(frozenset({a, b}))
    if direto:
        return direto
    # Regra de classe: BRA/IECA + qualquer AINE -> grave (efeito de classe).
    bra_ieca = {"losartana", "valsartana", "enalapril", "captopril"}
    if ({a, b} & bra_ieca) and ({a, b} & _AINES):
        return {
            "severidade": "grave",
            "mecanismo": ("Efeito de classe: AINE reduz ação anti-hipertensiva e "
                          "eleva risco de lesão renal."),
            "conduta_recomendada": "Evitar; preferir paracetamol. Monitorar PA e função renal.",
            "referencia": "Compêndio Care Plus DOC-RX-INTERACTIONS-CP-v1.4",
        }
    return None


def _alertas_dose(medicamentos: List[Dict[str, Any]],
                  perfil: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Gera alertas de adequação de dose conforme o perfil fisiológico."""
    alertas: List[Dict[str, Any]] = []
    tfg = perfil.get("tfg_ml_min")
    for m in medicamentos:
        nome = _normalizar(m["principio_ativo"])
        if nome == "metformina" and isinstance(tfg, (int, float)):
            if tfg < 30:
                alertas.append({
                    "tipo": "contraindicacao_renal", "medicamento": "metformina",
                    "mensagem": f"TFGe {tfg} mL/min: metformina CONTRAINDICADA (risco de acidose láctica).",
                    "conduta": "Suspender e revisar com o médico."})
            elif tfg < 45:
                dose_dia = float(m.get("dose_mg", 0)) * int(m.get("frequencia_diaria", 1))
                alertas.append({
                    "tipo": "dose_renal", "medicamento": "metformina",
                    "mensagem": (f"TFGe {tfg} mL/min: dose máxima recomendada 1000mg/dia "
                                 f"(dose atual: {dose_dia:.0f}mg/dia)."),
                    "conduta": "Revisar dose com o médico prescritor."})
            elif tfg < 60:
                dose_dia = float(m.get("dose_mg", 0)) * int(m.get("frequencia_diaria", 1))
                if dose_dia > 1500:
                    alertas.append({
                        "tipo": "dose_renal", "medicamento": "metformina",
                        "mensagem": (f"TFGe {tfg} mL/min: dose diária ({dose_dia:.0f}mg) "
                                     f"acima do recomendado (1500mg/dia)."),
                        "conduta": "Revisar dose com o médico prescritor."})
    return alertas


# --------------------------------------------------------------------------- #
# TOOL 3 — agendar_teleconsulta  (AÇÃO — human-in-the-loop obrigatório)
# --------------------------------------------------------------------------- #
# SLA (em horas) por prioridade — base para o horário simulado.
_SLA_HORAS = {"urgencia": 2, "prioritario": 24, "rotina": 72, "eletivo": 360}

_ESPECIALIDADES_VALIDAS = {
    "clinica_medica", "cardiologia", "pneumologia", "endocrinologia",
    "neurologia", "psiquiatria", "ginecologia", "urologia", "ortopedia",
    "dermatologia", "oftalmologia", "otorrinolaringologia", "gastroenterologia",
    "nefrologia", "infectologia", "reumatologia", "oncologia", "geriatria",
    "pediatria", "nutricao",
}


def agendar_teleconsulta(
    patient_token: str,
    especialidade: str,
    prioridade: str,
    motivo_consulta: str,
    operador_id: str,
    sessao_triagem_id: str,
    modalidade: str = "video",
    confirmacao_operador: bool = False,
    cid_suspeito: Optional[List[str]] = None,
    janela_disponibilidade: Optional[Dict[str, Any]] = None,
    documentos_anexar: Optional[List[str]] = None,
    agora: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Agenda uma teleconsulta (mock) — SOMENTE após confirmação do operador.

    Conforme a matriz de autorização da Sprint 1, esta é a única ferramenta com
    efeito no mundo real. Sem `confirmacao_operador=True`, retorna
    `status="aguardando_confirmacao"` e NÃO efetiva o agendamento
    (human-in-the-loop).

    Args:
        patient_token: UUID v4 pseudonimizado.
        especialidade: especialidade médica (enum).
        prioridade: 'urgencia' | 'prioritario' | 'rotina' | 'eletivo'.
        motivo_consulta: queixa principal (≤ 500 caracteres).
        operador_id: identificador do operador (auditoria).
        sessao_triagem_id: identificador da sessão.
        modalidade: 'video' | 'audio' | 'chat_assincrono'.
        confirmacao_operador: trava de human-in-the-loop. Padrão False.
        cid_suspeito, janela_disponibilidade, documentos_anexar: opcionais.
        agora: relógio injetável (para testes determinísticos).

    Returns:
        Dict com o agendamento efetivado, ou solicitação de confirmação.

    Raises:
        ToolValidationError: em violação de contrato.
    """
    _validar_patient_token(patient_token)
    _validar_sessao(sessao_triagem_id)
    _validar_operador(operador_id)
    _exigir(especialidade in _ESPECIALIDADES_VALIDAS,
            f"especialidade inválida: '{especialidade}'.")
    _exigir(prioridade in _SLA_HORAS, f"prioridade inválida: '{prioridade}'.")
    _exigir(isinstance(motivo_consulta, str) and 0 < len(motivo_consulta) <= 500,
            "motivo_consulta é obrigatório e deve ter no máximo 500 caracteres.")
    _exigir(modalidade in {"video", "audio", "chat_assincrono"},
            f"modalidade inválida: '{modalidade}'.")

    if patient_token not in _MOCK_PEP:
        return {"status": "erro", "codigo": "paciente_nao_encontrado",
                "mensagem": "Nenhum beneficiário associado ao token informado."}

    # TRAVA DE HUMAN-IN-THE-LOOP: não agenda sem confirmação explícita.
    if not confirmacao_operador:
        return {
            "status": "aguardando_confirmacao",
            "mensagem": ("Agendamento NÃO efetivado. Esta ação exige confirmação "
                         "explícita do operador de triagem antes de prosseguir."),
            "proposta": {
                "especialidade": especialidade, "prioridade": prioridade,
                "modalidade": modalidade, "motivo_consulta": motivo_consulta,
            },
            "como_confirmar": "Repetir a chamada com confirmacao_operador=true.",
        }

    # Agendamento efetivado (simulado, determinístico).
    base = agora or datetime.now(_TZ_BR)
    horario = base + timedelta(hours=_SLA_HORAS[prioridade])
    if prioridade == "urgencia":
        janela_disponibilidade = None  # urgência ignora preferências de horário

    sufixo = hashlib.sha1(
        f"{patient_token}{sessao_triagem_id}{especialidade}".encode()
    ).hexdigest()[:5].upper()
    numero = f"AGD-{base:%Y-%m%d}-{sufixo}"

    return {
        "status": "agendado",
        "numero_agendamento": numero,
        "medico_alocado": _alocar_medico(especialidade),
        "horario_consulta": horario.isoformat(),
        "prioridade": prioridade,
        "modalidade": modalidade,
        "tempo_espera_estimado_minutos": _SLA_HORAS[prioridade] * 60,
        "link_sala_virtual": f"https://telemed.careplus.com.br/sala/{numero}",
        "documentos_compartilhados": documentos_anexar or [],
        "operador_id": operador_id,
        "sessao_triagem_id": sessao_triagem_id,
    }


def _alocar_medico(especialidade: str) -> Dict[str, str]:
    """Aloca um médico fictício compatível com a especialidade (mock)."""
    catalogo = {
        "cardiologia": {"nome": "Dr. Roberto Lima", "crm": "CRM-SP 87654",
                        "especialidade": "Cardiologia"},
        "clinica_medica": {"nome": "Dra. Helena Costa", "crm": "CRM-SP 33221",
                           "especialidade": "Clínica Médica"},
        "psiquiatria": {"nome": "Dr. André Moraes", "crm": "CRM-SP 55110",
                        "especialidade": "Psiquiatria"},
    }
    return catalogo.get(especialidade,
                        {"nome": "Dra. Plantonista Care Plus", "crm": "CRM-SP 10000",
                         "especialidade": especialidade.replace("_", " ").title()})


# --------------------------------------------------------------------------- #
# TOOL 4 — recuperar_dados_wearable  (somente leitura, bonus)
# --------------------------------------------------------------------------- #
_METRICAS_VALIDAS = {
    "frequencia_cardiaca_repouso", "frequencia_cardiaca_maxima",
    "variabilidade_fc_hrv", "spo2_saturacao_oxigenio", "frequencia_respiratoria",
    "temperatura_corporal_pele", "temperatura_corporal_nucleo",
    "pressao_arterial_sistolica", "pressao_arterial_diastolica",
    "passos_diarios", "calorias_ativas", "minutos_atividade_moderada",
    "minutos_atividade_intensa", "distancia_km", "sono_total_horas",
    "sono_rem_horas", "sono_profundo_horas", "latencia_sono_minutos",
    "eficiencia_sono_percentual", "score_prontidao_oura", "nivel_estresse",
    "ecg_ritmo_detectado", "irregularidade_ritmo_afib",
    "glicose_intersticial_mgdl", "peso_kg", "imc",
}


def recuperar_dados_wearable(
    patient_token: str,
    metricas_solicitadas: List[str],
    periodo: Dict[str, Any],
    sessao_triagem_id: str,
    plataformas: Optional[List[str]] = None,
    granularidade: str = "por_hora",
    incluir_alertas_dispositivo: bool = True,
    contexto_clinico: Optional[Dict[str, Any]] = None,
    agora: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Recupera métricas biométricas de wearables (mock).

    Verifica o consentimento ativo antes de retornar qualquer dado (LGPD).
    Dados de wearable são indicativos e complementares — nunca substitutos de
    avaliação clínica formal.

    Args:
        patient_token: UUID v4 pseudonimizado.
        metricas_solicitadas: lista não vazia de métricas válidas (minimização).
        periodo: dict com 'ultimas_horas' OU 'data_inicio'.
        sessao_triagem_id: identificador da sessão.
        plataformas, granularidade, incluir_alertas_dispositivo, contexto_clinico:
            opcionais.
        agora: relógio injetável (testes determinísticos).

    Returns:
        Dict com as métricas solicitadas, alertas de dispositivo e resumo.

    Raises:
        ToolValidationError: em violação de contrato.
    """
    _validar_patient_token(patient_token)
    _validar_sessao(sessao_triagem_id)
    _exigir(isinstance(metricas_solicitadas, list) and len(metricas_solicitadas) >= 1,
            "metricas_solicitadas deve conter ao menos 1 métrica.")
    invalidas = set(metricas_solicitadas) - _METRICAS_VALIDAS
    _exigir(not invalidas, f"Métricas inválidas: {sorted(invalidas)}.")
    _exigir(isinstance(periodo, dict)
            and ("ultimas_horas" in periodo or "data_inicio" in periodo),
            "periodo deve conter 'ultimas_horas' ou 'data_inicio'.")

    if patient_token not in _MOCK_PEP:
        return {"status": "erro", "codigo": "paciente_nao_encontrado",
                "mensagem": "Nenhum beneficiário associado ao token informado."}

    # Verificação de consentimento (LGPD) — bloqueia se inativo.
    if not _CONSENTIMENTO_WEARABLE.get(patient_token, False):
        return {"status": "erro", "codigo": "consentimento_inativo",
                "mensagem": ("Consentimento para compartilhamento de dados de "
                             "wearable não está ativo para este beneficiário.")}

    base = agora or datetime.now(_TZ_BR)
    horas = int(periodo.get("ultimas_horas", 72))
    inicio = base - timedelta(hours=horas)

    metricas = {m: _gerar_metrica_mock(m) for m in metricas_solicitadas}

    alertas: List[Dict[str, Any]] = []
    if incluir_alertas_dispositivo and "irregularidade_ritmo_afib" in metricas_solicitadas:
        alertas.append({
            "tipo": "irregularidade_ritmo_cardiaco",
            "descricao": "Dispositivo detectou possível fibrilação atrial.",
            "severidade": "alta", "acao_sugerida_pelo_dispositivo": "Consultar médico"})

    return {
        "status": "sucesso",
        "sessao_triagem_id": sessao_triagem_id,
        "plataformas_consultadas": plataformas or ["apple_health"],
        "periodo_retornado": {"inicio": inicio.isoformat(), "fim": base.isoformat()},
        "granularidade": granularidade,
        "metricas": metricas,
        "alertas_dispositivo": alertas,
        "aviso": ("Dados de wearable são indicativos e complementares; não "
                  "substituem avaliação clínica formal."),
    }


def _gerar_metrica_mock(metrica: str) -> Dict[str, Any]:
    """Retorna um valor plausível e fixo para cada métrica (mock determinístico)."""
    catalogo: Dict[str, Dict[str, Any]] = {
        "frequencia_cardiaca_repouso": {"media_bpm": 72, "minimo_bpm": 58,
                                        "maximo_bpm": 110, "tendencia": "estavel"},
        "variabilidade_fc_hrv": {"media_ms": 48, "baseline_pessoal_ms": 52,
                                 "interpretacao": "dentro da faixa habitual"},
        "spo2_saturacao_oxigenio": {"media_percentual": 97.5,
                                    "minimo_percentual": 95.0, "episodios_abaixo_94": 0},
        "sono_total_horas": {"media_horas": 7.1, "tendencia": "estavel"},
        "ecg_ritmo_detectado": {"registros_realizados": 2,
                                "ritmos_detectados": ["sinusal", "sinusal"]},
        "irregularidade_ritmo_afib": {"deteccoes": 1, "ultima": "registro recente"},
        "passos_diarios": {"media": 6800, "tendencia": "estavel"},
        "temperatura_corporal_pele": {"desvio_vs_baseline_graus_c": 0.1,
                                      "tendencia": "estavel"},
    }
    return catalogo.get(metrica, {"valor": "indisponivel_no_mock", "metrica": metrica})


# --------------------------------------------------------------------------- #
# Utilitários
# --------------------------------------------------------------------------- #
def _normalizar(nome: str) -> str:
    """Normaliza nome de fármaco: minúsculas, sem acentos comuns, sem espaços extras."""
    nome = nome.strip().lower()
    tabela = str.maketrans("áàâãéêíóôõúç", "aaaaeeiooouc")
    return nome.translate(tabela)


# --------------------------------------------------------------------------- #
# Schemas no padrão Anthropic/OpenAI (para o parâmetro `tools` da API)
# --------------------------------------------------------------------------- #
# Versões compactadas dos schemas da Sprint 1; nomes, enums e `required` são
# fiéis ao contrato documentado (a API valida por esses campos).
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "consultar_historico_paciente",
        "description": ("Recupera o histórico clínico do beneficiário a partir do "
                        "PEP usando token pseudonimizado (LGPD). Somente leitura."),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_token": {"type": "string"},
                "secoes_solicitadas": {"type": "array",
                                       "items": {"type": "string",
                                                 "enum": sorted(_SECOES_VALIDAS)},
                                       "minItems": 1, "maxItems": 12},
                "profundidade": {"type": "string", "enum": ["resumo", "completo"],
                                 "default": "resumo"},
                "periodo_consulta": {"type": "object"},
                "sessao_triagem_id": {"type": "string"},
            },
            "required": ["patient_token", "secoes_solicitadas", "sessao_triagem_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "verificar_interacoes_medicamentosas",
        "description": ("Verifica interações entre fármacos e adequação de dose ao "
                        "perfil do paciente. Somente leitura. Mínimo 2 medicamentos."),
        "input_schema": {
            "type": "object",
            "properties": {
                "medicamentos": {"type": "array", "minItems": 2, "maxItems": 20,
                                 "items": {"type": "object"}},
                "perfil_paciente": {"type": "object"},
                "nivel_detalhe_retorno": {"type": "string",
                                          "enum": ["resumido", "detalhado", "completo"],
                                          "default": "detalhado"},
                "sessao_triagem_id": {"type": "string"},
            },
            "required": ["medicamentos", "perfil_paciente", "sessao_triagem_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "agendar_teleconsulta",
        "description": ("Agenda teleconsulta médica. AÇÃO com efeito real: só executa "
                        "após confirmação explícita do operador (human-in-the-loop)."),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_token": {"type": "string"},
                "especialidade": {"type": "string", "enum": sorted(_ESPECIALIDADES_VALIDAS)},
                "prioridade": {"type": "string",
                               "enum": ["urgencia", "prioritario", "rotina", "eletivo"]},
                "motivo_consulta": {"type": "string", "maxLength": 500},
                "modalidade": {"type": "string",
                               "enum": ["video", "audio", "chat_assincrono"],
                               "default": "video"},
                "confirmacao_operador": {"type": "boolean", "default": False},
                "cid_suspeito": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                "janela_disponibilidade": {"type": "object"},
                "documentos_anexar": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                "operador_id": {"type": "string"},
                "sessao_triagem_id": {"type": "string"},
            },
            "required": ["patient_token", "especialidade", "prioridade",
                         "motivo_consulta", "modalidade", "operador_id",
                         "sessao_triagem_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "recuperar_dados_wearable",
        "description": ("Recupera métricas biométricas de wearables (consentimento "
                        "LGPD obrigatório). Indicativo, não substitui avaliação clínica."),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_token": {"type": "string"},
                "metricas_solicitadas": {"type": "array", "minItems": 1,
                                         "items": {"type": "string",
                                                   "enum": sorted(_METRICAS_VALIDAS)}},
                "periodo": {"type": "object"},
                "plataformas": {"type": "array", "items": {"type": "string"}},
                "granularidade": {"type": "string",
                                  "enum": ["por_minuto", "por_hora", "diario"],
                                  "default": "por_hora"},
                "incluir_alertas_dispositivo": {"type": "boolean", "default": True},
                "contexto_clinico": {"type": "object"},
                "sessao_triagem_id": {"type": "string"},
            },
            "required": ["patient_token", "metricas_solicitadas", "periodo",
                         "sessao_triagem_id"],
            "additionalProperties": False,
        },
    },
]


# --------------------------------------------------------------------------- #
# Dispatcher — ponte entre o nome da tool (vindo do LLM) e a função Python
# --------------------------------------------------------------------------- #
TOOL_DISPATCHER: Dict[str, Callable[..., Dict[str, Any]]] = {
    "consultar_historico_paciente": consultar_historico_paciente,
    "verificar_interacoes_medicamentosas": verificar_interacoes_medicamentosas,
    "agendar_teleconsulta": agendar_teleconsulta,
    "recuperar_dados_wearable": recuperar_dados_wearable,
}


def executar_ferramenta(nome: str, argumentos: Dict[str, Any]) -> Dict[str, Any]:
    """Executa uma ferramenta pelo nome, devolvendo sempre um payload serializável.

    Converte violações de contrato (ToolValidationError) em um payload de erro
    estruturado, pois é isso que o LLM precisa receber como `tool_result` — uma
    exceção não tratada quebraria o laço de orquestração do agente.

    Args:
        nome: nome da ferramenta (conforme TOOL_SCHEMAS).
        argumentos: dict de argumentos vindos do `tool_use` do LLM.

    Returns:
        Dict com o resultado da ferramenta, ou um payload de erro estruturado.
    """
    funcao = TOOL_DISPATCHER.get(nome)
    if funcao is None:
        return {"status": "erro", "codigo": "ferramenta_desconhecida",
                "mensagem": f"Ferramenta '{nome}' não está registrada."}
    try:
        return funcao(**argumentos)
    except ToolValidationError as exc:
        return {"status": "erro", "codigo": "validacao", "mensagem": str(exc)}
    except TypeError as exc:
        # Argumentos faltando/excedentes em relação à assinatura da função.
        return {"status": "erro", "codigo": "argumentos_invalidos", "mensagem": str(exc)}
