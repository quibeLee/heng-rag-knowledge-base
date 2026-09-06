# 自定义 PostgreSQL 镜像：pgvector + zhparser（中文全文检索）
#
# 社区没有同时维护 pgvector + zhparser 的官方镜像，所以用多阶段构建：
# - builder 阶段在 postgres:16-bookworm 上编译 SCWS 与 zhparser
# - 运行阶段直接基于 pgvector/pgvector:pg16，把 zhparser 产物拷过去
# 一次 `docker compose up -d --build` 即可同时拿到两个扩展。

ARG PG_MAJOR=16

FROM postgres:${PG_MAJOR}-bookworm AS builder

ARG PG_MAJOR
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        postgresql-server-dev-${PG_MAJOR} \
    && rm -rf /var/lib/apt/lists/*

# SCWS：zhparser 的底层中文分词库
# 用官方发布的 1.2.3 tarball（自带 configure，避免对 autotools 链路的依赖），
# 上游 git master 的 Makefile.am 在新版 automake 下会失败
RUN curl -fsSL http://www.xunsearch.com/scws/down/scws-1.2.3.tar.bz2 -o /tmp/scws.tar.bz2 \
    && mkdir -p /tmp/scws \
    && tar -xjf /tmp/scws.tar.bz2 -C /tmp/scws --strip-components=1 \
    && cd /tmp/scws \
    && ./configure \
    && make -j"$(nproc)" \
    && make install

# zhparser：把 SCWS 包装成 PostgreSQL parser 扩展
RUN git clone --depth 1 https://github.com/amutu/zhparser.git /tmp/zhparser \
    && cd /tmp/zhparser \
    && make \
    && make install


FROM pgvector/pgvector:pg16

ARG PG_MAJOR=16

# 拷贝 zhparser 编译产物：动态库 + 扩展元数据 + 词典文件
COPY --from=builder /usr/lib/postgresql/${PG_MAJOR}/lib/zhparser.so \
    /usr/lib/postgresql/${PG_MAJOR}/lib/
COPY --from=builder /usr/local/lib/libscws.* /usr/local/lib/
COPY --from=builder /usr/share/postgresql/${PG_MAJOR}/extension/zhparser* \
    /usr/share/postgresql/${PG_MAJOR}/extension/
COPY --from=builder /usr/share/postgresql/${PG_MAJOR}/tsearch_data/ \
    /usr/share/postgresql/${PG_MAJOR}/tsearch_data/

# 让 ld 能找到 /usr/local/lib 下的 libscws.so
RUN ldconfig
