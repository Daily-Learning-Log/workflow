from huggingface_hub import HfApi
import json
import os
import requests
import re
from urllib.parse import urljoin, urlparse

# --- 配置部分 ---
HF_TOKEN = os.getenv("HF_TOKEN")
CNB_COOKIE = "" 

repos = [
    # 添加模型主页地址或huggingface模型ID
]

# --- 功能函数 ---

def get_save_path(filename, ref_path=""):
    """
    根据路径优先原则决定保存位置
    ref_path: 文件的 URL 或 相对路径 (例如 .../split_files/text_encoders/...)
    """
    filename = os.path.basename(filename)
    ref_path = ref_path.lower() # 转小写方便匹配
    filename_lower = filename.lower()
    
    # --- 优先级 1：绝对信任路径 (Folder Structure) ---
    # 这是最科学的方式。如果仓库管理员把文件放进了 text_encoders 文件夹，那它就是 Text Encoder。
    
    # 1.1 Text Encoders (匹配 text_encoder 或 text_encoders)
    if "text_encoder" in ref_path: 
        return f"models/text_encoders/{filename}"
        
    # 1.2 VAE
    if "vae" in ref_path:
        return f"models/vae/{filename}"
        
    # 1.3 Diffusion Models / UNET
    # 如果路径里明确写了 diffusion_models，就放进去
    if "diffusion_models" in ref_path:
        return f"models/diffusion_models/{filename}"
    if "unet" in ref_path:
        return f"models/unet/{filename}"

    # --- 优先级 2：文件后缀与特征 (Fallback) ---
    # 只有当路径里什么都没写 (比如文件在仓库根目录)，才通过文件名猜测
    
    # 2.1 GGUF (通常是 UNET)
    # 注意：放在优先级2，防止 text_encoders 里的 GGUF 被误判为 UNET
    if filename.endswith(".gguf"):
        return f"models/unet/{filename}"

    # 2.2 VAE 文件特征
    if "vae" in filename_lower and (filename.endswith(".pt") or filename.endswith(".safetensors")):
        return f"models/vae/{filename}"

    # 2.3 Text Encoder 文件特征 (常见编码器名字)
    if any(k in filename_lower for k in ["t5", "clip", "bert", "ul2", "qwen"]):
        # 特例防御：如果 Qwen 文件名带 image，通常是 DiT 主模型，而非纯文本编码器
        if "qwen" in filename_lower and "image" in filename_lower:
             return f"models/diffusion_models/{filename}"
        return f"models/text_encoders/{filename}"

    # 2.4 默认归类
    return f"models/diffusion_models/{filename}"

def get_cnb_headers():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://cnb.cool/"
    }
    if CNB_COOKIE:
        headers["Cookie"] = CNB_COOKIE
    return headers

def process_hf_repo(api, repo_id, result_list):
    """处理 Hugging Face 仓库"""
    print(f"正在处理 Hugging Face 仓库: {repo_id} ...")
    try:
        repo_info = api.repo_info(repo_id=repo_id, repo_type="model", files_metadata=True)
    except Exception as e:
        print(f"  ❌ 无法获取 HF 仓库信息: {e}")
        return

    for file_info in repo_info.siblings:
        if file_info.rfilename.endswith((".safetensors", ".gguf")):
            rfilename = file_info.rfilename
            url = f"https://huggingface.co/{repo_id}/resolve/main/{rfilename}"
            filename_only = os.path.basename(rfilename)
            
            sha256 = file_info.lfs.get("sha256", "N/A") if file_info.lfs else "N/A"
            size_bytes = file_info.lfs.get("size", 0) if file_info.lfs else 0
            size_gb = round(size_bytes / (1024 ** 3), 2)
            
            # 传入完整相对路径作为参考
            save_path = get_save_path(filename_only, ref_path=rfilename)
            
            result_list.append({
                "source": "huggingface",
                "repo": repo_id,
                "filename": filename_only,
                "url": url,
                "path": save_path,
                "sha256": sha256,
                "size": f"{size_gb}GB"
            })

def process_cnb_url(url, result_list):
    print(f"正在处理 CNB 链接: {url} ...")
    headers = get_cnb_headers()
    
    parsed = urlparse(url)
    repo_base_path = "/".join(parsed.path.split("/")[:4]) 
    
    visited_urls = set()
    processed_files = set()
    
    try:
        if "/blob/" in url and url.endswith((".gguf", ".safetensors")):
            print("  检测到单个文件链接，直接解析...")
            parse_cnb_file_page(url, headers, result_list)
        else:
            print("  检测到目录或主页，开始递归扫描...")
            parse_cnb_repo_recursive(url, headers, result_list, visited_urls, processed_files, repo_base_path)     
    except Exception as e:
        print(f"  ❌ 处理 CNB 出错: {e}")

