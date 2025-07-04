# ��һ�׶Σ���������
FROM ubuntu:20.04 as builder

# ���ù����׶λ���������������ʱ����
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# һ�����������ϵͳ������
RUN sed -i 's/archive.ubuntu.com/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    apt-get update -y && \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/
RUN pip3 install --user -r /tmp/requirements.txt

# �ڶ��׶Σ����վ���
FROM ubuntu:20.04

# ��������ʱ��������������ʱ����
ENV TZ=Asia/Shanghai \
    DEBIAN_FRONTEND=noninteractive \
    PATH="/root/.local/bin:${PATH}"

# һ��������ʱ���;���Դ
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

# ��builder�׶ο����Ѱ�װ��Python��
COPY --from=builder /root/.local /root/.local

# ����Ӧ���ļ�
COPY app.py run.sh /app/
RUN chmod +x /app/run.sh

# ���������˿�
EXPOSE 5000

ENTRYPOINT ["/app/run.sh"]