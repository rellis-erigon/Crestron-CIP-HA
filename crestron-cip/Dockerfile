ARG BUILD_FROM
FROM $BUILD_FROM

ARG BUILD_VERSION
ENV CRESTRON_CIP_VERSION=${BUILD_VERSION}

# config.yaml is the one place the version is written, and it travels with
# the image. BUILD_VERSION did not survive the Supervisor build — the add-on
# reported "vunknown" — so the manifest itself is the fallback rather than a
# hand-typed constant that would drift.
COPY config.yaml /app/config.yaml

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

COPY src /app/src
COPY static /app/static

WORKDIR /app
CMD ["python3", "src/main.py"]
