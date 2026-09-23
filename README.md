# Leitor de Documentos

Aplicação web em Flask para receber PDFs e imagens e extrair texto usando OCR.

## Instalação no GitHub Codespaces

### 1. Criar ambiente virtual

```bash
python -m venv venv
source venv/bin/activate
```

### 2. Instalar Tesseract

```bash
sudo apt update
sudo apt install -y tesseract-ocr tesseract-ocr-por
```

### 3. Instalar dependências Python

```bash
pip install -r requirements.txt
```

### 4. Verificar OCR

```bash
tesseract --version
tesseract --list-langs
```

Deve aparecer `por` na lista de idiomas.

### 5. Rodar a aplicação

```bash
python app.py
```

Abra a porta 5000 em **PORTS** no Codespaces.

## Formatos aceitos

- PDF
- PNG
- JPG
- JPEG
- TIFF

## Próxima evolução

A base está preparada para evoluir de simples OCR para:
- extração de campos;
- leitura de tabelas;
- CSV;
- Excel;
- processamento em lote;
- uso de IA para identificar informações específicas.
