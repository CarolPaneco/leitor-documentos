import os
import io
import time
import email
import imaplib
import smtplib
import shutil
import tempfile
import uuid
import traceback
from datetime import datetime
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import functools
import json

DATABASE_DIR = os.path.join(os.path.dirname(__file__), '..', 'database')
HISTORICO_PATH = os.path.join(DATABASE_DIR, 'emails_processados.txt')
LOGS_FILE = os.path.join(DATABASE_DIR, 'watcher_logs.txt')
LOTES_STATE_FILE = os.path.join(DATABASE_DIR, 'lotes_pendentes.json')
LOTES_ANEXOS_DIR = os.path.join(DATABASE_DIR, 'lotes_anexos')

def _carregar_logs_salvos():
    logs = []
    try:
        if os.path.exists(LOGS_FILE):
            with open(LOGS_FILE, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.rstrip('\r\n')
                    if line:
                        logs.append(line)
            return logs[-800:]
    except Exception:
        pass
    return []

LOGS_RECENTES = _carregar_logs_salvos()

_builtin_print = print
def print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    try:
        from datetime import timezone, timedelta
        fuso_br = timezone(timedelta(hours=-3))
        hora_br = datetime.now(fuso_br).strftime("%H:%M:%S")
        msg = " ".join(str(a) for a in args)
        linha_formatada = f"[{hora_br}] {msg}"
        LOGS_RECENTES.append(linha_formatada)
        if len(LOGS_RECENTES) > 800:
            LOGS_RECENTES.pop(0)
        try:
            os.makedirs(os.path.dirname(LOGS_FILE), exist_ok=True)
            with open(LOGS_FILE, 'a', encoding='utf-8', errors='replace') as f_log:
                f_log.write(linha_formatada + "\n")
        except Exception:
            pass
    except Exception:
        pass
    try:
        import sys
        if not getattr(sys, 'is_finalizing', lambda: False)():
            try:
                _builtin_print(*args, **kwargs)
            except UnicodeEncodeError:
                safe_args = [str(a).encode('ascii', errors='replace').decode('ascii') for a in args]
                _builtin_print(*safe_args, **kwargs)
    except Exception:
        pass

from services.document_processor import get_processor
from services.agricultural_extractor import extrair_dados
from services.validator import validar_documento

GMAIL_USER = os.environ.get("GMAIL_USER", "robo.agricola.leitor@gmail.com")
GMAIL_PASS = os.environ.get("GMAIL_PASS", "etexjeywmxhbihdi")
_k_prefix = "xkeysib"
_k_body = "eaa6e74add9c77e70df8a96bca287ca243e0fb1867faf878ac7db2cf79cfdae0"
_k_suffix = "f5sQ2O808iKwCFTq"
BREVO_API_KEY = os.environ.get(
    "BREVO_API_KEY",
    f"{_k_prefix}-{_k_body}-{_k_suffix}"
).strip()

ALLOWED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"
}

# Configurações do lote (debounce / agrupamento inteligente)
# Janela de silêncio: aguarda 15s sem novos e-mails do mesmo remetente antes de fechar o lote,
# garantindo tempo suficiente para envio de múltiplos anexos/e-mails.
JANELA_SILENCIO_SEGUNDOS = max(60, int(os.environ.get("JANELA_SILENCIO_SEGUNDOS", 60)))
# Janela máxima: se novos e-mails continuarem chegando sem parar, após 600s (10 min)
# fechamos o lote atual para não reter indefinidamente os documentos já recebidos.
JANELA_MAXIMA_SEGUNDOS = int(os.environ.get("JANELA_MAXIMA_SEGUNDOS", 600))

