# Movite — server (Python senza dipendenze esterne) + interfaccia web installabile
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MOVITE_HOST=0.0.0.0 \
    MOVITE_PORT=8080 \
    MOVITE_DATA=/data

WORKDIR /app
COPY server/ server/
COPY web/ web/

# utente senza privilegi; /data contiene orari scaricati e cache (da tenere in un volume)
RUN useradd --system --uid 10001 --no-create-home movite \
    && mkdir -p /data && chown movite /data
USER movite
VOLUME ["/data"]

EXPOSE 8080
# il primissimo avvio scarica e legge gli orari (~30 s): il controllo di salute aspetta
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import json,sys,urllib.request; s=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=4)); sys.exit(0 if s['ready'] else 1)"

CMD ["python", "-I", "server/server.py", "--no-browser"]
