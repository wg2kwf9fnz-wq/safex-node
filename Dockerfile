# Multistage docker build, requires docker 17.05

# builder stage
FROM ubuntu:18.04 as builder

RUN apt-get update && \
    apt-get --no-install-recommends --yes install \
        ca-certificates \
        cmake \
        g++ \
        make \
        pkg-config \
        graphviz \
        doxygen \
        git \
        curl \
        libtool-bin \
        autoconf \
        libgtest-dev \
        automake

WORKDIR /usr/local

## Boost
ARG BOOST_VERSION=1_66_0
ARG BOOST_VERSION_DOT=1.66.0
ARG BOOST_HASH=5721818253e6a0989583192f96782c4a98eb6204965316df9f5ad75819225ca9
RUN curl -s -L -o  boost_${BOOST_VERSION}.tar.bz2 https://archives.boost.io/release/${BOOST_VERSION_DOT}/source/boost_${BOOST_VERSION}.tar.bz2 \
    && echo "${BOOST_HASH} boost_${BOOST_VERSION}.tar.bz2" | sha256sum -c \
    && tar -xvf boost_${BOOST_VERSION}.tar.bz2 \
    && cd boost_${BOOST_VERSION} \
    && ./bootstrap.sh \
    && ./b2 --build-type=minimal link=static runtime-link=static --with-chrono --with-date_time --with-filesystem --with-program_options --with-regex --with-serialization --with-system --with-thread --with-locale threading=multi threadapi=pthread cflags="-fPIC" cxxflags="-fPIC" stage
ENV BOOST_ROOT /usr/local/boost_${BOOST_VERSION}

# OpenSSL
ARG OPENSSL_VERSION=1.0.2n
ARG OPENSSL_HASH=370babb75f278c39e0c50e8c4e7493bc0f18db6867478341a832a982fd15a8fe
COPY openssl-${OPENSSL_VERSION}.tar.gz /usr/local/
RUN echo "${OPENSSL_HASH} openssl-${OPENSSL_VERSION}.tar.gz" | sha256sum -c \
    && tar -xzf openssl-${OPENSSL_VERSION}.tar.gz \
    && cd openssl-${OPENSSL_VERSION} \
    && ./Configure linux-x86_64 no-shared --static -fPIC \
    && make build_crypto build_ssl \
    && find /usr/local/openssl-${OPENSSL_VERSION} \( -name ssl.h -o -name libcrypto.a -o -name libssl.a \) -print \
    && mkdir -p /opt/openssl-${OPENSSL_VERSION}/include /opt/openssl-${OPENSSL_VERSION}/lib \
    && cp -aL /usr/local/openssl-${OPENSSL_VERSION}/include/openssl /opt/openssl-${OPENSSL_VERSION}/include/ \
    && cp /usr/local/openssl-${OPENSSL_VERSION}/libcrypto.a /usr/local/openssl-${OPENSSL_VERSION}/libssl.a /opt/openssl-${OPENSSL_VERSION}/lib/ \
    && echo "=== STAGED OPENSSL ===" \
    && find /opt/openssl-${OPENSSL_VERSION} -maxdepth 3 -type f -print \
    && echo "=== END STAGED OPENSSL ==="
RUN echo "=== GCC HEADER TEST ===" && printf "#include <openssl/ssl.h>\nint main(void){return 0;}\n" > /tmp/test.c && gcc -I/opt/openssl-1.0.2n/include -c /tmp/test.c -o /tmp/test.o && echo "=== GCC HEADER TEST PASSED ==="

ENV OPENSSL_ROOT_DIR=/opt/openssl-${OPENSSL_VERSION}
ENV C_INCLUDE_PATH=/opt/openssl-${OPENSSL_VERSION}/include
ENV CPLUS_INCLUDE_PATH=/opt/openssl-${OPENSSL_VERSION}/include
ENV CPPFLAGS="-I/opt/openssl-${OPENSSL_VERSION}/include"
ENV CFLAGS="-I/opt/openssl-${OPENSSL_VERSION}/include"
ENV CXXFLAGS="-I/opt/openssl-${OPENSSL_VERSION}/include"