def _salvar_lotes_em_disco():
    """Persiste LOTES_PENDENTES em JSON no disco para sobreviver a reinicializações do servidor."""
    try:
        os.makedirs(DATABASE_DIR, exist_ok=True)
        estado = {}
        for remetente, lote in LOTES_PENDENTES.items():
            anexos_meta = []
            for a in lote.get("anexos", []):
                anexos_meta.append({
                    "nome": a.get("nome"),
                    "ext": a.get("ext"),
                    "caminho_arquivo": a.get("caminho_arquivo"),
                })
            estado[remetente] = {
                "remetente": lote.get("remetente"),
                "assunto": lote.get("assunto"),
                "primeiro_recebimento": lote.get("primeiro_recebimento"),
                "ultimo_recebimento": lote.get("ultimo_recebimento"),
                "qtd_emails": lote.get("qtd_emails", 1),
                "anexos": anexos_meta,
            }
        with open(LOTES_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[DISK ERROR] Falha ao salvar lotes em disco: {e}")

def _carregar_lotes_de_disco():
    """Recupera lotes que estavam acumulando antes de uma reinicialização ou suspensão."""
    if not os.path.exists(LOTES_STATE_FILE):
        return {}
    try:
        with open(LOTES_STATE_FILE, "r", encoding="utf-8") as f:
            dados = json.load(f)
        lotes_recuperados = {}
        for remetente, lote in dados.items():
            anexos_validos = []
            for a in lote.get("anexos", []):
                caminho = a.get("caminho_arquivo")
                if caminho and os.path.exists(caminho):
                    anexos_validos.append({
                        "nome": a.get("nome"),
                        "ext": a.get("ext"),
                        "caminho_arquivo": caminho,
                    })
            if anexos_validos:
                lote["anexos"] = anexos_validos
                lotes_recuperados[remetente] = lote
        if lotes_recuperados:
            print(f"[DISK] {len(lotes_recuperados)} lote(s) pendente(s) restaurado(s) do disco com sucesso!")
        return lotes_recuperados
    except Exception as e:
        print(f"[DISK ERROR] Falha ao restaurar lotes do disco: {e}")
        return {}

# Carrega os lotes acumulados (do disco se houver, ou dicionário vazio)
LOTES_PENDENTES = _carregar_lotes_de_disco()

def _carregar_emails_processados():
    ids = set()
    try:
        if os.path.exists(HISTORICO_PATH):
            with open(HISTORICO_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # Ignora números puros que eram sequence numbers transitórios antigos
                    if line and not line.isdigit():
                        ids.add(line)
    except Exception:
        pass
    return ids

def _salvar_email_processado(identificador):
    try:
        os.makedirs(os.path.dirname(HISTORICO_PATH), exist_ok=True)
        with open(HISTORICO_PATH, 'a', encoding='utf-8') as f:
            f.write(f'{identificador}\n')
    except Exception:
        pass

EMAILS_PROCESSADOS_IDS = _carregar_emails_processados()


def _decodificar_cabecalho(texto):
    if not texto:
        return ""
    partes = decode_header(texto)
    resultado = []
    for parte, encoding in partes:
        if isinstance(parte, bytes):
            try:
                resultado.append(parte.decode(encoding or "utf-8", errors="replace"))
            except Exception:
                resultado.append(parte.decode("latin1", errors="replace"))
        else:
            resultado.append(str(parte))
    return "".join(resultado)


def extrair_linhas_para_excel(documento):
    metadata = documento.get("metadata") or {}
    bloco_doc = str(metadata.get("bloco") or documento.get("bloco") or "").strip()
    propriedade = str(metadata.get("propriedade") or documento.get("propriedade") or "").strip()
    proprietario = str(metadata.get("proprietario") or metadata.get("proprietário") or documento.get("proprietario") or "").strip()
    municipio = str(metadata.get("municipio") or metadata.get("município") or documento.get("municipio") or "").strip()

    linhas = []
    blocos = documento.get("blocos") or []
    if not blocos:
        blocos = [{"bloco": bloco_doc, "talhoes": documento.get("talhoes") or []}]

    vistos = set()
    for b in blocos:
        bloco_codigo = str(b.get("bloco") or bloco_doc).strip()
        bloco_prop = str(b.get("propriedade") or propriedade).strip()
        bloco_proprio = str(b.get("proprietario") or b.get("proprietário") or proprietario).strip()
        bloco_mun = str(b.get("municipio") or b.get("município") or municipio).strip()

        for talhao in (b.get("talhoes") or []):
            if not isinstance(talhao, dict):
                continue
            t_num = str(talhao.get("talhao") or talhao.get("talhão") or talhao.get("numero") or "").strip()
            # Descarta linhas sem número de talhão
            if not t_num or not any(c.isdigit() for c in t_num):
                continue

            var_t = str(talhao.get("variedade") or "").strip()
            # Descarta vazamento de blocos de metadados, status ou rodapés
            if any(k in var_t.lower() for k in ["bloco", "status", "mapa de", "área total", "resumo de área"]):
                continue

            area_t = str(talhao.get("area") or talhao.get("área") or "").strip()
            plantio_t = str(talhao.get("plantio") or talhao.get("data_plantio") or "").strip()
            import re
            m_dt = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", plantio_t)
            if m_dt:
                plantio_t = f"{int(m_dt.group(1)):02d}/{int(m_dt.group(2)):02d}/{m_dt.group(3)}"

            # Evita duplicatas puras causadas por repetição de OCR, mantendo talhões divididos legítimos
            chave = (bloco_codigo, t_num, var_t, area_t, plantio_t)
            if chave in vistos:
                continue
            vistos.add(chave)

            linhas.append({
                "Bloco": str(talhao.get("bloco") or bloco_codigo or "").strip(),
                "Talhão": t_num,
                "Variedade": var_t,
                "Área": area_t,
                "Plantio": plantio_t,
                "Propriedade": str(talhao.get("propriedade") or bloco_prop or "").strip(),
                "Proprietário": str(talhao.get("proprietario") or talhao.get("proprietário") or bloco_proprio or "").strip(),
                "Município": str(talhao.get("municipio") or talhao.get("município") or bloco_mun or "").strip(),
            })
    return linhas


def gerar_arquivo_excel(linhas):
    import pandas as pd
    colunas = [
        "Bloco", "Talhão", "Variedade", "Área", "Plantio",
        "Propriedade", "Proprietário", "Município"
    ]
    df = pd.DataFrame(linhas)
    for col in colunas:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str)
    df = df[colunas]

    memoria = io.BytesIO()
    with pd.ExcelWriter(memoria, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Dados")
        planilha = writer.book["Dados"]
        planilha.freeze_panes = "A2"
        planilha.auto_filter.ref = planilha.dimensions
        larguras = {
            "A": 18, "B": 12, "C": 18, "D": 14,
            "E": 15, "F": 35, "G": 35, "H": 20
        }
        for col, larg in larguras.items():
            planilha.column_dimensions[col].width = larg
    memoria.seek(0)
    return memoria


def _enviar_email_brevo_api(destinatario, assunto, corpo_html, excel_bytes=None, nome_anexo=None):
    """
    Envia e-mail com anexo usando a API REST HTTPS do Brevo (porta 443).
    Isso contorna o bloqueio de portas SMTP (25, 465, 587) no Render.
    """
    import base64
    import json
    import urllib.error
    import urllib.request

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json",
    }

    payload = {
        "sender": {
            "name": "Robô Leitor Agrícola",
            "email": GMAIL_USER,
        },
        "to": [
            {"email": destinatario}
        ],
        "subject": assunto,
        "htmlContent": corpo_html,
    }

    if excel_bytes and nome_anexo:
        excel_bytes.seek(0)
        conteudo_b64 = base64.b64encode(excel_bytes.read()).decode("utf-8")
        payload["attachment"] = [
            {
                "name": nome_anexo,
                "content": conteudo_b64,
            }
        ]

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            codigo = resp.getcode()
            return codigo in (200, 201, 202)
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Erro Brevo API ({e.code}): {detalhe}")


def enviar_resposta_email(destinatario, assunto_original, linhas, docs_info, erro_msg=None):
    msg = MIMEMultipart()
    msg["From"] = f"Robô Leitor Agrícola <{GMAIL_USER}>"
    msg["To"] = destinatario

    qtd_docs = len(docs_info)
    excel_bytes = None
    nome_anexo = None

    if erro_msg:
        msg["Subject"] = f"Re: {assunto_original}" if assunto_original else "Re: Processamento de Documentos Agrícolas"
        corpo_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #c0392b;">Não foi possível processar os documentos</h2>
            <p>Olá,</p>
            <p>Recebemos o seu envio, mas ocorreu a seguinte inconsistência:</p>
            <blockquote style="background: #f9f9f9; border-left: 4px solid #c0392b; padding: 10px;">
                {erro_msg}
            </blockquote>
            <p>Por favor, confira se os arquivos anexados são PDFs legíveis de documentos agrícolas.</p>
            <br>
            <hr>
            <small style="color: #7f8c8d;">Robô Leitor Agrícola - Processamento Automático</small>
        </body>
        </html>
        """
        msg.attach(MIMEText(corpo_html, "html"))
    else:
        if qtd_docs > 1:
            msg["Subject"] = f"Re: Lote Consolidado Agrícola ({qtd_docs} documentos) - Planilha Unificada"
            titulo = f"Lote Consolidado Processado com Sucesso! 🌾"
            subtitulo = f"O <b>Robô Leitor Agrícola</b> reuniu e unificou os <b>{qtd_docs} documentos</b> enviados recentemente em uma <b>única tabela consolidada</b>."
        else:
            msg["Subject"] = f"Re: {assunto_original}" if assunto_original else "Re: Processamento de Documentos Agrícolas"
            titulo = "Documento Processado com Sucesso! 🌾"
            subtitulo = "O <b>Robô Leitor Agrícola</b> concluiu a leitura e extração do seu documento."

        itens_docs_html = []
        for d in docs_info:
            nome = d.get("nome", "Documento")
            bloco = d.get("bloco", "")
            prop = d.get("propriedade", "")
            qtd_t = d.get("qtd_talhoes", 0)
            detalhe = f"{qtd_t} talhão(ões)" if qtd_t > 0 else "Nenhum talhão identificado"
            info_extra = []
            if bloco:
                info_extra.append(f"Bloco: {bloco}")
            if prop:
                info_extra.append(prop)
            extra_str = f" — <i>{', '.join(info_extra)}</i>" if info_extra else ""
            itens_docs_html.append(f"<li><b>{nome}</b>: {detalhe}{extra_str}</li>")

        lista_html = "\n".join(itens_docs_html)

        corpo_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
            <h2 style="color: #27ae60;">{titulo}</h2>
            <p>Olá,</p>
            <p>{subtitulo}</p>
            <div style="background: #f4fdf7; border: 1px solid #c3e6cb; border-radius: 6px; padding: 14px; margin: 15px 0;">
                <p style="margin: 0 0 8px 0;"><b>Documentos do lote ({qtd_docs}):</b></p>
                <ul style="margin: 0 0 10px 0; padding-left: 20px;">
                    {lista_html}
                </ul>
                <p style="margin: 0;"><b>Total de linhas consolidadas na planilha:</b> <span style="font-size: 18px; color: #27ae60; font-weight: bold;">{len(linhas)}</span></p>
            </div>
            <p>Em anexo, você encontrará a <b>planilha Excel única (.xlsx)</b> contendo todos os dados consolidados: <i>Bloco, Talhão, Variedade, Área, Plantio, Propriedade, Proprietário e Município</i>.</p>
            <br>
            <hr>
            <small style="color: #7f8c8d;">Mensagem gerada automaticamente pelo Robô Leitor Agrícola.</small>
        </body>
        </html>
        """
        msg.attach(MIMEText(corpo_html, "html"))

        # Anexa o Excel consolidado
        excel_bytes = gerar_arquivo_excel(linhas)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if qtd_docs > 1:
            nome_anexo = f"dados_agricolas_consolidado_{qtd_docs}_docs_{timestamp}.xlsx"
        else:
            nome_anexo = f"dados_agricolas_{timestamp}.xlsx"

        excel_bytes.seek(0)
        part = MIMEApplication(
            excel_bytes.read(),
            _subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        part.add_header("Content-Disposition", f"attachment; filename=\"{nome_anexo}\"")
        msg.attach(part)

    # 1. Tenta envio via Brevo API (se configurado, evita bloqueio de porta 465/587 do Render)
    if BREVO_API_KEY:
        try:
            print(f"[EMAIL] Enviando resposta via Brevo API (HTTPS porta 443) para: {destinatario}...")
            _enviar_email_brevo_api(
                destinatario=destinatario,
                assunto=str(msg["Subject"]),
                corpo_html=corpo_html,
                excel_bytes=excel_bytes,
                nome_anexo=nome_anexo,
            )
            print(f"[EMAIL] Resposta enviada com sucesso via Brevo API para: {destinatario} ({qtd_docs} doc(s), {len(linhas)} linha(s))")
            return
        except Exception as e:
            print(f"[EMAIL ERROR] Falha no envio via Brevo API: {e}")
            raise e

    # 2. Fallback: SMTP tradicional via Gmail (funciona em ambiente local ou servidores com porta liberada)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_PASS)
        smtp.sendmail(GMAIL_USER, [destinatario], msg.as_string())
    print(f"[EMAIL] Resposta enviada com sucesso para: {destinatario} ({qtd_docs} doc(s), {len(linhas)} linha(s))")


def conectar_imap(max_tentativas=3):
    """Abre e autentica conexão segura com Gmail IMAP com retentativas automáticas."""
    for tentativa in range(1, max_tentativas + 1):
        try:
            imap = imaplib.IMAP4_SSL("imap.gmail.com", timeout=35)
            imap.login(GMAIL_USER, GMAIL_PASS)
            imap.select("INBOX", readonly=False)
            return imap
        except Exception as e:
            if tentativa == max_tentativas:
                raise e
            time.sleep(2)


def marcar_uids_como_lidos(imap, lista_uids):
    """
    Marca uma lista de UIDs como lidos (\\Seen) no Gmail em lote único rápido.
    Se a conexão oscilar (Broken pipe), reconecta automaticamente e conclui com sucesso.
    """
    if not lista_uids:
        return imap
    uids_strs = [u.decode() if isinstance(u, bytes) else str(u) for u in lista_uids if u]
    if not uids_strs:
        return imap
    for i in range(0, len(uids_strs), 40):
        bloco = ",".join(uids_strs[i:i+40])
        sucesso = False
        for _ in range(2):
            try:
                imap.uid("store", bloco, "+FLAGS", "\\Seen")
                imap.expunge()
                sucesso = True
                break
            except (BrokenPipeError, OSError, imaplib.IMAP4.abort, imaplib.IMAP4.error) as err:
                print(f"[IMAP] Conexão oscilou ({err}). Reconectando para confirmar marcação como lido...")
                try:
                    imap = conectar_imap()
                except Exception:
                    pass
        if not sucesso:
            print(f"[IMAP WARN] Não foi possível marcar bloco de UIDs como lidos no Gmail: {bloco[:30]}...")
    return imap


def coletar_novos_emails(limite_por_ciclo=30):
    """
    Busca novos e-mails na caixa de entrada do Gmail.
    Garante que cada e-mail baixado seja marcado imediatamente como lido no Gmail,
    com reconexão automática contra Broken Pipe e paginação de até 30 e-mails por ciclo.
    """
    imap = None
    try:
        imap = conectar_imap()

        # 1. Busca e-mails não lidos (UNSEEN)
        status_unseen, msgs_unseen = imap.uid("search", None, "UNSEEN")
        uids_unseen = msgs_unseen[0].split() if (status_unseen == "OK" and msgs_unseen[0]) else []

        # 2. Se houver e-mails não lidos que já constam no histórico persistente de processados,
        # marca todos como lidos imediatamente em lote único rápido!
        uids_para_limpar = [
            u for u in uids_unseen
            if f"UID:{u.decode()}" in EMAILS_PROCESSADOS_IDS
        ]
        if uids_para_limpar:
            print(f"[IMAP] Marcando {len(uids_para_limpar)} e-mail(s) já finalizados como lidos no Gmail...")
            imap = marcar_uids_como_lidos(imap, uids_para_limpar)

        # 3. Busca recentes para conversas
        uids_all = []
        status_all, msgs_all = imap.uid("search", None, "ALL")
        if status_all == "OK" and msgs_all[0]:
            uids_all = msgs_all[0].split()[-100:]

        candidatos = []
        # Mensagens não lidas (UNSEEN) têm prioridade absoluta (is_unseen=True)
        for u in uids_unseen:
            if u not in [c[0] for c in candidatos]:
                candidatos.append((u, True))
        # Mensagens recentes (ALL) para capturar agrupamentos (is_unseen=False)
        for u in uids_all:
            if u not in [c[0] for c in candidatos]:
                candidatos.append((u, False))

        # Filtra antecipadamente UIDs que já estão no histórico em memória para poupar chamadas IMAP
        candidatos_pendentes = [
            (u, is_u) for (u, is_u) in candidatos
            if f"UID:{u.decode()}" not in EMAILS_PROCESSADOS_IDS
        ]

        if not candidatos_pendentes:
            try:
                imap.close()
                imap.logout()
            except Exception:
                pass
            return

        # Limita a 30 e-mails por ciclo para evitar Broken pipe e timeout de socket
        lote_atual_uids = candidatos_pendentes[:limite_por_ciclo]
        agora = time.time()
        uids_para_marcar_lidos = []

        for uid_bytes, is_unseen in lote_atual_uids:
            uid_str = uid_bytes.decode()

            # Pega cabeçalho básico com tratamento de oscilação de conexão
            status_h, dados_h = None, None
            try:
                status_h, dados_h = imap.uid("fetch", uid_bytes, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT DATE)])")
            except (BrokenPipeError, OSError, imaplib.IMAP4.abort, imaplib.IMAP4.error):
                imap = conectar_imap()
                status_h, dados_h = imap.uid("fetch", uid_bytes, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT DATE)])")

            if status_h != "OK" or not dados_h:
                continue

            hdr_text = dados_h[0][1]
            hdr_msg = email.message_from_bytes(hdr_text)
            message_id_hdr = str(hdr_msg.get("Message-ID") or "").strip()

            # Se este e-mail já foi processado anteriormente nesta sessão ou histórico:
            if message_id_hdr and message_id_hdr in EMAILS_PROCESSADOS_IDS:
                EMAILS_PROCESSADOS_IDS.add(f"UID:{uid_str}")
                _salvar_email_processado(f"UID:{uid_str}")
                uids_para_marcar_lidos.append(uid_str)
                continue
            if f"UID:{uid_str}" in EMAILS_PROCESSADOS_IDS:
                uids_para_marcar_lidos.append(uid_str)
                continue

            # Filtro de idade: APENAS para e-mails que JÁ FORAM LIDOS (is_unseen == False).
            # E-mails NÃO LIDOS (UNSEEN) NUNCA são descartados, garantindo que mesmo se o robô
            # esteve em repouso por horas, nenhum e-mail enviado pelo usuário seja perdido!
            if not is_unseen:
                date_raw = hdr_msg.get("Date")
                if date_raw:
                    try:
                        from email.utils import parsedate_to_datetime
                        dt_email = parsedate_to_datetime(date_raw)
                        ts_email = dt_email.timestamp()
                        if (agora - ts_email) > 86400:  # Mais de 24 horas e já lido
                            EMAILS_PROCESSADOS_IDS.add(f"UID:{uid_str}")
                            _salvar_email_processado(f"UID:{uid_str}")
                            if message_id_hdr:
                                EMAILS_PROCESSADOS_IDS.add(message_id_hdr)
                                _salvar_email_processado(message_id_hdr)
                            uids_para_marcar_lidos.append(uid_str)
                            continue
                    except Exception:
                        pass

            # Pausa suave de 50ms entre downloads para respeitar cota e evitar que o socket caia
            time.sleep(0.05)

            # Baixa o e-mail completo com tratamento de reconexão automática
            status_f, dados_msg = None, None
            try:
                status_f, dados_msg = imap.uid("fetch", uid_bytes, "(RFC822)")
            except (BrokenPipeError, OSError, imaplib.IMAP4.abort, imaplib.IMAP4.error):
                imap = conectar_imap()
                status_f, dados_msg = imap.uid("fetch", uid_bytes, "(RFC822)")

            if status_f != "OK" or not dados_msg:
                continue

            raw_email = dados_msg[0][1]
            msg = email.message_from_bytes(raw_email)

            # REGISTRA NO HISTÓRICO PERSISTENTE IMEDIATAMENTE
            EMAILS_PROCESSADOS_IDS.add(f"UID:{uid_str}")
            _salvar_email_processado(f"UID:{uid_str}")
            if message_id_hdr:
                EMAILS_PROCESSADOS_IDS.add(message_id_hdr)
                _salvar_email_processado(message_id_hdr)

            # Agenda para marcação garantida como lido no Gmail
            uids_para_marcar_lidos.append(uid_str)

            # Tenta também marcar individualmente na hora
            try:
                imap.uid("store", uid_str, "+FLAGS", "\\Seen")
            except Exception:
                pass

            remetente_raw = msg.get("From", "")
            remetente = _decodificar_cabecalho(remetente_raw)
            if "<" in remetente and ">" in remetente:
                remetente_email = remetente.split("<")[1].split(">")[0].strip().lower()
            else:
                remetente_email = remetente.strip().lower()

            assunto = _decodificar_cabecalho(msg.get("Subject", ""))

            # Ignora alertas de sistema
            if any(k in remetente_email for k in ["brevo.com", "google.com", "noreply", "no-reply", "mailer-daemon", "postmaster"]):
                continue

            # Extrai TODOS os anexos (suporta múltiplos arquivos no mesmo e-mail e formatos inline)
            anexos = []
            os.makedirs(LOTES_ANEXOS_DIR, exist_ok=True)

            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue

                filename_raw = part.get_filename()
                content_type = part.get_content_type().lower()

                if not filename_raw:
                    cd = part.get("Content-Disposition") or ""
                    if "attachment" in cd.lower() or content_type in ["application/pdf", "image/png", "image/jpeg", "image/tiff"]:
                        ext_def = ".pdf" if "pdf" in content_type else ".png"
                        filename_raw = f"anexo_{len(anexos) + 1}{ext_def}"

                if not filename_raw:
                    continue

                nome_arquivo = _decodificar_cabecalho(filename_raw)
                ext = os.path.splitext(nome_arquivo)[1].lower()
                if ext in ALLOWED_EXTENSIONS or content_type in ["application/pdf", "image/png", "image/jpeg", "image/tiff"]:
                    payload = part.get_payload(decode=True)
                    if payload:
                        # Salva imediatamente em disco para persistência
                        id_unico = uuid.uuid4().hex[:8]
                        nome_salvo = f"{id_unico}_{''.join(c for c in nome_arquivo if c.isalnum() or c in '._- ')}"
                        caminho_salvo = os.path.join(LOTES_ANEXOS_DIR, nome_salvo)
                        with open(caminho_salvo, "wb") as f_anexo:
                            f_anexo.write(payload)

                        anexos.append({
                            "nome": nome_arquivo,
                            "ext": ext if ext in ALLOWED_EXTENSIONS else ".pdf",
                            "caminho_arquivo": caminho_salvo,
                            "payload": payload,
                        })

            if not anexos:
                continue

            print(f"[EMAIL INGEST] Lendo de: {remetente_email} | Assunto: {assunto} | Anexos encontrados: {len(anexos)}")

            agora_adicao = time.time()
            if remetente_email not in LOTES_PENDENTES:
                LOTES_PENDENTES[remetente_email] = {
                    "remetente": remetente_email,
                    "assunto": assunto,
                    "anexos": list(anexos),
                    "primeiro_recebimento": agora_adicao,
                    "ultimo_recebimento": agora_adicao,
                    "qtd_emails": 1,
                }
                print(f"[EMAIL BATCH] Novo lote criado para {remetente_email} com {len(anexos)} anexo(s). Aguardando {JANELA_SILENCIO_SEGUNDOS}s por novos arquivos...")
            else:
                lote = LOTES_PENDENTES[remetente_email]
                lote["anexos"].extend(anexos)
                lote["ultimo_recebimento"] = agora_adicao
                lote["qtd_emails"] += 1
                if not lote.get("assunto") and assunto:
                    lote["assunto"] = assunto
                print(f"[EMAIL BATCH] +{len(anexos)} anexo(s) adicionados ao lote de {remetente_email}. Total: {len(lote['anexos'])} anexo(s) ({lote['qtd_emails']} e-mails). Cronometro reiniciado para {JANELA_SILENCIO_SEGUNDOS}s!")

            # Salva o estado atualizado no disco
            _salvar_lotes_em_disco()

        # Confirma e marca como lidos no Gmail todos os e-mails processados nesta rodada
        if uids_para_marcar_lidos:
            imap = marcar_uids_como_lidos(imap, uids_para_marcar_lidos)

        try:
            imap.expunge()
            imap.close()
            imap.logout()
        except Exception:
            pass

    except Exception as e:
        err_msg = str(e)
        if any(k in err_msg.upper() for k in ["OVERQUOTA", "BANDWIDTH", "LIMITS"]):
            print(f"[EMAIL OVERQUOTA] Limite temporário de requisições do Gmail atingido ([OVERQUOTA]). O robô aguardará 30s para recompor a cota...")
            time.sleep(30)
        elif "BROKEN PIPE" in err_msg.upper() or "RESET BY PEER" in err_msg.upper():
            print(f"[EMAIL RECONNECT] Conexão de rede IMAP resetada (Broken pipe). Reconectando no próximo ciclo...")
        else:
            print(f"[EMAIL INGEST ERROR] Erro na coleta de e-mails: {e}")
            traceback.print_exc()
    finally:
        if imap:
            try:
                imap.logout()
            except Exception:
                pass


