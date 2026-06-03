import sys, http.server, socketserver, webbrowser, threading, base64, os, json, time, re, fitz
import xml.etree.ElementTree as ET

import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk
from tkinter import filedialog

# ============================================================
# 1. XFDF 處理邏輯
# ============================================================
def parse_xfdf_to_json(xfdf_path):
    if not os.path.exists(xfdf_path): return []
    try:
        tree = ET.parse(xfdf_path)
        root = tree.getroot()
        annots = []
        for node in root.iter():
            tag_name = node.tag.split('}')[-1].lower()
            if tag_name == "freetext" and 'rect' in node.attrib:
                r_str = node.attrib.get('rect')
                r = [float(x) for x in r_str.split(',')]
                annot_id = node.attrib.get('name', 'id-' + str(len(annots)))
                page_idx = int(node.attrib.get('page', 0))
                
                node_xml = ET.tostring(node, encoding='unicode')
                
                # ============================================================
                # 💡 【新增：深層粗體掃描】
                # 直接將 XML 壓縮去除空格並轉小寫，強力捕捉 "font-weight:bold"
                # ============================================================
                is_bold_in_xml = False

                # 1. 優先檢查外層 freetext 節點本身的 style 屬性
                node_style = node.attrib.get('style', '').replace(" ", "").lower()
                if "font-weight:bold" in node_style:
                    is_bold_in_xml = True
                else:
                    # 2. 檢查內部富文本 (Rich Text) 的 span 標籤
                    spans = node.findall('.//{*}span')
                    for span in spans:
                        span_style = span.attrib.get('style', '').replace(" ", "").lower()
                        
                        # 關鍵加強：取得該 span 內部的文字，並去除所有空白與換行
                        span_text = "".join(span.itertext()).strip()
                        
                        # 只有在 span 裡面「真的有實質文字」時，它的粗體設定才有效
                        if "font-weight:bold" in span_style and span_text:
                            is_bold_in_xml = True
                            break  # 只要有一個真正有字的 span 是粗體，就認定是粗體

                    # 3. 兜底檢查：如果完全沒有富文本 span（單純文字），才去看傳統 PDF 指令
                    if not spans:
                        da = node.attrib.get('defaultappearance', '').lower()
                        if "bold" in da or "co-bd" in da:
                            is_bold_in_xml = True
                
                # 3. 搜尋字體大小
                font_size = 12.0
                # 直接掃描 text-decoration 後面緊跟著的 font-size
                target_match = re.search(r'text-decoration:[^>]*?font-size:\s*(\d+\.?\d*)pt', node_xml)
                
                if target_match:
                    # 如果精準匹配成功，直接採用這個決定內文的大小
                    font_size = float(target_match.group(1))
                else:
                    # 兜底：如果完全沒有這種富文本結構，才去看傳統 PDF 指令
                    fs_match = re.search(r'(\d+\.?\d*)\s*Tf', node_xml)
                    if fs_match:
                        font_size = float(fs_match.group(1))

                # --- 3.5 新增：搜尋字體樣式 (Font Family) ---
                font_family = "未定義"
                if 'Arial' in node_xml:
                    font_family = "Arial"
                else:
                    all_fonts = re.findall(r"font-family:\s*['\"]?([^'\";>]+)['\"]?", node_xml)
                    if len(all_fonts) >= 2:
                        font_family = all_fonts[1].strip()
                    elif len(all_fonts) == 1:
                        font_family = all_fonts[0].strip()
                    else:
                        font_family = "Error"
                # ------------------------------------------

                # --- 3.6 新增：動態抓取 MSG 2.0 規範的顏色與不透明度 ---
                # 抓取 color 屬性（如 #FFBE9B），若沒定義則預設為透明白色 #FFFFFF
                xfdf_color = node.attrib.get('color', '#FFFFFF')
                # 抓取 style 屬性（如 "dash"），若沒定義則預設為 "solid"
                border_style = node.attrib.get('style', 'solid')

                is_dash_in_xml = False
                if 'style="dash"' in node_xml or 'dashes="2,2"' in node_xml:
                    is_dash_in_xml = True

                # 同步維持你原本舊的 border_style 變數，做到完美的向下相容
                border_style = "dash" if is_dash_in_xml else "solid"

                # 4. 內容抓取
                text_content = ""
                content_node = node.find('.//{*}contents')
                if content_node is not None:
                    text_content = content_node.text or ""
                if not text_content:
                    body_node = node.find('.//{http://www.w3.org/1999/xhtml}body')
                    if body_node is not None:
                        text_content = "".join(body_node.itertext())

                annots.append({
                    "id": annot_id, 
                    "page": page_idx, 
                    "rect": r, 
                    "type": tag_name, 
                    "text": text_content.strip(),
                    "fontSize": font_size,
                    "font": font_family,       # 將抓到的字體傳給前端
                    "isBold": is_bold_in_xml,  # 💡 【關鍵傳遞】：將粗體 Boolean 狀態回傳給網頁
                    "color": xfdf_color,
                    "borderStyle": border_style,
                    "isDash": is_dash_in_xml
                })
        return annots
    except Exception as e:
        print(f"解析錯誤: {e}"); return []
    
