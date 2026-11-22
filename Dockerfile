# Basis-Image per Digest festgenagelt: ein Build nimmt nie ungefragt ein anderes Image.
# Auffrischen (Sicherheitsupdates von Debian/Python): tools/update-deps.sh – setzt Digest UND requirements.lock neu.
FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libexpat1: vom osmium-Wheel (pyosmium) benoetigt, fehlt im slim-Image
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Exakte Versionen mit Pruefsummen (tools/update-deps.sh): ein manipuliertes Paket auf PyPI kommt
# nicht unbemerkt beim naechsten Build mit. requirements.txt bleibt die Liste der gewollten Pakete.
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock

# go-pmtiles: schneidet den Kartenstreifen fuer RigNavi-Reisepakete aus dem Protomaps-Build
# (Release ohne Pruefsummen-Datei -> sha256 beim Festlegen der Version selbst ermittelt)
ARG PMTILES_VERSION=1.31.2
ARG PMTILES_SHA256=f8bd47e7ea866863489cad588fbaf2f31f42e5821f7a03f009b3769f05801cb1
ADD --checksum=sha256:${PMTILES_SHA256} \
    https://github.com/protomaps/go-pmtiles/releases/download/v${PMTILES_VERSION}/go-pmtiles_${PMTILES_VERSION}_Linux_arm64.tar.gz /tmp/pmtiles.tgz
RUN tar -xzf /tmp/pmtiles.tgz -C /usr/local/bin pmtiles && rm /tmp/pmtiles.tgz && pmtiles version

# Offline-Kartenstil (tools/navistyle.sh)
COPY navistyle/ /navistyle/
COPY app/ .

# Nicht als root laufen; /data (OSM-DB, Overpass-Cache) gehoert dem app-User.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data \
    && chown app:app /data
USER app

VOLUME ["/data"]
EXPOSE 8000

# "starting" bis der Beispiel-Vorrat fertig ist (nach Code-/Datenaenderung bis ~30 min), dann "healthy"
HEALTHCHECK --interval=60s --timeout=5s --start-period=45m --start-interval=15s \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--timeout-graceful-shutdown", "5"]
