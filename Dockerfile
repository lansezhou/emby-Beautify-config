# 第一阶段：构建环境
FROM ubuntu:20.04 as builder

# 设置构建阶段环境变量（不设置时区）
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# 一次性完成所有系统级操作
RUN sed -i 's/archive.ubuntu.com/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    apt-get update -y && \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/
RUN pip3 install --user -r /tmp/requirements.txt

# 第二阶段：最终镜像
FROM ubuntu:20.04

# 设置运行时环境变量（包含时区）
ENV TZ=Asia/Shanghai \
    DEBIAN_FRONTEND=noninteractive \
    PATH="/root/.local/bin:${PATH}"

# 一次性设置时区和镜像源
RUN ln -fs /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone && \
    sed -i 's/archive.ubuntu.com/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    apt-get update -y && \
    apt-get install -y --no-install-recommends \
        python3 \
        tzdata \
        && \
    dpkg-reconfigure -f noninteractive tzdata && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 从builder阶段拷贝已安装的Python包
COPY --from=builder /root/.local /root/.local

# 复制应用文件
COPY app.py run.sh /app/
RUN chmod +x /app/run.sh

# 声明容器端口
EXPOSE 5000

ENTRYPOINT ["/app/run.sh"]