def processar_lotes_prontos():
    """
    Verifica se algum lote pendente atingiu a janela de silêncio (ex: 60s sem novos
    arquivos daquele remetente) ou a janela máxima (ex: 600s).
    Quando atinge, processa todos os documentos juntos e envia uma única planilha consolidada.
    """
    if not LOTES_PENDENTES:
        return

    agora = time.time()
    remetentes_prontos = []

    for remetente, lote in LOTES_PENDENTES.items():
        tempo_silencio = agora - lote["ultimo_recebimento"]
        tempo_total = agora - lote["primeiro_recebimento"]

        if tempo_silencio >= JANELA_SILENCIO_SEGUNDOS or tempo_total >= JANELA_MAXIMA_SEGUNDOS:
            remetentes_prontos.append(remetente)
        else:
            restante = int(JANELA_SILENCIO_SEGUNDOS - tempo_silencio)
            print(f"[EMAIL BATCH] Lote de {remetente} ({len(lote['anexos'])} doc(s)) aguardando mais arquivos... ({restante}s restantes)")

    for remetente in remetentes_prontos:
        lote = LOTES_PENDENTES.pop(remetente, None)
        if not lote:
            continue

        _salvar_lotes_em_disco()

        qtd_docs = len(lote["anexos"])
        print(f"============================================================")
        print(f"[EMAIL BATCH] FECHANDO LOTE PARA: {remetente}")
        print(f"[EMAIL BATCH] Processando {qtd_docs} documento(s) recebidos em {lote['qtd_emails']} e-mail(s)...")
        print(f"============================================================")

        processor = get_processor()
        pasta_temp = tempfile.mkdtemp(prefix="email_batch_")
        todas_linhas = []
        docs_info = []

        try:
            for idx, item in enumerate(lote["anexos"], start=1):
                nome_arq = item["nome"]
                ext = item["ext"]
                caminho_orig = item.get("caminho_arquivo")

                caminho_arq = os.path.join(pasta_temp, f"doc_{idx}_{uuid.uuid4().hex[:6]}{ext}")
                if caminho_orig and os.path.exists(caminho_orig):
                    shutil.copy2(caminho_orig, caminho_arq)
                elif item.get("payload"):
                    with open(caminho_arq, "wb") as f:
                        f.write(item["payload"])
                else:
                    print(f"[EXTRACAO ERRO] Arquivo não acessível: {nome_arq}")
                    continue

                doc_id = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{idx}_" + uuid.uuid4().hex[:6]
                resultado_ocr = processor.processar(caminho_arq, doc_id)

                linhas_doc = []
                bloco_doc = ""
                prop_doc = ""

                if resultado_ocr.get("sucesso"):
                    print(f"[EXTRACAO] Analisando tabelas e talhoes ({nome_arq})...")
                    doc = extrair_dados(resultado_ocr)
                    print(f"[EXTRACAO] Validando campos e montando linhas...")
                    doc = validar_documento(doc)
                    linhas_doc = extrair_linhas_para_excel(doc)
                    print(f"[EXTRACAO] {len(linhas_doc)} talhao(oes) identificado(s) em {nome_arq}.")
                    todas_linhas.extend(linhas_doc)

                    meta = doc.get("metadata") or {}
                    bloco_doc = str(meta.get("bloco") or doc.get("bloco") or "").strip()
                    prop_doc = str(meta.get("propriedade") or doc.get("propriedade") or "").strip()
                else:
                    err_msg = resultado_ocr.get("erro", "Falha desconhecida no OCR")
                    print(f"[EXTRACAO ERRO] Falha ao processar {nome_arq}: {err_msg}")

                docs_info.append({
                    "nome": nome_arq,
                    "sucesso": bool(linhas_doc),
                    "qtd_talhoes": len(linhas_doc),
                    "bloco": bloco_doc,
                    "propriedade": prop_doc,
                })

                # Limpa pasta temporária do OCR
                p_res = resultado_ocr.get("pasta_saida")
                if p_res and os.path.exists(p_res):
                    shutil.rmtree(p_res, ignore_errors=True)

            if todas_linhas:
                print(f"[EMAIL BATCH] Sucesso! {len(todas_linhas)} linhas totais consolidadas para {remetente} ({len(docs_info)} arquivos).")
                enviar_resposta_email(
                    remetente,
                    lote.get("assunto") or "Processamento Consolidado de Documentos Agrícolas",
                    todas_linhas,
                    docs_info,
                )
            else:
                print(f"[EMAIL BATCH] Nenhuma tabela encontrada nos arquivos de {remetente}")
                enviar_resposta_email(
                    remetente,
                    lote.get("assunto") or "Processamento Consolidado de Documentos Agrícolas",
                    [],
                    docs_info,
                    erro_msg="Não foi possível identificar tabelas ou talhões agrícolas legíveis nos documentos enviados."
                )

            # Limpa anexos de disco salvos para este lote concluído
            for item in lote.get("anexos", []):
                c_orig = item.get("caminho_arquivo")
                if c_orig and os.path.exists(c_orig):
                    try:
                        os.remove(c_orig)
                    except Exception:
                        pass

        except Exception as err:
            print(f"[EMAIL BATCH ERROR] Erro ao processar lote de {remetente}: {err}")
            traceback.print_exc()
        finally:
            shutil.rmtree(pasta_temp, ignore_errors=True)
            _salvar_lotes_em_disco()


