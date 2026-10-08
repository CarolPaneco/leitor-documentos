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
    propriedade = str(metadata.get("propriedade") or documento.get("propriedade") or "").strip()
    proprietario = str(metadata.get("proprietario") or documento.get("proprietario") or "").strip()
    municipio = str(metadata.get("municipio") or documento.get("municipio") or "").strip()

    linhas = []
    blocos = documento.get("blocos") or []
    if not blocos:
        blocos = [{"bloco": documento.get("bloco") or "", "talhoes": documento.get("talhoes") or []}]

    for b in blocos:
        bloco_codigo = str(b.get("bloco") or documento.get("bloco") or "").strip()
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
    df = df[colunas]

    memoria = io.BytesIO()
    with pd.ExcelWriter(memoria, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Dados Agrícolas")
        planilha = writer.sheets["Dados Agrícolas"]
        larguras = {
            "A": 18, "B": 12, "C": 18, "D": 14,
            "E": 15, "F": 35, "G": 35, "H": 20
        }
        for col, larg in larguras.items():
            planilha.column_dimensions[col].width = larg
    memoria.seek(0)
    return memoria


def enviar_resposta_email(destinatario, assunto_original, linhas, anexos_nomes, erro_msg=None):
    msg = MIMEMultipart()
    msg["From"] = f"Robô Leitor Agrícola <{GMAIL_USER}>"
    msg["To"] = destinatario
    msg["Subject"] = f"Re: {assunto_original}" if assunto_original else "Re: Processamento de Documentos Agrícolas"

    if erro_msg:
        corpo_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #c0392b;">Não foi possível processar o documento</h2>
            <p>Olá,</p>
            <p>Recebemos o seu e-mail, mas ocorreu a seguinte inconsistência:</p>
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
        corpo_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
            <h2 style="color: #27ae60;">Documento(s) Processado(s) com Sucesso! 🌾</h2>
            <p>Olá,</p>
            <p>O <b>Robô Leitor Agrícola</b> concluiu a leitura e extração dos seus documentos.</p>
            <div style="background: #f4fdf7; border: 1px solid #c3e6cb; border-radius: 5px; padding: 12px; margin: 15px 0;">
                <p style="margin: 0;"><b>Arquivos analisados:</b> {', '.join(anexos_nomes)}</p>
                <p style="margin: 5px 0 0 0;"><b>Total de linhas extraídas:</b> <span style="font-size: 18px; color: #27ae60;">{len(linhas)}</span></p>
            </div>
            <p>Em anexo, você encontrará a <b>planilha Excel (.xlsx)</b> consolidada com todas as colunas: <i>Bloco, Talhão, Variedade, Área, Plantio, Propriedade, Proprietário e Município</i>.</p>
            <br>
            <hr>
            <small style="color: #7f8c8d;">Mensagem gerada automaticamente pelo Robô Leitor Agrícola.</small>
        </body>
        </html>
        """
        msg.attach(MIMEText(corpo_html, "html"))

        # Anexa o Excel
        excel_bytes = gerar_arquivo_excel(linhas)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print(f"[EMAIL] Resposta enviada com sucesso para: {destinatario}")


def processar_emails_pendentes():
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(GMAIL_USER, GMAIL_PASS)
        imap.select("INBOX")

        status, mensagens = imap.search(None, "UNSEEN")
        if status != "OK" or not mensagens[0]:
            imap.logout()
            return

        ids = mensagens[0].split()
        print(f"[EMAIL] {len(ids)} e-mail(s) novo(s) encontrado(s)!")

        processor = get_processor()

        for msg_id in ids:
            status_f, dados_msg = imap.fetch(msg_id, "(RFC822)")
            if status_f != "OK" or not dados_msg:
                continue

            raw_email = dados_msg[0][1]
            msg = email.message_from_bytes(raw_email)

            remetente_raw = msg.get("From", "")
            remetente = _decodificar_cabecalho(remetente_raw)
            # Extrai apenas o endereço de e-mail (dentro de <...>)
            if "<" in remetente and ">" in remetente:
                remetente_email = remetente.split("<")[1].split(">")[0].strip()
            else:
                remetente_email = remetente.strip()

            assunto = _decodificar_cabecalho(msg.get("Subject", ""))
            print(f"[EMAIL] Processando e-mail de: {remetente_email} | Assunto: {assunto}")

            # Coleta anexos
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
                        anexos.append((nome_arquivo, ext, payload))

            if not anexos:
                print(f"[EMAIL] Nenhum anexo PDF/imagem em e-mail de {remetente_email}")
                enviar_resposta_email(
                    remetente_email,
                    assunto,
                    [],
                    [],
                    erro_msg="Nenhum arquivo PDF ou imagem válido foi encontrado em anexo."
                )
                continue

            pasta_temp = tempfile.mkdtemp(prefix="email_doc_")
            todas_linhas = []
            anexos_processados = []

            try:
                for nome_arq, ext, payload in anexos:
                    anexos_processados.append(nome_arq)
                    caminho_arq = os.path.join(pasta_temp, f"doc_{uuid.uuid4().hex[:6]}{ext}")
                    with open(caminho_arq, "wb") as f:
                        f.write(payload)

                    doc_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
                    resultado_ocr = processor.processar(caminho_arq, doc_id)

                    if resultado_ocr.get("sucesso"):
                        doc = extrair_dados(resultado_ocr)
                        doc = validar_documento(doc)
                        linhas = extrair_linhas_para_excel(doc)
                        todas_linhas.extend(linhas)

                    # Limpa pasta OCR
                    p_res = resultado_ocr.get("pasta_saida")
                    if p_res and os.path.exists(p_res):
                        shutil.rmtree(p_res, ignore_errors=True)

                if todas_linhas:
                    print(f"[EMAIL] Sucesso! {len(todas_linhas)} linhas extraídas para {remetente_email}")
                    enviar_resposta_email(
                        remetente_email,
                        assunto,
                        todas_linhas,
                        anexos_processados
                    )
                else:
                    print(f"[EMAIL] Nenhuma tabela encontrada para {remetente_email}")
                    enviar_resposta_email(
                        remetente_email,
                        assunto,
                        [],
                        anexos_processados,
                        erro_msg="Não foi possível identificar tabelas ou talhões agrícolas legíveis no documento enviado."
                    )

            finally:
                shutil.rmtree(pasta_temp, ignore_errors=True)

        imap.logout()

    except Exception as e:
        print(f"[EMAIL ERROR] Erro na verificação de e-mails: {e}")
        traceback.print_exc()


def loop_vigilante_email(intervalo_segundos=25):
    print(f"[EMAIL WORKER] Vigilante de e-mail iniciado para {GMAIL_USER} (intervalo: {intervalo_segundos}s)")
    while True:
        try:
            processar_emails_pendentes()
        except Exception as e:
            print(f"[EMAIL WORKER] Erro no loop: {e}")
        time.sleep(intervalo_segundos)
