
import sqlite3
import json
from datetime import datetime

from config import DATABASE_PATH


def conectar():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def inicializar_banco():
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            hash TEXT,
            data_processamento TEXT,
            status TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS extracoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            documento_id INTEGER,
            campo TEXT,
            valor TEXT,
            confianca REAL,
            corrigido INTEGER DEFAULT 0,
            FOREIGN KEY(documento_id)
                REFERENCES documentos(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS correcoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            documento_id INTEGER,
            campo TEXT,
            valor_original TEXT,
            valor_corrigido TEXT,
            contexto TEXT,
            data_correcao TEXT,
            FOREIGN KEY(documento_id)
                REFERENCES documentos(id)
        )
    """)

    conn.commit()
    conn.close()


def registrar_documento(nome, hash_documento, status="processando"):
    conn = conectar()

    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO documentos
        (nome, hash, data_processamento, status)
        VALUES (?, ?, ?, ?)
    """, (
        nome,
        hash_documento,
        datetime.now().isoformat(),
        status
    ))

    documento_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return documento_id


def atualizar_status(documento_id, status):
    conn = conectar()

    conn.execute("""
        UPDATE documentos
        SET status = ?
        WHERE id = ?
    """, (status, documento_id))

    conn.commit()
    conn.close()


def registrar_extracao(
    documento_id,
    campo,
    valor,
    confianca=0.0
):
    conn = conectar()

    conn.execute("""
        INSERT INTO extracoes
        (
            documento_id,
            campo,
            valor,
            confianca
        )
        VALUES (?, ?, ?, ?)
    """, (
        documento_id,
        campo,
        valor,
        confianca
    ))

    conn.commit()
    conn.close()


def registrar_correcao(
    documento_id,
    campo,
    valor_original,
    valor_corrigido,
    contexto=""
):
    conn = conectar()

    conn.execute("""
        INSERT INTO correcoes
        (
            documento_id,
            campo,
            valor_original,
            valor_corrigido,
            contexto,
            data_correcao
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        documento_id,
        campo,
        valor_original,
        valor_corrigido,
        contexto,
        datetime.now().isoformat()
    ))

    conn.commit()
    conn.close()


def estatisticas():
    conn = conectar()

    documentos = conn.execute(
        "SELECT COUNT(*) AS total FROM documentos"
    ).fetchone()["total"]

    extracoes = conn.execute(
        "SELECT COUNT(*) AS total FROM extracoes"
    ).fetchone()["total"]

    correcoes = conn.execute(
        "SELECT COUNT(*) AS total FROM correcoes"
    ).fetchone()["total"]

    conn.close()

    return {
        "documentos": documentos,
        "extracoes": extracoes,
        "correcoes": correcoes
    }
