#!/bin/bash

# ================= 配置区域 =================
# 配置模型工作目录
BASE_DATA_DIR="/workspace/ComfyUI"
# 如果你的模型需要权限（如 Flux.1 Dev），请填入 Token，否则留空或随意填写
HF_TOKEN=""
HF_MIRROR="https://hf-mirror.com"

# ✅ 在此处配置模型列表 (JSON格式)
MODEL_LIST_JSON='
[

]
'
# ============================================

# 简单检查 aria2 是否存在
if ! command -v aria2c &> /dev/null; then
    echo "❌ 错误: 未检测到 aria2c。"
    echo "💡 请确认 Dockerfile 中已包含 aria2 并重新构建环境。"
    exit 1
fi

echo "🚀 解析下载列表..."

ARIA2_SESSION_DIR="$BASE_DATA_DIR/.aria2_sessions"
mkdir -p "$ARIA2_SESSION_DIR"

# 初始化统计
TOTAL_COUNT=0
SUCCESS_COUNT=0
FAILED_COUNT=0

# 🔥 【核心修改点】使用进程替换 < <(...) 代替管道 |
# 这样 while 循环就在当前 shell 执行，变量累加才会生效
while IFS='|' read -r url rel_path sha256 size; do

    if [[ $url == "ERROR"* ]]; then
        echo "❌ JSON 解析失败: $url"
        exit 1
    fi

    ((TOTAL_COUNT++))

    # 1. 路径构建
    full_path="$BASE_DATA_DIR/$rel_path"
    save_dir=$(dirname "$full_path")
    filename=$(basename "$full_path")

    # 2. 检查文件是否存在且完整
    if [ -f "$full_path" ]; then
        if [ -n "$sha256" ]; then
            echo "🔍 [$filename] 文件存在，正在校验 SHA256..."
            computed_sha=$(sha256sum "$full_path" | cut -d' ' -f1)
            if [ "$computed_sha" = "$sha256" ]; then
                echo "✅ [完整] $filename ($size) - 跳过"
                ((SUCCESS_COUNT++))
                continue
            else
                echo "⚠️  [损坏] 校验失败，准备重新下载..."
                rm -f "$full_path"
            fi
        else
            echo "✅ [存在] $filename ($size) - 跳过 (无校验值)"
            ((SUCCESS_COUNT++))
            continue
        fi
    fi

    # 3. 链接处理 (镜像替换 + Token)
    header_cmd=""
    if [[ $url == *"huggingface.co"* ]]; then
        url="${url/https:\/\/huggingface.co/$HF_MIRROR}"
        if [ "$HF_TOKEN" != "your-token" ] && [ -n "$HF_TOKEN" ]; then
            header_cmd="Authorization: Bearer $HF_TOKEN"
        fi
    fi

    # 4. 创建目录
    mkdir -p "$save_dir"

    echo "---------------------------------------------------"
    echo "⬇️  [$TOTAL_COUNT] 下载: $filename"
    echo "    📂 路径: $rel_path"
    echo "    📊 大小: $size"
    
    # 5. 准备 Aria2 参数
    checksum_arg=""
    if [ -n "$sha256" ]; then
        checksum_arg="--checksum=sha-256=$sha256"
        echo "    🛡️  校验: 启用 (SHA256)"
    fi

    ARIA2_PARAMS=(
        --max-connection-per-server=16
        --split=16
        --min-split-size=2M
        --continue=true
        --max-tries=5
        --retry-wait=10
        --timeout=600
        --check-certificate=false
        --auto-file-renaming=false
        --allow-overwrite=true
        --file-allocation=prealloc
        --summary-interval=30
        --console-log-level=warn
        -d "$save_dir"
        -o "$filename"
    )

    # 6. 执行下载
    echo "    🚀 Aria2 引擎启动..."
    start_time=$(date +%s)
    
    if [ -n "$header_cmd" ]; then
        aria2c --header "$header_cmd" "${ARIA2_PARAMS[@]}" $checksum_arg "$url"
    else
        aria2c "${ARIA2_PARAMS[@]}" $checksum_arg "$url"
    fi

    exit_code=$?
    end_time=$(date +%s)
    duration=$((end_time - start_time))

    if [ $exit_code -eq 0 ]; then
        echo "✅ 完成: $filename (耗时: ${duration}s)"
        ((SUCCESS_COUNT++))
    else
        echo "❌ 失败: $filename (耗时: ${duration}s)"
        ((FAILED_COUNT++))
        rm -f "$full_path" "${full_path}.aria2"
    fi

done < <(python3 -c "
import json, os
try:
    data = json.loads('''$MODEL_LIST_JSON''')
    for item in data:
        url = item.get('url', '')
        path = item.get('path', '')
        sha = item.get('sha256', '')
        size = item.get('size', '未知大小')
        if url and path:
            print(f'{url}|{path}|{sha}|{size}')
except Exception as e:
    print(f'ERROR: {e}')
")

echo "==================================================="
echo "📊 下载统计: 总数 $TOTAL_COUNT | 成功 $SUCCESS_COUNT | 失败 $FAILED_COUNT"
echo "🎉 任务结束"
