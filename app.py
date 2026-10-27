import io
import os
import shutil
import tempfile
import uuid
from datetime import datetime

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_file,
)

from werkzeug.utils import secure_filename

from services.document_processor import get_processor
from services.agricultural_extractor import extrair_dados
from services.validator import validar_documento


# ============================================================
# CONFIGURAÇÃO
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)


ALLOWED_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "webp",
    "bmp",
    "tif",
    "tiff",
}


app = Flask(__name__)

# Mantemos um limite alto para cada arquivo individual.
# O navegador NÃO enviará mais todos os arquivos juntos.
app.config["MAX_CONTENT_LENGTH"] = (
    100 * 1024 * 1024
)


# ============================================================
# UTILITÁRIOS
# ============================================================

def extensao_permitida(nome):
    """
    Verifica se a extensão do arquivo é permitida.
    """

    return (
        "." in nome
        and nome.rsplit(
            ".",
            1
        )[1].lower()
        in ALLOWED_EXTENSIONS
    )


def extrair_linhas_excel(documento):
    """
    Converte o resultado de UM documento
    para as 6 colunas finais do Excel.

    Campos:

    1. Bloco
    2. Talhão
    3. Variedade
    4. Área
    5. Plantio
    6. Propriedade
    7. Proprietário
    8. Município
    """

    metadata = (
        documento.get("metadata")
        or {}
    )

    propriedade = str(
        metadata.get("propriedade")
        or documento.get("propriedade")
        or ""
    ).strip()

    proprietario = str(
        metadata.get("proprietario")
        or metadata.get("proprietário")
        or documento.get("proprietario")
        or documento.get("proprietário")
        or ""
    ).strip()

    municipio = str(
        metadata.get("municipio")
        or metadata.get("município")
        or documento.get("municipio")
        or documento.get("município")
        or ""
    ).strip()

    bloco_doc = str(
        metadata.get("bloco")
        or documento.get("bloco")
        or ""
    ).strip()

    blocos = documento.get("blocos") or []

    # Fallback se blocos estiver vazio mas talhoes existir
    if not blocos and (documento.get("talhoes") or []):
        blocos = [{
            "bloco": bloco_doc,
            "talhoes": documento.get("talhoes") or [],
        }]

    linhas = []

    for bloco in blocos:
        codigo_bloco = str(
            bloco.get("bloco")
            or bloco.get("codigo")
            or bloco_doc
            or ""
        ).strip()

        bloco_prop = str(
            bloco.get("propriedade")
            or propriedade
        ).strip()

        bloco_proprio = str(
            bloco.get("proprietario")
            or bloco.get("proprietário")
            or proprietario
        ).strip()

        bloco_mun = str(
            bloco.get("municipio")
            or bloco.get("município")
            or municipio
        ).strip()

        vistos = set()
        for talhao in (
            bloco.get("talhoes")
            or []
        ):
            if not isinstance(talhao, dict):
                continue

            t_num = str(
                talhao.get("talhao")
                or talhao.get("talhão")
                or talhao.get("numero")
                or ""
            ).strip()
            if not t_num or not any(c.isdigit() for c in t_num):
                continue

            var_t = str(talhao.get("variedade") or "").strip()
            if any(k in var_t.lower() for k in ["bloco", "status", "mapa de", "área total", "resumo de área"]):
                continue

            area_t = str(talhao.get("area") or talhao.get("área") or "").strip()
            plantio_t = str(talhao.get("plantio") or talhao.get("data_plantio") or "").strip()
            import re
            m_dt = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", plantio_t)
            if m_dt:
                plantio_t = f"{int(m_dt.group(1)):02d}/{int(m_dt.group(2)):02d}/{m_dt.group(3)}"

            chave = (codigo_bloco, t_num, var_t, area_t, plantio_t)
            if chave in vistos:
                continue
            vistos.add(chave)

            linhas.append({
                "Bloco":
                    str(
                        talhao.get("bloco")
                        or codigo_bloco
                        or ""
                    ).strip(),

                "Talhão":
                    t_num,

                "Variedade":
                    var_t,

                "Área":
                    area_t,

                "Plantio":
                    plantio_t,

                "Propriedade":
                    str(
                        talhao.get("propriedade")
                        or bloco_prop
                        or ""
                    ).strip(),

                "Proprietário":
                    str(
                        talhao.get("proprietario")
                        or talhao.get("proprietário")
                        or bloco_proprio
                        or ""
                    ).strip(),

                "Município":
                    str(
                        talhao.get("municipio")
                        or talhao.get("município")
                        or bloco_mun
                        or ""
                    ).strip(),
            })

    return linhas


