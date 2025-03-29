import tkinter as tk
from tkinter import filedialog, messagebox
import os
import json
from bs4 import BeautifulSoup

def select_input_directory():
    """选择存放 HTML 文件的文件夹"""
    directory = filedialog.askdirectory()
    if directory:
        input_dir_var.set(directory)

def select_output_directory():
    """选择处理后文件的输出文件夹"""
    directory = filedialog.askdirectory()
    if directory:
        output_dir_var.set(directory)

def process_html_files():
    """处理选定目录下的所有 HTML 文件，只保留案件名称到责任编辑的内容，并生成 -c.jsonl 文件"""
    input_dir = input_dir_var.get().strip()
    output_dir = output_dir_var.get().strip()

    if not os.path.isdir(input_dir):
        messagebox.showerror("错误", "请输入正确的HTML目录")
        return
    if not os.path.isdir(output_dir):
        messagebox.showerror("错误", "请输入正确的输出目录")
        return

    # 统计处理成功与失败的文件数量
    success_count = 0
    fail_count = 0

    # 遍历目录下的所有文件
    for filename in os.listdir(input_dir):
        if filename.lower().endswith(".html"):
            html_path = os.path.join(input_dir, filename)
            try:
                with open(html_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
                soup = BeautifulSoup(html_content, "html.parser")

                # 1) 找到案件名称所在的位置（例如 class="detail_bigtitle"）
                title_div = soup.find("div", class_="detail_bigtitle")
                case_name = title_div.get_text(strip=True) if title_div else ""

                # 2) 找到正文内容所在的位置（例如 class="detail_txt"）
                content_div = soup.find("div", class_="detail_txt")
                content_text = content_div.get_text("\n", strip=True) if content_div else ""

                # 3) 找到责任编辑（例如 class="compile" 且包含"责任编辑"）
                editor_div = soup.find("div", class_="compile")
                editor_text = editor_div.get_text(strip=True) if editor_div else ""
                # 比如 editor_div 里可能是 "责任编辑：XX"，可视需要再做拆分
                # 如果只想要人名，可以再做进一步处理:
                # if "责任编辑：" in editor_text:
                #     editor_text = editor_text.split("责任编辑：")[-1].strip()

                # 组装我们需要的“案件名称 ~ 正文 ~ 责任编辑” 这部分内容
                # 实际使用时，可根据需求自由拼接
                filtered_content = {
                    "case_name": case_name,
                    "content": content_text,
                    "editor": editor_text
                }

                # 输出的文件名：原文件名 + "-c.jsonl"
                base_name, _ = os.path.splitext(filename)
                output_file_name = base_name + "-c.jsonl"
                output_file_path = os.path.join(output_dir, output_file_name)

                # 写入 JSON Lines 格式（简单起见，这里只写一行）
                with open(output_file_path, "w", encoding="utf-8") as out_f:
                    json.dump(filtered_content, out_f, ensure_ascii=False)
                    out_f.write("\n")

                success_count += 1
            except Exception as e:
                fail_count += 1
                print(f"处理文件 {filename} 时出现错误：{e}")

    messagebox.showinfo("完成", f"处理完成：\n成功 {success_count} 个，失败 {fail_count} 个。")

# ------------------ GUI 部分 ------------------ #
root = tk.Tk()
root.title("HTML内容筛选并导出JSONL")

# 输入目录与输出目录变量
input_dir_var = tk.StringVar()
output_dir_var = tk.StringVar()

# 标签 + 文本框 + 按钮（选择 HTML 目录）
tk.Label(root, text="HTML目录:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
tk.Entry(root, textvariable=input_dir_var, width=40).grid(row=0, column=1, padx=5, pady=5)
tk.Button(root, text="选择HTML目录", command=select_input_directory).grid(row=0, column=2, padx=5, pady=5)

# 标签 + 文本框 + 按钮（选择输出目录）
tk.Label(root, text="输出目录:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
tk.Entry(root, textvariable=output_dir_var, width=40).grid(row=1, column=1, padx=5, pady=5)
tk.Button(root, text="选择输出目录", command=select_output_directory).grid(row=1, column=2, padx=5, pady=5)

# 开始处理按钮
tk.Button(root, text="开始处理", command=process_html_files, width=15).grid(row=2, column=1, pady=10)

root.mainloop()
