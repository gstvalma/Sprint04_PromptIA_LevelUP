# BluaDiagnostics — Especificação Técnica Detalhada
**Care Plus | Fluxo Arquitetural, Base RAG e Function Calling Tools**
*Versão 1.1 — Continuação do Documento de Arquitetura de IA*

---

## Sumário

1. [Fluxo Arquitetural Passo a Passo](#1-fluxo-arquitetural-passo-a-passo)
2. [Base de Conhecimento RAG — Documentos Iniciais](#2-base-de-conhecimento-rag--documentos-iniciais)
3. [Function Calling Tools — JSON Schemas](#3-function-calling-tools--json-schemas)
4. [Bonus — Wearable Data Retrieval Tool](#4-bonus--ferramenta-de-recuperação-de-dados-wearables)

---

## 1. Fluxo Arquitetural Passo a Passo

O fluxo completo do BluaDiagnostics percorre **7 etapas sequenciais**, com checkpoints de segurança em cada transição crítica. Abaixo, o diagrama de referência seguido da descrição detalhada de cada etapa.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    FLUXO ARQUITETURAL — BluaDiagnostics                     │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌──────────────────┐
  │ ETAPA 1          │
  │  User Input      │  Operador insere dados do beneficiário via interface web
  │  (Operador)      │
  └────────┬─────────┘
           │  [Pseudonimização de PHI antes de qualquer processamento]
           ▼
  ┌──────────────────┐
  │ ETAPA 2          │
  │  Intent Router   │  Classifica a intenção clínica do input
  │                  │  (triagem / prescrição / agendamento / consulta)
  └────────┬─────────┘
           │  [Roteamento para pipeline especializado]
           ▼
  ┌──────────────────┐
  │ ETAPA 3          │
  │  LLM Engine      │  Claude Sonnet 4 via AWS Bedrock processa o prompt
  │  (Claude S4)     │  com system prompt clínico + histórico da sessão
  └────────┬─────────┘
           │  [Decisão: necessita contexto externo?]
           ▼
  ┌──────────────────┐
  │ ETAPA 4          │
  │  RAG Pipeline    │  Recupera chunks relevantes das bases clínicas
  │                  │  (Manchester, ANVISA, protocolos Care Plus, etc.)
  └────────┬─────────┘
           │  [Chunks injetados no contexto do LLM — reranking por relevância]
           ▼
  ┌──────────────────┐
  │ ETAPA 5          │
  │  Function        │  LLM decide chamar ferramentas externas estruturadas
  │  Calling         │  (PEP, ANVISA, agenda, wearables)
  └────────┬─────────┘
           │  [Resposta da função retorna ao contexto do LLM]
           ▼
  ┌──────────────────┐
  │ ETAPA 6          │
  │  Guardrails      │  Validação multicamada da resposta gerada
  │  Engine          │  antes de qualquer exibição ao operador
  └────────┬─────────┘
           │  [Aprovado / Bloqueado / Reescrito]
           ▼
  ┌──────────────────┐
  │ ETAPA 7          │
  │  Output ao       │  Sugestão exibida ao operador com raciocínio,
  │  Operador        │  fontes, score de confiança e ação requerida
  └──────────────────┘
```

---

### Etapa 1 — User Input (Entrada do Operador)

**O que acontece:**
O Operador de Triagem insere dados do beneficiário na interface web do BluaDiagnostics. O input pode ser multimodal: texto livre (queixa principal em linguagem natural), formulário estruturado de anamnese, upload de resultado de exame (PDF/imagem) ou leitura automática de dados de wearables integrados.

**Processamento imediato na entrada:**
- **Sanitização de input:** Remoção de caracteres maliciosos e tentativas de prompt injection antes de qualquer processamento (ex.: filtro contra `"Ignore as instruções anteriores e..."`).
- **Pseudonimização de PHI:** O CPF do beneficiário é substituído por um token UUID criptografado; o nome é mascarado. Nenhum identificador direto trafega para o LLM. A chave de reversão fica em HSM (Hardware Security Module) isolado.
- **Enriquecimento de contexto:** O sistema recupera automaticamente do PEP (Prontuário Eletrônico do Paciente) o histórico resumido do beneficiário: alergias conhecidas, medicamentos em uso, comorbidades ativas, última consulta e exames recentes.
- **Registro de auditoria:** Timestamp, ID do operador e hash do input são gravados em log imutável (WORM) antes de qualquer etapa subsequente.

**Output da etapa:** Payload JSON estruturado contendo queixa principal, contexto clínico pseudonimizado e metadados da sessão.

---

### Etapa 2 — Intent Router (Roteamento de Intenção)

**O que acontece:**
Um modelo de classificação leve (fine-tuned BERT ou similar, hospedado on-premise) analisa o payload da etapa anterior e classifica a intenção clínica primária. O roteador determina qual pipeline especializado deve processar o caso.

**Taxonomia de intenções suportadas:**

| Código de Intenção | Descrição | Pipeline Acionado |
|---|---|---|
| `TRIAGE_URGENT` | Sintomas de alta gravidade (dor torácica, dispneia severa, AVC) | Pipeline de emergência + alerta médico imediato |
| `TRIAGE_STANDARD` | Triagem de rotina com classificação de risco Manchester | Pipeline principal de triagem |
| `RX_SUPPORT` | Suporte à prescrição (verificação de dose, interações) | Pipeline de prescrição com verificação ANVISA |
| `SCHEDULING` | Agendamento de teleconsulta ou exame | Pipeline de agendamento |
| `FOLLOWUP` | Acompanhamento de caso em andamento | Pipeline de continuidade com histórico expandido |
| `DATA_QUERY` | Consulta a dados do paciente (exames, laudos) | Pipeline de recuperação de dados |

**Lógica de segurança do roteador:**
- Qualquer input contendo termos críticos de emergência (ex.: "parada cardíaca", "não respira", "sangramento intenso") é automaticamente escalado para `TRIAGE_URGENT`, independentemente da classificação geral do modelo.
- Casos ambíguos com score de confiança do roteador inferior a 80% são tratados como `TRIAGE_STANDARD` por conservadorismo clínico.

**Output da etapa:** Código de intenção + score de confiança + parâmetros de roteamento para o pipeline especializado.

---

### Etapa 3 — LLM Engine (Claude Sonnet 4 via AWS Bedrock)

**O que acontece:**
O coração do BluaDiagnostics. O Claude Sonnet 4, acessado via endpoint privado do Amazon Bedrock dentro da VPC da Care Plus, processa o prompt clínico completo. Nenhum dado trafega pela internet pública.

**Composição do prompt enviado ao LLM:**

```
[SYSTEM PROMPT CLÍNICO]
  → Identidade e escopo: "Você é o BluaDiagnostics, assistente clínico de suporte..."
  → Restrições: "Nunca diagnostique. Nunca presceva sem confirmação humana..."
  → Formato de saída: JSON estruturado com campos obrigatórios
  → Idioma: Português Brasileiro formal

[CONTEXTO DO PACIENTE — pseudonimizado]
  → Histórico resumido (recuperado na Etapa 1)
  → Alergias, medicamentos em uso, comorbidades

[CHUNKS RAG — injetados na Etapa 4]
  → Protocolos clínicos relevantes recuperados da base vetorial

[HISTÓRICO DA SESSÃO — multi-turn]
  → Até 50 turnos anteriores da mesma sessão de triagem

[INPUT DO OPERADOR]
  → Queixa principal + dados estruturados da anamnese
```

**Parâmetros de geração:**
- `temperature: 0.1` — Baixíssima criatividade; respostas clínicas exigem determinismo
- `max_tokens: 1024` — Suficiente para sugestões estruturadas sem outputs excessivos
- `top_p: 0.9` — Equilíbrio entre consistência e cobertura vocabular clínica
- Streaming ativado para exibição progressiva na interface do operador

**Output da etapa:** Resposta intermediária do LLM (pode incluir solicitações de function calls) + decisão sobre necessidade de RAG adicional.

---

### Etapa 4 — RAG Pipeline (Recuperação de Conhecimento Clínico)

**O que acontece:**
O sistema de Retrieval-Augmented Generation recupera, em tempo real, os chunks de conhecimento clínico mais relevantes para o caso em triagem. Isso garante que o LLM responda baseado em fontes autorizadas e atualizadas, não apenas em seu conhecimento paramétrico de treinamento.

**Arquitetura do pipeline RAG:**

```
Query do LLM
     │
     ▼
┌─────────────────────────────────────┐
│     Embedding Model                 │
│  (text-embedding-3-large / Cohere)  │
│  Converte query em vetor semântico  │
└──────────────────┬──────────────────┘
                   │
                   ▼
┌─────────────────────────────────────┐
│      Vector Database (Weaviate)     │
│  Busca por similaridade cossenoidal │
│  Top-K = 10 chunks candidatos       │
└──────────────────┬──────────────────┘
                   │
                   ▼
┌─────────────────────────────────────┐
│      Reranker (Cross-Encoder)       │
│  Reordena por relevância contextual │
│  Seleciona Top-3 chunks finais      │
└──────────────────┬──────────────────┘
                   │
                   ▼
┌─────────────────────────────────────┐
│   Injeção no Contexto do LLM        │
│   com metadados de fonte e versão   │
└─────────────────────────────────────┘
```

**Metadados obrigatórios por chunk:**
Cada chunk recuperado carrega: `fonte`, `versão_documento`, `data_atualização`, `nível_evidência` (ex.: Grau A, B, C) e `responsável_técnico`. Esses metadados são exibidos ao operador junto à sugestão final, garantindo rastreabilidade e transparência clínica.

**Atualização da base vetorial:**
A base RAG é re-indexada automaticamente sempre que um documento-fonte é atualizado, com versionamento completo. Chunks de versões antigas são arquivados, não deletados, para fins de auditoria.

**Output da etapa:** Top-3 chunks clinicamente relevantes injetados no contexto expandido do LLM para geração da resposta final.

---

### Etapa 5 — Function Calling (Integração com Sistemas Externos)

**O que acontece:**
Após processar o contexto completo, o Claude Sonnet 4 pode decidir autonomamente — ou ser instruído explicitamente — a chamar ferramentas externas para obter dados que não estão em seu contexto. O LLM gera um payload JSON estruturado especificando qual ferramenta chamar e com quais parâmetros.

**Ferramentas disponíveis (detalhadas na Seção 3):**

| Tool Name | Propósito | Sistema Integrado |
|---|---|---|
| `consultar_historico_paciente` | Busca dados clínicos do beneficiário | PEP interno Care Plus |
| `verificar_interacoes_medicamentosas` | Checa interações entre medicamentos | Base ANVISA + Micromedex |
| `agendar_teleconsulta` | Reserva slot de teleconsulta | Sistema de agendamento Care Plus |
| `recuperar_dados_wearable` | Obtém métricas biométricas recentes | Apple Health / Oura API |

**Ciclo de function calling:**

```
LLM gera: { "tool": "verificar_interacoes_medicamentosas", "params": {...} }
     │
     ▼
Orchestration Layer valida o schema e autoriza a chamada
     │
     ▼
API externa é chamada com timeout de 3s (fallback para cache se timeout)
     │
     ▼
Resultado retorna ao LLM como nova mensagem com role "tool"
     │
     ▼
LLM incorpora resultado na resposta clínica final
```

**Controles de segurança no function calling:**
- Whitelist de funções permitidas por tipo de intenção (ex.: `TRIAGE_STANDARD` não pode chamar `agendar_teleconsulta` diretamente sem confirmação do operador)
- Rate limiting por sessão para evitar loops de chamadas excessivas
- Timeout máximo de 3 segundos por chamada externa; resultado de cache usado como fallback

**Output da etapa:** Dados estruturados das APIs externas integrados ao contexto do LLM para composição da resposta final.

---

### Etapa 6 — Guardrails Engine (Validação Multicamada)

**O que acontece:**
Antes de qualquer saída ser exibida ao operador, a resposta gerada pelo LLM passa por um pipeline de validação multicamada independente. O Guardrails Engine é completamente separado do LLM — ele não usa o mesmo modelo para se autovalidar.

**Camadas de validação:**

```
Resposta bruta do LLM
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  CAMADA 1 — Validação de Schema                           │
│  A resposta está no formato JSON esperado?                │
│  Todos os campos obrigatórios presentes?                  │
│  → Falha: resposta rejeitada e regenerada (max 2x)        │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  CAMADA 2 — Validação Clínica por Regras                  │
│  Dosagens estão dentro dos limites seguros do RxNorm?     │
│  Medicamentos são contraindicados para as alergias?       │
│  CID sugerido é compatível com a queixa principal?        │
│  → Falha: sugestão bloqueada + alerta vermelho ao operador│
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  CAMADA 3 — Classificação de Toxicidade                   │
│  NLP model detecta linguagem prejudicial, discriminatória │
│  ou que possa causar dano psicológico ao beneficiário     │
│  → Falha: conteúdo filtrado e reescrito automaticamente   │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  CAMADA 4 — Confidence Scoring                            │
│  Score ≥ 85%: exibição normal (verde)                     │
│  Score 70–84%: exibição com aviso de baixa confiança      │
│  Score < 70%: bloqueio + escalada obrigatória ao médico   │
└───────────────────────────────────────────────────────────┘
        │
        ▼
Resposta validada e enriquecida com metadados
```

**Output da etapa:** Resposta clínica aprovada, enriquecida com score de confiança, fontes citadas e classificação de ação requerida (`CONFIRMAR` / `REVISAR` / `ESCALAR`).

---

### Etapa 7 — Output ao Operador

**O que acontece:**
A resposta validada é renderizada na interface web do operador em formato estruturado, projetado para facilitar a tomada de decisão clínica rápida sem induzir automação passiva.

**Estrutura do output na interface:**

```
┌─────────────────────────────────────────────────────────────┐
│  🔵 BluaDiagnostics — Sugestão Clínica                      │
│  Paciente: TOKEN-7F4A | Sessão: 2026-05-17T14:32:00Z        │
├─────────────────────────────────────────────────────────────┤
│  📋 CLASSIFICAÇÃO DE RISCO SUGERIDA                         │
│  Manchester: LARANJA (Muito Urgente) — Atendimento em 10min │
│  Confiança: 91% ████████████░░ [CONFIRMAR]                  │
├─────────────────────────────────────────────────────────────┤
│  🧠 RACIOCÍNIO CLÍNICO (Chain-of-Thought)                   │
│  "Paciente masculino, 58 anos, com queixa de dor torácica   │
│  opressiva irradiando para braço esquerdo há 20 minutos,    │
│  diaforese associada. Histórico de HAS e DM2. Perfil        │
│  compatível com SCA — síndrome coronariana aguda..."        │
├─────────────────────────────────────────────────────────────┤
│  📚 FONTES UTILIZADAS                                       │
│  • Protocolo Manchester Care Plus v3.2 (Jan/2026) — Grau A  │
│  • Diretriz ACC/AHA de SCA — Grau de Evidência I            │
│  • Histórico do paciente — última consulta: 12/03/2026      │
├─────────────────────────────────────────────────────────────┤
│  ⚡ AÇÕES SUGERIDAS                                         │
│  1. ECG de 12 derivações imediato                           │
│  2. Dosagem de troponina I de alta sensibilidade            │
│  3. Acesso venoso periférico + AAS 200mg VO                 │
│  4. Contato com cardiologista de plantão                    │
├─────────────────────────────────────────────────────────────┤
│  [✅ CONFIRMAR TRIAGEM]  [✏️ EDITAR]  [🔺 ESCALAR MÉDICO]  │
└─────────────────────────────────────────────────────────────┘
```

**Controles de UX anti-automação passiva:**
- Não existe botão "Aceitar Tudo" — cada ação deve ser confirmada individualmente
- Para classificações `LARANJA` ou `VERMELHO`, o sistema exige que o operador leia o raciocínio clínico completo antes de habilitar o botão de confirmação (rastreado por scroll position)
- Todas as ações do operador (confirmação, edição, rejeição) são gravadas no log de auditoria com timestamp e ID do profissional

---

## 2. Base de Conhecimento RAG — Documentos Iniciais

A base RAG inicial do BluaDiagnostics é composta por **5 documentos-âncora**, criteriosamente selecionados para cobrir os principais domínios clínicos e operacionais da triagem remota da Care Plus. Cada documento passa por um processo de chunking semântico (chunks de 512 tokens com overlap de 50 tokens) antes da indexação no Weaviate.

---

### Documento 1 — Protocolo de Triagem Manchester Adaptado Care Plus

**ID do Documento:** `DOC-TRIAGE-MANCHESTER-CP-v3.2`
**Fonte original:** Manchester Triage Group + adaptações internas Care Plus
**Última atualização:** Janeiro de 2026
**Nível de evidência:** Grau A (protocolo validado internacionalmente)
**Responsável técnico:** Diretoria Médica Care Plus — Dr. Paulo Salave

**Descrição:**
Versão adaptada do Sistema de Triagem de Manchester (STM) para o contexto de atendimento remoto e teleconsulta da Care Plus. O documento original do STM foi reprocessado para incluir modificações específicas ao ambiente digital: critérios de triagem adaptados para anamnese por vídeo (sem possibilidade de ausculta presencial), fluxogramas de 52 queixas principais categorizadas por cores de prioridade (Vermelho, Laranja, Amarelo, Verde, Azul), discriminadores clínicos para cada categoria e protocolos de escalada quando sinais vitais não podem ser verificados presencialmente. Inclui também a árvore de decisão para encaminhamento ao SAMU vs. UPA vs. teleconsulta vs. orientação domiciliar, calibrada para a rede credenciada Care Plus nas principais capitais brasileiras.

**Conteúdo-chave para chunking:** Fluxogramas por queixa principal, discriminadores clínicos, critérios de escalada, tabela de tempo máximo de espera por cor de prioridade, contraindicações para triagem remota (casos que exigem presença física obrigatória).

---

### Documento 2 — Política de Telemedicina e Prescrição Remota Care Plus

**ID do Documento:** `DOC-POLICY-TELEMED-CP-v2.1`
**Fonte original:** Compliance jurídico Care Plus + Resolução CFM nº 2.314/2022
**Última atualização:** Março de 2026
**Nível de evidência:** Normativo (compliance obrigatório)
**Responsável técnico:** Departamento Jurídico e de Compliance Care Plus

**Descrição:**
Documento interno de política operacional que define o escopo, os limites e os procedimentos obrigatórios para a prática de telemedicina dentro do ecossistema Care Plus, em conformidade com a Resolução CFM nº 2.314/2022. Inclui: condições clínicas permitidas para teleconsulta (e as explicitamente proibidas, como primeiro atendimento de urgência sem avaliação presencial prévia), requisitos legais para prescrição eletrônica remota (assinatura digital ICP-Brasil, receituário especial para controlados), obrigações de registro em prontuário eletrônico, fluxo de consentimento informado digital do beneficiário, e limitações do papel do operador de triagem versus o papel exclusivo do médico na prescrição. Define também os SLAs de atendimento por tipo de plano Care Plus (Premium, Gold, Standard).

**Conteúdo-chave para chunking:** Lista de condições permitidas/proibidas para teleatendimento, requisitos de prescrição eletrônica, fluxo de consentimento, SLAs por plano, responsabilidades do operador vs. médico.

---

### Documento 3 — Compêndio de Interações Medicamentosas — Top 200 Care Plus

**ID do Documento:** `DOC-RX-INTERACTIONS-CP-v1.4`
**Fonte original:** Micromedex + ANVISA + revisão farmacológica interna
**Última atualização:** Fevereiro de 2026
**Nível de evidência:** Grau A/B (evidência clínica robusta)
**Responsável técnico:** Farmácia Clínica Care Plus — Dra. Renata Ferreira, CFF-SP

**Descrição:**
Base de conhecimento farmacológico estruturada contendo as 200 interações medicamentosas mais relevantes para a população de beneficiários Care Plus, identificadas a partir da análise epidemiológica do próprio banco de dados de prescrições da operadora. Para cada interação, o documento especifica: nível de gravidade (contraindicado / grave / moderado / leve), mecanismo fisiopatológico da interação, manifestações clínicas esperadas, conduta recomendada (substituição, ajuste de dose, monitoramento, suspensão imediata), e referências bibliográficas primárias. Inclui seção especial para populações de risco: idosos acima de 75 anos, gestantes, pacientes renais crônicos (TFG < 30) e hepatopatas. O documento é atualizado mensalmente conforme novos alertas da ANVISA.

**Conteúdo-chave para chunking:** Tabelas de interação por par de medicamentos, condutas por nível de gravidade, ajustes de dose para insuficiência renal/hepática, alertas especiais para populações vulneráveis.

---

### Documento 4 — Guia de Doenças Crônicas Prevalentes na Base Care Plus

**ID do Documento:** `DOC-CHRONIC-DISEASE-CP-v2.0`
**Fonte original:** Diretrizes SBC, SBD, SBR + dados epidemiológicos internos Care Plus
**Última atualização:** Dezembro de 2025
**Nível de evidência:** Grau A (diretrizes de sociedades médicas nacionais)
**Responsável técnico:** Diretoria de Gestão de Crônicos Care Plus

**Descrição:**
Documento clínico estruturado cobrindo as 15 doenças crônicas de maior prevalência e custo assistencial na base de beneficiários Care Plus, identificadas via análise de sinistralidade: Hipertensão Arterial Sistêmica, Diabetes Mellitus tipo 2, Insuficiência Cardíaca, Doença Arterial Coronariana, DPOC, Asma, Hipotireoidismo, Obesidade, Síndrome Metabólica, Depressão, Ansiedade Generalizada, Doença Renal Crônica, Dislipidemia, Osteoporose e Artrite Reumatoide. Para cada condição, o documento apresenta: critérios diagnósticos atualizados, metas terapêuticas por perfil de risco, red flags para urgência/emergência, medicamentos de primeira linha alinhados ao rol ANS, e indicadores de controle para monitoramento proativo. Seção especial sobre multimorbidade e o impacto da polifarmácia no idoso Care Plus.

**Conteúdo-chave para chunking:** Critérios diagnósticos por doença, red flags de urgência, metas terapêuticas, medicamentos de primeira escolha, indicadores de controle, manejo da multimorbidade.

---

### Documento 5 — Protocolo de Saúde Mental e Avaliação de Risco em Telemedicina

**ID do Documento:** `DOC-MENTAL-HEALTH-CP-v1.0`
**Fonte original:** CFP + CFM + OMS + protocolos internos de psiquiatria Care Plus
**Última atualização:** Abril de 2026
**Nível de evidência:** Grau B (consenso de especialistas + evidência clínica moderada)
**Responsável técnico:** Coordenação de Saúde Mental Care Plus — Dr. André Moraes, CRM-SP

**Descrição:**
Protocolo clínico específico para triagem e manejo inicial de condições de saúde mental no contexto de teleconsulta e atendimento remoto. Inclui: escalas de rastreamento validadas para uso remoto (PHQ-9 para depressão, GAD-7 para ansiedade, AUDIT para uso de álcool, DAST-10 para outras substâncias), protocolo de avaliação de risco suicida para ambiente de telemedicina (com instruções específicas para quando o operador deve acionar serviços de emergência presencial), linguagem segura e empática para abordagem de crise, fluxo de encaminhamento para psiquiatria, psicologia e CAPS, contraindicações para manejo exclusivamente remoto de condições de saúde mental, e diretrizes de documentação clínica que preservam a dignidade do paciente. Inclui protocolo especial de atenção à diversidade: abordagem afirmativa para beneficiários LGBTQIA+, idosos em isolamento social e vítimas de violência doméstica.

**Conteúdo-chave para chunking:** Escalas de rastreamento com pontos de corte, protocolo de risco suicida, linguagem segura, fluxo de encaminhamento, contraindicações para teleatendimento em saúde mental, abordagem afirmativa.

---

## 3. Function Calling Tools — JSON Schemas

Os schemas abaixo seguem estritamente o padrão Anthropic/OpenAI para function calling (tool use), compatíveis com a API do Claude Sonnet 4 via AWS Bedrock. Cada schema inclui todos os campos obrigatórios: `name`, `description`, `input_schema` com `type`, `properties` e `required`.

---

### Tool 1 — `consultar_historico_paciente`

```json
{
  "name": "consultar_historico_paciente",
  "description": "Recupera o histórico clínico completo ou segmentado de um beneficiário Care Plus a partir do Prontuário Eletrônico do Paciente (PEP). Utiliza o token pseudonimizado do paciente para garantir conformidade com a LGPD. Deve ser chamada quando o contexto clínico atual for insuficiente para uma triagem segura, ou quando houver necessidade de verificar alergias, comorbidades, medicamentos em uso ou histórico de internações. NUNCA utilizar o CPF real — sempre usar o patient_token pseudonimizado.",
  "input_schema": {
    "type": "object",
    "properties": {
      "patient_token": {
        "type": "string",
        "description": "Token UUID pseudonimizado do beneficiário, gerado no processo de autenticação da sessão. Substitui o CPF real para garantia de privacidade. Formato: UUID v4 (ex.: '550e8400-e29b-41d4-a716-446655440000').",
        "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
      },
      "secoes_solicitadas": {
        "type": "array",
        "description": "Lista das seções do prontuário a serem recuperadas. Use apenas as seções necessárias para minimizar a exposição de dados (princípio da minimização — LGPD Art. 6º, III).",
        "items": {
          "type": "string",
          "enum": [
            "alergias",
            "medicamentos_em_uso",
            "comorbidades_ativas",
            "historico_internacoes",
            "consultas_recentes",
            "exames_laboratoriais",
            "exames_imagem",
            "vacinas",
            "cirurgias_procedimentos",
            "historico_familiar",
            "habitos_vida",
            "dados_antropometricos"
          ]
        },
        "minItems": 1,
        "maxItems": 12
      },
      "periodo_consulta": {
        "type": "object",
        "description": "Intervalo de tempo para filtrar registros históricos. Omitir para recuperar todos os registros disponíveis.",
        "properties": {
          "data_inicio": {
            "type": "string",
            "format": "date",
            "description": "Data de início do período no formato ISO 8601 (YYYY-MM-DD). Ex.: '2024-01-01'."
          },
          "data_fim": {
            "type": "string",
            "format": "date",
            "description": "Data de fim do período no formato ISO 8601 (YYYY-MM-DD). Padrão: data atual."
          }
        },
        "required": []
      },
      "profundidade": {
        "type": "string",
        "description": "Nível de detalhamento dos dados retornados. 'resumo' retorna apenas os últimos 3 registros de cada seção; 'completo' retorna todo o histórico disponível dentro do período.",
        "enum": ["resumo", "completo"],
        "default": "resumo"
      },
      "sessao_triagem_id": {
        "type": "string",
        "description": "Identificador único da sessão de triagem atual, para fins de auditoria e rastreabilidade. Gerado automaticamente pelo sistema no início da sessão.",
        "pattern": "^SESS-[A-Z0-9]{10}$"
      }
    },
    "required": [
      "patient_token",
      "secoes_solicitadas",
      "sessao_triagem_id"
    ],
    "additionalProperties": false
  }
}
```

**Exemplo de chamada pelo LLM:**
```json
{
  "name": "consultar_historico_paciente",
  "input": {
    "patient_token": "550e8400-e29b-41d4-a716-446655440000",
    "secoes_solicitadas": ["alergias", "medicamentos_em_uso", "comorbidades_ativas"],
    "profundidade": "resumo",
    "sessao_triagem_id": "SESS-K7M2X9ABCD"
  }
}
```

**Exemplo de resposta da ferramenta:**
```json
{
  "status": "sucesso",
  "patient_token": "550e8400-e29b-41d4-a716-446655440000",
  "ultima_atualizacao_pep": "2026-05-10T09:15:00Z",
  "dados": {
    "alergias": [
      {
        "substancia": "Penicilina",
        "reacao": "Anafilaxia",
        "severidade": "grave",
        "confirmada_em": "2019-03-15"
      }
    ],
    "medicamentos_em_uso": [
      {
        "principio_ativo": "Metformina",
        "dose": "850mg",
        "frequencia": "2x/dia",
        "prescrito_em": "2025-11-20",
        "prescrito_por": "MÉDICO-CRM12345"
      },
      {
        "principio_ativo": "Losartana",
        "dose": "50mg",
        "frequencia": "1x/dia",
        "prescrito_em": "2025-11-20",
        "prescrito_por": "MÉDICO-CRM12345"
      }
    ],
    "comorbidades_ativas": [
      { "cid": "E11", "descricao": "Diabetes mellitus tipo 2", "desde": "2019" },
      { "cid": "I10", "descricao": "Hipertensão arterial essencial", "desde": "2018" }
    ]
  }
}
```

---

### Tool 2 — `verificar_interacoes_medicamentosas`

```json
{
  "name": "verificar_interacoes_medicamentosas",
  "description": "Verifica interações medicamentosas, contraindicações e adequação de dosagem entre dois ou mais medicamentos, considerando as condições clínicas e o perfil fisiológico do paciente. Consulta em tempo real a base farmacológica integrada Care Plus (Micromedex + ANVISA + RxNorm). OBRIGATÓRIA antes de qualquer sugestão de medicamento pelo assistente, quando o paciente já faz uso de outros medicamentos. Também deve ser chamada para verificar adequação de dose em situações de insuficiência renal, hepática, gestação ou idade avançada.",
  "input_schema": {
    "type": "object",
    "properties": {
      "medicamentos": {
        "type": "array",
        "description": "Lista de medicamentos a serem verificados quanto a interações entre si. Incluir tanto os medicamentos que o paciente já usa quanto o(s) novo(s) medicamento(s) sendo considerado(s). Mínimo de 2 itens para verificação de interação.",
        "items": {
          "type": "object",
          "properties": {
            "principio_ativo": {
              "type": "string",
              "description": "Nome do princípio ativo do medicamento em português. Usar nomenclatura DCI (Denominação Comum Internacional). Ex.: 'atorvastatina', 'losartana', 'metformina'."
            },
            "dose_mg": {
              "type": "number",
              "description": "Dose do medicamento em miligramas (mg). Para medicamentos com outras unidades (mcg, UI), converter ou especificar na unidade_dose.",
              "minimum": 0.001
            },
            "unidade_dose": {
              "type": "string",
              "description": "Unidade de medida da dose.",
              "enum": ["mg", "mcg", "g", "UI", "mEq", "mg/kg", "mcg/kg"],
              "default": "mg"
            },
            "frequencia_diaria": {
              "type": "integer",
              "description": "Número de tomadas por dia. Ex.: 1 para dose única, 2 para 12/12h, 3 para 8/8h.",
              "minimum": 1,
              "maximum": 24
            },
            "via_administracao": {
              "type": "string",
              "description": "Via de administração do medicamento.",
              "enum": ["oral", "endovenosa", "intramuscular", "subcutânea", "inalatória", "tópica", "sublingual", "retal", "transdérmica"],
              "default": "oral"
            },
            "novo_medicamento": {
              "type": "boolean",
              "description": "Indica se este medicamento está sendo considerado para adição ao esquema atual (true) ou se já faz parte do esquema em uso pelo paciente (false).",
              "default": false
            }
          },
          "required": ["principio_ativo", "dose_mg", "frequencia_diaria", "novo_medicamento"]
        },
        "minItems": 2,
        "maxItems": 20
      },
      "perfil_paciente": {
        "type": "object",
        "description": "Dados fisiológicos e clínicos do paciente relevantes para a avaliação farmacológica. Essenciais para verificação de adequação de dose.",
        "properties": {
          "idade_anos": {
            "type": "integer",
            "description": "Idade do paciente em anos completos.",
            "minimum": 0,
            "maximum": 130
          },
          "peso_kg": {
            "type": "number",
            "description": "Peso corporal em kilogramas, utilizado para cálculo de dose por kg.",
            "minimum": 0.5,
            "maximum": 500
          },
          "sexo_biologico": {
            "type": "string",
            "description": "Sexo biológico do paciente, relevante para farmacocinética e ajuste de dose.",
            "enum": ["masculino", "feminino", "não_informado"]
          },
          "gestante": {
            "type": "boolean",
            "description": "Indica se a paciente está gestando. Se true, a verificação incluirá análise de teratogenicidade (classificação FDA de risco na gestação).",
            "default": false
          },
          "semanas_gestacao": {
            "type": "integer",
            "description": "Semanas de gestação, obrigatório se gestante=true. Relevante para análise de risco por trimestre.",
            "minimum": 1,
            "maximum": 42
          },
          "tfg_ml_min": {
            "type": "number",
            "description": "Taxa de Filtração Glomerular estimada (TFGe) em mL/min/1,73m², para ajuste de dose em insuficiência renal. Omitir se função renal desconhecida.",
            "minimum": 0,
            "maximum": 200
          },
          "child_pugh": {
            "type": "string",
            "description": "Classificação de Child-Pugh para insuficiência hepática, relevante para metabolismo hepático de fármacos.",
            "enum": ["A", "B", "C", "nao_avaliado"]
          },
          "alergias_medicamentosas": {
            "type": "array",
            "description": "Lista de princípios ativos ou classes farmacológicas aos quais o paciente tem alergia documentada.",
            "items": {
              "type": "string"
            }
          },
          "condicoes_clinicas": {
            "type": "array",
            "description": "Lista de CIDs das condições clínicas ativas relevantes para a análise farmacológica (ex.: insuficiência cardíaca, DPOC, epilepsia).",
            "items": {
              "type": "string",
              "description": "Código CID-10 ou CID-11 da condição clínica. Ex.: 'I50.0', 'J44.1'."
            }
          }
        },
        "required": ["idade_anos", "sexo_biologico"]
      },
      "nivel_detalhe_retorno": {
        "type": "string",
        "description": "Nível de detalhamento das informações retornadas sobre cada interação identificada.",
        "enum": ["resumido", "detalhado", "completo"],
        "default": "detalhado"
      },
      "sessao_triagem_id": {
        "type": "string",
        "description": "Identificador único da sessão de triagem atual, para fins de auditoria farmacológica.",
        "pattern": "^SESS-[A-Z0-9]{10}$"
      }
    },
    "required": [
      "medicamentos",
      "perfil_paciente",
      "sessao_triagem_id"
    ],
    "additionalProperties": false
  }
}
```

**Exemplo de chamada pelo LLM:**
```json
{
  "name": "verificar_interacoes_medicamentosas",
  "input": {
    "medicamentos": [
      {
        "principio_ativo": "metformina",
        "dose_mg": 850,
        "frequencia_diaria": 2,
        "via_administracao": "oral",
        "novo_medicamento": false
      },
      {
        "principio_ativo": "losartana",
        "dose_mg": 50,
        "frequencia_diaria": 1,
        "via_administracao": "oral",
        "novo_medicamento": false
      },
      {
        "principio_ativo": "ibuprofeno",
        "dose_mg": 600,
        "frequencia_diaria": 3,
        "via_administracao": "oral",
        "novo_medicamento": true
      }
    ],
    "perfil_paciente": {
      "idade_anos": 62,
      "peso_kg": 78,
      "sexo_biologico": "masculino",
      "gestante": false,
      "tfg_ml_min": 55,
      "condicoes_clinicas": ["E11", "I10"]
    },
    "nivel_detalhe_retorno": "detalhado",
    "sessao_triagem_id": "SESS-K7M2X9ABCD"
  }
}
```

**Exemplo de resposta da ferramenta:**
```json
{
  "status": "sucesso",
  "interacoes_identificadas": [
    {
      "par": ["losartana", "ibuprofeno"],
      "severidade": "grave",
      "mecanismo": "AINEs reduzem o efeito anti-hipertensivo dos BRA e podem precipitar insuficiência renal aguda, especialmente em pacientes com TFGe comprometida (atual: 55 mL/min).",
      "conduta_recomendada": "Evitar combinação. Considerar paracetamol como alternativa analgésica/antipirética. Se uso imprescindível, monitorar função renal e PA rigorosamente.",
      "referencia": "Micromedex 2026 — Nível de Evidência: Excelente"
    },
    {
      "par": ["metformina", "ibuprofeno"],
      "severidade": "moderado",
      "mecanismo": "AINEs podem causar retenção de sódio e redução da TFG, aumentando risco de acidose lática em pacientes em uso de metformina com função renal limítrofe.",
      "conduta_recomendada": "Monitorar creatinina se uso por mais de 3 dias. TFGe atual de 55 mL/min exige cautela adicional.",
      "referencia": "ANVISA — Bula metformina cloridrato (2025)"
    }
  ],
  "alertas_adicionais": [
    {
      "tipo": "dose_renal",
      "medicamento": "metformina",
      "mensagem": "TFGe entre 30–60 mL/min: dose máxima recomendada é 1.500mg/dia. Dose atual (1.700mg/dia) excede o limite para o perfil renal do paciente.",
      "conduta": "Revisar dose com médico prescritor."
    }
  ],
  "resumo_seguranca": "ATENÇÃO — 2 interações identificadas (1 grave, 1 moderada) e 1 alerta de adequação de dose. Combinação com ibuprofeno NÃO RECOMENDADA para este perfil clínico."
}
```

---

### Tool 3 — `agendar_teleconsulta`

```json
{
  "name": "agendar_teleconsulta",
  "description": "Agenda uma teleconsulta médica para o beneficiário Care Plus no sistema de agendamento integrado. Seleciona o especialista adequado com base na queixa principal e na disponibilidade da rede credenciada. Deve ser chamada apenas após confirmação explícita do operador de triagem — NUNCA de forma autônoma pelo assistente. Suporta agendamento de urgência (disponibilidade em até 2 horas), rotina (até 72 horas) e eletivo (até 15 dias). Respeita o plano do beneficiário e a rede de prestadores credenciados.",
  "input_schema": {
    "type": "object",
    "properties": {
      "patient_token": {
        "type": "string",
        "description": "Token UUID pseudonimizado do beneficiário, gerado no processo de autenticação da sessão.",
        "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
      },
      "especialidade": {
        "type": "string",
        "description": "Especialidade médica requerida para a teleconsulta, com base na queixa e triagem realizadas.",
        "enum": [
          "clinica_medica",
          "cardiologia",
          "pneumologia",
          "endocrinologia",
          "neurologia",
          "psiquiatria",
          "ginecologia",
          "urologia",
          "ortopedia",
          "dermatologia",
          "oftalmologia",
          "otorrinolaringologia",
          "gastroenterologia",
          "nefrologia",
          "infectologia",
          "reumatologia",
          "oncologia",
          "geriatria",
          "pediatria",
          "nutricao"
        ]
      },
      "prioridade": {
        "type": "string",
        "description": "Nível de prioridade do agendamento, definido com base na classificação de risco Manchester da triagem. Urgência: dentro de 2h. Prioritário: dentro de 24h. Rotina: dentro de 72h. Eletivo: dentro de 15 dias úteis.",
        "enum": ["urgencia", "prioritario", "rotina", "eletivo"]
      },
      "motivo_consulta": {
        "type": "string",
        "description": "Descrição resumida do motivo da consulta (queixa principal), a ser incluída no pré-aviso enviado ao médico antes da teleconsulta. Máximo 500 caracteres.",
        "maxLength": 500
      },
      "cid_suspeito": {
        "type": "array",
        "description": "Lista de CIDs suspeitos identificados na triagem, para orientação prévia do médico que realizará a teleconsulta.",
        "items": {
          "type": "string",
          "description": "Código CID-10 ou CID-11. Ex.: 'I20.0' para angina instável."
        },
        "maxItems": 5
      },
      "janela_disponibilidade": {
        "type": "object",
        "description": "Preferências de horário do beneficiário para o agendamento. Ignorado para consultas de urgência, que são alocadas no primeiro slot disponível.",
        "properties": {
          "data_preferencial": {
            "type": "string",
            "format": "date",
            "description": "Data de preferência para a teleconsulta (YYYY-MM-DD). Deve ser futura."
          },
          "turno_preferencial": {
            "type": "string",
            "description": "Turno de preferência.",
            "enum": ["manha", "tarde", "noite", "qualquer"]
          },
          "dias_semana_disponiveis": {
            "type": "array",
            "description": "Dias da semana em que o beneficiário tem disponibilidade.",
            "items": {
              "type": "string",
              "enum": ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]
            }
          }
        }
      },
      "modalidade": {
        "type": "string",
        "description": "Modalidade da consulta a ser agendada.",
        "enum": ["video", "audio", "chat_assincrono"],
        "default": "video"
      },
      "idioma_preferencial": {
        "type": "string",
        "description": "Idioma de preferência do beneficiário para a consulta, para alocação de médico adequado.",
        "enum": ["pt-BR", "en-US", "es-AR", "libras"],
        "default": "pt-BR"
      },
      "documentos_anexar": {
        "type": "array",
        "description": "IDs de documentos do PEP a serem disponibilizados ao médico antes da consulta (ex.: últimos exames, laudos, resumo da triagem atual).",
        "items": {
          "type": "string",
          "description": "ID do documento no PEP Care Plus."
        },
        "maxItems": 10
      },
      "operador_id": {
        "type": "string",
        "description": "ID único do operador de triagem que está realizando o agendamento, para fins de auditoria e rastreabilidade.",
        "pattern": "^OP-[A-Z0-9]{8}$"
      },
      "sessao_triagem_id": {
        "type": "string",
        "description": "Identificador único da sessão de triagem atual.",
        "pattern": "^SESS-[A-Z0-9]{10}$"
      }
    },
    "required": [
      "patient_token",
      "especialidade",
      "prioridade",
      "motivo_consulta",
      "modalidade",
      "operador_id",
      "sessao_triagem_id"
    ],
    "additionalProperties": false
  }
}
```

**Exemplo de chamada pelo LLM:**
```json
{
  "name": "agendar_teleconsulta",
  "input": {
    "patient_token": "550e8400-e29b-41d4-a716-446655440000",
    "especialidade": "cardiologia",
    "prioridade": "urgencia",
    "motivo_consulta": "Dor torácica opressiva há 30min com irradiação para MSE, diaforese, paciente hipertenso e diabético. Suspeita de SCA.",
    "cid_suspeito": ["I20.0", "I21.9"],
    "modalidade": "video",
    "documentos_anexar": ["DOC-ECG-2026-0517", "DOC-LAB-20260510"],
    "operador_id": "OP-X7K2M9AB",
    "sessao_triagem_id": "SESS-K7M2X9ABCD"
  }
}
```

**Exemplo de resposta da ferramenta:**
```json
{
  "status": "agendado",
  "numero_agendamento": "AGD-2026-0517-00892",
  "medico_alocado": {
    "nome": "Dr. Roberto Lima",
    "crm": "CRM-SP 87654",
    "especialidade": "Cardiologia",
    "subespecialidade": "Cardiologia Intervencionista"
  },
  "horario_consulta": "2026-05-17T15:10:00-03:00",
  "tempo_espera_estimado_minutos": 38,
  "link_sala_virtual": "https://telemed.careplus.com.br/sala/AGD-2026-0517-00892",
  "instrucoes_beneficiario": "Permaneça em repouso. O médico entrará em contato em até 38 minutos. Mantenha o celular disponível.",
  "documentos_compartilhados": ["DOC-ECG-2026-0517", "DOC-LAB-20260510"]
}
```

---

## 4. Bonus — Ferramenta de Recuperação de Dados de Wearables

### Tool 4 — `recuperar_dados_wearable`

```json
{
  "name": "recuperar_dados_wearable",
  "description": "Recupera métricas biométricas recentes coletadas por dispositivos wearable do beneficiário (Apple Health, Oura Ring, Fitbit, Samsung Health, Garmin, Withings). Os dados são obtidos via API das plataformas, com consentimento prévio do beneficiário registrado no onboarding Care Plus. Deve ser utilizada para enriquecer o contexto clínico da triagem com dados objetivos e contínuos de saúde, como frequência cardíaca, variabilidade da FC, saturação de oxigênio, padrões de sono, temperatura corporal e atividade física. IMPORTANTE: dados de wearables são indicativos e complementares — nunca substitutos de avaliação clínica formal. Verificar sempre se o consentimento para compartilhamento está ativo antes de chamar esta ferramenta.",
  "input_schema": {
    "type": "object",
    "properties": {
      "patient_token": {
        "type": "string",
        "description": "Token UUID pseudonimizado do beneficiário. O sistema resolve internamente o vínculo com os dispositivos registrados na conta Care Plus do beneficiário.",
        "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
      },
      "plataformas": {
        "type": "array",
        "description": "Lista de plataformas de wearable das quais recuperar dados. Se omitido, consulta todas as plataformas vinculadas à conta do beneficiário com consentimento ativo.",
        "items": {
          "type": "string",
          "enum": [
            "apple_health",
            "oura_ring",
            "fitbit",
            "samsung_health",
            "garmin_connect",
            "withings",
            "polar",
            "whoop",
            "google_fit"
          ]
        }
      },
      "metricas_solicitadas": {
        "type": "array",
        "description": "Lista das métricas biométricas a recuperar. Solicitar apenas as clinicamente relevantes para a triagem em curso (princípio da minimização de dados).",
        "items": {
          "type": "string",
          "enum": [
            "frequencia_cardiaca_repouso",
            "frequencia_cardiaca_maxima",
            "variabilidade_fc_hrv",
            "spo2_saturacao_oxigenio",
            "frequencia_respiratoria",
            "temperatura_corporal_pele",
            "temperatura_corporal_nucleo",
            "pressao_arterial_sistolica",
            "pressao_arterial_diastolica",
            "passos_diarios",
            "calorias_ativas",
            "minutos_atividade_moderada",
            "minutos_atividade_intensa",
            "distancia_km",
            "sono_total_horas",
            "sono_rem_horas",
            "sono_profundo_horas",
            "latencia_sono_minutos",
            "eficiencia_sono_percentual",
            "score_prontidao_oura",
            "nivel_estresse",
            "ecg_ritmo_detectado",
            "irregularidade_ritmo_afib",
            "glicose_intersticial_mgdl",
            "peso_kg",
            "imc"
          ]
        },
        "minItems": 1
      },
      "periodo": {
        "type": "object",
        "description": "Período de tempo para recuperação dos dados. Para triagem aguda, priorizar as últimas 24–72 horas. Para check-up proativo, considerar até 30 dias.",
        "properties": {
          "ultimas_horas": {
            "type": "integer",
            "description": "Recuperar dados das últimas N horas (tem precedência sobre data_inicio/data_fim se informado).",
            "minimum": 1,
            "maximum": 720
          },
          "data_inicio": {
            "type": "string",
            "format": "date-time",
            "description": "Data e hora de início do período no formato ISO 8601. Ex.: '2026-05-10T00:00:00-03:00'."
          },
          "data_fim": {
            "type": "string",
            "format": "date-time",
            "description": "Data e hora de fim do período. Padrão: momento atual da consulta."
          }
        },
        "oneOf": [
          { "required": ["ultimas_horas"] },
          { "required": ["data_inicio"] }
        ]
      },
      "granularidade": {
        "type": "string",
        "description": "Granularidade temporal dos dados retornados. 'por_minuto' para análise de eventos agudos; 'por_hora' para padrões intradiários; 'diario' para tendências de médio prazo.",
        "enum": ["por_minuto", "por_hora", "diario"],
        "default": "por_hora"
      },
      "incluir_alertas_dispositivo": {
        "type": "boolean",
        "description": "Se true, inclui alertas gerados pelo próprio dispositivo no período (ex.: detecção de fibrilação atrial pela Apple Watch, aviso de FC baixa, queda detectada).",
        "default": true
      },
      "contexto_clinico": {
        "type": "object",
        "description": "Contexto clínico da solicitação, para personalizar a análise e destacar anomalias relevantes para a queixa em triagem.",
        "properties": {
          "queixa_principal": {
            "type": "string",
            "description": "Queixa principal do paciente nesta sessão de triagem. Ex.: 'palpitações', 'cansaço progressivo', 'dor torácica'.",
            "maxLength": 200
          },
          "cids_suspeitos": {
            "type": "array",
            "description": "CIDs suspeitos da triagem, para contextualizar a análise das métricas.",
            "items": { "type": "string" },
            "maxItems": 5
          }
        }
      },
      "sessao_triagem_id": {
        "type": "string",
        "description": "Identificador único da sessão de triagem atual, para fins de auditoria.",
        "pattern": "^SESS-[A-Z0-9]{10}$"
      }
    },
    "required": [
      "patient_token",
      "metricas_solicitadas",
      "periodo",
      "sessao_triagem_id"
    ],
    "additionalProperties": false
  }
}
```

**Exemplo de chamada pelo LLM:**
```json
{
  "name": "recuperar_dados_wearable",
  "input": {
    "patient_token": "550e8400-e29b-41d4-a716-446655440000",
    "plataformas": ["apple_health", "oura_ring"],
    "metricas_solicitadas": [
      "frequencia_cardiaca_repouso",
      "variabilidade_fc_hrv",
      "spo2_saturacao_oxigenio",
      "ecg_ritmo_detectado",
      "irregularidade_ritmo_afib",
      "sono_total_horas",
      "temperatura_corporal_pele"
    ],
    "periodo": {
      "ultimas_horas": 72
    },
    "granularidade": "por_hora",
    "incluir_alertas_dispositivo": true,
    "contexto_clinico": {
      "queixa_principal": "palpitações e tontura ao esforço",
      "cids_suspeitos": ["I49.9", "R00.2"]
    },
    "sessao_triagem_id": "SESS-K7M2X9ABCD"
  }
}
```

**Exemplo de resposta da ferramenta:**
```json
{
  "status": "sucesso",
  "plataformas_consultadas": ["apple_health", "oura_ring"],
  "periodo_retornado": {
    "inicio": "2026-05-14T14:32:00-03:00",
    "fim": "2026-05-17T14:32:00-03:00"
  },
  "metricas": {
    "frequencia_cardiaca_repouso": {
      "media_bpm": 88,
      "minimo_bpm": 72,
      "maximo_bpm": 134,
      "tendencia": "elevada_vs_baseline_habitual_68bpm"
    },
    "variabilidade_fc_hrv": {
      "media_ms": 28,
      "baseline_pessoal_ms": 52,
      "reducao_percentual": 46,
      "interpretacao": "HRV significativamente reduzida — pode indicar estresse fisiológico, infecção ou disautonomia"
    },
    "spo2_saturacao_oxigenio": {
      "media_percentual": 96.2,
      "minimo_percentual": 91.0,
      "episodios_abaixo_94": 3,
      "horarios_criticos": ["2026-05-16T03:14:00", "2026-05-16T04:02:00", "2026-05-17T02:51:00"]
    },
    "ecg_ritmo_detectado": {
      "registros_realizados": 4,
      "ritmos_detectados": ["sinusal", "sinusal", "inconclusivo", "fibrilacao_atrial_suspeita"],
      "ultimo_registro": "2026-05-17T13:45:00-03:00"
    },
    "temperatura_corporal_pele": {
      "desvio_vs_baseline_graus_c": +0.8,
      "tendencia": "elevacao_progressiva_72h"
    }
  },
  "alertas_dispositivo": [
    {
      "tipo": "irregularidade_ritmo_cardiaco",
      "descricao": "Apple Watch detectou possível fibrilação atrial às 13:45 do dia 17/05/2026.",
      "severidade": "alta",
      "acao_sugerida_pelo_dispositivo": "Consultar médico"
    }
  ],
  "resumo_clinico_automatico": "ATENÇÃO CLÍNICA: Dados das últimas 72h revelam FC de repouso elevada (+29% acima do baseline), queda acentuada de HRV (-46%), 3 episódios de SpO2 < 94% e 1 alerta de possível FA pelo Apple Watch. Padrão consistente com a queixa de palpitações. Escalada clínica recomendada."
}
```

---

## Notas de Implementação — Function Calling

### Compatibilidade com a API Anthropic (Claude Sonnet 4)

Para uso via AWS Bedrock com o Claude Sonnet 4, os schemas acima devem ser passados no parâmetro `tools` da requisição, conforme o padrão Anthropic:

```python
import anthropic

client = anthropic.AnthropicBedrock(
    aws_region="sa-east-1"  # Região Brasil — residência de dados LGPD
)

response = client.messages.create(
    model="anthropic.claude-sonnet-4-20250514-v1:0",
    max_tokens=1024,
    tools=[
        consultar_historico_paciente_schema,
        verificar_interacoes_medicamentosas_schema,
        agendar_teleconsulta_schema,
        recuperar_dados_wearable_schema
    ],
    tool_choice={"type": "auto"},  # LLM decide quando chamar
    messages=[
        {"role": "user", "content": prompt_clinico_montado}
    ]
)
```

### Controle de Autorização por Ferramenta

| Tool | Pode ser chamada por | Requer confirmação do operador? |
|---|---|---|
| `consultar_historico_paciente` | LLM automaticamente | Não (somente leitura) |
| `verificar_interacoes_medicamentosas` | LLM automaticamente | Não (somente leitura) |
| `agendar_teleconsulta` | LLM propõe → Operador confirma | **SIM — obrigatório** |
| `recuperar_dados_wearable` | LLM automaticamente | Não (consentimento pré-dado) |

> **Princípio de autorização mínima:** O LLM só tem permissão para executar automaticamente ferramentas de leitura. Qualquer ação com efeito no mundo real (agendamento, prescrição, notificação) exige confirmação explícita do operador de triagem antes de ser efetivada.

---

*Documento de especificação técnica do BluaDiagnostics — Care Plus.*
*Versão 1.1 | Maio de 2026 | Classificação: CONFIDENCIAL — USO INTERNO*