def parse_cnb_file_page(file_page_url, headers, result_list):
    """解析单个 CNB 文件详情页"""
    try:
        response = requests.get(file_page_url, headers=headers, timeout=10)
    except Exception:
        print(f"  ❌ 请求超时: {file_page_url}")
        return

    if response.status_code != 200:
        return

    html = response.text
    filename = os.path.basename(urlparse(file_page_url).path)
    
    sha_match = re.search(r'SHA256\s*[:：]?\s*([a-fA-F0-9]{64})', html)
    sha256 = sha_match.group(1) if sha_match else "N/A"
    
    size_str = "N/A"
    size_match_a = re.search(r'(?:文件大小|Size)\s*[:：]?\s*(?:<[^>]+>|\s|&nbsp;)*([\d\.]+\s*[KMGT]?i?B)', html, re.IGNORECASE)
    if size_match_a:
        size_str = size_match_a.group(1)
    else:
        size_match_b = re.search(r'(\d+(?:\.\d+)?\s*(?:GiB|MiB))', html)
        if size_match_b:
            size_str = size_match_b.group(1)

    if sha256 != "N/A":
        if "/-/blob/" in file_page_url:
            repo_base = file_page_url.split("/-/blob/")[0]
        elif "/blob/" in file_page_url:
            repo_base = file_page_url.split("/blob/")[0].rstrip("/")
        else:
            repo_base = os.path.dirname(file_page_url)
        download_url = f"{repo_base}/-/lfs/{sha256}?name={filename}"
    else:
        print(f"  ⚠️ 警告: 未找到 SHA256，使用备用 raw 链接: {filename}")
        download_url = file_page_url.replace("/blob/", "/raw/").replace("/-/raw/", "/raw/")
    
    # 传入文件详情页 URL，因为它包含了完整的目录结构
    save_path = get_save_path(filename, ref_path=file_page_url)
    
    print(f"  -> 找到文件: {filename} | 路径: {save_path}")

    result_list.append({
        "source": "cnb.cool",
        "repo": "cnb_recursive",
        "filename": filename,
        "url": download_url,
        "path": save_path,
        "sha256": sha256,
        "size": size_str
    })

def parse_cnb_repo_recursive(current_url, headers, result_list, visited_urls, processed_files, repo_base_path):
    """递归解析仓库目录"""
    clean_current_url = current_url.split('?')[0]
    
    if clean_current_url in visited_urls:
        return
    visited_urls.add(clean_current_url)
    
    try:
        response = requests.get(current_url, headers=headers, timeout=10)
    except Exception:
        return

    if response.status_code != 200: return
    
    html_content = response.text
    
    all_links = re.findall(r'href="([^"]+)"', html_content)
    unique_links = set()
    for link in all_links:
        full_url = urljoin(current_url, link)
        unique_links.add(full_url)
        
    for full_url in unique_links:
        if repo_base_path not in full_url:
            continue
            
        if full_url.endswith((".gguf", ".safetensors")) and "/blob/" in full_url:
            clean_file_url = full_url.split('?')[0]
            if clean_file_url not in processed_files:
                processed_files.add(clean_file_url) 
                parse_cnb_file_page(full_url, headers, result_list)
        
        elif "/tree/" in full_url:
            clean_dir_url = full_url.split('?')[0]
            if clean_dir_url != clean_current_url and clean_dir_url not in visited_urls:
                parse_cnb_repo_recursive(full_url, headers, result_list, visited_urls, processed_files, repo_base_path)

# --- 主程序 ---

api = HfApi(token=HF_TOKEN)
result = []

for repo in repos:
    if repo.startswith("http"):
        if "cnb.cool" in repo:
            process_cnb_url(repo, result)
        else:
            print(f"跳过不支持的 URL: {repo}")
    else:
        process_hf_repo(api, repo, result)

# 按文件名排序
result.sort(key=lambda x: x["filename"])

output_file = "file-list.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=4, ensure_ascii=False)

print(f"\n✅ 处理完成！JSON 文件已保存为 {output_file}")

print(f"共找到 {len(result)} 个文件（已按文件名排序）。")