def save_json_to_xfdf(json_data, original_xfdf, output_xfdf):
    try:
        with open(original_xfdf, 'r', encoding='utf-8') as f:
            content = f.read()
        
        current_ids = [str(item['id']) for item in json_data]
        new_annotations_to_append = [] # 用來收集全新複製出來的物件
        
        for item in json_data:
            node_id = str(item['id'])
            r = item['rect']
            new_fs = float(item.get('fontSize', 8.0))
            new_rect_str = f"{r[0]:.6f},{r[1]:.6f},{r[2]:.6f},{r[3]:.6f}"

            # 💡【關鍵新增】：從前端傳進來的最新文字與粗體狀態
            old_text = item.get('oldText', None)
            new_text = item.get('text', None)
            is_bold = item.get('isBold', False)
            is_dash = item.get('isDash', False)
            
            # 1. 嘗試找出該 ID 的完整 freetext 區塊 (修改既有物件)
            pattern = rf'(<freetext[^>]+?name="{node_id}".*?</freetext>)'
            match = re.search(pattern, content, flags=re.DOTALL | re.IGNORECASE)
            
            if match:
                block = match.group(1)

                # 根據最新狀態動態補上或移除虛線標籤
                is_dash_requested = item.get('isDash', False)

                if is_dash_requested:
                    # 【情況 A：使用者想要變虛線】
                    if 'style="dash"' in block or 'dashes="2,2"' in block:
                        # 如果原本就是虛線，什麼都不用動
                        pass
                    else:
                        # 如果原本沒有虛線屬性，直接在必有的 subject="Text Box" 後面插隊塞入
                        # 注意：我們在 dashes 前面補一個空格，確保符合 XML 格式
                        subject_anchor = 'subject="Text Box"'
                        if subject_anchor in block:
                            block = block.replace(subject_anchor, subject_anchor + ' dashes="2,2" style="dash"')
                else:
                    # 【情況 B：使用者想要變實線（不加虛線）】
                    # 不管順序如何，精準地把這兩個特定屬性字串連同前面的空格一併拔除
                    if ' style="dash"' in block:
                        block = block.replace(' style="dash"', '')
                    if ' dashes="2,2"' in block:
                        block = block.replace(' dashes="2,2"', '')
                    # 兜底防護：防止沒有空格的變體殘留
                    if 'style="dash"' in block:
                        block = block.replace('style="dash"', '')
                    if 'dashes="2,2"' in block:
                        block = block.replace('dashes="2,2"', '')

                if 'color' in item and item['color']:
                    new_color = item['color']
                else:
                    orig_color_match = re.search(r'color="([^"]+)"', block)
                    new_color = orig_color_match.group(1) if orig_color_match else "#FFFF96"

                # 不論原本是 #FAFF9B 還是其他舊色，全面動態換成前端使用者選定的 new_color
                block = re.sub(r'color="#[A-Fa-f0-9]{6}"', f'color="{new_color}"', block)
                
                # A. 替換所有 style 中的 font-size: 12.00pt
                block = re.sub(r'font-size:\s*\d+\.?\d*pt', f'font-size:{new_fs:.2f}pt', block)
                
                # B. 替換 defaultappearance 中的 12 Tf (PDF 指令格式)
                block = re.sub(r'(\d+\.?\d*)\s+Tf', f'{new_fs:.1f} Tf', block)
                
                # C. 替換最外層的 rect 屬性
                block = re.sub(r'rect="[^"]+"', f'rect="{new_rect_str}"', block)
                
                # ============================================================
                # 💡【純字串精準切片方案】：完全不用正則、不用 old_text
                # ============================================================
                if old_text:
                    # 1. 為了防止看不見的隱藏空格干擾，先嘗試最單純的直接取代
                    block = block.replace(old_text, new_text)
                    
                    # 2. 【核心防護】如果還是沒換成功，代表 XFDF 內部可能帶有隱藏的空白字元
                    # 我們幫前端傳過來的 old_text 進行去空白、或是將其兩側也納入考慮
                    cleaned_old = old_text.strip()
                    if cleaned_old in block:
                        block = block.replace(cleaned_old, new_text)

                
                # ============================================================
                # 💡【純粹替換 2】：根據粗體狀態，單純取代關鍵字
                # ============================================================
                # 1. 先確認前端最新的粗體狀態
                is_bold_requested = item.get('isBold', False)
                
                # 2. 安全無損的精準取代邏輯
                if is_bold_requested:
                    # 【情況 A：使用者想要變粗體】
                    if "font-weight:normal" in block:
                        # 如果原本有寫 normal，直接改為 bold
                        block = block.replace("font-weight:normal", "font-weight:bold")
                    elif "font-weight:bold" in block:
                        # 如果本來就是 bold，什麼都不用動
                        pass
                    else:
                        # 【最關鍵的核心修復】：如果原本完全沒有 font-weight！
                        # 我們不盲目覆蓋，而是利用一定會存在的 "font-family" 作為定錨點，
                        # 在它前面「精準插隊」塞入 font-weight:bold;
                        # 同時支援單引號或雙引號的 'Arial' 或 "Arial"
                        if "font-family:" in block:
                            block = block.replace("font-family:", "font-weight:bold;font-family:")
                else:
                    # 【情況 B：使用者想要變正常體（不加粗）】
                    if "font-weight:bold" in block:
                        # 如果原本是粗體，直接改為 normal
                        block = block.replace("font-weight:bold", "font-weight:normal")
                    elif "font-weight:normal" in block:
                        # 本來就是 normal，不用動
                        pass
                    else:
                        # 如果原本就沒有寫，代表本來就是正常體，也不用動
                        pass

                # 寫回主內容
                content = content.replace(match.group(1), block)

            else:
                # ============================================================
                # 💡【複製物件宇宙終極完全體 - 消除空白 Bug 版】：
                # 完全沿用原本 match 分支中最穩定的正則雷射方案，不手動拼湊 XML 內部結構
                # 100% 保證 Kofax 順暢讀取，徹底消滅字體重複與文字空白問題！
                # ============================================================
                source_id = item.get('sourceId', None)
                new_block = ""
                
                # 安全轉型
                s_color = str(item.get('color', '#FFFFAA')).upper()
                s_page = str(page_idx) if 'page_idx' in locals() else str(item.get('page', 0))
                s_rect = str(new_rect_str) 
                s_node = str(node_id)
                
                if source_id:
                    # 1. 嘗試去原始 XFDF 檔案中尋找指定的母體標籤
                    src_pattern = rf'(<freetext[^>]+?name="{source_id}".*?</freetext>)'
                    src_match = re.search(src_pattern, content, flags=re.DOTALL | re.IGNORECASE)
                    
                    if src_match:
                        new_block = src_match.group(1)
                        # 2. 替換 Unique ID
                        new_block = new_block.replace(f'name="{source_id}"', f'name="{s_node}"')
                    else:
                        # 3. 多代複製防護
                        any_pattern = r'(<freetext[^>]+?name="([^"]+)".*?</freetext>)'
                        any_match = re.search(any_pattern, content, flags=re.DOTALL | re.IGNORECASE)
                        if any_match:
                            orig_any_id = any_match.group(2)
                            new_block = any_match.group(1)
                            new_block = new_block.replace(f'name="{orig_any_id}"', f'name="{s_node}"')
                
                if new_block:
                    # 4. 【外殼基本屬性更新】：換掉 rect、page、color 與時間
                    new_block = re.sub(r'rect="[^"]+"', f'rect="{s_rect}"', new_block)
                    new_block = re.sub(r'page="[^"]+"', f'page="{s_page}"', new_block)
                    new_block = re.sub(r'color="#[A-Fa-f0-9]{6}"', f'color="{s_color}"', new_block)
                    current_time_str = f"D:{time.strftime('%Y%m%d%H%M%S')}+08'00'"
                    new_block = re.sub(r'date="[^"]+"', f'date="{current_time_str}"', new_block)
                    
                    # 5. 【連動 Panel 屬性修改】：處理字體大小、虛線與粗體
                    new_block = re.sub(r'font-size:\s*\d+\.?\d*pt', f'font-size:{new_fs:.2f}pt', new_block)
                    new_block = re.sub(r'(\d+\.?\d*)\s+Tf', f'{new_fs:.1f} Tf', new_block)
                    
                    if is_dash:
                        if 'style="dash"' not in new_block and 'dashes="2,2"' not in new_block:
                            subject_anchor = 'subject="Text Box"'
                            if subject_anchor in new_block:
                                new_block = new_block.replace(subject_anchor, subject_anchor + ' dashes="2,2" style="dash"')
                    else:
                        new_block = new_block.replace(' style="dash"', '').replace(' dashes="2,2"', '')
                        new_block = new_block.replace('style="dash"', '').replace('dashes="2,2"', '')
                    
                    if is_bold:
                        if "font-weight:normal" in new_block:
                            new_block = new_block.replace("font-weight:normal", "font-weight:bold")
                        elif "font-weight:bold" not in new_block and "font-family:" in new_block:
                            new_block = new_block.replace("font-family:", "font-weight:bold;font-family:")
                    else:
                        if "font-weight:bold" in new_block:
                            new_block = new_block.replace("font-weight:bold", "font-weight:normal")
                    
                    # 🎯 ============================================================
                    # ⚡【終極文字更換防護】：完全複製你原本 match 區塊內大獲成功的文字替換代碼
                    # ============================================================
                    if old_text:
                        # 先嘗試最單純的直接取代內文
                        new_block = new_block.replace(old_text, new_text)
                        
                        cleaned_old = old_text.strip()
                        if cleaned_old in new_block:
                            new_block = new_block.replace(cleaned_old, new_text)
                    
                    # 兜底防護：萬一 old_text 對不上，直接用正則精準重刷 <contents> 的純文字內容
                    new_block = re.sub(r'(<contents[^>]*>).*?(</contents>)', f'\\1{new_text}\\2', new_block, flags=re.DOTALL)
                    
                    # 關鍵：利用正則，只清洗最深層富文本 span 夾住的文字內容，絕不破壞外層結構
                    # 先排除空文字狀況，確保只針對有字的地方做精準單一取代
                    if new_text:
                        new_block = re.sub(r'(<span[^>]*>).*?(</span>)', f'\\1{new_text}\\2', new_block, count=1, flags=re.DOTALL)
                
                else:
                    # 6. 安全兜底（全檔案無任何標註時才啟用）
                    font_weight_style = "font-weight:bold;" if is_bold else "font-weight:normal;"
                    new_block = f'<freetext rect="{s_rect}" creationdate="" name="{s_node}" opacity="1" color="{s_color}" flags="print" date="D:{time.strftime("%Y%m%d%H%M%S")}+08\'00\'" title="User" subject="Text Box" rotation="0" page="{s_page}" width="1" head="None" fringe="0.000000,0.000000,0.000000,0.000000"><contents-richtext><body xmlns="http://w3.org" xmlns:xfa="http://xfa.org" xfa:APIVersion="Acrobat:7.0.0" xfa:spec="2.0.2" style="font-size:12.00pt;font-family:\'Helvetica\'"><p><span style="text-decoration:;{font_weight_style}font-style:normal;font-family:\'Arial\'">{new_text}</span></p></body></contents-richtext><defaultappearance>0.000000 0.000000 0.000000 rg /Helv 12.0 Tf</defaultappearance><defaultstyle>text-decoration:;font-size:12.00pt;font-family:\'Helvetica\'</defaultstyle></freetext>'
                
                new_annotations_to_append.append(new_block)

        # 2. 刪除邏輯 (比照辦理)
        all_freetexts = re.findall(rf'(<freetext[^>]+?name="([^"]+)"[.\s\S]*?</freetext>)', content, flags=re.IGNORECASE)
        for full_tag, f_id in all_freetexts:
            if f_id not in current_ids:
                content = content.replace(full_tag, "")

        if new_annotations_to_append:
            combined_new_blocks = "\n".join(new_annotations_to_append)
            if "</annots>" in content:
                content = content.replace("</annots>", f"{combined_new_blocks}\n</annots>")
            elif "</xfdf>" in content:
                content = content.replace("</xfdf>", f"<annots>\n{combined_new_blocks}\n</annots>\n</xfdf>")

        with open(output_xfdf, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    except Exception as e:
        print(f"儲存異常: {e}")
        return False

# ============================================================
# 2. HTML 模板
# ============================================================
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>aCRF Editor</title>
    <style>
        body { margin: 0; background: #222; color: white; font-family: sans-serif; overflow: hidden; display: flex; flex-direction: column; height: 100vh; }
        #toolbar { height: 50px; background: #111; display: flex; align-items: center; padding: 0 20px; border-bottom: 1px solid #444; gap: 15px; }
        #main { 
            display: flex; 
            flex: 1; 
            overflow: hidden; 
            height: calc(100vh - 50px); /* 扣掉頂部 toolbar 的高度 */
        }
        #viewer { 
            flex: 1; 
            overflow: auto; 
            background: #444; 
            display: block; 
            position: relative; 
            padding: 20px; 
            box-sizing: border-box; 
        }
        #side-panel { width: 280px; background: #2a2a2a; border-left: 5px solid #444; padding: 5px; display: flex; flex-direction: column; gap: 0px; }
        #canvas-wrapper { 
            position: relative; 
            margin: 0 auto; 
            box-shadow: 0 0 15px black; 
            transform-origin: top left !important;
            display: block;
            width: fit-content; 
        }

        /* 書籤項目樣式 */
        .bookmark-item {
            padding: 5px 8px;
            cursor: pointer;
            border-radius: 4px;
            transition: background 0.2s;
            word-break: break-word;
            display: block;
        }
        .bookmark-item:hover {
            background: #3d3d3d;
            color: #fff;
        }
        /* 依據階層自動縮排 */
        .bm-level-0 { padding-left: 5px; font-weight: bold; color: #eee; }
        .bm-level-1 { padding-left: 20px; font-size: 12px; color: #ccc; }
        .bm-level-2 { padding-left: 35px; font-size: 12px; color: #aaa; }
        .bm-level-3 { padding-left: 50px; font-size: 11px; color: #888; }

        canvas { 
            display: block; 
            background: white; 
            transform-origin: top left !important;
        }
        #layer { position: absolute; top: 0; left: 0; width:100%; height:100%; pointer-events: auto; z-index: 500; }
        .annot-box { position: absolute; border: 2px solid red; background: rgba(255,0,0,0.1); cursor: move; pointer-events: auto; box-sizing: border-box; }
        .annot-box.selected { border: 2px solid #00ff00; background: rgba(0,255,0,0.15); z-index: 1000 !important; }
        .resizer { width: 8px; height: 8px; background: #00ff00; position: absolute; right: -4px; bottom: -4px; cursor: nwse-resize; z-index: 1001; display: none; border-radius: 50%; border: 1px solid white; box-shadow: 0 0 4px rgba(0,0,0,0.5); }
        .annot-box.selected .resizer { display: block; }
        #marquee { position: absolute; border: 1px dashed #3498db; background: rgba(52,152,219,0.2); pointer-events: none; display: none; z-index: 2000; }
        .prop-group { display: flex; flex-direction: column; gap: 5px; }
        input { background: #444; border: 1px solid #666; color: white; padding: 8px; width: 100%; box-sizing: border-box; }
        #txt_info { background: #111; color: #0f0; padding: 10px; font-weight: bold; border: 1px solid #444; min-height: 20px; word-break: break-all; border-radius: 4px; font-size: 14px; }
        button { cursor: pointer; padding: 10px; background: #444; color: white; border: 1px solid #666; width: 100%; }
        button:hover { background: #555; }
        .btn-row { display: flex; gap: 5px; width: 100%; }
        .btn-row > div, .btn-row button { flex: 1; }
        #msg { margin-left: auto; color: #0f0; transition: color 0.3s; }
        /* 左側搜尋面板：加入捲動條 */
        #search-panel { 
            width: 200px; 
            background: #2a2a2a; 
            border-right: 1px solid #444; 
            display: flex; 
            flex-direction: column; 
            padding: 12px; 
            gap: 10px;
            overflow-y: auto; /* 重點：內容過長時顯示捲動條 */
        }

        /* 右側屬性面板：加入捲動條 */
        #side-panel { 
            width: 250px; 
            background: #2a2a2a; 
            border-left: 1px solid #444; 
            padding: 12px; 
            display: flex; 
            flex-direction: column; 
            gap: 8px; 
            overflow-y: auto; /* 重點：內容過長時顯示捲動條 */
        }

        /* 讓捲動條外觀好看一點 (選配) */

        /* 重疊警告樣式：使用橙色虛線外框 */
        .annot-box.overlap-warning {
            border: 2px dashed #ffc107 !important;
            background: rgba(255, 193, 7, 0.2) !important;
        }
        /* 如果同時被選中且重疊，保留選中樣式但疊加警告感 */
        .annot-box.selected.overlap-warning {
            border-color: #00ff00 !important;
            box-shadow: 0 0 10px #ffc107;
        }

        /* 側邊欄標題 */
        #side-panel h3 { 
            font-size: 16px; 
            margin-bottom: 4px; 
            color: #ddd; 
        }
        #search-panel h3 { 
            font-size: 16px; 
            margin-bottom: 4px; 
            color: #ddd; 
        }
        /* 欄位名稱 (Label) */
        .prop-group label { 
            font-size: 16px; 
            color: #ddd; 
            margin-bottom: 4px; 
        }
        /* 輸入框 (Input) */
        #side-panel input { 
            font-size: 14px; 
            padding: 3px 6px; 
            height: 26px; 
            background: #333;
            border: 1px solid #555;
            color: #fff;
        }
        /* 文字資訊顯示盒 (Text Info) */
        #txt_info { 
            font-size: 14px; 
            padding: 6px; 
            min-height: 20px; 
            line-height: 1.2;
        }
        /* 按鈕文字 */
        #side-panel button {
            font-size: 14px;
            padding: 6px 0;
        }

        .search-item {
            padding: 8px;
            border-bottom: 1px solid #444;
            cursor: pointer;
            transition: background 0.2s;
        }
        .search-item:hover {
            background: #3d3d3d;
        }
        .search-item .page-num {
            color: #3498db;
            font-weight: bold;
            margin-right: 5px;
        }

        /* 壓縮所有 h3 的上下間距 */
        #side-panel h3 { 
            margin: 1px 0 1px 0; /* 上 10px, 下 5px */
        }
        #search-panel h3 { 
            margin: 1px 0 1px 0; /* 上 10px, 下 5px */
        }
        /* 強制收窄分隔線間距 */
        #side-panel hr {
            border: 0;
            border-top: 1px solid #444;
            margin: 8px 0 !important; /* 強制設定上下各留 8px */
        }
        #search-panel hr {
            border: 0;
            border-top: 1px solid #444;
            margin: 8px 0 !important; /* 強制設定上下各留 8px */
        }
        /* 移除可能造成間距的 prop-group 預設外距 */
        .prop-group {
            margin-bottom: 3px; 
        }

        .qc-item {
            padding: 8px;
            background: #333;
            border-radius: 4px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            transition: background 0.2s;
        }
        .qc-item:hover { background: #444; }
        .qc-label { font-size: 12px; color: #bbb; }
        .qc-count { font-size: 12px; font-weight: bold; }
        #count_overlap { color: #ffc107; } /* 黃橙色表示警告 */
        #count_font { color: #ffc107; }    /* 紅色表示規範錯誤 */

        /* ============================================================ */
        /* 🚀 快捷鍵問號按鈕與懸浮字卡核心樣式（修復排版防護） */
        /* ============================================================ */

        /* 1. 預設隱藏字卡，並將其設定為絕對定位（這行能阻止它卡在工具列上） */
        .shortcut-container .shortcut-dropdown {
            display: none;
            position: absolute;
            
            /* 🚀 關鍵修復：從向正下方彈出，改為向左下方斜向彈出 */
            right: 0;                       /* 改為對齊問號按鈕的右側邊界 */
            left: auto;                     /* 解除左側對齊 */
            top: 32px;                      /* 貼近按鈕下緣 */
            
            background: #1e1e1e;
            border: 1px solid #444;
            padding: 12px 16px;
            border-radius: 6px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.6);
            
            /* 🚀 關鍵修復：強制將層級提升到最高，直接浮在所有 PDF 與工具列之上 */
            z-index: 99999 !important; 
        }

        /* 2. 🔥 核心靈魂：當滑鼠指標經過（Hover）整個容器時，字卡才會瞬間彈出 */
        .shortcut-container:hover .shortcut-dropdown {
            display: block !important;     /* 強制覆蓋所有隱藏屬性 */
        }

        /* 3. 鍵盤按鍵美化（讓 Ctrl + Z 看起來像實體按鍵） */
        kbd {
            background: #555;
            color: #fff;
            padding: 2px 5px;
            border-radius: 3px;
            font-family: monospace;
            font-size: 11px;
            box-shadow: 0 1px 0 #111;
        }
    </style>
</head>
<body onkeydown="handleKeyDown(event)">
    <div id="toolbar">
        <button onclick="changePage(-1)" style="width:auto">Prev</button>
        <div style="display:flex; align-items:center; gap:5px;">
            <span>Page:</span>
            <input type="text" id="p_input" min="1" 
                style="width: 50px; text-align: center; padding: 2px; height: 25px;"
                onkeydown="if(event.key==='Enter') jumpToPage(this.value)">
            <span id="p_total">/ -</span>
        </div>
        <button onclick="changePage(1)" style="width:auto">Next</button>
        <button onclick="submitSave()" style="background:#28a745; border:none; padding: 8px 20px; width:auto; margin-left: 10px;">Save XFDF</button>

        <!-- 懸浮出來的快捷鍵提示字卡 -->
        <div class="shortcut-container" style="position: relative; display: inline-block; margin-left: 10px;">
            
            <!-- 這是實體問號按鈕 -->
            <button type="button" style="background: #444; border: 1px solid #555; color: #fff; width: 30px; height: 30px; padding: 0; border-radius: 4px; font-weight: bold; cursor: pointer;">?</button>
            
            <!-- 這是提示字卡（現在被包在裡面了，絕對不會再單獨印在工具列上） -->
            <div class="shortcut-dropdown">
                <h4 style="margin: 0 0 8px 0; color: #2ecc71; font-size: 14px; border-bottom: 1px solid #444; padding-bottom: 4px; font-family: sans-serif;">Shortcuts Guide</h4>
                <div style="display: grid; grid-template-columns: auto auto; gap: 6px 12px; font-size: 12px; color: #ddd; white-space: nowrap; font-family: sans-serif; text-align: left;">
                    <div><kbd>Ctrl</kbd> + <kbd>Z</kbd></div><div>Undo</div>
                    <div><kbd>Delete</kbd></div><div>Delete selected annotation</div>
                    <div><kbd>*ALL*</kbd></div><div>Select all annotations</div>
                </div>
            </div>
        </div>

        <span id="msg">Ready</span>
    </div>

    <div id="main">
        <!-- 新增：左側搜尋面板 -->
        <div id="search-panel" style="width: 200px; background: #2a2a2a; border-right: 1px solid #444; display: flex; flex-direction: column; padding: 10px; gap: 5px;">
            <h3>Bookmark Search</h3>
            <div class="prop-group">
                <!-- 調整一：改為按下 Enter 鍵才觸發搜尋，或者按右邊的按鈕 -->
                <input type="text" id="bookmark_search_input" placeholder="Enter bookmark keyword..." onkeyup="if(event.key==='Enter') triggerBookmarkSearch()">
                <!-- 調整二：新增實體按鈕，與下方註解搜尋完美對稱 -->
                <button onclick="triggerBookmarkSearch()" style="background:#555; color: #0f0; margin-top:8px; width: 100%; padding: 4px;">Search Bookmarks</button>
            </div>
            <hr style="width:100%; border:0; border-top:1px solid #444; margin:5px 0;">

            <h3>Annotation Search</h3>
            <div class="prop-group">
                <input type="text" id="search_input" placeholder="Enter search keyword..." onkeyup="if(event.key==='Enter') searchAnnotations()">
                <button onclick="searchAnnotations()" style="background:#555; color: #0f0; margin-top:8px;">Search Full Document</button>
            </div>
            <hr style="width:100%; border:0; border-top:1px solid #444; margin:5px 0;">

            <h3>QC Summary</h3>
            <div id="qc_summary" style="display: flex; flex-direction: column; gap: 8px;">
                <!-- 警告統計會顯示在這裡 -->
                <div onclick="filterAnomalies('overlap')" class="qc-item">
                    <span class="qc-label">Duplicate Objects:</span> 
                    <span class="qc-count" id="count_overlap">0</span> <!-- 確保 ID 在這個 span 上 -->
                </div>
                <div onclick="filterAnomalies('font')" class="qc-item">
                    <span class="qc-label">Font Mismatches:</span> 
                    <span class="qc-count" id="count_font">0</span>
                </div>
            </div>
            <div id="search_results" style="flex: 1; overflow-y: auto; font-size: 12px; color: #ccc;">
                <!-- 搜尋結果會顯示在這裡 -->
                <div style="color: #666; text-align: center; margin-top: 10px;">No results found</div>
            </div>
        </div>

        <div id="viewer"><div id="canvas-wrapper"><canvas id="c"></canvas><div id="layer"></div><div id="marquee"></div></div></div>
        <div id="side-panel">
            <h3>Property Editor</h3>
            <div class="prop-group">
                <!-- 使用 flex 讓文字與按鈕並排，並拉開間距 -->
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                    <label style="margin: 0;">Content:</label>
                    <!-- 新增粗體狀態按鈕 -->
                    <div style="display: flex; gap: 6px; margin-right: 8px; margin-left: auto;">
                        <!-- B 按鈕 -->
                        <button id="btn_bold_toggle" onclick="toggleSelectBold()" style="
                        width: 24px; 
                        height: 24px; 
                        padding: 0; 
                        margin: 0;
                        font-weight: bold; 
                        font-size: 13px; 
                        background: #444; 
                        border: 1px solid #666; 
                        color: #fff; 
                        border-radius: 3px;
                        border-top-left-radius: 3px;
                        border-bottom-left-radius: 3px;
                        cursor: pointer;
                        line-height: 22px;
                        transition: all 0.2s;
                        ">B</button>

                        <!-- D 按鈕 -->
                        <button id="btn_dash_toggle" onclick="toggleSelectDash()" style="
                        width: 24px; 
                        height: 24px; 
                        padding: 0; 
                        margin: 0;
                        font-weight: bold; 
                        font-size: 13px; 
                        background: #444; 
                        border: 1px solid #666; 
                        color: #fff; 
                        border-radius: 3px;
                        border-top-right-radius: 3px;
                        border-bottom-right-radius: 3px;
                        cursor: pointer;
                        line-height: 22px;
                        transition: all 0.2s;
                        ">D</button>
                    </div>
                </div>
                <textarea id="txt_info" 
                oninput="updateAnnotText(this.value)"
                style="background: #111; color: #0f0; padding: 8px; font-weight: bold; border: 1px solid #444; 
                min-height: 60px; width: 100%; box-sizing: border-box; border-radius: 4px; 
                font-size: 14px; resize: none; cursor: text;"></textarea>
            </div>


            <div class="btn-row" style="margin-bottom:4px;">
                <div class="prop-group"><label>Width:</label><input type="number" id="inp_w" step="0.1"></div>
                <div class="prop-group"><label>Height:</label><input type="number" id="inp_h" step="0.1"></div>
            </div>

            <div class="prop-group" style="margin-bottom:4px;">
                <label>Font Size / Family:</label>
                <div style="display: flex; gap: 8px; width: 100%; margin-top: 4px;">
                    
                    <!-- 左側：字體大小 (寬度佔 50%，維持你原本的 id="inp_fs") -->
                    <input type="number" id="inp_fs" step="0.5" 
                           style="width: 50%; box-sizing: border-box;">
                    
                    <!-- 右側：字體樣式 (寬度佔 50%，維持你原本的樣式、id="inp_font" 與 oninput) -->
                    <input type="text" id="inp_font" oninput="updateAnnotFont(this.value)" 
                           placeholder="Arial" 
                           style="width: 50%; background: #111; color: #0f0; padding: 14px 8px; font-weight: bold; border: 1px solid #444; box-sizing: border-box;">
                </div>
            </div>

            <div class="prop-group" style="margin-bottom:4px;">
                <label>Annotation Color:</label>
                <div class="prop-group" style="margin-bottom:4px;">

                <div style="position: relative; width: 100%;">
                    <select id="inp_color" onchange="handleColorChange(this.value)" 
                            style="width: 100%; background: #333; color: #fff; padding: 6px 30px 6px 6px; border: 1px solid #555; border-radius: 4px; box-sizing: border-box; font-weight: bold; transition: background-color 0.2s; -webkit-appearance: none; -moz-appearance: none; appearance: none; cursor: pointer;">
                        <option value="#BFFFFF" style="background-color: #BFFFFF; color: #000;">Light Blue (Domain 1)</option>
                        <option value="#FFFFAA" style="background-color: #FFFFAA; color: #000;">Light Yellow (Domain 2)</option>
                        <option value="#96FF96" style="background-color: #96FF96; color: #000;">Light Green (Domain 3)</option>
                        <option value="#FFBE96" style="background-color: #FFBE96; color: #000;">Light Orange (Domain 4)</option>
                        <option value="#1CBBEB" style="background-color: #1CBBEB; color: #fff;">Bright Blue (Domain 5)</option>
                        <option value="#CA7EEF" style="background-color: #CA7EEF; color: #fff;">Light Purple (Domain 6)</option>
                    </select>
                    <div style="position: absolute; right: 10px; top: 50%; transform: translateY(-50%); pointer-events: none; color: inherit; font-size: 10px;">▼</div>
                </div>
            </div>
            </div>

            <div class="btn-row" style="margin-bottom:3px;">
                <div class="prop-group"><label>X1:</label><input type="number" id="inp_x1" step="0.1"></div>
                <div class="prop-group"><label>Y1:</label><input type="number" id="inp_y1" step="0.1"></div>
            </div>
            <button onclick="applyBatchProps()" style="background:#3498db; border:none; margin-top:5px;">Apply Properties Globally</button>

            <hr> <!-- 使用上面 CSS 定義的緊湊 hr -->
            <h3>Bulk Edit Full Document</h3>
            <div class="prop-group" style="margin-bottom:3px;">
                <label>Target Text (Exact Match):</label>
                <input type="text" id="batch_filter_text" placeholder="e.g., AE (Adverse Events)">
            </div>
            <button onclick="applyGlobalUpdate()" style="background:#e67e22; border:none; margin-top:5px;">Apply to All Pages</button>
            <hr>

            <h3>Alignment Tools</h3>
            <div class="btn-row" style="margin-bottom:5px;">
                <button onclick="alignBatch('left')">Align Left</button>
                <button onclick="alignBatch('right')">Align Right</button>
            </div>            
            <div class="btn-row" style="margin-bottom:5px;">
                <button onclick="alignBatch('top')">Align Top</button>
                <button onclick="alignBatch('bottom')">Align Bottom</button>
            </div>
            <div class="btn-row" style="margin-bottom:5px;">
                <button onclick="distribute('v')">Dist. Vertically</button>
                <button onclick="distribute('h')">Dist. Horizontally</button>
            </div>
            <div class="btn-row">
                <button onclick="deleteSelected()" style="background:#dc3545; border:none;">Delete</button>
                <button onclick="undo()" style="background:#6c757d; border:none;">Undo</button>
            </div>
        </div>
    </div>
    <script src="/pdf.js"></script>
    <script>
        const pdfjsLib = window.pdfjsLib;
        pdfjsLib.GlobalWorkerOptions.workerSrc = '/pdf.worker.js';
        let pdfDoc = null, pageNum = 1, viewport = null, annots = __ANNOT_JSON__;
        let originalTextMap = {}; 
        let selectedIds = new Set(), isMarquee = false, isDragging = false, isResizing = false;
        let startX, startY, lastMouseX, lastMouseY, historyStack = [];
        let copiedAnnotsArrayBuffer = [];
        let rawBookmarksList = [];

        pdfjsLib.getDocument({data: Uint8Array.from(atob("__PDF_B64__"), c => c.charCodeAt(0))}).promise.then(pdf => {
            pdfDoc = pdf;
            
            // 1. 讀取原本的頁碼設定
            const savedPage = localStorage.getItem('last_pdf_page');
            const startPage = savedPage ? parseInt(savedPage) : (annots.length > 0 ? (annots[0].page + 1) : 1);
            renderPage(startPage); 
            initEvents();

            // 2. 【核心新增】：異步抓取 PDF 內建書籤目錄
            return pdfDoc.getOutline();
        }).then(outline => {
            if (!outline || outline.length === 0) {
                document.getElementById("bookmark_tree").innerHTML = '<div style="color: #666; text-align: center; margin-top: 10px;">此文件無內建書籤</div>';
                return;
            }
            
            // 3. 將巢狀的書籤樹，扁平化轉換為帶有階層標記的陣列
            rawBookmarksList = [];
            processOutlineNode(outline, 0);
            
            // 4. 渲染書籤到畫面上
            renderBookmarks(rawBookmarksList);
        }).catch(err => {
            console.error("讀取書籤失敗:", err);
            document.getElementById("bookmark_tree").innerHTML = '<div style="color: #de6161; text-align: center; margin-top: 10px;">書籤載入異常</div>';
        });

        async function processOutlineNode(nodes, level) {
            for (const node of nodes) {
                let matchedPage = null;
                
                if (node.dest) {
                    try {
                        // 解析 PDF 複雜的 dest 定位節點
                        const explicitDest = typeof node.dest === 'string' ? await pdfDoc.getDestination(node.dest) : node.dest;
                        if (Array.isArray(explicitDest) && explicitDest.length > 0) {
                            const pageRef = explicitDest[0];
                            // 透過 pageRef 換算成 0-based 的索引，再 +1 得到真實頁碼
                            const pageIndex = await pdfDoc.getPageIndex(pageRef);
                            matchedPage = pageIndex + 1;
                        } else if (typeof node.dest === 'number') {
                            matchedPage = node.dest + 1;
                        }
                    } catch (e) {
                        console.warn("解析單個書籤頁碼失敗:", e);
                    }
                }

                // 將解析完頁碼的書籤推入清單中
                rawBookmarksList.push({
                    title: node.title,
                    dest: node.dest,
                    level: level,
                    page: matchedPage
                });

                // 如果有子章節，繼續往下遞迴等待
                if (node.items && node.items.length > 0) {
                    await processOutlineNode(node.items, level + 1);
                }
            }
        }

        // 渲染書籤清單的核心函數
        function renderBookmarks(list) {
            const container = document.getElementById("bookmark_tree");
            container.innerHTML = "";
            
            if (list.length === 0) {
                container.innerHTML = '<div style="color: #666; text-align: center; margin-top: 10px;">No results found</div>';
                return;
            }

            list.forEach(bm => {
                const div = document.createElement("div");
                div.className = `bookmark-item bm-level-${Math.min(bm.level, 3)}`;
                
                // 【核心修改】：如果這條書籤有成功解析出頁碼，就加上 P.XX 的藍色粗體標籤，排版與下方完美統一
                const pageBadge = bm.page ? `<span class="page-num" style="color:#3498db; font-weight:bold; margin-right:8px;">P.${bm.page}</span>` : '';
                div.innerHTML = `${pageBadge}<span>${bm.title}</span>`;
                
                // 點擊直接快速跳頁（因為前面已經算好頁碼了，這裡跳轉可以直接沿用 bm.page，速度更快）
                div.onclick = () => {
                    if (bm.page) {
                        console.log(`[書籤導覽] 跳轉至 P.${bm.page}`);
                        renderPage(bm.page);
                    } else if (bm.dest) {
                        // 兜底防護：萬一前期沒解析出來，維持原本舊有的 dest 解析機制
                        const destPromise = typeof bm.dest === 'string' ? pdfDoc.getDestination(bm.dest) : Promise.resolve(bm.dest);
                        destPromise.then(explicitDest => {
                            if (explicitDest) return pdfDoc.getPageIndex(explicitDest[0]);
                        }).then(pageIndex => {
                            renderPage(pageIndex + 1);
                        });
                    }
                };
                container.appendChild(div);
            });
        }

        // 【關鍵功能】：書籤即時搜尋過濾器
        function triggerBookmarkSearch() {
            const val = document.getElementById("bookmark_search_input").value;
            filterBookmarks(val);
        }
        function filterBookmarks(keyword) {
            const cleanKw = keyword.trim().toLowerCase();
            
            // 💡 關鍵病因：確保這裡抓到的 container ID 是你最下方那個留白的「搜尋預覽結果」面板！
            // 如果你最下方的 div id 叫 search_results，請用 "search_results"
            const container = document.getElementById("search_results") || document.getElementById("all_search_results"); 
            
            if (!container) {
                console.error("找不到下方的搜尋結果面板容器！");
                return;
            }

            if (!cleanKw) {
                container.innerHTML = '<div style="color: #666; text-align: center; margin-top: 10px;">No results found</div>';
                return;
            }
            
            // 當搜書籤時，貼心把註解搜尋框清空，避免雙邊混淆
            document.getElementById("search_input").value = "";

            // 進行陣列過濾
            const filtered = rawBookmarksList.filter(bm => bm.title && bm.title.toLowerCase().includes(cleanKw));
            
            // 開始往最下方的面板灌入 HTML
            container.innerHTML = "";
            
            if (filtered.length === 0) {
                container.innerHTML = '<div style="color: #666; text-align: center; margin-top: 10px;">No results found</div>';
                return;
            }

            // 逐筆生成書籤結果，直接借用你最習慣的樣式噴在最底下
            filtered.forEach(bm => {
                const div = document.createElement("div");
                div.className = "bookmark-item"; // 確保 CSS 有定義這個類別，或者改用 search-result
                div.style.fontSize = "12px";
                div.style.padding = "6px 8px";
                div.style.cursor = "pointer";
                div.style.borderBottom = "1px solid #333";
                
                // 組裝藍色頁碼與書籤文字
                const pageBadge = bm.page ? `<span style="color:#3498db; font-weight:bold; margin-right:8px;">P.${bm.page}</span>` : '<span style="color:#666; margin-right:8px;">P.0</span>';
                div.innerHTML = `${pageBadge}<span style="color:#eee;">${bm.title}</span>`;
                
                // 點擊事件：直接點擊最下方的結果跳頁
                div.onclick = () => {
                    if (bm.page) {
                        console.log(`[書籤導覽] 跳轉至 P.${bm.page}`);
                        renderPage(bm.page);
                    } else if (bm.dest) {
                        // 異步解析兜底
                        const destPromise = typeof bm.dest === 'string' ? pdfDoc.getDestination(bm.dest) : Promise.resolve(bm.dest);
                        destPromise.then(explicitDest => {
                            if (explicitDest) return pdfDoc.getPageIndex(explicitDest);
                        }).then(pageIndex => {
                            if (pageIndex !== undefined) renderPage(pageIndex + 1);
                        });
                    }
                };
                
                container.appendChild(div);
            });
        }

        function generateUniqueXfdfName() {
            // 產生標準的 32 位元 UUID 格式 (含連字號)，專門用來替換和標記最外層的 XFDF name
            return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
                var r = Math.random() * 16 | 0,
                    v = c == 'x' ? r : (r & 0x3 | 0x8);
                return v.toString(16);
            });
        }

        window.addEventListener("keydown", function(e) {
            const activeEl = document.activeElement;
            if (activeEl && (activeEl.id === "txt_info" || activeEl.id === "search_input" || activeEl.id === "p_input")) {
                return; 
            }

            // --------------------------------------------------------
            // 【動作一】：Ctrl + C 批量複製當前所有選中的物件
            // --------------------------------------------------------
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c") {
                if (selectedIds.size > 0) {
                    e.preventDefault(); 
                    copiedAnnotsArrayBuffer = []; // 清空上一次的快取
                    
                    // 遍歷當前畫面上所有被框選/選中的 ID
                    selectedIds.forEach(currentId => {
                        const targetAnnot = annots.find(x => x.id === currentId);
                        if (targetAnnot) {
                            let deepCopy = JSON.parse(JSON.stringify(targetAnnot));
                            deepCopy.sourceId = currentId; // 為每一個複製體綁定各自的母體 ID 供後端定錨
                            copiedAnnotsArrayBuffer.push(deepCopy);
                        }
                    });
                    console.log(`[批量複製成功] 已成功暫存 ${copiedAnnotsArrayBuffer.length} 個標註物件！`);
                }
            }

            // --------------------------------------------------------
            // 【動作二】：Ctrl + V 批量跨頁貼上（精準獨立 UUID 與座標偏移）
            // --------------------------------------------------------
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "v") {
                if (copiedAnnotsArrayBuffer.length > 0) {
                    e.preventDefault();
                    if (typeof saveHistory === "function") saveHistory(); // 支援 Ctrl+Z 還原整批物件

                    const newSelectedIds = new Set(); // 用來記錄新貼上物件的選取狀態
                    
                    // 遍歷快取矩陣，對每個物件進行獨立重構
                    copiedAnnotsArrayBuffer.forEach(item => {
                        let newAnnot = JSON.parse(JSON.stringify(item));
                        const newId = generateUniqueXfdfName(); // 產生全新唯一的 UUID
                        newAnnot.id = newId;
                        
                        // 強制將新物件的頁碼，對齊當前網頁正在顯示的正確頁碼 (0-based)
                        newAnnot.page = pageNum - 1; 
                        
                        // 座標處理邏輯：
                        // 如果是「在同一頁」連續貼上，整批物件集體往右下角偏移 15 像素，保持相對排版結構
                        // 如果是「已經換頁」，則保持原汁原味的相同絕對座標，方便表格對齊
                        if (item.page === newAnnot.page) {
                            const offset = 15;
                            newAnnot.rect[0] += offset;
                            newAnnot.rect[1] += offset;
                            newAnnot.rect[2] += offset;
                            newAnnot.rect[3] += offset;
                        }

                        annots.push(newAnnot);     // 推進主標註資料庫
                        newSelectedIds.add(newId); // 塞入新的選取集合中
                    });

                    // 🎯【極致體感】：自動將畫面的選取焦點切換到這整批剛貼好的新物件上
                    selectedIds = newSelectedIds;

                    // 全介面即時刷新
                    if (typeof draw === "function") draw();
                    if (typeof updatePropInputs === "function") updatePropInputs();
                    if (typeof updateQCSummary === "function") updateQCSummary();
                    
                    console.log(`[批量貼上成功] 已成功將 ${copiedAnnotsArrayBuffer.length} 個物件部署至 P.${pageNum}`);
                }
            }
        });

        function saveHistory() {
            historyStack.push(JSON.parse(JSON.stringify(annots)));
            if (historyStack.length > 30) historyStack.shift();
        }

        function undo() {
            if (historyStack.length > 0) {
                annots = historyStack.pop(); draw();
                document.getElementById("msg").textContent = "已還原";
                setTimeout(() => document.getElementById("msg").textContent = "Ready", 1000);
            }
        }

        // 1. 新增跳頁函式
        function jumpToPage(val) {
            const n = parseInt(val);
            if (n >= 1 && n <= pdfDoc.numPages) {
                renderPage(n);
            } else {
                // 如果輸入無效，彈回當前頁碼
                document.getElementById("p_input").value = pageNum;
            }
        }

        function renderPage(num) {
            // 確保頁碼在有效範圍內
            if (num < 1 || (pdfDoc && num > pdfDoc.numPages)) return;

            pdfDoc.getPage(num).then(page => {
                // 1. 重新定義 viewport
                viewport = page.getViewport({ scale: 1.5 });
                const canvas = document.getElementById("c");
                const ctx = canvas.getContext("2d");
                
                // 2. 設定畫布尺寸
                canvas.width = viewport.width;
                canvas.height = viewport.height;

                // 3. 執行渲染 
                // 加入 intent: 'print' 強制渲染所有底圖內容與圖層 (OCG)
                // 這樣 AE, FA, CO 等彩色區塊即使在隱藏狀態也會被畫出來
                const renderContext = {
                    canvasContext: ctx,
                    viewport: viewport,
                    annotationMode: 2,
                    intent: 'print' 
                };

                page.render(renderContext).promise.then(() => {
                    pageNum = num;
                    document.getElementById("p_input").value = num;
                    document.getElementById("p_total").textContent = " / " + pdfDoc.numPages; 

                    // 4. 成功後存入 localStorage，下次刷新就會回到這一頁
                    localStorage.setItem('last_pdf_page', num);
                    
                    draw();
                });
            });
        }

        // 用來記錄是否已經為當前打字操作存過歷史紀錄
        let isTypingHistorySaved = false;

        // 當鍵盤輸入時即時同步資料
        function updateAnnotText(newText) {
            if (selectedIds.size === 1) {
                const id = Array.from(selectedIds)[0];
                const a = annots.find(x => x.id === id);
                
                if (a) {
                    // 代表這是使用者「這一次點進來」準備改字的第一發，立刻把最原始的字鎖死！
                    if (!(id in originalTextMap)) {
                        originalTextMap[id] = a.text || "";
                    }
                    // 第一次打字時存入歷史紀錄，方便 Ctrl+Z 一鍵還原整段字
                    if (!isTypingHistorySaved) {
                        saveHistory();
                        isTypingHistorySaved = true;
                    }
                    
                    // 更新記憶體資料
                    a.text = newText;
                    
                    // 即時重新渲染畫面，讓畫布上的文字同步改變
                    draw();
                }
            }
        }

        // 監聽文字框的滑鼠點擊/聚焦事件，當使用者換物件或離開時，重設歷史紀錄旗標
        document.getElementById("txt_info").addEventListener("focus", () => {
            isTypingHistorySaved = false;
        });
        document.getElementById("txt_info").addEventListener("blur", () => {
            isTypingHistorySaved = false;
        });

        function updateAnnotFont(newFont) {
            if (selectedIds.size === 1) {
                const id = Array.from(selectedIds)[0];
                const a = annots.find(x => x.id === id);
                if (a) {
                    a.font = newFont;
                    
                    // MSG 2.0 規範即時視覺反饋
                    const fontI = document.getElementById("inp_font");
                    if (newFont.toLowerCase().includes("arial")) {
                        fontI.style.color = "#0f0"; // 合規綠色
                    } else {
                        fontI.style.color = "#ffeb3b"; // 不合規黃色
                    }
                    // 不需要 draw() 重新渲染，因為字體名稱不影響紅框繪製
                }
            }
        }

        // 更新左側面板的警告統計
        function updateQCSummary() {
            const overlapIds = new Set();
            // 遍歷「所有」標註（全文件統計）
            for (let i = 0; i < annots.length; i++) {
                for (let j = i + 1; j < annots.length; j++) {
                    if (annots[i].page === annots[j].page && checkOverlapping(annots[i], annots[j], 0.3)) {
                        overlapIds.add(annots[i].id);
                        overlapIds.add(annots[j].id);
                    }
                }
            }

            const fontAnomalies = annots.filter(a => !a.font || !a.font.toLowerCase().includes('arial'));

            // 更新介面
            const overlapNum = document.getElementById("count_overlap");
            const fontNum = document.getElementById("count_font");
            
            if (overlapNum) overlapNum.textContent = overlapIds.size;
            if (fontNum) fontNum.textContent = fontAnomalies.length;
        }


        // 點擊摘要後，直接在下方的搜尋結果清單列出這些問題物件
        function filterAnomalies(type) {
            const container = document.getElementById("search_results");
            container.innerHTML = "";
            let results = [];

            if (type === 'font') {
                results = annots.filter(a => !a.font || !a.font.toLowerCase().includes('arial'));
                document.getElementById("msg").textContent = "Filtered: Font Mismatches";
            } 
            else if (type === 'overlap') {
                // --- 補上這段偵測重疊的邏輯 ---
                const overlapIds = new Set();
                for (let i = 0; i < annots.length; i++) {
                    for (let j = i + 1; j < annots.length; j++) {
                        if (annots[i].page === annots[j].page && checkOverlapping(annots[i], annots[j], 0.3)) {
                            overlapIds.add(annots[i].id);
                            overlapIds.add(annots[j].id);
                        }
                    }
                }
                // 從所有標註中找出 ID 在 overlapIds 裡的物件
                results = annots.filter(a => overlapIds.has(a.id));
                document.getElementById("msg").textContent = "Filtered: Overlapping Annotations";
                // --------------------------
            }

            // 渲染清單 (這部分你原本的程式碼應該已經有了)
            results.forEach(a => {
                const item = document.createElement("div");
                item.className = "search-item";
                item.innerHTML = `<span class="page-num" style="color:#ffc107">P.${a.page + 1}</span> <span>${a.text || "(無文字)"}</span>`;
                item.onclick = () => { 
                    renderPage(a.page + 1); 
                    selectedIds.clear(); 
                    selectedIds.add(a.id); 
                    setTimeout(() => {
                        draw(); 
                        updatePropInputs(); // 關鍵：手動觸發右側面板更新屬性
                    }, 150); // 稍微加長一點點延遲(150ms)，確保 PDF 渲染更穩定
                };
                container.appendChild(item);
            });
        }

        function searchAnnotations() {
            const term = document.getElementById("search_input").value.trim();
            const container = document.getElementById("search_results");
            container.innerHTML = "";

            if (!term) return;

            // 篩選所有頁面匹配的標註
            const results = annots.filter(a => a.text && a.text.includes(term));

            if (results.length === 0) {
                container.innerHTML = `<div style="color: #666; text-align: center; margin-top: 20px;">未找到匹配項</div>`;
                return;
            }

            results.forEach(a => {
                const item = document.createElement("div");
                item.className = "search-item";
                // 顯示頁碼 (a.page 是 0-based) 與文字預覽
                item.innerHTML = `<span class="page-num">P.${a.page + 1}</span> <span>${a.text.substring(0, 20)}</span>`;
                
                // 點擊功能：跳轉頁面並選中
                item.onclick = () => {
                    renderPage(a.page + 1); 
                    selectedIds.clear();
                    selectedIds.add(a.id);
                    // 延遲一點點確保頁面渲染完後再畫框
                    setTimeout(() => {
                        draw(); 
                        updatePropInputs(); 
                    }, 150); // 稍微增加到 150ms 確保頁面切換更穩定
                };
                container.appendChild(item);
            });

            // 同步更新右側的「全文件批量修改」目標文字，方便一鍵套用
            document.getElementById("batch_filter_text").value = term;
        }

        function checkOverlapping(a1, a2, threshold = 0.3) {
            const r1 = a1.rect;
            const r2 = a2.rect;

            // 計算交集區域 (Intersection)
            const xLeft = Math.max(r1[0], r2[0]);
            const yBottom = Math.max(r1[1], r2[1]);
            const xRight = Math.min(r1[2], r2[2]);
            const yTop = Math.min(r1[3], r2[3]);

            if (xRight <= xLeft || yTop <= yBottom) return false; // 無重疊

            const intersectArea = (xRight - xLeft) * (yTop - yBottom);
            const area1 = Math.abs(r1[2] - r1[0]) * Math.abs(r1[3] - r1[1]);
            const area2 = Math.abs(r2[2] - r2[0]) * Math.abs(r2[3] - r2[1]);

            // 如果交集佔任一物件面積超過 threshold，就視為嚴重重疊
            return (intersectArea / area1 > threshold) || (intersectArea / area2 > threshold);
        }

        function toggleSelectBold() {
            // 確保當前只有選中一個物件
            if (selectedIds.size === 1) {
                const id = Array.from(selectedIds)[0];
                const a = annots.find(x => x.id === id);
                if (a) {
                    saveHistory(); // 紀錄歷史紀錄，支援 Ctrl+Z 還原
                    
                    // 1. 切換 Boolean 狀態
                    a.isBold = !a.isBold; 
                    
                    // 2. 顯示訊息提示
                    document.getElementById("msg").textContent = a.isBold ? "已設定為粗體" : "已取消粗體";
                    document.getElementById("msg").style.color = "#0f0";
                    
                    // 3. 重新繪製畫布（文字的 fontWeight 會依據 a.isBold 即時改變）
                    draw(); 
                }
            }
        }

        function toggleSelectDash() {
            // 確保當前只有選中一個物件
            if (selectedIds.size === 1) {
                // 💡 修正 1：加上 [0]，確保拿到單一 ID，而不是整個陣列
                const id = Array.from(selectedIds)[0]; 
                const a = annots.find(x => x.id === id);
                if (a) {
                    saveHistory(); // 紀錄歷史紀錄，支援 Ctrl+Z 還原
                    
                    // 💡 修正 2：完全向 Bold 模式看齊！
                    // 不用兩個變數打架，直接對決 borderStyle，是 dash 就變 solid，反之亦然
                    if (a.borderStyle === "dash" || a.isDash === true) {
                        a.borderStyle = "solid";
                        a.isDash = false;
                    } else {
                        a.borderStyle = "dash";
                        a.isDash = true;
                    }
                    
                    // 2. 顯示訊息提示
                    document.getElementById("msg").textContent = (a.borderStyle === "dash") ? "已設定為虛線" : "已取消虛線";
                    document.getElementById("msg").style.color = "#0f0";
                    
                    // 3. 重新繪製畫布（現在 borderStyle 徹底變更，畫布與右側按鈕顏色就會立刻跟著變！）
                    draw(); 
                }
            }
        }


        function draw() {
            const layer = document.getElementById("layer"); 
            layer.innerHTML = "";
            const m = viewport.transform;
            const pageAnnots = annots.filter(a => a.page === (pageNum - 1));
            
            // 偵測重疊的 ID 集合
            const overlappingIds = new Set();
            for (let i = 0; i < pageAnnots.length; i++) {
                for (let j = i + 1; j < pageAnnots.length; j++) {
                    if (checkOverlapping(pageAnnots[i], pageAnnots[j], 0.3)) {
                        overlappingIds.add(pageAnnots[i].id);
                        overlappingIds.add(pageAnnots[j].id);
                    }
                }
            }
            
            pageAnnots.forEach(a => {
                const r = a.rect;
                const x1 = r[0]*m[0] + r[1]*m[2] + m[4], y1 = r[0]*m[1] + r[1]*m[3] + m[5];
                const x2 = r[2]*m[0] + r[3]*m[2] + m[4], y2 = r[2]*m[1] + r[3]*m[3] + m[5];
                
                const div = document.createElement("div");
                let className = "annot-box" + (selectedIds.has(a.id) ? " selected" : "");
                if (overlappingIds.has(a.id)) className += " overlap-warning";
                div.className = className;
                
                div.style.left = Math.min(x1, x2) + "px"; 
                div.style.top = Math.min(y1, y2) + "px";
                div.style.width = Math.abs(x2 - x1) + "px"; 
                div.style.height = Math.abs(y2 - y1) + "px";
                div.dataset.id = a.id;

                // ============================================================
                // 🛠️【修改：遵照 MSG 2.0 規範，直接套用 100% 實色背景，不帶透明度】
                // ============================================================
                const cssBorderStyle = (a.borderStyle === "dash" || a.isDash === true) ? "dashed" : "solid";
                
                if (selectedIds.has(a.id)) {
                    // 被選取時：邊框加粗為 2px，顏色為螢光綠，並且【同步保留虛線樣式】
                    div.style.border = `2px ${cssBorderStyle} #00ff00`; 
                } else {
                    // 平常沒選取時：維持標準 2px、黑色邊框，並依據新舊狀態決定實線或虛線
                    div.style.border = `2px ${cssBorderStyle} black`;
                }
                div.style.backgroundColor = a.color || "#FFFFFF";

                
                // 前端 draw() 函數內部的文字產生片段
                const isBold = a.isBold ? "bold" : "normal";
                const currentScale = (typeof viewport !== 'undefined' && viewport.scale) ? viewport.scale : 1.5;
                
                const textSpan = document.createElement("span");
                textSpan.textContent = a.text || "";
                
                textSpan.style.position = "absolute";
                textSpan.style.left = "0px";
                textSpan.style.top = "0px";
                textSpan.style.width = "100%";
                textSpan.style.height = "100%";
                
                // 強制靠左對齊，置中對齊
                textSpan.style.display = "flex";
                textSpan.style.justifyContent = "flex-start"; 
                textSpan.style.alignItems = "flex-start";         
                
                // 左側內縮補償，達到像素級完美對齊
                textSpan.style.boxSizing = "border-box";
                textSpan.style.paddingLeft = (1.0 * currentScale) + "px"; 
                textSpan.style.paddingRight = (0.5 * currentScale) + "px"; 
                textSpan.style.paddingTop = (1.15 * currentScale) + "px";
                
                textSpan.style.whiteSpace = "pre-wrap"; 
                textSpan.style.wordBreak = "break-word";
                textSpan.style.lineHeight = "1.15";
                textSpan.style.letterSpacing = "0.001em"; 

                textSpan.style.color = "black";
                textSpan.style.fontFamily = "Arial, sans-serif";
                textSpan.style.fontWeight = isBold; // 套用粗體狀態
                textSpan.style.pointerEvents = "none"; 
                textSpan.style.fontSize = (a.fontSize || 10) * currentScale + "px";
                
                // ============================================================
                // 【新增：消除網頁與 PDF 字體飄移的關鍵屬性】
                // ============================================================
                textSpan.style.fontKerning = "none"; 
                textSpan.style.fontVariantLigatures = "none";
                textSpan.style.textRendering = "geometricPrecision"; 
                textSpan.style.webkitFontSmoothing = "antialiased";  

                div.appendChild(textSpan);
                
                // 這裡維持你原本的控制點
                const rs = document.createElement("div"); 
                rs.className = "resizer"; 
                div.appendChild(rs);
                
                layer.appendChild(div);
            });
            
            // 更新狀態列警告
            const msgEl = document.getElementById("msg");
            if (overlappingIds.size > 0) {
                msgEl.textContent = ` Warning: ${overlappingIds.size} objects are significantly overlapping`;
                msgEl.style.color = "#ffc107";
            } else {
                msgEl.textContent = "Ready";
                msgEl.style.color = "#0f0";
            }
            updatePropInputs();
            updateQCSummary();
        }

        function handleColorChange(newColor) {
            const colorEl = document.getElementById("inp_color");
            if (!colorEl) return;
            
            // 如果選回了預設的空值選項，不做任何處理
            if (newColor === "") {
                delete colorEl.dataset.chosenColor;
                colorEl.style.backgroundColor = "#333";
                colorEl.style.color = "#fff";
                return;
            }
            
            const upperColor = newColor.toUpperCase().trim();
            
            // 1. 將新顏色精確記錄在暫存標記中，等按按鈕時才讀取
            colorEl.dataset.chosenColor = upperColor;
            
            // 2. 讓選單外殼立刻變色（多選時面板成功更新！）
            colorEl.style.backgroundColor = upperColor;
            if (upperColor === "#1CBBEB" || upperColor === "#CA7EEF") {
                colorEl.style.color = "#ffffff";
            } else {
                colorEl.style.color = "#000000"; 
            }      
        }

        function applyGlobalUpdate() {
                const targetText = document.getElementById("batch_filter_text").value.trim();
                if (!targetText) {
                    alert("請輸入要匹配的目標文字、*ALL* 關鍵字 或 *#HEX into NEW_COLOR* 指令");
                    return;
                }

                // 取得當前面板上的設定值 (與 applyBatchProps 邏輯一致)
                const nw = parseFloat(document.getElementById("inp_w").value);
                const nh = parseFloat(document.getElementById("inp_h").value);
                const nfs = parseFloat(document.getElementById("inp_fs").value);
                const fontI = document.getElementById("inp_font");
                const colorEl = document.getElementById("inp_color");

                const nfont = fontI ? fontI.value.trim() : "";
                
                // 【優化防呆】：優先拿暫存顏色，若沒有則直接拿下拉選單當前的 value
                let ncolor = (colorEl && colorEl.dataset.chosenColor) ? colorEl.dataset.chosenColor : "";
                if (!ncolor && colorEl) {
                    ncolor = colorEl.value;
                }

                // ============================================================
                // 💡【解析動態換色指令】：格式如 "#FFFF00 into NEW_COLOR"
                // ============================================================
                let isColorReplaceMode = false;
                let srcColorToReplace = "";

                if (targetText.toLowerCase().includes(" into new_color")) {
                    isColorReplaceMode = true;
                    // 提取 " into " 前面的怪顏色字串，並轉成大寫
                    srcColorToReplace = targetText.split(/ into /i)[0].trim().toUpperCase();
                    // 防呆機制：如果使用者忘記輸入 # 號，自動幫他補上
                    if (srcColorToReplace && !srcColorToReplace.startsWith("#")) {
                        srcColorToReplace = "#" + srcColorToReplace;
                    }
                    
                    // 強制檢查：動態換色模式下，面板（或下拉選單）必須要有一個選好的新顏色
                    if (!ncolor || ncolor === "#FFFFFF") {
                        alert("使用動態換色指令時，請先在上方『註解顏色』選單中選擇一個標準新顏色！");
                        return;
                    }
                } else {
                    // 原本的防錯檢查：如果不是換色模式，只要面板上有填寫「任何一個」屬性，就放行一鍵套用
                    if (isNaN(nw) && isNaN(nh) && isNaN(nfs) && !nfont && !ncolor) {
                        alert("請至少在上方屬性欄輸入或選擇一個要修改的值 (寬、高、字體、樣式或顏色)");
                        return;
                    }
                }

                saveHistory();
                let count = 0;

                // 遍歷所有頁面的標註
                annots.forEach(a => {
                    // ============================================================
                    // 💡【核心創新擴充】：三合一匹配邏輯
                    // 1. 顏色匹配模式：如果符合舊顏色，直接判定命中
                    // 2. *ALL* 關鍵字 $\rightarrow$ 100% 無條件命中全文件所有標註！
                    // 3. 精準文字匹配 $\rightarrow$ 走你原本寫好的精準文字匹配
                    // ============================================================
                    let isMatch = false;
                    
                    if (isColorReplaceMode) {
                        isMatch = (a.color && a.color.toUpperCase() === srcColorToReplace);
                    } else {
                        isMatch = (targetText === "*ALL*") || (a.text && a.text.trim() === targetText);
                    }

                    if (isMatch) {
                        // 修改寬度 (動態換色模式下，如果使用者有填寬高也會順便改，極具彈性)
                        if (!isNaN(nw)) {
                            a.rect[2] = a.rect[0] + nw;
                        }
                        // 修改高度
                        if (!isNaN(nh)) {
                            a.rect[3] = a.rect[1] + nh;
                        }
                        // 修改字體大小
                        if (!isNaN(nfs)) {
                            a.fontSize = nfs;
                        }
                        // 批量修改字體樣式
                        if (nfont) {
                            a.font = nfont;
                        }
                        // 批量修改註解顏色
                        // 在動態換色模式下，這裡會直接套用你在上方獲取的選單新顏色 `ncolor`
                        if (ncolor) {
                            a.color = ncolor;
                        }
                        count++;
                    }
                });

                if (count > 0) {
                    draw(); // 重新渲染當前頁面
                    
                    if (isColorReplaceMode) {
                        document.getElementById("msg").textContent = `顏色清洗完成：共更新 ${count} 處舊顏色`;
                    } else {
                        document.getElementById("msg").textContent = `批量修改完成：共更新 ${count} 處`;
                    }
                    document.getElementById("msg").style.color = "#0f0";
                    
                    // ============================================================
                    // 一鍵套用完成後，隨手清除選單的換色暫存，防止下一次干擾
                    // ============================================================
                    if (colorEl) delete colorEl.dataset.chosenColor;
                } else {
                    alert("未找到符合條件的標註內容或顏色");
                }
            }


        function updatePropInputs() {
            const wI = document.getElementById("inp_w"), hI = document.getElementById("inp_h");
            const fsI = document.getElementById("inp_fs"), tD = document.getElementById("txt_info");
            const x1I = document.getElementById("inp_x1"), y1I = document.getElementById("inp_y1");
            const fontI = document.getElementById("inp_font");
            // 【新增】：獲取你的 MSG 2.0 顏色下拉選單物件
            const colorI = document.getElementById("inp_color");

            if (selectedIds.size === 1) {
                tD.readOnly = false;
                tD.style.cursor = "text";
                // --- 單選模式：顯示該物件的詳細屬性 ---
                const a = annots.find(x => x.id === Array.from(selectedIds)[0]);
                if (!a) return;

                const activeEl = document.activeElement;

                // 只有在非輸入狀態時才由資料庫同步數值，避免打字時被刷掉
                if (activeEl !== tD) tD.value = a.text || "";
                if (activeEl !== wI) wI.value = Math.abs(a.rect[2] - a.rect[0]).toFixed(1);
                if (activeEl !== hI) hI.value = Math.abs(a.rect[3] - a.rect[1]).toFixed(1);
                if (activeEl !== fsI) fsI.value = a.fontSize || 10;
                if (activeEl !== x1I) x1I.value = a.rect[0].toFixed(1);
                if (activeEl !== y1I) y1I.value = a.rect[1].toFixed(1);

                const currentFont = a.font || "未定義";
                if (activeEl !== fontI) fontI.value = currentFont;
                fontI.style.color = currentFont.toLowerCase().includes("arial") ? "#0f0" : "#ffeb3b";

                // ============================================================
                // 🛠️【新增：單選物件時，同步更新下拉選單的值、外殼背景與文字顏色】
                // ============================================================
                if (colorI && activeEl !== colorI) {
                    let rawColor = a.color ? a.color.trim() : "#FFFFFF";
                    
                    if (rawColor.startsWith("#") && rawColor.length === 7) {
                        const currentHex = rawColor.toUpperCase();
                        
                        // 1. 先徹底清除所有先前可能殘留的「未知色號」臨時選項
                        const oldTempOption = document.getElementById("temp_unknown_color_opt");
                        if (oldTempOption) {
                            oldTempOption.remove(); 
                        }

                        // 2. 檢查這個顏色是不是標準的 MSG 規範色
                        // 建立一個標準色號陣列 (對應你 HTML 裡的 6 個標準色 + 純白)
                        const standardColors = ["#BFFFFF", "#FFFFAA", "#96FF96", "#FFBE96", "#1CBBEB", "#CA7EEF", "#FFFFFF"];
                        const isStandard = standardColors.includes(currentHex);

                        // 3. 如果不是標準色，立刻動態追加臨時選項，並強制塞入
                        if (!isStandard) {
                            const newOpt = document.createElement("option");
                            newOpt.id = "temp_unknown_color_opt";
                            newOpt.value = currentHex;
                            // 關鍵：強制寫入 textContent，保證畫面上一定看得到字！
                            newOpt.textContent = `Unknown Color (${currentHex})`; 
                            newOpt.style.backgroundColor = currentHex;
                            newOpt.style.color = "#000000";
                            
                            // 暴力塞在選單的最前面
                            colorI.appendChild(newOpt); 
                        }

                        // 4. 強制將選單的值指過去，並同步更新背景與文字顏色
                        colorI.value = currentHex;
                        colorI.style.backgroundColor = currentHex;

                        // 5. 文字對比色防錯處理
                        colorI.style.color = "#000000";

                        // 6. 自動把換色指令填入最下方的批量修改框
                        if (!isStandard) {
                            const batchInput = document.getElementById("batch_filter_text");
                            if (batchInput) {
                                batchInput.value = `${currentHex} into NEW_COLOR`;
                            }
                        }
                    }
                }

                // === 新增：同步粗體, 虛線按鈕外觀 ===
                const btnBold = document.getElementById("btn_bold_toggle");
                if (btnBold) {
                    if (a.isBold) {
                        // 如果是粗體，按鈕打亮（綠色背景、黑字）
                        btnBold.style.background = "#0f0";
                        btnBold.style.color = "#000";
                        btnBold.style.borderColor = "#0f0";
                    } else {
                        // 如果不是粗體，維持原本的暗色外觀
                        btnBold.style.background = "#444";
                        btnBold.style.color = "#fff";
                        btnBold.style.borderColor = "#666";
                    }
                    // 單選時確保按鈕可點擊
                    btnBold.disabled = false;
                    btnBold.style.opacity = "1";
                }

                const btnDash = document.getElementById("btn_dash_toggle");
                if (btnDash) {
                    // 💡 關鍵修正：只要符合舊的 borderStyle 或是新的 isDash，按鈕就要亮起螢光綠！
                    if (a.borderStyle === "dash" || a.isDash === true) {
                        // 如果是虛線，D 按鈕打亮（綠色背景、黑字）
                        btnDash.style.background = "#0f0";
                        btnDash.style.color = "#000";
                        btnDash.style.borderColor = "#0f0";
                    } else {
                        // 如果不是虛線，維持原本的暗色外觀
                        btnDash.style.background = "#444";
                        btnDash.style.color = "#fff";
                        btnDash.style.borderColor = "#666";
                    }
                    // 單選時確保按鈕可點擊
                    btnDash.disabled = false;
                    btnDash.style.opacity = "1";
                }

                // ============================================================
                // 💡【關鍵同步】：點選新物件時，隨手重置暫存，防止前一個物件的舊字干擾
                // ============================================================
                if (a) {
                    a.oldText = a.text; 
                }

            } else if (selectedIds.size > 1) {
                // --- 多選模式：清空座標與尺寸欄位以防止批量誤改 ---
                // 顯示選取數量
                tD.value = `已選取 ${selectedIds.size} 個物件`;
                tD.readOnly = true;
                tD.style.cursor = "not-allowed";

                // 重要：清空這些欄位。
                x1I.value = "";
                y1I.value = "";

                wI.value = ""; 
                hI.value = "";
                fsI.value = "";
                fontI.value = "";
                fontI.style.color = "#888";

                // ============================================================
                // 🛠️【新增：多選物件時，將選單外殼恢復成你要求不干擾的乾淨淺灰色】
                // ============================================================
                if (colorI) {
                    colorI.value = ""; // 多選時不咬死任何一色
                    colorI.style.backgroundColor = "#333"; // 回歸 Font Size 的淺灰底色
                    colorI.style.color = "#fff";
                }

                // 在多選與無選取模式的區塊內皆加上這段：
                const btnBold = document.getElementById("btn_bold_toggle");
                if (btnBold) {
                    btnBold.style.background = "#222";
                    btnBold.style.color = "#555";
                    btnBold.style.borderColor = "#333";
                    btnBold.disabled = true; // 多選時不開放直接點擊單一開關
                }

                const btnDash = document.getElementById("btn_dash_toggle");
                if (btnDash) {
                    btnDash.style.background = "#222";
                    btnDash.style.color = "#555";
                    btnDash.style.borderColor = "#333";
                    btnDash.disabled = true; // 多選或空選時強制停用
                }

            } else {
                tD.value = "";
                tD.readOnly = true;
                tD.style.cursor = "not-allowed";
                // --- 無選取模式：清空所有欄位 ---
                [tD, wI, hI, fsI, x1I, y1I, fontI].forEach(el => {
                    if (el) el.value = "";
                });
                if (fontI) fontI.style.color = "#888";

                // ============================================================
                // 🛠️【新增：完全無選取物件時，重設選單外殼背景為預設淺灰色】
                // ============================================================
                if (colorI) {
                    colorI.value = "#FFFFFF"; // 預設指回白色
                    colorI.style.backgroundColor = "#333"; // 回歸 Font Size 的淺灰底色
                    colorI.style.color = "#fff";
                }

                // 在多選與無選取模式的區塊內皆加上這段：
                const btnBold = document.getElementById("btn_bold_toggle");
                if (btnBold) {
                    btnBold.style.background = "#222";
                    btnBold.style.color = "#555";
                    btnBold.style.borderColor = "#333";
                    btnBold.disabled = true; // 多選時不開放直接點擊單一開關
                }

                const btnDash = document.getElementById("btn_dash_toggle");
                if (btnDash) {
                    btnDash.style.background = "#222";
                    btnDash.style.color = "#555";
                    btnDash.style.borderColor = "#333";
                    btnDash.disabled = true; // 多選或空選時強制停用
                }
            }
        }


        function deleteSelected() {
            if (selectedIds.size === 0) return;
            if (confirm(`確定刪除選中的 ${selectedIds.size} 個物件？`)) {
                saveHistory(); annots = annots.filter(a => !selectedIds.has(a.id)); selectedIds.clear(); draw();
            }
        }

        function initEvents() {
            const layer = document.getElementById("layer"), mq = document.getElementById("marquee");
            layer.onmousedown = (e) => {
                lastMouseX = e.clientX; lastMouseY = e.clientY;
                if (e.target.classList.contains("resizer")) {
                    saveHistory(); isResizing = true; e.stopPropagation();
                } else if (e.target.classList.contains("annot-box")) {
                    saveHistory(); isDragging = true; const id = e.target.dataset.id;
                    if (!e.ctrlKey && !selectedIds.has(id)) selectedIds.clear();
                    selectedIds.add(id); draw();
                } else {
                    isMarquee = true; if (!e.ctrlKey) selectedIds.clear();
                    const rect = layer.getBoundingClientRect();
                    startX = e.clientX - rect.left; startY = e.clientY - rect.top;
                    mq.style.display = "block"; mq.style.width = "0"; mq.style.height = "0"; draw();
                }
            };
            window.onmousemove = (e) => {
                const dx = (e.clientX - lastMouseX) / 1.5, dy = (e.clientY - lastMouseY) / 1.5;
                if (isResizing) {
                    const a = annots.find(x => x.id === Array.from(selectedIds)[0]);
                    
                    // dx: 螢幕向右為正。a.rect[2] 是右邊界，增加它就是往右延伸。
                    a.rect[2] += dx; 
                    
                    // dy: 螢幕向下為正。a.rect[1] 是底部邊界。
                    // 在 PDF 座標中，下方數值較小，所以「往下延伸」等於「減小底部的 Y 座標」。
                    a.rect[1] -= dy; 
                    
                    lastMouseX = e.clientX; 
                    lastMouseY = e.clientY; 
                    draw();
                } else if (isDragging) {
                    selectedIds.forEach(id => {
                        const a = annots.find(x => x.id === id);
                        a.rect[0] += dx; a.rect[2] += dx; a.rect[1] -= dy; a.rect[3] -= dy;
                    });
                    lastMouseX = e.clientX; lastMouseY = e.clientY; draw();
                } else if (isMarquee) {
                    const rect = layer.getBoundingClientRect();
                    const curX = e.clientX - rect.left, curY = e.clientY - rect.top;
                    const mx = Math.min(startX, curX), my = Math.min(startY, curY), mw = Math.abs(curX - startX), mh = Math.abs(curY - startY);
                    mq.style.left = mx + "px"; mq.style.top = my + "px"; mq.style.width = mw + "px"; mq.style.height = mh + "px";
                    document.querySelectorAll(".annot-box").forEach(box => {
                        const bx = box.offsetLeft, by = box.offsetTop, bw = box.offsetWidth, bh = box.offsetHeight;
                        if (bx < mx + mw && bx + bw > mx && by < my + mh && by + bh > my) selectedIds.add(box.dataset.id);
                    });
                    draw();
                }
            };
            window.onmouseup = () => { isDragging = false; isMarquee = false; isResizing = false; mq.style.display = "none"; };
        }

        function handleKeyDown(e) {
            // ============================================================
            // 💡【修改 1】：將 TEXTAREA 也納入防禦，確保打字時完全不觸發任何快捷鍵
            // ============================================================
            if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
            
            if ((e.ctrlKey || e.metaKey) && e.key === 'z') { e.preventDefault(); undo(); return; }
            if (selectedIds.size === 0) return;
            
            // ============================================================
            // 💡【修改 2】：移除 e.key === "Backspace"，嚴格限制只有 Delete 才能刪除
            // ============================================================
            if (e.key === "Delete") { deleteSelected(); return; }
            
            const step = (e.shiftKey ? 5 : 1) / 1.5;
            selectedIds.forEach(id => {
                const a = annots.find(x => x.id === id);
                if (!a) return; // 微調：加上防錯，確保選中的 ID 依然存在
                
                if (e.key === "ArrowLeft") { a.rect[0] -= step; a.rect[2] -= step; }
                if (e.key === "ArrowRight") { a.rect[0] += step; a.rect[2] += step; }
                if (e.key === "ArrowUp") { a.rect[1] += step; a.rect[3] += step; }
                if (e.key === "ArrowDown") { a.rect[1] -= step; a.rect[3] -= step; }
            });
            draw(); 
            if (e.key.startsWith("Arrow")) e.preventDefault();
        }


        function applyBatchProps() {
            saveHistory();
            const nw = parseFloat(document.getElementById("inp_w").value), nh = parseFloat(document.getElementById("inp_h").value);
            const nfs = parseFloat(document.getElementById("inp_fs").value), nx1 = parseFloat(document.getElementById("inp_x1").value), ny1 = parseFloat(document.getElementById("inp_y1").value);
            
            // ============================================================
            // 💡【關鍵新增】：獲取你的顏色下拉選單物件
            // ============================================================
            const colorEl = document.getElementById("inp_color");

            selectedIds.forEach(id => {
                const a = annots.find(x => x.id === id);
                if (!a) return; // 防錯機制

                if (!isNaN(nx1)) { let w = a.rect[2]-a.rect[0]; a.rect[0]=nx1; a.rect[2]=nx1+w; }
                if (!isNaN(ny1)) { let h = a.rect[3]-a.rect[1]; a.rect[1]=ny1; a.rect[3]=ny1+h; }
                if (!isNaN(nw)) a.rect[2] = a.rect[0] + nw;
                if (!isNaN(nh)) a.rect[3] = a.rect[1] + nh;
                if (!isNaN(nfs)) a.fontSize = nfs;

                // ============================================================
                // 💡【關鍵新增】：只有當選單有被更動、存有新顏色時，按下按鈕才寫入記憶體！
                // ============================================================
                if (colorEl && colorEl.dataset.chosenColor) {
                    a.color = colorEl.dataset.chosenColor;
                }
            });

            // 批量寫入完成後，執行你原本寫好的核心 draw()，讓畫布上的方框一鍵集體進行變色！
            draw();

            // ============================================================
            // 💡【關鍵新增】：重新繪製完成後，隨手清除選單的多選換色暫存，防止下一次干擾
            // ============================================================
            if (colorEl) delete colorEl.dataset.chosenColor;
        }


        function alignBatch(type) {
            if (selectedIds.size < 2) return; // 至少選兩個才對齊
            saveHistory();
            
            const selectedAnnots = annots.filter(a => selectedIds.has(a.id));
            
            // 取得基準值
            let base;
            if (type === 'left')   base = Math.min(...selectedAnnots.map(a => a.rect[0]));
            if (type === 'right')  base = Math.max(...selectedAnnots.map(a => a.rect[2]));
            if (type === 'top')    base = Math.max(...selectedAnnots.map(a => a.rect[1])); // 注意 PDF 座標 y 往上
            if (type === 'bottom') base = Math.min(...selectedAnnots.map(a => a.rect[3]));

            selectedAnnots.forEach(a => {
                const w = a.rect[2] - a.rect[0];
                const h = a.rect[3] - a.rect[1];
                
                if (type === 'left') {
                    a.rect[0] = base; a.rect[2] = base + w;
                } else if (type === 'right') {
                    a.rect[2] = base; a.rect[0] = base - w;
                } else if (type === 'top') {
                    a.rect[1] = base; a.rect[3] = base + h;
                } else if (type === 'bottom') {
                    a.rect[3] = base; a.rect[1] = base - h;
                }
            });
            
            draw();
            document.getElementById("msg").textContent = `對齊完成 (${type})`;
        }
                
        function distribute(dir) {
            if (selectedIds.size < 3) {
                alert("請至少選取三個項目"); return;
            }
            saveHistory();
            const selectedAnnots = annots.filter(a => selectedIds.has(a.id));

            if (dir === 'v') {
                // 垂直排列
                selectedAnnots.sort((a, b) => a.rect[1] - b.rect[1]);
                const firstY = selectedAnnots[0].rect[1];
                const lastY = selectedAnnots[selectedAnnots.length - 1].rect[1];
                const totalHeightOfBoxes = selectedAnnots.reduce((sum, a) => sum + Math.abs(a.rect[3] - a.rect[1]), 0);
                const totalGap = (lastY - firstY) - (totalHeightOfBoxes - Math.abs(selectedAnnots[selectedAnnots.length-1].rect[3] - selectedAnnots[selectedAnnots.length-1].rect[1]));
                const stepGap = totalGap / (selectedAnnots.length - 1);

                let currentY = firstY;
                selectedAnnots.forEach((a, i) => {
                    const h = Math.abs(a.rect[3] - a.rect[1]);
                    a.rect[1] = currentY;
                    a.rect[3] = currentY + h;
                    currentY += h + stepGap;
                });
            } else {
                // 水平排列 (解決你圖中的問題)
                // 1. 先按左側座標排序
                selectedAnnots.sort((a, b) => a.rect[0] - b.rect[0]);
                
                const firstX = selectedAnnots[0].rect[0];
                const lastX = selectedAnnots[selectedAnnots.length - 1].rect[0];
                
                // 2. 計算所有框框的總寬度 (不含最後一個，因為我們算的是間隙)
                let totalBoxWidths = 0;
                for(let i=0; i < selectedAnnots.length - 1; i++) {
                    totalBoxWidths += Math.abs(selectedAnnots[i].rect[2] - selectedAnnots[i].rect[0]);
                }

                // 3. 計算總間隙並平分
                const totalSpace = lastX - firstX;
                const stepGap = (totalSpace - totalBoxWidths) / (selectedAnnots.length - 1);

                // 4. 重新定位
                let currentX = firstX;
                selectedAnnots.forEach((a) => {
                    const w = Math.abs(a.rect[2] - a.rect[0]);
                    a.rect[0] = currentX;
                    a.rect[2] = currentX + w;
                    currentX += w + stepGap; // 移動到下一個框的起點
                });
            }
            draw();
        }

        // --- 核心修改：儲存中的狀態顯示 ---
        function submitSave() {
            const msgEl = document.getElementById("msg");
            msgEl.textContent = "Saving...";
            msgEl.style.color = "#ffc107"; // 轉為黃色提示

            // 橫向強制封裝，絕對不允許任何事件在中途污染新舊文字
            const finalizedAnnots = annots.map(a => {
                // 從我們獨立的守護者暫存器裡抓出這筆物件最初的舊文字
                // 如果 originalTextMap 裡有記錄就用它；如果沒有（代表使用者完全沒動過這筆），舊文字就等於它原本的 a.text
                const realOldText = (a.id in originalTextMap) ? originalTextMap[a.id] : (a.text || "");
                
                return {
                    id: a.id,
                    page: a.page,
                    rect: a.rect,
                    type: a.type,
                    fontSize: a.fontSize,
                    font: a.font,
                    isBold: a.isBold,
                    isDash: a.isDash,
                    color: a.color,
                    borderStyle: a.borderStyle,
                    text: a.text || "",       // 這絕對是您最新輸入的「新文字」(如 CO (Cx))
                    oldText: realOldText,     // 這絕對是最初凍結的「舊文字」(如 CO (Comments))
                    sourceId: a.sourceId || null 
                };
            });

            // 印出前端打包後的成果，您可以按 F12 檢查 Console 到底有沒有對！
            console.log("=== [前端打包排查] 準備送出之資料 ===", finalizedAnnots);

            fetch("/save", { 
                method: "POST", 
                headers: { "Content-Type": "application/json" }, // 加上 Content-Type 確保後端精準解析
                body: JSON.stringify(finalizedAnnots) 
            })
            .then(() => { 
                msgEl.textContent = "Saved successfully! Saved as a new file"; 
                msgEl.style.color = "#0f0";

                // 【關鍵清理】：儲存成功後，清空所有記憶體中的 oldText 狀態，為下一次編輯做準備
                originalTextMap = {};

                setTimeout(() => {
                    msgEl.textContent = "Ready";
                    msgEl.style.color = "#0f0";
                }, 3000); // 停留 3 秒
            })
            .catch(() => {
                console.error("儲存失敗錯誤紀錄:", err);
                msgEl.textContent = "Save failed";
                msgEl.style.color = "#f00";
            });
        }

        function changePage(num) { renderPage(pageNum + num); }
        // 新增心跳發送，每 3 秒自動通知後端「網頁還開啟著」
        try {
            const workerCode = `
                setInterval(() => {
                    self.postMessage('ping');
                }, 3000);
            `;
            const blob = new Blob([workerCode], { type: 'application/javascript' });
            const worker = new Worker(URL.createObjectURL(blob));
            
            worker.onmessage = function() {
                fetch('/heartbeat', { method: 'POST' }).catch(() => {});
            };
        } catch (e) {
            // 備用方案：如果瀏覽器不支援 Worker，退回原本的 setInterval
            setInterval(() => {
                fetch('/heartbeat', { method: 'POST' }).catch(() => {});
            }, 3000);
        }
    </script>