# ZMQ
ARG ZMQ_VERSION=v4.2.3
ARG ZMQ_HASH=3226b8ebddd9c6c738ba42986822c26418a49afb
RUN git clone https://github.com/zeromq/libzmq.git -b ${ZMQ_VERSION} \
    && cd libzmq \
    && test `git rev-parse HEAD` = ${ZMQ_HASH} || exit 1 \
    && ./autogen.sh \
    && CFLAGS="-fPIC" CXXFLAGS="-fPIC" ./configure --enable-static --disable-shared \
    && make \
    && make install \
    && ldconfig

# zmq.hpp
ARG CPPZMQ_HASH=6aa3ab686e916cb0e62df7fa7d12e0b13ae9fae6
RUN git clone https://github.com/zeromq/cppzmq.git -b ${ZMQ_VERSION} \
    && cd cppzmq \
    && test `git rev-parse HEAD` = ${CPPZMQ_HASH} || exit 1 \
    && mv *.hpp /usr/local/include

# Readline
ARG READLINE_VERSION=7.0
ARG READLINE_HASH=750d437185286f40a369e1e4f4764eda932b9459b5ec9a731628393dd3d32334
RUN curl -s -O https://ftp.gnu.org/gnu/readline/readline-${READLINE_VERSION}.tar.gz \
    && echo "${READLINE_HASH} readline-${READLINE_VERSION}.tar.gz" | sha256sum -c \
    && tar -xzf readline-${READLINE_VERSION}.tar.gz \
    && cd readline-${READLINE_VERSION} \
    && CFLAGS="-fPIC" CXXFLAGS="-fPIC" ./configure \
    && make \
    && make install

# Sodium
ARG SODIUM_VERSION=1.0.16
ARG SODIUM_HASH=675149b9b8b66ff44152553fb3ebf9858128363d
RUN git clone https://github.com/jedisct1/libsodium.git -b ${SODIUM_VERSION} \
    && cd libsodium \
    && test `git rev-parse HEAD` = ${SODIUM_HASH} || exit 1 \
    && ./autogen.sh \
    && CFLAGS="-fPIC" CXXFLAGS="-fPIC" ./configure \
    && make \
    && make check \
    && make install

# Protobuf
ARG PROTOBUF_VERSION=v3.7.1
ARG PROTOBUF_HASH=6973c3a5041636c1d8dc5f7f6c8c1f3c15bc63d6
RUN set -ex \
    && git clone https://github.com/protocolbuffers/protobuf -b ${PROTOBUF_VERSION} \
    && cd protobuf \
    && test `git rev-parse HEAD` = ${PROTOBUF_HASH} || exit 1 \
    && git submodule update --init --recursive \
    && ./autogen.sh \
    && ./configure --enable-static --disable-shared \
    && make \
    && make install \
    && ldconfig


WORKDIR /src
COPY . .
RUN git apply safex-7.0.3-compat.patch &&     sed -i '/include_directories(SYSTEM ${OPENSSL_INCLUDE_DIR})/a include_directories(BEFORE /opt/openssl-1.0.2n/include)' external/unbound/CMakeLists.txt
RUN echo "=== FINDING SSL HEADER ===" && find /usr/local/openssl-1.0.2n /opt/openssl-1.0.2n -name ssl.h -print && echo "=== FINDING OPENSSL LIBS ===" && find /usr/local/openssl-1.0.2n /opt/openssl-1.0.2n \( -name libcrypto.a -o -name libssl.a \) -print

ARG NPROC
RUN rm -rf build && \
    if [ -z "$NPROC" ];then make VERBOSE=1 -j1 release-static;else make VERBOSE=1 -j1 release-static;fi

# runtime stage
FROM ubuntu:18.04

RUN apt-get update && \
    apt-get --no-install-recommends --yes install ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt

COPY --from=builder /src/build/release/bin/* /usr/local/bin/

# Contains the blockchain
VOLUME /root/.safex

# Generate your wallet via accessing the container and run:
# cd /wallet
# safex-wallet-cli
VOLUME /wallet

EXPOSE 17401
EXPOSE 17402

ENTRYPOINT ["safexd"]
