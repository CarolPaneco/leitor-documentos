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

from services.document_processor import get_processor
from services.agricultural_extractor import extrair_dados
from services.validator import validar_documento

GMAIL_USER = os.environ.get("GMAIL_USER", "robo.agricola.leitor@gmail.com")
GMAIL_PASS = os.environ.get("GMAIL_PASS", "etexjeywmxhbihdi")

ALLOWED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"
}

# Configurações do lote (debounce / agrupamento inteligente)
# Janela de silêncio: se nenhum novo e-mail chegar do mesmo remetente em 45 segundos,
# consideramos que o usuário concluiu o upload do lote e processamos todos os arquivos juntos.
JANELA_SILENCIO_SEGUNDOS = int(os.environ.get("JANELA_SILENCIO_SEGUNDOS", 45))
# Janela máxima: se novos e-mails continuarem chegando sem parar, após 180s (3 min)
# fechamos o lote atual para não reter indefinidamente os documentos já recebidos.
JANELA_MAXIMA_SEGUNDOS = int(os.environ.get("JANELA_MAXIMA_SEGUNDOS", 180))

# Dicionário em memória com os lotes acumulados
# Chave: remetente_email (minúsculas)
LOTES_PENDENTES = {}


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
    proprietario = str(metadata.get("proprietario") or documento.get("proprietario") or "").strip()
    municipio = str(metadata.get("municipio") or documento.get("municipio") or "").strip()

    linhas = []
    blocos = documento.get("blocos") or []
    if not blocos:
        blocos = [{"bloco": bloco_doc, "talhoes": documento.get("talhoes") or []}]

    for b in blocos:
        bloco_codigo = str(b.get("bloco") or bloco_doc).strip()
        bloco_prop = str(b.get("propriedade") or propriedade).strip()
        bloco_proprio = str(b.get("proprietario") or proprietario).strip()
        bloco_mun = str(b.get("municipio") or municipio).strip()

        for talhao in (b.get("talhoes") or []):
            linhas.append({
                "Bloco": bloco_codigo,
                "Talhão": str(talhao.get("talhao") or "").strip(),
                "Variedade": str(talhao.get("variedade") or "").strip(),
                "Área": str(talhao.get("area") or "").strip(),
                "Plantio": str(talhao.get("plantio") or "").strip(),
                "Propriedade": str(talhao.get("propriedade") or bloco_prop).strip(),
                "Proprietário": str(talhao.get("proprietario") or bloco_proprio).strip(),
                "Município": str(talhao.get("municipio") or bloco_mun).strip(),
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


def enviar_resposta_email(destinatario, assunto_original, linhas, docs_info, erro_msg=None):
    msg = MIMEMultipart()
    msg["From"] = f"Robô Leitor Agrícola <{GMAIL_USER}>"
    msg["To"] = destinatario

    qtd_docs = len(docs_info)
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

        part = MIMEApplication(
            excel_bytes.read(),
            _subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        part.add_header("Content-Disposition", f"attachment; filename=\"{nome_anexo}\"")
        msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_PASS)
        smtp.sendmail(GMAIL_USER, [destinatario], msg.as_string())
    print(f"[EMAIL] Resposta enviada com sucesso para: {destinatario} ({qtd_docs} doc(s), {len(linhas)} linha(s))")


def coletar_novos_emails():
    """
    Busca novos e-mails não lidos na caixa de entrada do Gmail e adiciona os anexos
    ao lote pendente do remetente.
    """
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(GMAIL_USER, GMAIL_PASS)
        imap.select("INBOX")

        status, mensagens = imap.search(None, "UNSEEN")
        if status != "OK" or not mensagens[0]:
            imap.logout()
            return

        ids = mensagens[0].split()
        if not ids:
            imap.logout()
            return

        print(f"[EMAIL INGEST] {len(ids)} novo(s) e-mail(s) detectado(s)!")
        agora = time.time()

        for msg_id in ids:
            status_f, dados_msg = imap.fetch(msg_id, "(RFC822)")
            if status_f != "OK" or not dados_msg:
                continue

            raw_email = dados_msg[0][1]
            msg = email.message_from_bytes(raw_email)

            remetente_raw = msg.get("From", "")
            remetente = _decodificar_cabecalho(remetente_raw)
            if "<" in remetente and ">" in remetente:
                remetente_email = remetente.split("<")[1].split(">")[0].strip().lower()
            else:
                remetente_email = remetente.strip().lower()

            assunto = _decodificar_cabecalho(msg.get("Subject", ""))
            print(f"[EMAIL INGEST] Lendo de: {remetente_email} | Assunto: {assunto}")

            # Coleta anexos deste e-mail
            anexos = []
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                if part.get("Content-Disposition") is None:
                    continue

                filename_raw = part.get_filename()
                if not filename_raw:
                    continue

                nome_arquivo = _decodificar_cabecalho(filename_raw)
                ext = os.path.splitext(nome_arquivo)[1].lower()
                if ext in ALLOWED_EXTENSIONS:
                    payload = part.get_payload(decode=True)
                    if payload:
                        anexos.append({
                            "nome": nome_arquivo,
                            "ext": ext,
                            "payload": payload,
                        })

            if not anexos:
                print(f"[EMAIL INGEST] Sem anexos válidos no e-mail de {remetente_email}")
                enviar_resposta_email(
                    remetente_email,
                    assunto,
                    [],
                    [],
                    erro_msg="Nenhum arquivo PDF ou imagem válido foi encontrado em anexo."
                )
                continue

            # Adiciona ao lote pendente do remetente
            if remetente_email not in LOTES_PENDENTES:
                LOTES_PENDENTES[remetente_email] = {
                    "remetente": remetente_email,
                    "assunto": assunto,
                    "anexos": list(anexos),
                    "primeiro_recebimento": agora,
                    "ultimo_recebimento": agora,
                    "qtd_emails": 1,
                }
                print(f"[EMAIL BATCH] Novo lote criado para {remetente_email} com {len(anexos)} anexo(s).")
            else:
                lote = LOTES_PENDENTES[remetente_email]
                lote["anexos"].extend(anexos)
                lote["ultimo_recebimento"] = agora
                lote["qtd_emails"] += 1
                if not lote.get("assunto") and assunto:
                    lote["assunto"] = assunto
                print(f"[EMAIL BATCH] +{len(anexos)} anexo(s) adicionados ao lote de {remetente_email}. Total: {len(lote['anexos'])} anexo(s) ({lote['qtd_emails']} e-mails).")

        imap.logout()

    except Exception as e:
        print(f"[EMAIL INGEST ERROR] Erro na coleta de e-mails: {e}")
        traceback.print_exc()


def processar_lotes_prontos():
    """
    Verifica se algum lote pendente atingiu a janela de silêncio (ex: 45s sem novos
    arquivos daquele remetente) ou a janela máxima (ex: 180s).
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
                payload = item["payload"]

                caminho_arq = os.path.join(pasta_temp, f"doc_{idx}_{uuid.uuid4().hex[:6]}{ext}")
                with open(caminho_arq, "wb") as f:
                    f.write(payload)

                doc_id = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{idx}_" + uuid.uuid4().hex[:6]
                resultado_ocr = processor.processar(caminho_arq, doc_id)

                linhas_doc = []
                bloco_doc = ""
                prop_doc = ""

                if resultado_ocr.get("sucesso"):
                    doc = extrair_dados(resultado_ocr)
                    doc = validar_documento(doc)
                    linhas_doc = extrair_linhas_para_excel(doc)
                    todas_linhas.extend(linhas_doc)

                    meta = doc.get("metadata") or {}
                    bloco_doc = str(meta.get("bloco") or doc.get("bloco") or "").strip()
                    prop_doc = str(meta.get("propriedade") or doc.get("propriedade") or "").strip()

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

        except Exception as err:
            print(f"[EMAIL BATCH ERROR] Erro ao processar lote de {remetente}: {err}")
            traceback.print_exc()
        finally:
            shutil.rmtree(pasta_temp, ignore_errors=True)


def loop_vigilante_email(intervalo_segundos=15):
    print(f"[EMAIL WORKER] Vigilante de e-mail iniciado para {GMAIL_USER} (intervalo: {intervalo_segundos}s, janela silêncio: {JANELA_SILENCIO_SEGUNDOS}s)")
    while True:
        try:
            coletar_novos_emails()
            processar_lotes_prontos()
        except Exception as e:
            print(f"[EMAIL WORKER] Erro no ciclo: {e}")
            traceback.print_exc()
        time.sleep(intervalo_segundos)