def criar_excel(linhas):
    """
    Cria o Excel somente em memória.

    Nenhum Excel é salvo no projeto.
    """

    import pandas as pd

    colunas = [
        "Bloco",
        "Talhão",
        "Variedade",
        "Área",
        "Plantio",
        "Propriedade",
        "Proprietário",
        "Município",
    ]

    linhas_normalizadas = []
    for item in (linhas or []):
        if not isinstance(item, dict):
            continue

        linhas_normalizadas.append({
            "Bloco": str(item.get("Bloco") or item.get("bloco") or "").strip(),
            "Talhão": str(item.get("Talhão") or item.get("Talhao") or item.get("talhao") or item.get("talhão") or "").strip(),
            "Variedade": str(item.get("Variedade") or item.get("variedade") or "").strip(),
            "Área": str(item.get("Área") or item.get("Area") or item.get("area") or item.get("área") or "").strip(),
            "Plantio": str(item.get("Plantio") or item.get("plantio") or item.get("data_plantio") or "").strip(),
            "Propriedade": str(item.get("Propriedade") or item.get("propriedade") or "").strip(),
            "Proprietário": str(item.get("Proprietário") or item.get("Proprietario") or item.get("proprietario") or item.get("proprietário") or "").strip(),
            "Município": str(item.get("Município") or item.get("Municipio") or item.get("municipio") or item.get("município") or "").strip(),
        })

    df = pd.DataFrame(
        linhas_normalizadas,
        columns=colunas
    )

    # Mantém tudo como texto limpo.
    for coluna in colunas:
        df[coluna] = (
            df[coluna]
            .fillna("")
            .astype(str)
        )

    memoria = io.BytesIO()

    with pd.ExcelWriter(
        memoria,
        engine="openpyxl"
    ) as writer:

        df.to_excel(
            writer,
            index=False,
            sheet_name="Dados"
        )

        planilha = (
            writer.book["Dados"]
        )

        planilha.freeze_panes = "A2"

        planilha.auto_filter.ref = (
            planilha.dimensions
        )

        larguras = {
            "A": 18,
            "B": 12,
            "C": 18,
            "D": 14,
            "E": 15,
            "F": 35,
            "G": 35,
            "H": 20,
        }

        for coluna, largura in (
            larguras.items()
        ):

            planilha.column_dimensions[
                coluna
            ].width = largura

    memoria.seek(0)

    return memoria


def processar_arquivo_temporario(
    arquivo,
    processor,
    indice
):
    """
    Processa UM arquivo.

    O arquivo é salvo somente em uma pasta
    temporária do sistema.

    Depois do processamento:
    - upload é apagado;
    - pasta de resultado do OCR é apagada.

    Nada é salvo em uploads/ do projeto.
    """

    nome_original = (
        arquivo.filename
        or f"documento_{indice}"
    )

    extensao = os.path.splitext(
        nome_original
    )[1].lower()

    pasta_temp = tempfile.mkdtemp(
        prefix="leitor_documentos_"
    )

    nome_seguro = secure_filename(
        nome_original
    )

    if not nome_seguro:

        nome_seguro = (
            f"documento_{indice}"
            f"{extensao}"
        )

    caminho_temp = os.path.join(
        pasta_temp,
        nome_seguro
    )

    resultado_ocr = None

    try:

        # ----------------------------------------------------
        # SALVA TEMPORARIAMENTE
        # ----------------------------------------------------

        arquivo.save(
            caminho_temp
        )


        # ----------------------------------------------------
        # ID DO PROCESSAMENTO
        # ----------------------------------------------------

        documento_id = (
            datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )
            + "_"
            + uuid.uuid4().hex[:8]
            + f"_{indice}"
        )


        # ----------------------------------------------------
        # OCR
        # ----------------------------------------------------

        resultado_ocr = (
            processor.processar(
                caminho_temp,
                documento_id
            )
        )


        if not resultado_ocr.get(
            "sucesso"
        ):

            raise RuntimeError(
                resultado_ocr.get(
                    "erro",
                    "Não foi possível "
                    "processar o documento."
                )
            )


        # ----------------------------------------------------
        # EXTRAÇÃO AGRÍCOLA
        # ----------------------------------------------------

        documento = extrair_dados(
            resultado_ocr
        )


        # ----------------------------------------------------
        # VALIDAÇÃO
        # ----------------------------------------------------

        documento = validar_documento(
            documento
        )


        # ----------------------------------------------------
        # CONVERTE PARA AS 6 COLUNAS
        # ----------------------------------------------------

        linhas = extrair_linhas_excel(
            documento
        )


        return {
            "nome": nome_original,
            "linhas": linhas,
        }


    finally:

        # ----------------------------------------------------
        # REMOVE UPLOAD TEMPORÁRIO
        # ----------------------------------------------------

        try:

            shutil.rmtree(
                pasta_temp,
                ignore_errors=True
            )

        except Exception:
            pass


        # ----------------------------------------------------
        # REMOVE RESULTADO DO OCR
        # ----------------------------------------------------

        if resultado_ocr:

            pasta_resultado = (
                resultado_ocr.get(
                    "pasta_saida"
                )
            )

            if pasta_resultado:

                try:

                    shutil.rmtree(
                        pasta_resultado,
                        ignore_errors=True
                    )

                except Exception:
                    pass


