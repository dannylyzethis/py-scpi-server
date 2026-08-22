FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --system emulator && useradd --system --gid emulator emulator

COPY ["pyproject.toml", "readme.md", "MIT License.md", "./"]
COPY src ./src
COPY scpi_instruments_example.csv ./

RUN python -m pip install --no-cache-dir .

USER emulator

EXPOSE 5555 5559

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import socket; s=socket.create_connection(('127.0.0.1',5555),2); s.sendall(b'*IDN?\\n'); assert s.recv(4096); s.close()"

ENTRYPOINT ["scpi-emulator"]
CMD ["--load", "scpi_instruments_example.csv", "--start", "--host", "0.0.0.0"]
