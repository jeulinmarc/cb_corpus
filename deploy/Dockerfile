FROM python:3.13-slim

ARG SUPERCRONIC_VERSION=v0.2.47
ARG SUPERCRONIC_SHA256_AMD64=dcb1403c188a9438c47d4bba82a9c357fc9351ce91627fb2bae627f0f5becfc4
ARG SUPERCRONIC_SHA256_ARM64=e1124aa34294e2bb8ab7002f347f4363ba35097f3daf4d3c44e9d813c1fb2bb8

RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium git openssh-client rsync curl ca-certificates tzdata libnss-wrapper \
    && rm -rf /var/lib/apt/lists/*

RUN ARCH=$(dpkg --print-architecture) \
    && if [ "$ARCH" = "amd64" ]; then SHA="$SUPERCRONIC_SHA256_AMD64"; \
       else SHA="$SUPERCRONIC_SHA256_ARM64"; fi \
    && curl -fsSLo /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${ARCH}" \
    && echo "${SHA}  /usr/local/bin/supercronic" | sha256sum -c - \
    && chmod +x /usr/local/bin/supercronic

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY cb_corpus /app/cb_corpus
COPY deploy /app/deploy
RUN chmod +x /app/deploy/*.sh || true

# HOME=/tmp : le conteneur tourne sous un UID arbitraire (compose `user:`)
# absent de /etc/passwd ; ssh/git/chromium ont besoin d'un HOME inscriptible.
ENV PYTHONUNBUFFERED=1 HOME=/tmp

CMD ["/app/deploy/entrypoint.sh"]