# ============================================================
# PÁGINA
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def index():

    return render_template(
        "index.html"
    )


@app.route(
    "/ping",
    methods=["GET"]
)
def ping():
    return jsonify({
        "status": "ok",
        "servico": "Robô Leitor Agrícola",
        "vigilante_email": "ativo"
    }), 200


@app.route(
    "/api/watcher/verificar-agora",
    methods=["POST", "GET"]
)
def api_watcher_verificar_agora():
    """Força verificação e processamento imediato dos e-mails no Gmail."""
    try:
        from services.email_watcher import forcar_verificacao_emails
        import threading
        threading.Thread(target=forcar_verificacao_emails, daemon=True).start()
        return jsonify({
            "status": "ok",
            "mensagem": "Verificação manual do Gmail disparada com sucesso."
        }), 200
    except Exception as e:
        return jsonify({
            "status": "erro",
            "mensagem": str(e)
        }), 500


@app.route(
    "/status-watcher",
    methods=["GET"]
)
def status_watcher():
    import time
    import html as pyhtml
    try:
        from services.email_watcher import (
            LOTES_PENDENTES,
            EMAILS_PROCESSADOS_IDS,
            LOGS_RECENTES,
            JANELA_SILENCIO_SEGUNDOS,
            forcar_verificacao_emails,
        )

        # Se acessado via navegador (não JSON), dispara checagem imediata em segundo plano
        if request.args.get("json") != "1" and request.headers.get("Accept") != "application/json":
            import threading
            threading.Thread(target=forcar_verificacao_emails, daemon=True).start()
        lotes_info = {}
        agora = time.time()
        for k, v in LOTES_PENDENTES.items():
            lotes_info[k] = {
                "remetente": v.get("remetente"),
                "qtd_docs": len(v.get("anexos", [])),
                "docs": [a.get("nome") for a in v.get("anexos", [])],
                "segundos_desde_ultimo": round(agora - v.get("ultimo_recebimento", agora), 1),
                "segundos_restantes": max(0, int(JANELA_SILENCIO_SEGUNDOS - (agora - v.get("ultimo_recebimento", agora)))),
            }

        # Suporte a JSON via query param ?json=1
        if request.args.get("json") == "1" or request.headers.get("Accept") == "application/json":
            return jsonify({
                "status": "ok",
                "janela_silencio_segundos": JANELA_SILENCIO_SEGUNDOS,
                "lotes_pendentes": lotes_info,
                "total_emails_processados_sessao": len(EMAILS_PROCESSADOS_IDS),
                "logs_recentes": LOGS_RECENTES[-400:],
            }), 200

        def get_log_class(txt):
            if any(k in txt for k in ["ERRO", "Falha", "Exception"]):
                return "log-line log-err"
            elif any(k in txt for k in ["Sucesso", "enviada com sucesso"]):
                return "log-line log-ok"
            elif any(k in txt for k in ["Processando", "EXTRACAO", "FECHANDO LOTE"]):
                return "log-line log-warn"
            elif "[EMAIL INGEST]" in txt:
                return "log-line log-ingest"
            return "log-line"

        # Renderiza página web limpa com UMA LINHA PARA CADA LOG
        linhas_html = "".join(f"<div class='{get_log_class(log)}'>{pyhtml.escape(log)}</div>" for log in LOGS_RECENTES[-400:])
        if not linhas_html:
            linhas_html = "<div class='log-empty'>Aguardando novos eventos...</div>"

        lotes_html = ""
        if lotes_info:
            for rem, info in lotes_info.items():
                docs_str = pyhtml.escape(", ".join(str(d) for d in info["docs"]))
                lotes_html += f"""
                <div class='lote-card'>
                    <div><b>Remetente:</b> {pyhtml.escape(rem)}</div>
                    <div><b>Documentos ({info['qtd_docs']}):</b> {docs_str}</div>
                    <div style='margin-top: 5px;'><b>Tempo para fechar lote:</b> <span class='badge-time'>{info['segundos_restantes']}s</span></div>
                </div>
                """
        else:
            lotes_html = "<div class='text-muted'>Nenhum lote acumulando no momento (robô livre).</div>"

        html_template = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Status do Robô Leitor</title>
    <style>
        * {
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 1240px;
            margin: 0 auto;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #334155;
            padding-bottom: 16px;
            margin-bottom: 20px;
        }
        .title {
            font-size: 22px;
            font-weight: 700;
            color: #38bdf8;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .badge-status {
            background: #14532d;
            color: #86efac;
            border: 1px solid #22c55e;
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .cards-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
        }
        .card {
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 16px;
        }
        .card-label {
            font-size: 12px;
            color: #94a3b8;
            text-transform: uppercase;
            font-weight: 600;
            margin-bottom: 6px;
        }
        .card-value {
            font-size: 22px;
            font-weight: 700;
            color: #f1f5f9;
        }
        .section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }
        .section-title {
            font-size: 16px;
            font-weight: 600;
            color: #cbd5e1;
        }
        .pill-refresh {
            font-size: 12px;
            color: #38bdf8;
            background: #082f49;
            border: 1px solid #0369a1;
            padding: 4px 12px;
            border-radius: 14px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .terminal-wrapper {
            position: relative;
        }
        .terminal {
            background: #020617;
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 18px 20px;
            font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
            font-size: 13.5px;
            height: 620px;
            max-height: 72vh;
            overflow-y: auto;
            line-height: 1.65;
            scroll-behavior: smooth;
        }
        .log-line {
            white-space: pre-wrap;
            word-break: break-word;
            padding: 3px 0;
            border-bottom: 1px solid #0b1329;
            color: #cbd5e1;
        }
        .log-line:last-child {
            border-bottom: none;
        }
        .log-err {
            color: #f87171 !important;
            font-weight: 600;
            background: rgba(239, 68, 68, 0.08);
            padding-left: 4px;
            border-radius: 3px;
        }
        .log-ok {
            color: #4ade80 !important;
            font-weight: 600;
        }
        .log-warn {
            color: #fbbf24 !important;
        }
        .log-ingest {
            color: #38bdf8 !important;
        }
        .log-empty {
            color: #64748b;
            font-style: italic;
        }
        .btn-scroll-bottom {
            display: none;
            position: absolute;
            right: 24px;
            bottom: 24px;
            background: #0284c7;
            color: #ffffff;
            border: none;
            padding: 10px 18px;
            border-radius: 24px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            box-shadow: 0 4px 16px rgba(0,0,0,0.6);
            transition: all 0.2s;
            z-index: 10;
        }
        .btn-scroll-bottom:hover {
            background: #0369a1;
            transform: translateY(-2px);
        }
        .btn-verificar {
            background: #0284c7;
            color: #ffffff;
            border: 1px solid #38bdf8;
            padding: 7px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.2s;
        }
        .btn-verificar:hover {
            background: #0369a1;
            transform: translateY(-1px);
        }
        .btn-verificar:disabled {
            opacity: 0.6;
            cursor: not-allowed;
        }
        .banner-keepalive {
            background: #0c4a6e;
            border: 1px solid #0284c7;
            border-radius: 8px;
            padding: 12px 16px;
            margin-bottom: 20px;
            font-size: 13.5px;
            color: #e0f2fe;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .lote-card {
            background: #1e293b;
            border-left: 4px solid #f59e0b;
            border-radius: 6px;
            padding: 12px 16px;
            margin-bottom: 12px;
            font-size: 14px;
            line-height: 1.5;
        }
        .badge-time {
            color: #fbbf24;
            font-weight: 700;
        }
        .text-muted {
            color: #64748b;
            font-size: 14px;
            font-style: italic;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title">🌾 Robô Leitor Agrícola</div>
            <div style="display: flex; align-items: center; gap: 10px;">
                <button id="btnVerificar" onclick="forcarVerificacao()" class="btn-verificar">⚡ Checar Novos E-mails Agora</button>
                <div class="badge-status">● Vigilante 100% Ativo</div>
            </div>
        </div>

        <div class="banner-keepalive">
            <span style="font-size: 18px;">🛡️</span>
            <div><b>Monitoramento 24/7 Ativo:</b> O robô monitora o Gmail continuamente. O auto-pinger mantém o servidor acordado no Render, e os lotes e anexos são gravados em disco de forma persistente.</div>
        </div>

        <div class="cards-row">
            <div class="card">
                <div class="card-label">E-mails Processados</div>
                <div class="card-value" id="statEmails">__TOTAL_EMAILS__</div>
            </div>
            <div class="card">
                <div class="card-label">Janela de Silêncio</div>
                <div class="card-value">__JANELA_SILENCIO__s</div>
            </div>
            <div class="card">
                <div class="card-label">Caixa Monitorada</div>
                <div class="card-value" style="font-size: 15px; word-break: break-all;">robo.agricola.leitor@gmail.com</div>
            </div>
        </div>

        <div style="margin-bottom: 20px;">
            <div class="section-title" style="margin-bottom: 10px;">📬 Lotes de Documentos em Andamento</div>
            <div id="lotesContainer">
                __LOTES_HTML__
            </div>
        </div>

        <div>
            <div class="section-header">
                <div class="section-title">📜 Logs do Robô (Tempo Real)</div>
                <div class="pill-refresh">● Atualização ao vivo contínua</div>
            </div>
            <div class="terminal-wrapper">
                <div class="terminal" id="terminal">
                    __LINHAS_HTML__
                </div>
                <button id="btnScroll" class="btn-scroll-bottom">⬇ Ir para os logs mais recentes</button>
            </div>
        </div>
    </div>

    <script>
        const terminal = document.getElementById("terminal");
        const btnScroll = document.getElementById("btnScroll");
        let autoScroll = true;

        function escapeHtml(text) {
            const div = document.createElement("div");
            div.textContent = text;
            return div.innerHTML;
        }

        function getLogClass(txt) {
            if (txt.includes("ERRO") || txt.includes("Falha") || txt.includes("Exception")) return "log-line log-err";
            if (txt.includes("Sucesso") || txt.includes("enviada com sucesso")) return "log-line log-ok";
            if (txt.includes("Processando") || txt.includes("EXTRACAO") || txt.includes("FECHANDO LOTE")) return "log-line log-warn";
            if (txt.includes("[EMAIL INGEST]")) return "log-line log-ingest";
            return "log-line";
        }

        function rolarParaOFim(forcar = false) {
            if (autoScroll || forcar) {
                terminal.scrollTop = terminal.scrollHeight;
            }
        }

        terminal.addEventListener("scroll", () => {
            const distFim = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight;
            autoScroll = distFim < 60;
            if (btnScroll) {
                btnScroll.style.display = autoScroll ? "none" : "inline-flex";
            }
        });

        if (btnScroll) {
            btnScroll.addEventListener("click", () => {
                autoScroll = true;
                rolarParaOFim(true);
                btnScroll.style.display = "none";
            });
        }

        // Rola para o final logo no carregamento inicial
        rolarParaOFim(true);
        setTimeout(() => rolarParaOFim(true), 100);
        setTimeout(() => rolarParaOFim(true), 350);

        // Função para forçar checagem imediata via botão
        async function forcarVerificacao() {
            const btn = document.getElementById("btnVerificar");
            if (btn) {
                btn.disabled = true;
                btn.innerText = "⏳ Verificando Gmail...";
            }
            try {
                await fetch("/api/watcher/verificar-agora", { method: "POST" });
                await atualizarEmTempoReal();
            } catch (err) {
                console.error("Erro na verificação manual:", err);
            } finally {
                setTimeout(() => {
                    if (btn) {
                        btn.disabled = false;
                        btn.innerText = "⚡ Checar Novos E-mails Agora";
                    }
                }, 1500);
            }
        }

        // Atualização em tempo real sem recarregar a tela (zero piscada, preservando a leitura)
        async function atualizarEmTempoReal() {
            try {
                const res = await fetch("/status-watcher?json=1");
                if (!res.ok) return;
                const data = await res.json();

                // Atualiza contadores
                const elEmails = document.getElementById("statEmails");
                if (elEmails) elEmails.textContent = data.total_emails_processados_sessao;

                // Atualiza lotes
                const elLotes = document.getElementById("lotesContainer");
                if (elLotes) {
                    const chaves = Object.keys(data.lotes_pendentes || {});
                    if (chaves.length === 0) {
                        elLotes.innerHTML = "<div class='text-muted'>Nenhum lote acumulando no momento (robô livre).</div>";
                    } else {
                        let html = "";
                        for (const k of chaves) {
                            const info = data.lotes_pendentes[k];
                            const docsStr = escapeHtml((info.docs || []).join(", "));
                            html += `
                            <div class='lote-card'>
                                <div><b>Remetente:</b> ${escapeHtml(info.remetente || "")}</div>
                                <div><b>Documentos (${info.qtd_docs || 0}):</b> ${docsStr}</div>
                                <div style='margin-top: 5px;'><b>Tempo para fechar lote:</b> <span class='badge-time'>${info.segundos_restantes}s</span></div>
                            </div>`;
                        }
                        elLotes.innerHTML = html;
                    }
                }

                // Atualiza terminal
                const logs = data.logs_recentes || [];
                if (logs.length > 0) {
                    terminal.innerHTML = logs.map(l => `<div class='${getLogClass(l)}'>${escapeHtml(l)}</div>`).join("");
                    rolarParaOFim();
                }
            } catch (err) {
                console.error("Erro na atualização:", err);
            }
        }

        setInterval(atualizarEmTempoReal, 2000);
    </script>
</body>
</html>"""

        html_out = (
            html_template
            .replace("__TOTAL_EMAILS__", str(len(EMAILS_PROCESSADOS_IDS)))
            .replace("__JANELA_SILENCIO__", str(JANELA_SILENCIO_SEGUNDOS))
            .replace("__LOTES_HTML__", lotes_html)
            .replace("__LINHAS_HTML__", linhas_html)
        )
        return html_out, 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception as e:
        return f"<h3>Erro no painel: {e}</h3>", 500


# ============================================================



# ============================================================
# PROCESSAR UM ARQUIVO
# ============================================================

@app.route(
    "/api/processar-arquivo",
    methods=["POST"]
)
def api_processar_arquivo():

    arquivo = request.files.get(
        "arquivo"
    )


    # --------------------------------------------------------
    # VERIFICA ARQUIVO
    # --------------------------------------------------------

    if (
        arquivo is None
        or not arquivo.filename
    ):

        return jsonify({

            "sucesso": False,

            "erro":
                "Nenhum arquivo foi enviado."

        }), 400


    # --------------------------------------------------------
    # VERIFICA EXTENSÃO
    # --------------------------------------------------------

    if not extensao_permitida(
        arquivo.filename
    ):

        return jsonify({

            "sucesso": False,

            "erro":
                (
                    "Formato não permitido: "
                    + arquivo.filename
                )

        }), 400


    try:

        processor = get_processor()


        resultado = (
            processar_arquivo_temporario(
                arquivo,
                processor,
                1
            )
        )


        linhas = (
            resultado.get(
                "linhas",
                []
            )
        )


        return jsonify({

            "sucesso": True,

            "nome":
                resultado["nome"],

            "linhas":
                linhas,

            "quantidade_linhas":
                len(linhas),

        })


    except Exception as erro:

        import traceback

        traceback.print_exc()


        return jsonify({

            "sucesso": False,

            "erro":
                str(erro),

        }), 500


# ============================================================
# GERAR EXCEL FINAL
# ============================================================

@app.route(
    "/api/gerar-excel",
    methods=["POST"]
)
def api_gerar_excel():

    try:

        dados = request.get_json(
            silent=True
        )


        if not dados:

            return jsonify({

                "sucesso": False,

                "erro":
                    "Nenhum dado foi recebido."

            }), 400


        linhas = (
            dados.get(
                "linhas",
                []
            )
        )


        if not isinstance(
            linhas,
            list
        ):

            return jsonify({

                "sucesso": False,

                "erro":
                    "Formato de dados inválido."

            }), 400


        if not linhas:

            return jsonify({

                "sucesso": False,

                "erro":
                    (
                        "Nenhuma linha foi "
                        "extraída dos arquivos."
                    )

            }), 422


        # ----------------------------------------------------
        # GERA EXCEL
        # ----------------------------------------------------

        excel = criar_excel(
            linhas
        )


        nome_download = (
            "dados_agricolas_"
            + datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )
            + ".xlsx"
        )


        resposta = send_file(

            excel,

            as_attachment=True,

            download_name=(
                nome_download
            ),

            mimetype=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),

        )


        return resposta


    except Exception as erro:

        import traceback

        traceback.print_exc()


        return jsonify({

            "sucesso": False,

            "erro":
                str(erro),

        }), 500


@app.route(
    "/api/processar-e-gerar-excel",
    methods=["POST"]
)
def api_processar_e_gerar_excel():
    import base64

    nome_arquivo = request.headers.get("X-Filename") or request.headers.get("x-filename") or "documento.pdf"
    conteudo_bytes = None

    if "arquivo" in request.files:
        arq = request.files["arquivo"]
        nome_arquivo = arq.filename or nome_arquivo
        conteudo_bytes = arq.read()
    elif request.is_json:
        dados = request.get_json(silent=True) or {}
        nome_arquivo = dados.get("nome", nome_arquivo)
        b64 = dados.get("conteudo") or dados.get("arquivo") or ""
        try:
            conteudo_bytes = base64.b64decode(b64)
        except Exception:
            conteudo_bytes = None
    else:
        conteudo_bytes = request.get_data()

    if not conteudo_bytes:
        return jsonify({
            "sucesso": False,
            "erro": "Nenhum arquivo ou conteúdo binário foi recebido.",
        }), 400

    extensao = os.path.splitext(nome_arquivo)[1].lower() or ".pdf"
    if extensao not in [f".{ext}" for ext in ALLOWED_EXTENSIONS]:
        extensao = ".pdf"

    pasta_temp = tempfile.mkdtemp(prefix="pa_upload_")
    caminho_temp = os.path.join(pasta_temp, f"doc{extensao}")
    resultado_ocr = None

    try:
        with open(caminho_temp, "wb") as f:
            f.write(conteudo_bytes)

        documento_id = (
            datetime.now().strftime("%Y%m%d_%H%M%S")
            + "_"
            + uuid.uuid4().hex[:8]
        )

        processor = get_processor()
        resultado_ocr = processor.processar(caminho_temp, documento_id)

        if not resultado_ocr.get("sucesso"):
            raise RuntimeError(
                resultado_ocr.get("erro", "Erro no processamento do documento.")
            )

        documento = extrair_dados(resultado_ocr)
        documento = validar_documento(documento)
        linhas = extrair_linhas_excel(documento)

        if not linhas:
            return jsonify({
                "sucesso": False,
                "erro": "Nenhum dado ou tabela encontrada no documento.",
            }), 422

        excel = criar_excel(linhas)
        nome_base = os.path.splitext(nome_arquivo)[0]
        nome_download = f"{nome_base}_dados.xlsx"

        return send_file(
            excel,
            as_attachment=True,
            download_name=nome_download,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    except Exception as erro:
        import traceback
        traceback.print_exc()
        return jsonify({
            "sucesso": False,
            "erro": str(erro),
        }), 500

    finally:
        try:
            shutil.rmtree(pasta_temp, ignore_errors=True)
        except Exception:
            pass

        if resultado_ocr:
            pasta_res = resultado_ocr.get("pasta_saida")
            if pasta_res and os.path.exists(pasta_res):
                try:
                    shutil.rmtree(pasta_res, ignore_errors=True)
                except Exception:
                    pass


# ============================================================

# ============================================================
# COMPATIBILIDADE
# ============================================================

@app.route(
    "/api/processar",
    methods=["POST"]
)
def api_processar_compatibilidade():

    """
    Mantém a rota antiga funcionando.

    Porém o novo JavaScript não utiliza essa rota.

    A nova rota /api/processar-arquivo
    envia somente UM arquivo por requisição.
    """

    arquivos = request.files.getlist(
        "arquivos"
    )


    if not arquivos:

        arquivo = request.files.get(
            "arquivo"
        )

        if arquivo is not None:

            arquivos = [arquivo]


    if not arquivos:

        return jsonify({

            "sucesso": False,

            "erro":
                "Nenhum arquivo foi enviado."

        }), 400


    try:

        processor = get_processor()

        todas_linhas = []

        erros = []

        processados = 0


        for indice, arquivo in enumerate(
            arquivos,
            start=1
        ):

            if (
                not arquivo
                or not arquivo.filename
            ):
                continue


            if not extensao_permitida(
                arquivo.filename
            ):

                erros.append({

                    "arquivo":
                        arquivo.filename,

                    "erro":
                        "Formato não permitido."

                })

                continue


            try:

                resultado = (
                    processar_arquivo_temporario(
                        arquivo,
                        processor,
                        indice
                    )
                )


                todas_linhas.extend(
                    resultado["linhas"]
                )


                processados += 1


            except Exception as erro:

                erros.append({

                    "arquivo":
                        arquivo.filename,

                    "erro":
                        str(erro),

                })


        if not todas_linhas:

            return jsonify({

                "sucesso": False,

                "erro":
                    (
                        "Nenhum dado agrícola "
                        "foi extraído."
                    ),

                "erros":
                    erros,

            }), 422


        excel = criar_excel(
            todas_linhas
        )


        nome_download = (
            "dados_agricolas_"
            + datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )
            + ".xlsx"
        )


        resposta = send_file(

            excel,

            as_attachment=True,

            download_name=(
                nome_download
            ),

            mimetype=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),

        )


        resposta.headers[
            "X-Arquivos-Processados"
        ] = str(
            processados
        )


        resposta.headers[
            "X-Linhas-Extraidas"
        ] = str(
            len(todas_linhas)
        )


        if erros:

            resposta.headers[
                "X-Arquivos-Com-Erros"
            ] = str(
                len(erros)
            )


        return resposta


    except Exception as erro:

        import traceback

        traceback.print_exc()


        return jsonify({

            "sucesso": False,

            "erro":
                str(erro),

        }), 500


# ============================================================
# ERRO 413
# ============================================================

@app.errorhandler(413)
def arquivo_muito_grande(_erro):

    return jsonify({

        "sucesso": False,

        "erro":
            (
                "O arquivo individual "
                "ultrapassa o limite de "
                "100 MB."
            )

    }), 413


# ============================================================
# VIGILANTE DE E-MAILS (AUTOMÁTICO)
# ============================================================

def _iniciar_vigilante_se_apropriado():
    if os.environ.get("WERKZEUG_RUN_MAIN") == "false":
        return
    if os.environ.get("DISABLE_EMAIL_WATCHER") == "1":
        print("[APP] Vigilante de e-mails DESATIVADO por configuracao.")
        return
    try:
        import threading
        from services.email_watcher import loop_vigilante_email
        _email_thread = threading.Thread(target=loop_vigilante_email, args=(10,), daemon=True)
        _email_thread.start()
        print("[APP] Vigilante de e-mails iniciado em segundo plano!")
    except Exception as _e:
        print(f"[APP] Falha ao iniciar vigilante de e-mail: {_e}")

_iniciar_vigilante_se_apropriado()

# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":

    print("=" * 70)
    print("IA DOCUMENTOS AGRÍCOLAS")
    print("Modo: múltiplos arquivos -> Excel")
    print("Upload: arquivos processados individualmente")
    print("=" * 70)


    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=debug_mode,
        use_reloader=False,
    )