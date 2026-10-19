FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860 \
    RENDER=true \
    ENABLE_EMAIL_WATCHER=1 \
    JANELA_SILENCIO_SEGUNDOS=5 \
    OMP_THREAD_LIMIT=4

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-por \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Configura usuário com UID 1000 para total compatibilidade com Render e Hugging Face
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR $HOME/app

COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user:user . .

EXPOSE 7860

# --capture-output e --enable-stdio-inheritance garantem logs em TEMPO REAL no console do Render
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-7860} --workers 1 --threads 4 --timeout 300 --capture-output --enable-stdio-inheritance app:app"]
