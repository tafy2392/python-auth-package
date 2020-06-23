FROM python:3.8.3-slim-buster AS builder
ADD . /src
RUN pip install --upgrade pip && \
    pip wheel /src --wheel-dir=/wheels
WORKDIR /src
RUN python setup.py bdist_wheel

FROM python:3.8.3-slim-buster
COPY --from=builder /src/dist /dist
COPY --from=builder /wheels /wheels
RUN pip install --upgrade pip && \
    pip install /dist/*.whl -f /wheels && \
    rm -rf /dist /wheels
CMD ["marathon-acme-trio", "run-service"]