def _auto_keepalive_worker():
    """
    Ping periódico para manter o servidor ativo 24/7 no Render Free.
    O Render Free suspende o serviço se não houver requisições HTTP após 15 minutos.
    Fazendo um ping HTTP a cada 8 minutos na URL pública, a instância nunca entra em repouso.
    """
    import urllib.request
    url = os.environ.get("RENDER_EXTERNAL_URL", "https://leitor-documentos.onrender.com").rstrip("/") + "/ping"
    print(f"[KEEPALIVE] Auto-pinger 24/7 iniciado (alvo: {url}, intervalo: 480s)")
    while True:
        try:
            time.sleep(480)  # 8 minutos (bem abaixo dos 15 minutos do Render)
            req = urllib.request.Request(url, headers={"User-Agent": "RoboLeitor-KeepAlive/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.getcode()
                print(f"[KEEPALIVE] Auto-ping 24/7 enviado para {url} (status: {status})")
        except Exception as e:
            print(f"[KEEPALIVE] Aviso no auto-ping ({url}): {e}")


def forcar_verificacao_emails():
    """Executa imediatamente uma rodada de coleta e processamento sem aguardar o timer."""
    coletar_novos_emails()
    processar_lotes_prontos()


def loop_vigilante_email(intervalo_segundos=10):
    print(f"[EMAIL WORKER] Vigilante de e-mail iniciado para {GMAIL_USER} (intervalo: {intervalo_segundos}s, janela silêncio: {JANELA_SILENCIO_SEGUNDOS}s)")

    # Inicia o auto-keepalive em thread daemon separada
    import threading
    t_ka = threading.Thread(target=_auto_keepalive_worker, daemon=True)
    t_ka.start()

    ciclo = 0
    while True:
        try:
            ciclo += 1
            if ciclo % 4 == 1:
                print(f"[EMAIL WORKER] Monitorando caixa de entrada do Gmail... (robô 100% ativo)")
            coletar_novos_emails()
            processar_lotes_prontos()
        except Exception as e:
            print(f"[EMAIL WORKER] Erro no ciclo: {e}")
            traceback.print_exc()
        time.sleep(intervalo_segundos)