</body>
</html>
"""

# 設定全域主題風格（可選 'blue', 'green', 'dark-blue'）
ctk.set_appearance_mode("System")  # 跟隨系統主題 (Dark/Light)
ctk.set_default_color_theme("blue")

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# === UI 選擇 ===
def launch_ui():
    def start_server():
        pdf_path = pdf_var.get()
        xfdf_path = xfdf_var.get()
        port = port_var.get()
        if not port:
            port = "8080"
        
    # 定義當網頁關閉時，GUI 要執行的回復動作
        def reset_button():
            print("【GUI 排查】正在執行 reset_button，準備將按鈕變回藍色...")
            start_btn.configure(
                text="Launch Editor", 
                state="normal", 
                fg_color=["#3a7ebf", "#1f538d"] # 恢復成原廠預設藍色
            )
            # 強制讓視窗重新整理渲染一次
            root.update_idletasks() 

        # 透過 root.after 安全地從背景執行緒叫醒主視窗更新 UI
        def on_browser_close():
            print("【GUI 排查】收到後端超時通知，準備排程 root.after...")
            root.after(10, reset_button)
        
        import threading
        server_thread = threading.Thread(
            target=run_server, 
            args=(pdf_path, xfdf_path, int(port), on_browser_close), 
            daemon=True
        )
        server_thread.start()
        
        # 啟動後讓按鈕顯示已啟動狀態
        start_btn.configure(text="Server Running...", state="disabled", fg_color="#2ecc71")

    def choose_pdf():
        path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if path: pdf_var.set(path)

    def choose_xfdf():
        path = filedialog.askopenfilename(filetypes=[("XFDF files", "*.xfdf")])
        if path: xfdf_var.set(path)

    # 初始化主視窗
    root = ctk.CTk()
    root.title("aCRF Editor Launcher")
    root.geometry("520://320") # 設定適合的寬高
    root.resizable(False, False)

    pdf_var = ctk.StringVar()
    xfdf_var = ctk.StringVar()
    port_var = ctk.StringVar(value="8080")

    # === 標題區塊 ===
    title_label = ctk.CTkLabel(root, text="aCRF Editor", font=ctk.CTkFont(size=24, weight="bold"))
    title_label.pack(pady=(20, 15))

    # === 輸入表單容器 ===
    form_frame = ctk.CTkFrame(root, fg_color="transparent")
    form_frame.pack(padx=30, fill="x")

    # PDF 欄位
    ctk.CTkLabel(form_frame, text="PDF File:", font=ctk.CTkFont(size=13)).grid(row=0, column=0, sticky="w", pady=8)
    pdf_entry = ctk.CTkEntry(form_frame, textvariable=pdf_var, width=280, placeholder_text="Select aCRF PDF...")
    pdf_entry.grid(row=0, column=1, padx=10, pady=8)
    ctk.CTkButton(form_frame, text="Browse", width=80, command=choose_pdf).grid(row=0, column=2, pady=8)

    # XFDF 欄位
    ctk.CTkLabel(form_frame, text="XFDF File:", font=ctk.CTkFont(size=13)).grid(row=1, column=0, sticky="w", pady=8)
    xfdf_entry = ctk.CTkEntry(form_frame, textvariable=xfdf_var, width=280, placeholder_text="Select XFDF data...")
    xfdf_entry.grid(row=1, column=1, padx=10, pady=8)
    ctk.CTkButton(form_frame, text="Browse", width=80, command=choose_xfdf).grid(row=1, column=2, pady=8)

    # Port 欄位
    ctk.CTkLabel(form_frame, text="Server Port:", font=ctk.CTkFont(size=13)).grid(row=2, column=0, sticky="w", pady=8)
    port_entry = ctk.CTkEntry(form_frame, textvariable=port_var, width=100)
    port_entry.grid(row=2, column=1, sticky="w", padx=10, pady=8)

    # === 底部啟動按鈕 ===
    start_btn = ctk.CTkButton(root, text="Launch Editor", font=ctk.CTkFont(size=15, weight="bold"), width=200, height=40, command=start_server)
    start_btn.pack(pady=(25, 20))

    root.mainloop()

# ============================================================
# 3. 伺服器啟動 (鎖定 8080)
# ============================================================
def run_server(pdf_file, xfdf_file, port, on_browser_close_callback=None):
    if hasattr(sys, '_MEIPASS'):
        exe_dir = os.path.dirname(sys.executable)
    else:
        exe_dir = os.path.dirname(os.path.abspath(__file__))

    os.chdir(exe_dir)

    # 🚀 關鍵修正 1：改用類別屬性來共享變數，確保 do_POST 內修改的時間能被外部看見
    class CustomHandler(http.server.SimpleHTTPRequestHandler):
        last_heartbeat_time = time.time()  # 類別層級的變數

        def do_GET(self):
            if self.path == "/":
                try:
                    self.send_response(200)
                    self.send_header("Content-type", "text/html; charset=utf-8")
                    self.end_headers()

                    target_pdf = pdf_file.strip("{}")
                    target_xfdf = xfdf_file.strip("{}")

                    with open(target_pdf, "rb") as f:
                        pdf_b64 = base64.b64encode(f.read()).decode()

                    data = parse_xfdf_to_json(target_xfdf)
                    html = HTML_TEMPLATE.replace("__PDF_B64__", pdf_b64) \
                                        .replace("__ANNOT_JSON__", json.dumps(data))

                    self.wfile.write(html.encode('utf-8'))
                    
                    # 網頁載入成功，初始化時間
                    CustomHandler.last_heartbeat_time = time.time()
                except Exception as e:
                    import traceback
                    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
                    with open(os.path.join(desktop, "server_error.txt"), "a", encoding="utf-8") as err_f:
                        err_f.write(f"--- Error ---\n")
                        traceback.print_exc(file=err_f)
            else:
                super().do_GET()

        def do_POST(self):
            # 🚀 關鍵修正 2：精準更新類別變數
            if self.path == "/heartbeat":
                CustomHandler.last_heartbeat_time = time.time()  # 確實刷新全域時間
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
                return

            if self.path == "/save":
                length = int(self.headers['Content-Length'])
                data = json.loads(self.rfile.read(length))
                output_file = os.path.splitext(xfdf_file)[0] + "_updated.xfdf"
                save_json_to_xfdf(data, xfdf_file, output_file)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(("127.0.0.1", port), CustomHandler)

    # 背景生命週期檢查執行緒
    def check_browser_lifecycle():
        # 給網頁 8 秒的啟動緩衝
        time.sleep(8)
        while True:
            time.sleep(2)
            # 🚀 關鍵修正 3：對齊讀取 CustomHandler 的時間
            elapsed = time.time() - CustomHandler.last_heartbeat_time
            if elapsed > 7:
                print(f"【啟動器通知】已超過 {elapsed:.1f} 秒未收到心跳，正在關閉伺服器...")
                if on_browser_close_callback:
                    on_browser_close_callback()
                
                # 執行安全關機
                threading.Thread(target=server.shutdown).start()
                break

    threading.Thread(target=check_browser_lifecycle, daemon=True).start()

    threading.Thread(
        target=lambda: webbrowser.open(f"http://127.0.0.1:{port}")
    ).start()

    server.serve_forever()

if __name__ == "__main__":
    launch_ui()
