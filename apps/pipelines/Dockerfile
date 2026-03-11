FROM prefecthq/prefect:3-python3.12

WORKDIR /app

# Install uv
RUN pip install uv

# Copy project definition first (layer cache — only re-installs deps when pyproject changes)
COPY pyproject.toml .

# Install all dependencies (numpy/pandas first to satisfy pandas-ta build)
RUN uv pip install --system numpy pandas && \
    uv pip install --system -e .

# Copy source after deps so code changes don't bust the dep cache
COPY src/ ./src/
