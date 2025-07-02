FROM osgeo/gdal:ubuntu-small-3.2.0

RUN apt update
RUN apt install -y \
    wget \
    python3-pip \
    libopencv-dev \
    python3-opencv

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH \
    RUST_VERSION=1.80.0

RUN set -eux; \
    dpkgArch="$(dpkg --print-architecture)"; \
    case "${dpkgArch##*-}" in \
    amd64) rustArch='x86_64-unknown-linux-gnu'; rustupSha256='6aeece6993e902708983b209d04c0d1dbb14ebb405ddb87def578d41f920f56d' ;; \
    armhf) rustArch='armv7-unknown-linux-gnueabihf'; rustupSha256='3c4114923305f1cd3b96ce3454e9e549ad4aa7c07c03aec73d1a785e98388bed' ;; \
    arm64) rustArch='aarch64-unknown-linux-gnu'; rustupSha256='1cffbf51e63e634c746f741de50649bbbcbd9dbe1de363c9ecef64e278dba2b2' ;; \
    i386) rustArch='i686-unknown-linux-gnu'; rustupSha256='0a6bed6e9f21192a51f83977716466895706059afb880500ff1d0e751ada5237' ;; \
    ppc64el) rustArch='powerpc64le-unknown-linux-gnu'; rustupSha256='079430f58ad4da1d1f4f5f2f0bd321422373213246a93b3ddb53dad627f5aa38' ;; \
    s390x) rustArch='s390x-unknown-linux-gnu'; rustupSha256='e7f89da453c8ce5771c28279d1a01d5e83541d420695c74ec81a7ec5d287c51c' ;; \
    *) echo >&2 "unsupported architecture: ${dpkgArch}"; exit 1 ;; \
    esac; \
    url="https://static.rust-lang.org/rustup/archive/1.27.1/${rustArch}/rustup-init"; \
    wget "$url"; \
    echo "${rustupSha256} *rustup-init" | sha256sum -c -; \
    chmod +x rustup-init; \
    ./rustup-init -y --no-modify-path --profile minimal --default-toolchain $RUST_VERSION --default-host ${rustArch}; \
    rm rustup-init; \
    chmod -R a+w $RUSTUP_HOME $CARGO_HOME; \
    rustup --version; \
    cargo --version; \
    rustc --version;

# Add UbuntuGIS repository for GDAL
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository -y ppa:ubuntugis/ubuntugis-unstable \
    && apt-get update

# Install system dependencies with specific GDAL version
RUN apt-get install -y --no-install-recommends \
    git \
    build-essential \
    libssl-dev \
    pkg-config \
    python3-pip \
    python3-setuptools \
    python3-wheel \
    python3-dev \
    gdal-bin \
    libgdal-dev \
    python3-gdal \
    && rm -rf /var/lib/apt/lists/*

# Set GDAL environment variables
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal
ENV LD_LIBRARY_PATH=/usr/lib

# Upgrade pip and install Python packages with specific versions
RUN python3 -m pip install --upgrade pip==23.0.1 && \
    pip install \
        'setuptools==65.5.0' \
        'wheel==0.40.0' && \
    # Install GDAL Python bindings that match system version
    pip install --no-cache-dir \
        'numpy==1.24.4' \
        'pandas==1.5.3' \
        'matplotlib==3.7.1' \
        'shapely==2.0.1' \
        'pyyaml==6.0' \
        'maturin==1.0.0'

# Install Rust toolchain with specific version
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
ENV PATH="/root/.cargo/bin:${PATH}"
RUN rustup default stable && \
    rustup update && \
    rustup component add rustfmt clippy

# Clone and install swifco-rs with verbose output
RUN echo "=== Cloning swifco-rs ===" && \
    git clone --depth 1 https://git.ufz.de/ecoepi/swifco-rs.git /tmp/swifco-rs && \
    cd /tmp/swifco-rs && \
    echo "\n=== Building swifco-rs ===" && \
    # Install in development mode with verbose output
    RUSTFLAGS="-C target-cpu=native" \
    pip install -e . 2>&1 | tee /tmp/build.log || { echo "\n=== Build failed, showing log ===" && cat /tmp/build.log && exit 1; } && \
    echo "\n=== Build successful, verifying installation ===" && \
    # Verify installation with simple import check
    python3 -c "import sys; print('Python path:', sys.path)" && \
    python3 -c "import importlib.util; print('swifco_rs spec:', importlib.util.find_spec('swifco_rs'))" && \
    python3 -c "import swifco_rs; print('swifco_rs imported successfully')" && \
    # Clean up
    cd / && \
    rm -rf /root/.cargo/git /root/.cargo/registry /tmp/build.log

COPY shiny.py /code/experiments/shiny.py

ENTRYPOINT ["python", "/code/experiments/shiny.py"]
CMD []
