ARG BUILD_FROM
FROM $BUILD_FROM

# Supervisor passes the version from config.yaml, so the running add-on
# reports what it was built from rather than a hand-typed constant.
ARG BUILD_VERSION
ENV CRESTRON_CIP_VERSION=${BUILD_VERSION}

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

COPY src /app/src
COPY static /app/static

WORKDIR /app
CMD ["python3", "src/main.py"]
