import streamlit as st
import sqlite3
import datetime
import requests
import base64
from PIL import Image
import io
import hashlib
import pandas as pd
import json
import time
import os
import sqlite3
import pandas as pd


# ===================== 配置 =====================
BAIDU_API_KEY = "无"
BAIDU_SECRET_KEY = "无"

# ===================== 工具函数 =====================
def hash_password(pwd):
    return hashlib.md5(pwd.encode()).hexdigest()

def img_to_base64(img):
    buf = io.BytesIO()
    # 确保图片模式为 RGB，避免 RGBA 或 P 模式导致 JPEG 保存失败
    if img.mode != 'RGB':
        img = img.convert('RGB')
    # 增加质量参数，平衡大小和清晰度
    img.save(buf, format='JPEG', quality=85)
    return base64.b64encode(buf.getvalue()).decode('utf-8')

def base64_to_img(b64_str):
    if not b64_str:
        return None
        
    try:
        # 1. 清理可能存在的 data URI 前缀 (例如 "data:image/jpeg;base64,")
        if ',' in b64_str:
            b64_str = b64_str.split(',', 1)[1]
        
        # 2. 去除所有的空白字符（空格、换行等），防止解码失败
        b64_str = "".join(b64_str.split())
        
        if not b64_str:
            return None
            
        # 3. 填充缺失的 padding '='
        missing_padding = len(b64_str) % 4
        if missing_padding:
            b64_str += '=' * (4 - missing_padding)
            
        # 4. 解码并打开图片
        img_data = base64.b64decode(b64_str)
        img = Image.open(io.BytesIO(img_data))
        
        # 5. 强制转换为 RGB 模式，确保后续处理（如保存为 JPEG 或 Streamlit 显示）兼容
        if img.mode != 'RGB':
            img = img.convert('RGB')
            
        return img
    except Exception as e:
        # 打印具体错误以便调试，生产环境可改为日志记录
        print(f"Base64 转图片失败: {str(e)}")
        return None

# ===================== 百度AI识别（已修复 100% 可用） =====================
def get_baidu_token():
    url = "https://aip.baidubce.com/oauth/2.0/token"
    params = {
        "grant_type": "client_credentials",
        "client_id": BAIDU_API_KEY,
        "client_secret": BAIDU_SECRET_KEY
    }
    try:
        r = requests.post(url, params=params, timeout=10)
        res = r.json()
        return res.get("access_token")
    except:
        return None

def ai_recognize(img_file):
    if not img_file:
        return "无图片"

    token = get_baidu_token()
    if not token:
        return "AI令牌获取失败"

    try:
        img = Image.open(img_file)
        buf = io.BytesIO()
        img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=60)
        byte_data = buf.getvalue()

        if len(byte_data) > 4 * 1024 * 1024:
            return "图片超过4M"

        b64_image = base64.b64encode(byte_data).decode()

        url = f"https://aip.baidubce.com/rest/2.0/image-classify/v2/advanced_general?access_token={token}"
        data = {"image": b64_image, "top_num": 3}

        response = requests.post(url, data=data, timeout=15)
        result_json = response.json()

        # 检查百度API是否返回错误
        if "error_code" in result_json:
            return f"API错误: {result_json.get('error_msg', '未知错误')}"
        
        if "result" not in result_json:
            return "未识别到物品"

        keywords = [item["keyword"] for item in result_json["result"][:3]]
        return "、".join(keywords)

    except Exception as e:
        return f"识别异常：{str(e)[:20]}"

# ===================== 数据库 =====================
def init_db():
    conn = sqlite3.connect("lost_found.db")
    c = conn.cursor()

    # 修改 items 表结构，增加 item_no
    c.execute('''CREATE TABLE IF NOT EXISTS items
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  item_no TEXT, 
                  type TEXT, name TEXT, category TEXT,

                  time TEXT, location TEXT, description TEXT,
                  image TEXT, ai_result TEXT,
                  publisher TEXT, contact TEXT, create_time TEXT,
                  status INTEGER DEFAULT 0, 
                  is_hidden INTEGER DEFAULT 0)''')

    # 检查并添加可能缺失的列
    columns_to_check = [
        ("item_no", "ALTER TABLE items ADD COLUMN item_no TEXT"),
        ("status", "ALTER TABLE items ADD COLUMN status INTEGER DEFAULT 0"),
        ("is_hidden", "ALTER TABLE items ADD COLUMN is_hidden INTEGER DEFAULT 0"),
        ("images", "ALTER TABLE items ADD COLUMN images TEXT")
    ]
    
    for col_name, alter_sql in columns_to_check:
        try:
            c.execute(f"SELECT {col_name} FROM items LIMIT 1")
        except:
            try:
                c.execute(alter_sql)
            except:
                pass

    # 修改 users 表结构，增加 user_no
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (username TEXT PRIMARY KEY, 
                  user_no TEXT,
                  password TEXT, role TEXT DEFAULT 'user')''')
    
    user_columns_to_check = [
        ("user_no", "ALTER TABLE users ADD COLUMN user_no TEXT"),
        ("nickname", "ALTER TABLE users ADD COLUMN nickname TEXT"),
        ("avatar", "ALTER TABLE users ADD COLUMN avatar TEXT"),
        ("contact", "ALTER TABLE users ADD COLUMN contact TEXT"),
        ("is_active", "ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
    ]
    
    for col_name, alter_sql in user_columns_to_check:
        try:
            c.execute(f"SELECT {col_name} FROM users LIMIT 1")
        except:
            try:
                c.execute(alter_sql)
            except:
                pass

    # ===================== 新增：强制创建 chat_messages 表 =====================
    # 之前代码缺失此表的创建逻辑，导致私聊功能报错
    c.execute('''CREATE TABLE IF NOT EXISTS chat_messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  item_id INTEGER,
                  sender TEXT,
                  receiver TEXT,
                  message TEXT,
                  create_time TEXT)''')
    # ===================== 结束聊天表创建 =====================

    # ===================== 新增：为历史数据补全编号 =====================
    
    # 1. 补全用户编号 (USER-XXXX)
    c.execute("SELECT username FROM users WHERE user_no IS NULL OR user_no = '' ORDER BY username ASC")
    users_to_fix = c.fetchall()
    if users_to_fix:
        # 获取当前最大的 USER 编号
        c.execute("SELECT MAX(CAST(SUBSTR(user_no, 6) AS INTEGER)) FROM users WHERE user_no LIKE 'USER-%'")
        max_user_no = c.fetchone()[0]
        next_user_no = (max_user_no + 1) if max_user_no else 1
        
        for u in users_to_fix:
            uname = u[0]
            # 跳过 admin 的特殊处理，如果 admin 也没编号，这里也会给它编一个，或者可以在下面单独处理
            new_uno = f"USER-{next_user_no:04d}"
            c.execute("UPDATE users SET user_no=? WHERE username=?", (new_uno, uname))
            next_user_no += 1
            
    # 2. 补全物品编号 (ITEM-XXXX)
    c.execute("SELECT id FROM items WHERE item_no IS NULL OR item_no = '' ORDER BY id ASC")
    items_to_fix = c.fetchall()
    if items_to_fix:
        # 获取当前最大的 ITEM 编号
        c.execute("SELECT MAX(CAST(SUBSTR(item_no, 6) AS INTEGER)) FROM items WHERE item_no LIKE 'ITEM-%'")
        max_item_no = c.fetchone()[0]
        next_item_no = (max_item_no + 1) if max_item_no else 1
        
        for it in items_to_fix:
            iid = it[0]
            new_ino = f"ITEM-{next_item_no:04d}"
            c.execute("UPDATE items SET item_no=? WHERE id=?", (new_ino, iid))
            next_item_no += 1

    # ===================== 结束补全逻辑 =====================

    # 初始化 admin 用户，如果不存在
    c.execute("SELECT * FROM users WHERE username='admin'")
    if not c.fetchone():
        # 生成 admin 的 user_no，确保唯一性 (此时可能已经被上面的逻辑覆盖，但为了保险保留逻辑)
        c.execute("SELECT MAX(CAST(SUBSTR(user_no, 6) AS INTEGER)) FROM users WHERE user_no LIKE 'USER-%'")
        max_no = c.fetchone()[0]
        next_no = (max_no + 1) if max_no else 1
        admin_no = f"USER-{next_no:04d}"
        c.execute("INSERT INTO users (username, user_no, password, role, is_active) VALUES (?, ?, ?, 'admin', 1)", 
                  ('admin', admin_no, hash_password("admin123")))
    else:
        # 如果 admin 存在但没有编号，也补上
        c.execute("SELECT user_no FROM users WHERE username='admin'")
        admin_res = c.fetchone()
        if not admin_res or not admin_res[0]:
             c.execute("SELECT MAX(CAST(SUBSTR(user_no, 6) AS INTEGER)) FROM users WHERE user_no LIKE 'USER-%'")
             max_no = c.fetchone()[0]
             next_no = (max_no + 1) if max_no else 1
             admin_no = f"USER-{next_no:04d}"
             c.execute("UPDATE users SET user_no=? WHERE username='admin'", (admin_no,))

    conn.commit()
    conn.close()

# ===================== 搜索 & 统计 =====================
def search_items(keyword, category, item_type):
    conn = sqlite3.connect("lost_found.db")
    c = conn.cursor()
    query = "SELECT * FROM items WHERE is_hidden=0"
    params = []

    if keyword:
        query += " AND (name LIKE ? OR description LIKE ? OR publisher LIKE ? OR category LIKE ?)"
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    if category and category != "全部" and category != "自定义":
        query += " AND category = ?"
        params.append(category)
    if item_type != "全部":
        t = "lost" if item_type == "丢失" else "found"
        query += " AND type = ?"
        params.append(t)

    query += " ORDER BY create_time DESC"
    c.execute(query, params)
    items = c.fetchall()
    conn.close()
    return items

def get_stats():
    conn = sqlite3.connect("lost_found.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM items WHERE is_hidden=0")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM items WHERE is_hidden=0 AND type='lost'")
    lost = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM items WHERE is_hidden=0 AND type='found'")
    found = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM items WHERE is_hidden=0 AND status=1")
    solved = c.fetchone()[0]
    conn.close()
    return total, lost, found, solved

def export_to_excel(items, filename="失物招领数据.xlsx"):
    try:
        import openpyxl
    except ImportError:
        raise ImportError("请安装 openpyxl 库: pip install openpyxl")
        
    rows = []
    for it in items:
        rows.append({
            "ID": it[0],
            "类型": "丢失" if it[1]=="lost" else "捡到",
            "物品名称": it[2],
            "类别": it[3],
            "时间": it[4],
            "地点": it[5],
            "描述": it[6],
            "AI识别": it[8],
            "发布人": it[9],
            "联系方式": it[10],
            "发布时间": it[11],
            "状态": "已解决" if it[12]==1 else "寻找中"
        })
    df = pd.DataFrame(rows)
    with io.BytesIO() as buffer:
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name="数据")
        buffer.seek(0)
        return buffer.read()

def get_user_items(username):
    conn = sqlite3.connect("lost_found.db")
    c = conn.cursor()
    c.execute("SELECT * FROM items WHERE publisher=? AND is_hidden=0 ORDER BY create_time DESC", (username,))
    items = c.fetchall()
    conn.close()
    return items

def get_ai_matches(user_items, all_items, col_index):
    """
    修复：增加 col_index 参数，避免硬编码索引导致的字段读取错误
    """
    matches = []
    # 构建全局索引：key为 (type, keyword) 或 (type, name)
    # 为了高效，我们分别建立名称索引和AI关键词索引
    name_index = {}  # key: name, value: list of items
    ai_keyword_index = {} # key: (type, keyword), value: list of items
    
    # 获取动态索引
    idx_id = col_index.get('id', 0)
    idx_type = col_index.get('type', 1) # 注意：原代码 type 是第2列，但 item_no 插入后可能变动，需依赖 col_index
    # 检查 col_index 中 type 的实际位置。在原 init_db 中，type 是第3个定义的，但 item_no 插入在第2位。
    # SELECT * 返回顺序即为定义顺序。
    # items 表定义: id, item_no, type, name, category, time, location, description, image, ai_result, publisher, contact, create_time, status, is_hidden
    # 所以 type 应该是 index 2。但为了安全，完全依赖 col_index。
    
    idx_name = col_index.get('name', 2)
    idx_ai = col_index.get('ai_result', 8)
    idx_stat = col_index.get('status', 12)
    idx_hide = col_index.get('is_hidden', 13)

    for item in all_items:
        iid = item[idx_id]
        itype = item[idx_type]
        iname = item[idx_name]
        
        # 安全获取状态，防止 KeyError 或 IndexError
        ihide = item[idx_hide] if idx_hide < len(item) else 0
        istat = item[idx_stat] if idx_stat < len(item) else 0
        
        # 只索引未隐藏且未解决的物品，提高匹配质量
        if ihide or istat == 1:
            continue
            
        iai = item[idx_ai] if idx_ai < len(item) else ""

        # 1. 建立名称索引
        if iname not in name_index:
            name_index[iname] = []
        name_index[iname].append(item)
        
        # 2. 建立AI关键词索引
        if iai and iai not in ["未识别", "无图片", "识别失败", "未上传"]:
            # 分割关键词，去除空白
            keywords = [k.strip() for k in iai.split("、") if k.strip()]
            for kw in keywords:
                key = (itype, kw)
                if key not in ai_keyword_index:
                    ai_keyword_index[key] = []
                ai_keyword_index[key].append(item)

    for my_item in user_items:
        iid = my_item[idx_id]
        itype = my_item[idx_type]
        iname = my_item[idx_name]
        
        # 安全获取状态
        ihide = my_item[idx_hide] if idx_hide < len(my_item) else 0
        istat = my_item[idx_stat] if idx_stat < len(my_item) else 0
        
        # 如果我的物品已隐藏或已解决，不进行匹配
        if ihide or istat == 1:
            continue
        
        # 确定对方物品的类型：我丢(found) vs 我捡(lost)
        oppo_type = "found" if itype == "lost" else "lost"
        
        matched_ids = set()
        
        # 策略1：名称完全匹配
        if iname in name_index:
            for item in name_index[iname]:
                # 必须是相反类型，且不是自己，且未解决
                # 再次检查 item 的状态，因为索引中可能包含旧数据或逻辑遗漏
                m_stat = item[idx_stat] if idx_stat < len(item) else 0
                m_type = item[idx_type]
                m_id = item[idx_id]
                
                if m_type == oppo_type and m_id != iid and m_stat == 0:
                    matched_ids.add(m_id)
        
        # 策略2：AI关键词匹配
        iai = my_item[idx_ai] if idx_ai < len(my_item) else ""
        
        if iai and iai not in ["未识别", "无图片", "识别失败", "未上传"]:
            my_keywords = [k.strip() for k in iai.split("、") if k.strip()]
            for kw in my_keywords:
                key = (oppo_type, kw)
                if key in ai_keyword_index:
                    for item in ai_keyword_index[key]:
                        m_id = item[idx_id]
                        if m_id != iid:
                            matched_ids.add(m_id)
                            
        # 收集最终匹配项
        final_matches = []
        # 优化：直接从 all_items 中找出 ID 在 matched_ids 中的项
        # 为了快速查找，构建一个临时映射
        id_to_item = {item[idx_id]: item for item in all_items}
        for mid in matched_ids:
            if mid in id_to_item:
                final_matches.append(id_to_item[mid])
                    
        matches.append((my_item, final_matches))
        
    return matches

# ===================== 美化界面 =====================
def set_style():
    st.set_page_config(page_title="🎓 校园失物招领", page_icon="🎓", layout="wide")
    st.markdown("""
    <style>
    /* 全局字体与背景 */
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&display=swap');
    html, body, * { font-family: 'Noto Sans SC', sans-serif; }
    .main { background-color: #fdfbf7; }
    .block-container { padding-top: 2rem; padding-bottom: 2rem; }
    
    /* 标题卡片 */
    .title-card {
        background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%);
        padding: 25px 30px;
        border-radius: 20px;
        color: #555;
        margin-bottom: 25px;
        box-shadow: 0 8px 20px rgba(168, 237, 234, 0.3);
        text-align: center;
    }
    .title-card h1 { margin: 0; font-weight: 700; letter-spacing: 1px; }
    
    /* 统计卡片 */
    .stat-card {
        padding: 25px 15px;
        border-radius: 18px;
        color: white;
        text-align: center;
        box-shadow: 0 6px 15px rgba(0,0,0,0.08);
        transition: transform 0.3s ease;
        height: 100%;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
    }
    .stat-card:hover { transform: translateY(-5px); }
    .stat-card h3 { margin: 0 0 10px 0; font-size: 1rem; opacity: 0.9; font-weight: 500; }
    .stat-card h1 { margin: 0; font-size: 2.2rem; font-weight: 700; }
    
    .stat-total { background: linear-gradient(135deg, #84fab0 0%, #8fd3f4 100%); }
    .stat-lost { background: linear-gradient(135deg, #ff9a9e 0%, #fecfef 99%, #fecfef 100%); }
    .stat-found { background: linear-gradient(135deg, #a18cd1 0%, #fbc2eb 100%); }
    .stat-solved { background: linear-gradient(135deg, #fccb90 0%, #d57eeb 100%); }
    
    /* 搜索区域 */
    .search-card {
        background: white;
        padding: 30px;
        border-radius: 20px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.03);
        margin-bottom: 30px;
        border: 1px solid #f0f0f0;
    }
    
    /* 左右分栏面板 */
    .panel-container {
        border-radius: 24px;
        padding: 20px;
        min-height: 400px;
        height: 100%;
        border: 1px solid rgba(0,0,0,0.05);
    }
    .lost-panel {
        background: #fff5f5;
        border: 2px solid #ffe3e3;
    }
    .found-panel {
        background: #f0fff4;
        border: 2px solid #c6f6d5;
    }
    
    .panel-header {
        text-align: center;
        padding-bottom: 15px;
        margin-bottom: 15px;
        border-bottom: 2px dashed rgba(0,0,0,0.1);
    }
    
    /* 优化 Expander */
    .stExpander {
        border-radius: 12px !important;
        border: 1px solid #eee !important;
        box-shadow: 0 2px 8px rgba(0,0,0,0.02) !important;
        margin-bottom: 12px !important;
        background-color: white !important;
    }
    .stExpander header {
        background-color: #fafafa !important;
        border-radius: 12px 12px 0 0 !important;
    }
    
    /* 按钮美化 */
    .stButton>button {
        border-radius: 12px;
        font-weight: 500;
        border: none;
        background-color: #e2e8f0;
        color: #4a5568;
        transition: all 0.3s ease;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        background-color: #cbd5e0;
    }
    
    /* 侧边栏美化 */
    .css-1d391kg { background-color: #ffffff; }
    </style>
    """, unsafe_allow_html=True)

# ===================== 登录 =====================
def login_page():
    st.markdown("""<div class='title-card'><h1>🎓 校园失物招领智能平台</h1></div>""", unsafe_allow_html=True)
    st.subheader("🔐 安全登录 / 注册")
    tab1, tab2 = st.tabs(["登录", "注册"])
    with tab1:
        user = st.text_input("用户名", key="login_user")
        pwd = st.text_input("密码", type="password", key="login_pwd")
        if st.button("登录", key="login_btn", use_container_width=True):
            conn = sqlite3.connect("lost_found.db")
            c = conn.cursor()
            c.execute("SELECT password, role, is_active FROM users WHERE username=?", (user,))
            res = c.fetchone()
            conn.close()
            if res:
                if len(res) > 2 and res[2] == 0:
                    st.error("该账号已被管理员禁用")
                elif res[0] == hash_password(pwd):
                    st.session_state["user"] = user
                    st.session_state["role"] = res[1]
                    st.success("登录成功！")
                    st.rerun()
                else:
                    st.error("用户名或密码错误")
            else:
                st.error("用户名或密码错误")
    with tab2:
        new_user = st.text_input("用户名", key="reg_user")
        new_pwd = st.text_input("密码", type="password", key="reg_pwd")
        confirm = st.text_input("确认密码", type="password", key="reg_confirm")
        if st.button("注册", key="reg_btn", use_container_width=True):
            if new_pwd != confirm:
                st.warning("两次密码不一致")
                return
            if not new_user or not new_pwd:
                st.warning("用户名和密码不能为空")
                return
            conn = sqlite3.connect("lost_found.db")
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE username=?", (new_user,))
            if c.fetchone():
                st.error("用户名已存在")
            else:
                # 生成用户编号 USER-XXXX，基于最大值递增
                c.execute("SELECT MAX(CAST(SUBSTR(user_no, 6) AS INTEGER)) FROM users WHERE user_no LIKE 'USER-%'")
                max_no = c.fetchone()[0]
                next_no = (max_no + 1) if max_no else 1
                new_user_no = f"USER-{next_no:04d}"
                
                c.execute("INSERT INTO users (username, user_no, password, role, is_active) VALUES (?, ?, ?, 'user', 1)", 
                          (new_user, new_user_no, hash_password(new_pwd)))
                conn.commit()
                st.success(f"注册成功！您的用户编号是: {new_user_no}")
            conn.close()

# ===================== 主界面 =====================
def main_page():
    if "user" not in st.session_state:
        login_page()
        return

    user = st.session_state["user"]
    role = st.session_state.get("role", "user")

    # 【新增】优先处理私聊跳转：如果当前有活跃的私聊会话，直接显示私聊界面
    if st.session_state.get("chat_with"):
        render_chat_interface(user)
        return

    menu = ["🏠 首页", "🔴 我丢了东西", "🟢 我捡到东西", "🤖 AI匹配", "📋 我的发布", "⚙️ 账号设置", "💬 我的私聊"]
    if role == "admin":
        menu.append("🔐 管理面板")
        menu.append("📊 一键导入数据")

    choice = st.sidebar.selectbox("菜单导航", menu)
    st.sidebar.write(f"👤 当前用户：**{user}**")
    if st.sidebar.button("🚪 退出登录", use_container_width=True):
        st.session_state.clear()
        st.rerun()

    conn = sqlite3.connect("lost_found.db")
    c = conn.cursor()

    # 动态获取列索引，以应对 ALTER TABLE ADD COLUMN 导致的列顺序变化
    c.execute("PRAGMA table_info(items)")
    columns_info = c.fetchall()
    col_names = [col[1] for col in columns_info]
    
    # 建立列名到索引的映射
    col_index = {name: idx for idx, name in enumerate(col_names)}
    
    # 获取关键列的索引，提供默认值以防万一
    idx_image = col_index.get('image', 7)
    idx_images = col_index.get('images', -1) # 可能不存在
    idx_ai = col_index.get('ai_result', 8)
    idx_pub = col_index.get('publisher', 9)
    idx_con = col_index.get('contact', 10)
    idx_stat = col_index.get('status', 12)
    idx_hide = col_index.get('is_hidden', 13)

    CATEGORY_OPTIONS = [
        "全部", "校园卡", "身份证", "学生证", "耳机", "蓝牙耳机",
        "钥匙", "车钥匙", "手机", "平板", "电脑", "手表", "眼镜",
        "钱包", "银行卡", "书包", "水杯", "雨伞", "书籍", "证件",
        "化妆品", "饰品", "衣物", "电子产品", "其他"
    ]

    # 优先处理用户主页跳转，确保点击后立即响应
    if "view_user" in st.session_state:
        target_user = st.session_state["view_user"]
        
        # 获取用户基本信息
        conn_u = sqlite3.connect("lost_found.db")
        c_u = conn_u.cursor()
        display_name = target_user
        user_avatar = None
        user_contact = ""
        user_no_display = "N/A" # 新增：用户编号
        
        try:
            # 尝试从 users 表获取昵称、头像、联系方式和用户编号
            c_u.execute("SELECT nickname, avatar, contact, user_no FROM users WHERE username=?", (target_user,))
            res_u = c_u.fetchone()
            if res_u:
                if res_u[0]:
                    display_name = res_u[0]
                user_avatar = res_u[1]
                user_contact = res_u[2] if res_u[2] else "未设置"
                user_no_display = res_u[3] if res_u[3] else "未分配" # 新增
        except:
            pass
        conn_u.close()

        # 渲染个人主页头部
        st.markdown(f"""<div class='title-card'><h1>👤 {display_name}</h1><p>账号：{target_user} | 编号：{user_no_display}</p></div>""", unsafe_allow_html=True)
        
        # 显示头像和信息卡片
        info_col1, info_col2 = st.columns([1, 3])
        with info_col1:
            if user_avatar:
                try:
                    img = base64_to_img(user_avatar)
                    if img:
                        st.image(img, width=150, caption="头像")
                    else:
                        st.markdown("<div style='text-align:center; font-size:50px;'>👤</div>", unsafe_allow_html=True)
                except:
                    st.markdown("<div style='text-align:center; font-size:50px;'>👤</div>", unsafe_allow_html=True)
            else:
                st.markdown("<div style='text-align:center; font-size:50px;'>👤</div>", unsafe_allow_html=True)
                
        with info_col2:
            st.markdown(f"**📝 昵称**：{display_name}")
            st.markdown(f"**🆔 账号**：{target_user}")
            st.markdown(f"**📞 联系方式**：{user_contact}")
            
            # 如果是本人，提供编辑资料按钮
            if target_user == user:
                if st.button("✏️ 编辑个人资料", use_container_width=True, key="btn_edit_profile"):
                    if "view_user" in st.session_state:
                        del st.session_state["view_user"]
                    # 强制切换到设置页逻辑可以通过 rerun 后判断，或者直接在这里渲染设置表单
                    # 这里为了简单，我们清除 view_user 并让下方逻辑自然进入 "⚙️ 账号设置" 或者用户手动点击
                    # 更好的体验是直接在这里嵌入设置表单，但为了保持结构清晰，我们提示用户去设置页
                    st.info("请前往左侧菜单【⚙️ 账号设置】修改头像和资料")

        st.divider()

        user_items = get_user_items(target_user)
        u_lost = [x for x in user_items if x[1] == "lost"]
        u_found = [x for x in user_items if x[1] == "found"]

        # 提供返回和私聊按钮
        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            if st.button("← 返回首页", use_container_width=True, key="btn_back_home"):
                if "view_user" in st.session_state:
                    del st.session_state["view_user"]
                st.rerun()
        with btn_col2:
            if target_user != user:
                if st.button(f"💬 发送私聊", use_container_width=True, key="btn_chat_from_profile"):
                    st.info("请点击下方具体物品下的“私聊”按钮开始对话。")
        
        st.divider()

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"<div class='lost-panel' style='padding:15px; border-radius:15px; margin-bottom:10px;'><h3 style='margin:0;'>🔴 TA丢失的物品 ({len(u_lost)})</h3></div>", unsafe_allow_html=True)
            for item in u_lost:
                # 新增：获取物品编号 - 修复：使用动态索引
                idx_item_no = col_index.get('item_no', 1)
                item_no_val = item[idx_item_no] if len(item) > idx_item_no and item[idx_item_no] else f"ID:{item[0]}"
                with st.expander(f"🔴 [{item_no_val}] {item[2]}"):
                    # 显示图片 (复用上述逻辑)
                    img_displayed = False
                    
                    # 使用动态索引
                    if not img_displayed and idx_images != -1 and item[idx_images]:
                         try:
                             imgs_str = item[idx_images]
                             if imgs_str:
                                 img_list = json.loads(imgs_str)
                                 if isinstance(img_list, list) and img_list:
                                     img = base64_to_img(img_list[0])
                                     if img:
                                         st.image(img, width=300, caption="物品图片")
                                         img_displayed = True
                         except:
                             pass

                    if not img_displayed and item[idx_image]:
                        try:
                            img_data = item[idx_image]
                            if isinstance(img_data, str):
                                if img_data.startswith('['):
                                    img_list = json.loads(img_data)
                                else:
                                    img_list = [img_data]
                                if isinstance(img_list, list) and img_list:
                                    img = base64_to_img(img_list[0])
                                    if img:
                                        st.image(img, width=300, caption="物品图片")
                                        img_displayed = True
                        except:
                            pass

                    st.write(f"**类别**：{item[3]} | **时间**：{item[4]}")
                    st.write(f"**地点**：{item[5]}")
                    st.write(f"**描述**：{item[6]}")
                    
                    if st.button(f"💬 私聊 {target_user}", key=f"up_{item[0]}", use_container_width=True):
                        st.session_state["chat_with"] = {"item_id": item[0], "target_user": target_user, "item_name": item[2]}
                        if "view_user" in st.session_state:
                            del st.session_state["view_user"]
                        st.rerun()

        with col2:
            st.markdown(f"<div class='found-panel' style='padding:15px; border-radius:15px; margin-bottom:10px;'><h3 style='margin:0;'>🟢 TA捡到的物品 ({len(u_found)})</h3></div>", unsafe_allow_html=True)
            for item in u_found:
                # 新增：获取物品编号 - 修复：使用动态索引
                idx_item_no = col_index.get('item_no', 1)
                item_no_val = item[idx_item_no] if len(item) > idx_item_no and item[idx_item_no] else f"ID:{item[0]}"
                with st.expander(f"🟢 [{item_no_val}] {item[2]}"):
                    # 显示图片
                    img_displayed = False
                    
                    # 使用动态索引
                    if not img_displayed and idx_images != -1 and item[idx_images]:
                         try:
                             imgs_str = item[idx_images]
                             if imgs_str:
                                 img_list = json.loads(imgs_str)
                                 if isinstance(img_list, list) and img_list:
                                     img = base64_to_img(img_list[0])
                                     if img:
                                         st.image(img, width=300, caption="物品图片")
                                         img_displayed = True
                         except:
                             pass

                    if not img_displayed and item[idx_image]:
                        try:
                            img_data = item[idx_image]
                            if isinstance(img_data, str):
                                if img_data.startswith('['):
                                    img_list = json.loads(img_data)
                                else:
                                    img_list = [img_data]
                                if isinstance(img_list, list) and img_list:
                                    img = base64_to_img(img_list[0])
                                    if img:
                                        st.image(img, width=300, caption="物品图片")
                                        img_displayed = True
                        except:
                            pass

                    st.write(f"**类别**：{item[3]} | **时间**：{item[4]}")
                    st.write(f"**地点**：{item[5]}")
                    st.write(f"**描述**：{item[6]}")
                    
                    if st.button(f"💬 私聊 {target_user}", key=f"uf_{item[0]}", use_container_width=True):
                        st.session_state["chat_with"] = {"item_id": item[0], "target_user": target_user, "item_name": item[2]}
                        if "view_user" in st.session_state:
                            del st.session_state["view_user"]
                        st.rerun()
        
        # 渲染完用户主页后直接返回，不再执行后续的首页或其他页面逻辑
        conn.close()
        return

    if choice == "🏠 首页":
        st.markdown("""<div class='title-card'><h1>🏠 校园失物招领平台</h1><p style='margin-top:10px; opacity:0.8'>让每一次丢失都有回响，让每一次拾起都有温度</p></div>""", unsafe_allow_html=True)

        total, lost_cnt, found_cnt, solved_cnt = get_stats()
        
        col1, col2, col3, col4 = st.columns(4, gap="large")
        with col1:
            st.markdown(f"""<div class='stat-card stat-total'><h3>总物品</h3><h1>{total}</h1></div>""", unsafe_allow_html=True)
        with col2:
            st.markdown(f"""<div class='stat-card stat-lost'><h3>等待找回</h3><h1>{lost_cnt}</h1></div>""", unsafe_allow_html=True)
        with col3:
            st.markdown(f"""<div class='stat-card stat-found'><h3>等待认领</h3><h1>{found_cnt}</h1></div>""", unsafe_allow_html=True)
        with col4:
            st.markdown(f"""<div class='stat-card stat-solved'><h3>已圆满解决</h3><h1>{solved_cnt}</h1></div>""", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        st.markdown("<div class='search-card'>", unsafe_allow_html=True)
        st.subheader("🔍 寻找线索")
        scol1, scol2, scol3 = st.columns([2, 1, 1])
        with scol1:
            keyword = st.text_input("", placeholder="输入物品名称、描述或地点...", key="search_keyword", label_visibility="collapsed")
        with scol2:
            category = st.selectbox("", CATEGORY_OPTIONS, key="search_category", label_visibility="collapsed")
        with scol3:
            t_filter = st.selectbox("", ["全部", "丢失", "捡到"], key="search_type", label_visibility="collapsed")
        st.markdown("</div>", unsafe_allow_html=True)

        all_items = search_items(keyword, category, t_filter)
        lost_items = [x for x in all_items if x[1] == "lost"]
        found_items = [x for x in all_items if x[1] == "found"]

        if all_items:
            excel_data = export_to_excel(all_items)
            st.download_button(
                label="📥 导出当前列表为 Excel",
                data=excel_data,
                file_name=f"失物招领_{datetime.date.today()}.xlsx",
                mime="application/vnd.ms-excel",
                use_container_width=True
            )

        col_lost, col_found = st.columns(2, gap="large")
        
        # 定义一个辅助函数来渲染物品卡片，避免代码重复并确保图片逻辑一致
        def render_item_card(item, is_lost_panel=True):
            panel_class = 'lost-panel' if is_lost_panel else 'found-panel'
            panel_title = '🔴 丢失物品' if is_lost_panel else '🟢 捡到物品'
            panel_color = '#e53e3e' if is_lost_panel else '#38a169'
            
            # 注意：这里只渲染头部统计，具体物品在循环中渲染
            pass 

        with col_lost:
            st.markdown(f"""
            <div class='panel-container lost-panel'>
                <div class='panel-header'>
                    <h3 style='color:#e53e3e; margin:0;'>🔴 丢失物品</h3>
                    <span style='font-size:0.9em; color:#718096'>共 {len(lost_items)} 条线索</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            # 增加序号展示 -> 修改为直接显示编码，不再使用自然序号 idx
            for item in lost_items:
                # 新增：获取物品编号 - 修复：使用动态索引
                idx_item_no = col_index.get('item_no', 1)
                item_no_val = item[idx_item_no] if len(item) > idx_item_no and item[idx_item_no] else f"ID:{item[0]}"
                # 修改：标题只保留编码和名称，去掉前面的自然序号
                with st.expander(f"🔴 [{item_no_val}] {item[2]}"):
                    info_col1, info_col2 = st.columns(2)
                    with info_col1:
                        st.markdown(f"**🏷 类别**：{item[3]}")
                        st.markdown(f"**📅 时间**：{item[4]}")
                        st.markdown(f"**📍 地点**：{item[5]}")
                    with info_col2:
                        st.markdown(f"**👤 发布人**：{item[idx_pub]}")
                        st.markdown(f"**📞 联系**：{item[idx_con]}")
                    
                    st.markdown(f"**📝 描述**：{item[6]}")
                    
                    # --- 修复图片显示逻辑 Start ---
                    img_shown = False
                    # 使用动态获取的索引

                    # 策略1: 尝试从 images 字段 (JSON list of base64) 获取
                    if not img_shown and idx_images != -1 and item[idx_images]:
                        try:
                            images_str = item[idx_images]
                            if isinstance(images_str, str):
                                img_list = json.loads(images_str)
                                if isinstance(img_list, list) and img_list:
                                    # 取第一张图
                                    img = base64_to_img(img_list[0])
                                    if img:
                                        st.image(img, width=300, caption="物品图片")
                                        img_shown = True
                        except Exception as e:
                            pass # st.warning(f"Images字段解析错误: {e}")

                    # 策略2: 尝试从 image 字段 (Base64 string or JSON list) 获取
                    if not img_shown and item[idx_image]:
                        try:
                            img_data = item[idx_image]
                            if isinstance(img_data, str):
                                # 可能是 JSON 列表或纯 Base64
                                if img_data.startswith('['):
                                    img_list = json.loads(img_data)
                                else:
                                    img_list = [img_data]
                                
                                if isinstance(img_list, list) and img_list:
                                    img = base64_to_img(img_list[0])
                                    if img:
                                        st.image(img, width=300, caption="物品图片")
                                        img_shown = True
                        except Exception as e:
                            pass # st.warning(f"Image字段解析错误: {e}")
                    # --- 修复图片显示逻辑 End ---

                    # 显示 AI 结果
                    ai_res = item[idx_ai]
                    if ai_res and ai_res not in ["未识别", "无图片", "识别失败", "未上传"]:
                         st.info(f"🤖 AI推测特征：{ai_res}")
                    
                    st.divider()
                    c1, c2 = st.columns(2)
                    with c1:
                        # 修复查看主页逻辑：使用唯一 key 并确保 session state 设置后立即 rerun
                        if st.button(f"👤 查看TA主页", key=f"profile_lost_{item[0]}", use_container_width=True):
                            st.session_state["view_user"] = item[idx_pub]
                            # 清除可能的聊天状态冲突
                            if "chat_with" in st.session_state:
                                del st.session_state["chat_with"]
                            st.rerun()
                    with c2:
                        if item[idx_pub] != user:
                            if st.button(f"💬 私聊TA", key=f"chat_lost_{item[0]}", use_container_width=True):
                                st.session_state["chat_with"] = {"item_id": item[0], "target_user": item[idx_pub], "item_name": item[2]}
                                # 【修改】清除 view_user 避免冲突，并确保立即 rerun
                                if "view_user" in st.session_state:
                                    del st.session_state["view_user"]
                                st.rerun()

        with col_found:
            st.markdown(f"""
            <div class='panel-container found-panel'>
                <div class='panel-header'>
                    <h3 style='color:#38a169; margin:0;'>🟢 捡到物品</h3>
                    <span style='font-size:0.9em; color:#718096'>共 {len(found_items)} 条线索</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            # 增加序号展示 -> 修改为直接显示编码，不再使用自然序号 idx
            for item in found_items:
                # 新增：获取物品编号 - 修复：使用动态索引
                idx_item_no = col_index.get('item_no', 1)
                item_no_val = item[idx_item_no] if len(item) > idx_item_no and item[idx_item_no] else f"ID:{item[0]}"
                # 修改：标题只保留编码和名称，去掉前面的自然序号
                with st.expander(f"🟢 [{item_no_val}] {item[2]}"):
                    info_col1, info_col2 = st.columns(2)
                    with info_col1:
                        st.markdown(f"**🏷 类别**：{item[3]}")
                        st.markdown(f"**📅 时间**：{item[4]}")
                        st.markdown(f"**📍 地点**：{item[5]}")
                    with info_col2:
                        st.markdown(f"**👤 发布人**：{item[idx_pub]}")
                        st.markdown(f"**📞 联系**：{item[idx_con]}")
                    
                    st.markdown(f"**📝 描述**：{item[6]}")
                    
                    # --- 修复图片显示逻辑 Start (Found Items) ---
                    img_shown = False
                    # 使用动态获取的索引

                    # 策略1: images 字段
                    if not img_shown and idx_images != -1 and item[idx_images]:
                        try:
                            images_str = item[idx_images]
                            if isinstance(images_str, str):
                                img_list = json.loads(images_str)
                                if isinstance(img_list, list) and img_list:
                                    img = base64_to_img(img_list[0])
                                    if img:
                                        st.image(img, width=300, caption="物品图片")
                                        img_shown = True
                        except:
                            pass

                    # 策略2: image 字段
                    if not img_shown and item[idx_image]:
                        try:
                            img_data = item[idx_image]
                            if isinstance(img_data, str):
                                if img_data.startswith('['):
                                    img_list = json.loads(img_data)
                                else:
                                    img_list = [img_data]
                                
                                if isinstance(img_list, list) and img_list:
                                    img = base64_to_img(img_list[0])
                                    if img:
                                        st.image(img, width=300, caption="物品图片")
                                        img_shown = True
                        except:
                            pass
                    # --- 修复图片显示逻辑 End ---

                    ai_res = item[idx_ai]
                    if ai_res and ai_res not in ["未识别", "无图片", "识别失败", "未上传"]:
                        st.success(f"🤖 AI推测特征：{ai_res}")
                        
                    st.divider()
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button(f"👤 查看TA主页", key=f"profile_found_{item[0]}", use_container_width=True):
                            st.session_state["view_user"] = item[idx_pub]
                            # 清除可能的聊天状态冲突
                            if "chat_with" in st.session_state:
                                del st.session_state["chat_with"]
                            st.rerun()
                    with c2:
                        if item[idx_pub] != user:
                            if st.button(f"💬 私聊TA", key=f"chat_found_{item[0]}", use_container_width=True):
                                st.session_state["chat_with"] = {"item_id": item[0], "target_user": item[idx_pub], "item_name": item[2]}
                                # 【修改】清除 view_user 避免冲突，并确保立即 rerun
                                if "view_user" in st.session_state:
                                    del st.session_state["view_user"]
                                st.rerun()

    elif choice == "🔴 我丢了东西":
        st.markdown("""<div class='title-card'><h1>🔴 发布丢失物品</h1></div>""", unsafe_allow_html=True)
        with st.form("lost"):
            name = st.text_input("物品名称", key="lost_name")
            cate_choice = st.selectbox("类别", CATEGORY_OPTIONS, key="lost_cate")
            if cate_choice == "其他":
                cate = st.text_input("请输入具体类别", key="lost_custom_cate")
            else:
                cate = cate_choice
            time = st.date_input("丢失时间", key="lost_time")
            loc = st.text_input("丢失地点", key="lost_loc")
            desc = st.text_area("物品描述", key="lost_desc")
            contact = st.text_input("联系方式", key="lost_contact")
            imgs = st.file_uploader("上传图片（可多选）", type=["jpg","png"], accept_multiple_files=True)
            if st.form_submit_button("✅ 发布丢失信息", use_container_width=True):
                ai = "未上传"
                img_json_str = None
                if imgs:
                    try:
                        ai = ai_recognize(imgs[0])
                    except:
                        ai = "识别失败"
                    b64_list = []
                    for img_file in imgs:
                        try:
                            img = Image.open(img_file)
                            b64_list.append(img_to_base64(img))
                        except:
                            pass
                    if b64_list:
                        img_json_str = json.dumps(b64_list)
                
                # 生成物品编号 ITEM-XXXX，基于最大值递增，确保不重复
                c.execute("SELECT MAX(CAST(SUBSTR(item_no, 6) AS INTEGER)) FROM items WHERE item_no LIKE 'ITEM-%'")
                max_no = c.fetchone()[0]
                next_no = (max_no + 1) if max_no else 1
                new_item_no = f"ITEM-{next_no:04d}"

                c.execute('''INSERT INTO items (item_no, type, name, category, time, location, description, image, ai_result, publisher, contact, create_time, status, is_hidden)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (new_item_no, "lost", name, cate, str(time), loc, desc, img_json_str, ai, user, contact, str(datetime.datetime.now()), 0, 0))
                conn.commit()
                st.success(f"发布成功！物品编号: {new_item_no}")

    elif choice == "🟢 我捡到东西":
        st.markdown("""<div class='title-card'><h1>🟢 发布捡到物品</h1></div>""", unsafe_allow_html=True)
        with st.form("found"):
            name = st.text_input("物品名称", key="found_name")
            cate_choice = st.selectbox("类别", CATEGORY_OPTIONS, key="found_cate")
            if cate_choice == "其他":
                cate = st.text_input("请输入具体类别", key="found_custom_cate")
            else:
                cate = cate_choice
            time = st.date_input("捡到时间", key="found_time")
            loc = st.text_input("捡到地点", key="found_loc")
            desc = st.text_area("物品描述", key="found_desc")
            contact = st.text_input("联系方式", key="found_contact")
            imgs = st.file_uploader("上传图片（可多选）", type=["jpg","png"], accept_multiple_files=True)
            if st.form_submit_button("✅ 发布捡到信息", use_container_width=True):
                ai = "未上传"
                img_json_str = None
                if imgs:
                    try:
                        ai = ai_recognize(imgs[0])
                    except:
                        ai = "识别失败"
                    b64_list = []
                    for img_file in imgs:
                        try:
                            img = Image.open(img_file)
                            b64_list.append(img_to_base64(img))
                        except:
                            pass
                    if b64_list:
                        img_json_str = json.dumps(b64_list)
                
                # 生成物品编号 ITEM-XXXX，基于最大值递增，确保不重复
                c.execute("SELECT MAX(CAST(SUBSTR(item_no, 6) AS INTEGER)) FROM items WHERE item_no LIKE 'ITEM-%'")
                max_no = c.fetchone()[0]
                next_no = (max_no + 1) if max_no else 1
                new_item_no = f"ITEM-{next_no:04d}"

                c.execute('''INSERT INTO items (item_no, type, name, category, time, location, description, image, ai_result, publisher, contact, create_time, status, is_hidden)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (new_item_no, "found", name, cate, str(time), loc, desc, img_json_str, ai, user, contact, str(datetime.datetime.now()), 0, 0))
                conn.commit()
                st.success(f"发布成功！物品编号: {new_item_no}")

    elif choice == "🤖 AI匹配":
        st.markdown("""<div class='title-card'><h1>🤖 AI智能匹配系统</h1></div>""", unsafe_allow_html=True)
        st.info("💡 匹配规则：结合物品名称与AI图像识别特征进行综合匹配，仅显示未解决的物品")
        
        c.execute('SELECT * FROM items WHERE publisher=?', (user,))
        user_items = c.fetchall()
        
        c.execute('SELECT * FROM items WHERE is_hidden=0')
        all_items = c.fetchall()
        
        if not user_items:
            st.info("你还未发布任何物品")
        else:
            # 修复：传递 col_index 给 get_ai_matches
            matches = get_ai_matches(user_items, all_items, col_index)
            
            for my_item, matched_list in matches:
                # 使用动态索引映射来获取字段，避免硬编码索引错误
                
                iid = my_item[col_index.get('id', 0)]
                itype = my_item[col_index.get('type', 2)]
                iname = my_item[col_index.get('name', 3)]
                icat = my_item[col_index.get('category', 4)]
                itime = my_item[col_index.get('time', 5)]
                iloc = my_item[col_index.get('location', 6)]
                idesc = my_item[col_index.get('description', 7)]
                iimg = my_item[idx_image]
                iai = my_item[idx_ai]
                istat = my_item[idx_stat]
                
                # 新增：获取我的物品编号 - 修复：使用动态索引
                idx_item_no = col_index.get('item_no', 1)
                my_item_no = my_item[idx_item_no] if len(my_item) > idx_item_no and my_item[idx_item_no] else f"ID:{iid}"
                
                ico = "🔴" if itype=="lost" else "🟢"
                
                # 修改：标题突出显示编码
                with st.expander(f"{ico} 我的发布：[{my_item_no}] {iname} | {'✅ 已解决' if istat==1 else '🔍 寻找中'}"):
                    st.write(f"**我的描述**：{idesc}")
                    if iai and iai not in ["未识别", "无图片", "识别失败", "未上传"]:
                        st.caption(f"🤖 AI提取特征：{iai}")
                    
                    if matched_list:
                        st.success(f"✨ 发现 {len(matched_list)} 个潜在匹配！")
                        for m in matched_list:
                            m_id = m[col_index.get('id', 0)]
                            m_type = m[col_index.get('type', 2)]
                            m_name = m[col_index.get('name', 3)]
                            m_cat = m[col_index.get('category', 4)]
                            m_loc = m[col_index.get('location', 6)]
                            m_ai = m[idx_ai]
                            m_pub = m[idx_pub]
                            
                            idx_cre = col_index.get('create_time', 11)
                            m_create = m[idx_cre]
                            m_stat = m[idx_stat]
                            
                            # 新增：获取匹配物品的编号 - 修复：使用动态索引
                            m_item_no = m[idx_item_no] if len(m) > idx_item_no and m[idx_item_no] else f"ID:{m_id}"
                            
                            m_ico = "🟢" if m_type=="found" else "🔴"
                            
                            with st.container(border=True):
                                col_m1, col_m2 = st.columns([3, 1])
                                with col_m1:
                                    # 修改：显示匹配物品的编码
                                    st.markdown(f"**{m_ico} [{m_item_no}] {m_name}** ({m_cat})")
                                    st.caption(f"📍 {m_loc} | 📅 {m_create}")
                                    st.caption(f"👤 发布人：{m_pub}")
                                    if m_ai and m_ai not in ["未识别", "无图片", "识别失败", "未上传"]:
                                        st.caption(f"🤖 AI特征：{m_ai}")
                                with col_m2:
                                    if st.button(f"💬 联系", key=f"ma_{iid}_{m_id}"):
                                        st.session_state["chat_with"] = {"item_id":m_id, "target_user":m_pub, "item_name":m_name}
                                        st.rerun()
                    else:
                        st.warning("暂无基于名称或AI特征的匹配项")

    elif choice == "📋 我的发布":
        st.markdown("""<div class='title-card'><h1>📋 我的发布管理</h1></div>""", unsafe_allow_html=True)
        t1, t2 = st.tabs(["进行中", "已归档"])
        with t1:
            c.execute('SELECT * FROM items WHERE publisher=? AND is_hidden=0', (user,))
            items = c.fetchall()
            for it in items:
                # 新增：获取物品编号 - 修复：使用动态索引
                idx_item_no = col_index.get('item_no', 1)
                it_no = it[idx_item_no] if len(it) > idx_item_no and it[idx_item_no] else f"ID:{it[0]}"
                # 修改：标题以编码为主
                with st.expander(f"{'🔴' if it[1]=='lost' else '🟢'} [{it_no}] {it[2]}"):
                    # --- 新增：在我的发布中显示图片 ---
                    img_shown = False
                    # 策略1: images 字段
                    if not img_shown and idx_images != -1 and it[idx_images]:
                        try:
                            images_str = it[idx_images]
                            if isinstance(images_str, str):
                                img_list = json.loads(images_str)
                                if isinstance(img_list, list) and img_list:
                                    img = base64_to_img(img_list[0])
                                    if img:
                                        st.image(img, width=300, caption="物品图片")
                                        img_shown = True
                        except:
                            pass
                    # 策略2: image 字段
                    if not img_shown and it[idx_image]:
                        try:
                            img_data = it[idx_image]
                            if isinstance(img_data, str):
                                if img_data.startswith('['):
                                    img_list = json.loads(img_data)
                                else:
                                    img_list = [img_data]
                                if isinstance(img_list, list) and img_list:
                                    img = base64_to_img(img_list[0])
                                    if img:
                                        st.image(img, width=300, caption="物品图片")
                                        img_shown = True
                        except:
                            pass
                    # ----------------------------------

                    st.write(f"类别：{it[3]} | 时间：{it[4]}")
                    st.write(f"描述：{it[6]}")
                    c1, c2 = st.columns(2)
                    with c1:
                        # 使用动态索引获取 status
                        if it[idx_stat]==0 and st.button("✅ 标记已解决", key=f"d{it[0]}", use_container_width=True):
                            c.execute("UPDATE items SET status=1 WHERE id=?", (it[0],))
                            conn.commit()
                            st.rerun()
                    with c2:
                        if st.button("🗑️ 删除", key=f"x{it[0]}", use_container_width=True):
                            c.execute("DELETE FROM items WHERE id=?", (it[0],))
                            conn.commit()
                            st.rerun()
        with t2:
            c.execute('SELECT * FROM items WHERE publisher=? AND (is_hidden=1 OR status=1)', (user,))
            items = c.fetchall()
            for it in items:
                # 新增：获取物品编号 - 修复：使用动态索引
                idx_item_no = col_index.get('item_no', 1)
                it_no = it[idx_item_no] if len(it) > idx_item_no and it[idx_item_no] else f"ID:{it[0]}"
                # 修改：标题以编码为主
                with st.expander(f"{'🔴' if it[1]=='lost' else '🟢'} [{it_no}] {it[2]}"):
                    st.write("已归档/已解决")

    elif choice == "⚙️ 账号设置":
        st.markdown("""<div class='title-card'><h1>⚙️ 账号设置</h1></div>""", unsafe_allow_html=True)
        
        # 获取当前用户最新资料
        conn_set = sqlite3.connect("lost_found.db")
        c_set = conn_set.cursor()
        # 修改查询以包含 user_no
        c_set.execute("SELECT nickname, avatar, contact, user_no FROM users WHERE username=?", (user,))
        user_info = c_set.fetchone()
        current_nick = user_info[0] if user_info and user_info[0] else user
        current_avatar_b64 = user_info[1] if user_info and user_info[1] else None
        current_contact = user_info[2] if user_info and user_info[2] else ""
        current_user_no = user_info[3] if user_info and user_info[3] else "未分配"
        conn_set.close()

        tab1, tab2 = st.tabs(["👤 个人资料", "🔑 安全设置"])
        
        with tab1:
            st.subheader("基本资料")
            # 显示用户编号
            st.markdown(f"**🆔 用户编号**: `{current_user_no}`")
            new_nick = st.text_input("昵称", value=current_nick)
            new_contact = st.text_input("默认联系方式", value=current_contact, placeholder="用于发布物品时自动填充")
            
            st.markdown("**头像设置**")
            if current_avatar_b64:
                try:
                    curr_img = base64_to_img(current_avatar_b64)
                    if curr_img:
                        st.image(curr_img, width=100, caption="当前头像")
                except:
                    pass
            
            avatar_file = st.file_uploader("上传新头像", type=["jpg", "png"], key="upload_avatar")
            
            if st.button("💾 保存个人资料", use_container_width=True, key="save_profile_btn"):
                conn_up = sqlite3.connect("lost_found.db")
                c_up = conn_up.cursor()
                
                avatar_b64_to_save = current_avatar_b64
                if avatar_file:
                    try:
                        img = Image.open(avatar_file)
                        avatar_b64_to_save = img_to_base64(img)
                    except Exception as e:
                        st.error(f"头像处理失败: {e}")
                        conn_up.close()
                        return

                try:
                    c_up.execute("UPDATE users SET nickname=?, avatar=?, contact=? WHERE username=?", 
                                 (new_nick, avatar_b64_to_save, new_contact, user))
                    conn_up.commit()
                    st.success("资料保存成功！")
                    st.rerun()
                except Exception as e:
                    st.error(f"保存失败: {e}")
                finally:
                    conn_up.close()

        with tab2:
            st.subheader("修改密码/账号")
            with st.form("set_security"):
                old = st.text_input("当前密码", type="password", key="sec_old")
                new_u = st.text_input("新用户名", key="sec_new_u")
                new_p = st.text_input("新密码", type="password", key="sec_new_p")
                cfm = st.text_input("确认新密码", type="password", key="sec_cfm")
                if st.form_submit_button("保存安全设置", use_container_width=True, key="sec_submit"):
                    conn2 = sqlite3.connect("lost_found.db")
                    cr2 = conn2.cursor()
                    cr2.execute("SELECT password FROM users WHERE username=?", (user,))
                    row = cr2.fetchone()
                    conn2.close()
                    if not row or row[0] != hash_password(old):
                        st.error("密码错误")
                    else:
                        sql = []
                        val = []
                        if new_u and new_u != user:
                            sql.append("username=?")
                            val.append(new_u)
                        if new_p and new_p == cfm:
                            sql.append("password=?")
                            val.append(hash_password(new_p))
                        if sql:
                            val.append(user)
                            c.execute(f"UPDATE users SET {','.join(sql)} WHERE username=?", val)
                            conn.commit()
                            if new_u:
                                st.session_state["user"] = new_u
                            st.success("修改成功")
                            st.rerun()

    elif choice == "💬 我的私聊":
        # 【修改】由于顶部已经拦截了 chat_with 状态，这里主要处理从历史记录进入的情况
        # 如果此时 session_state 里突然有了 chat_with (比如通过其他按钮触发)，再次检查
        if st.session_state.get("chat_with"):
            render_chat_interface(user)
            return

        c.execute('''SELECT DISTINCT item_id, CASE WHEN sender=? THEN receiver ELSE sender END AS other FROM chat_messages WHERE sender=? OR receiver=?''', (user, user, user))
        chats = c.fetchall()
        if not chats:
            st.info("暂无聊天")
        else:
            for iid, other in chats:
                c.execute("SELECT name FROM items WHERE id=?", (iid,))
                row = c.fetchone()
                name = row[0] if row else "物品"
                if st.button(f"💬 {name} ↔ {other}", use_container_width=True):
                    st.session_state["chat_with"] = {"item_id": iid, "target_user": other, "item_name": name}
                    st.rerun()

    elif choice == "🔐 管理面板" and role == "admin":
        st.markdown("""<div class='title-card'><h1>🔐 管理员面板</h1></div>""", unsafe_allow_html=True)
        
        t1, t2 = st.tabs(["物品管理", "用户管理"])
        
        with t1:
            c.execute("SELECT * FROM items ORDER BY create_time DESC")
            its = c.fetchall()
            st.metric("全部物品", len(its))
            if its:
                ex = export_to_excel(its)
                st.download_button("📥 导出全部数据", ex, "全站数据.xlsx", use_container_width=True)
            
            # 增加搜索功能
            admin_search_kw = st.text_input("🔍 搜索物品编号、名称或发布人", key="admin_item_search")
            
            # 过滤物品
            filtered_its = its
            if admin_search_kw:
                kw_lower = admin_search_kw.lower()
                filtered_its = []
                for it in its:
                    # Schema: id(0), item_no(1), type(2), name(3), category(4), time(5), location(6), description(7), image(8), ai_result(9), publisher(10), contact(11), create_time(12), status(13), is_hidden(14)
                    # 使用动态索引更安全，但这里为了性能直接假设标准顺序，因为这是管理员视图，通常结构固定
                    # 为了保险，我们还是用之前定义的 col_index
                    idx_item_no = col_index.get('item_no', 1)
                    idx_name = col_index.get('name', 3)
                    idx_pub = col_index.get('publisher', 10)
                    
                    item_no = str(it[idx_item_no]).lower() if it[idx_item_no] else ""
                    name = str(it[idx_name]).lower()
                    pub = str(it[idx_pub]).lower()
                    if kw_lower in item_no or kw_lower in name or kw_lower in pub:
                        filtered_its.append(it)
            
            if not filtered_its:
                st.info("未找到匹配的物品")
            else:
                # 使用表格或列表展示，包含编号
                for it in filtered_its:
                    idx_id = col_index.get('id', 0)
                    idx_item_no = col_index.get('item_no', 1)
                    idx_name = col_index.get('name', 3)
                    idx_pub = col_index.get('publisher', 10)
                    idx_cat = col_index.get('category', 4)
                    idx_loc = col_index.get('location', 6)
                    idx_desc = col_index.get('description', 7)
                    idx_con = col_index.get('contact', 11)
                    idx_image = col_index.get('image', 8)
                    idx_images = col_index.get('images', -1)
                    
                    item_id = it[idx_id]
                    display_no = it[idx_item_no] if it[idx_item_no] else f"ID:{item_id}"
                    
                    # 使用唯一的 key 前缀
                    item_key_prefix = f"admin_item_{item_id}"
                    
                    col1, col2, col3 = st.columns([4, 1, 1])
                    # 修改：明确显示编码
                    col1.write(f"**物品编码**: `{display_no}` | **名称**: {it[idx_name]} | **发布人**: {it[idx_pub]}")
                    
                    # 编辑功能
                    if col2.button("✏️ 编辑", key=f"{item_key_prefix}_edit_btn"):
                        # 使用 session_state 标记正在编辑的物品 ID
                        st.session_state[f"editing_item_id"] = item_id
                    
                    if col3.button("🗑️ 删除", key=f"{item_key_prefix}_del_btn"):
                        c.execute("DELETE FROM items WHERE id=?", (item_id,))
                        conn.commit()
                        st.rerun()
                    
                    # 编辑表单区域 - 独立于循环的按钮逻辑，通过 session_state 判断
                    if st.session_state.get("editing_item_id") == item_id:
                        with st.container(border=True):
                            st.write(f"**编辑物品 No.{display_no}**")
                            
                            # --- 新增：在管理面板编辑时显示图片 ---
                            img_shown_admin = False
                            if not img_shown_admin and idx_images != -1 and it[idx_images]:
                                try:
                                    images_str = it[idx_images]
                                    if isinstance(images_str, str):
                                        img_list = json.loads(images_str)
                                        if isinstance(img_list, list) and img_list:
                                            img = base64_to_img(img_list[0])
                                            if img:
                                                st.image(img, width=200, caption="当前图片")
                                                img_shown_admin = True
                                except:
                                    pass
                            if not img_shown_admin and it[idx_image]:
                                try:
                                    img_data = it[idx_image]
                                    if isinstance(img_data, str):
                                        if img_data.startswith('['):
                                            img_list = json.loads(img_data)
                                        else:
                                            img_list = [img_data]
                                        if isinstance(img_list, list) and img_list:
                                            img = base64_to_img(img_list[0])
                                            if img:
                                                st.image(img, width=200, caption="当前图片")
                                                img_shown_admin = True
                                except:
                                    pass
                            # --------------------------------------

                            # 确保 input 的 key 也是唯一的
                            e_name = st.text_input("名称", value=it[idx_name], key=f"{item_key_prefix}_e_name")
                            e_cat = st.text_input("类别", value=it[idx_cat], key=f"{item_key_prefix}_e_cat")
                            e_loc = st.text_input("地点", value=it[idx_loc], key=f"{item_key_prefix}_e_loc")
                            e_desc = st.text_area("描述", value=it[idx_desc], key=f"{item_key_prefix}_e_desc")
                            e_con = st.text_input("联系方式", value=it[idx_con], key=f"{item_key_prefix}_e_con")
                            
                            ec1, ec2 = st.columns(2)
                            with ec1:
                                if st.button("保存修改", key=f"{item_key_prefix}_save_btn"):
                                    c.execute("UPDATE items SET name=?, category=?, location=?, description=?, contact=? WHERE id=?", 
                                              (e_name, e_cat, e_loc, e_desc, e_con, item_id))
                                    conn.commit()
                                    st.success("修改成功")
                                    # 清除编辑状态
                                    if "editing_item_id" in st.session_state:
                                        del st.session_state["editing_item_id"]
                                    st.rerun()
                            with ec2:
                                if st.button("取消", key=f"{item_key_prefix}_cancel_btn"):
                                    if "editing_item_id" in st.session_state:
                                        del st.session_state["editing_item_id"]
                                    st.rerun()
                        
        with t2:
            st.subheader("用户详细信息管理")
            
            # 增加用户搜索
            admin_user_search_kw = st.text_input("🔍 搜索用户编号、用户名或昵称", key="admin_user_search")

            # 获取所有用户详细信息
            c.execute("SELECT username, user_no, role, nickname, avatar, contact, is_active FROM users")
            users_info = c.fetchall()
            
            # 过滤用户
            filtered_users = users_info
            if admin_user_search_kw:
                kw_lower = admin_user_search_kw.lower()
                filtered_users = []
                for u in users_info:
                    # u[0]=username, u[1]=user_no, u[3]=nickname
                    uname = str(u[0]).lower()
                    uno = str(u[1]).lower() if u[1] else ""
                    unick = str(u[3]).lower() if u[3] else ""
                    if kw_lower in uname or kw_lower in uno or kw_lower in unick:
                        filtered_users.append(u)

            if not filtered_users:
                st.info("未找到匹配的用户")
            else:
                for u_info in filtered_users:
                    u_name, u_no, u_role, u_nick, u_avatar, u_contact, u_active = u_info
                    
                    # 构建显示名称
                    display_name = u_nick if u_nick else u_name
                    # 确保 u_no 有值
                    display_u_no = u_no if u_no else "未分配"
                    
                    with st.expander(f"👤 [{display_u_no}] {u_name} ({display_name}) - {'✅ 正常' if u_active else '🚫 禁用'}"):
                        info_col1, info_col2 = st.columns([1, 2])
                        
                        with info_col1:
                            # 显示头像
                            if u_avatar:
                                try:
                                    img = base64_to_img(u_avatar)
                                    if img:
                                        st.image(img, width=80, caption="头像")
                                    else:
                                        st.markdown("<div style='text-align:center;'>👤</div>", unsafe_allow_html=True)
                                except:
                                    st.markdown("<div style='text-align:center;'>👤</div>", unsafe_allow_html=True)
                            else:
                                st.markdown("<div style='text-align:center; font-size:30px;'>👤</div>", unsafe_allow_html=True)
                            
                            st.markdown(f"**角色**: `{u_role}`")
                            st.markdown(f"**状态**: {'正常' if u_active else '禁用'}")

                        with info_col2:
                            st.markdown(f"**用户编号**: `{display_u_no}`")
                            st.markdown(f"**昵称**: {u_nick if u_nick else '未设置'}")
                            st.markdown(f"**联系方式**: {u_contact if u_contact else '未设置'}")
                            st.markdown(f"**账号**: `{u_name}`")
                            
                            # 操作区域
                            op_col1, op_col2, op_col3, op_col4 = st.columns(4)
                            
                            # 1. 编辑资料
                            with op_col1:
                                if st.button("✏️ 编辑", key=f"edit_u_{u_name}"):
                                    st.session_state[f"edit_user_{u_name}"] = True
                            
                            # 2. 禁用/启用
                            with op_col2:
                                new_status = 0 if u_active else 1
                                btn_text = "🚫 禁用" if u_active else "✅ 启用"
                                if st.button(btn_text, key=f"toggle_u_{u_name}"):
                                    c.execute("UPDATE users SET is_active=? WHERE username=?", (new_status, u_name))
                                    conn.commit()
                                    st.success(f"已{'禁用' if new_status==0 else '启用'}用户 {u_name}")
                                    st.rerun()
                                    
                            # 3. 私聊
                            with op_col3:
                                if st.button("💬 私聊", key=f"chat_u_{u_name}"):
                                    st.session_state["chat_with"] = {
                                        "item_id": 0, 
                                        "target_user": u_name, 
                                        "item_name": f"与 {display_name} 的对话"
                                    }
                                    st.rerun()
                                    
                            # 4. 删除
                            with op_col4:
                                if u_name != "admin":
                                    if st.button("🗑️ 删除", key=f"del_u_{u_name}"):
                                        c.execute("DELETE FROM users WHERE username=?", (u_name,))
                                        conn.commit()
                                        st.success(f"已删除用户 {u_name}")
                                        st.rerun()
                                else:
                                    st.caption("超级管理员不可删")

                        # 编辑表单区域
                        if st.session_state.get(f"edit_user_{u_name}", False):
                            st.markdown("---")
                            st.write(f"**编辑用户: {u_name}**")
                            e_nick = st.text_input("昵称", value=u_nick if u_nick else "", key=f"e_nick_{u_name}")
                            e_contact = st.text_input("联系方式", value=u_contact if u_contact else "", key=f"e_con_{u_name}")
                            e_role = st.selectbox("角色", ["user", "admin"], index=0 if u_role=="user" else 1, key=f"e_role_{u_name}")
                            
                            ec1, ec2 = st.columns(2)
                            with ec1:
                                if st.button("保存修改", key=f"save_edit_{u_name}"):
                                    c.execute("UPDATE users SET nickname=?, contact=?, role=? WHERE username=?", 
                                              (e_nick, e_contact, e_role, u_name))
                                    conn.commit()
                                    st.success("修改成功")
                                    del st.session_state[f"edit_user_{u_name}"]
                                    st.rerun()
                            with ec2:
                                if st.button("取消", key=f"cancel_edit_{u_name}"):
                                    del st.session_state[f"edit_user_{u_name}"]
                                    st.rerun()

    elif choice == "📊 一键导入数据" and role == "admin":
        st.markdown("""<div class='title-card'><h1>📊 批量导入</h1></div>""", unsafe_allow_html=True)
        f = st.file_uploader("上传Excel", type=["xlsx", "csv"])
        if f:
            try:
                df = pd.read_excel(f, header=None, skiprows=2) if f.name.endswith("xlsx") else pd.read_csv(f, header=None, skiprows=2)
                st.dataframe(df.head(10))
                if st.button("开始导入", use_container_width=True):
                    cnt = 0
                    for _, row in df.iterrows():
                        try:
                            name = str(row[0]).strip()
                            time = str(row[1])
                            loc = str(row[2])
                            cate = str(row[3])
                            contact = str(row[5])
                            if pd.isna(name) or pd.isna(loc): continue
                            itype = "found" if "招领" in cate else "lost"
                            c.execute('''INSERT INTO items (type,name,category,time,location,contact,publisher,create_time) VALUES (?,?,?,?,?,?,?,?)''',
                            (itype, name, cate, time, loc, contact, "admin", str(datetime.datetime.now())))
                            cnt += 1
                        except:
                            continue
                    conn.commit()
                    st.success(f"导入成功 {cnt} 条")
            except:
                st.error("导入失败")

    conn.close()

# ===================== 新增：独立的私聊渲染函数 =====================
def render_chat_interface(user):
    """
    独立渲染私聊界面，确保在任何地方调用都能正确显示
    """
    st.markdown("""<div class='title-card'><h1>💬 站内私聊</h1></div>""", unsafe_allow_html=True)
    
    chat = st.session_state.get("chat_with")
    if not chat:
        # 如果状态丢失，返回首页
        st.warning("会话已过期，请重新选择物品进行私聊")
        if st.button("返回首页"):
            if "chat_with" in st.session_state:
                del st.session_state["chat_with"]
            st.rerun()
        return

    iid = chat["item_id"]
    target = chat["target_user"]
    iname = chat["item_name"]    
    st.subheader(f"物品：{iname}")
    st.caption(f"对话对象：{target}")
    
    with st.form("send", clear_on_submit=True):
        msg = st.text_area("输入消息", key="chat_input_area")
        if st.form_submit_button("发送", use_container_width=True) and msg.strip():
            conn = sqlite3.connect("lost_found.db")
            c = conn.cursor()
            c.execute('INSERT INTO chat_messages VALUES (NULL,?,?,?,?,?)',
                     (iid, user, target, msg.strip(), str(datetime.datetime.now())))
            conn.commit()
            conn.close()
            st.rerun()
            
    conn = sqlite3.connect("lost_found.db")
    c = conn.cursor()
    
    # 获取聊天记录
    # 注意：item_id 为 0 表示管理员与用户的通用私聊，不绑定特定物品
    if iid == 0:
        c.execute('''SELECT * FROM chat_messages WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?) ORDER BY create_time ASC''',
                  (user, target, target, user))
    else:
        c.execute('''SELECT * FROM chat_messages WHERE item_id=? AND (sender=? OR receiver=?) ORDER BY create_time ASC''',
                  (iid, user, user))
                  
    ms = c.fetchall()
    conn.close()
    
    # 显示消息 - 修改为兼容性更好的方式，避免 st.chat_message 在旧版本报错
    chat_container = st.container()
    with chat_container:
        for m in ms:
            # chat_messages 表结构: id(0), item_id(1), sender(2), receiver(3), message(4), create_time(5)
            s = m[2] # sender
            t = m[4] # message
            time_str = m[5] if len(m) > 5 else ""
            
            if s == user:
                # 右侧显示（我）
                st.markdown(f"""
                <div style="display:flex; justify-content:flex-end; margin-bottom:10px;">
                    <div style="background-color:#dcf8c6; padding:10px 15px; border-radius:15px; max-width:70%; box-shadow:0 1px 2px rgba(0,0,0,0.1);">
                        <div style="font-size:0.8em; color:#555; text-align:right; margin-bottom:2px;">我 {time_str.split(' ')[1] if ' ' in time_str else ''}</div>
                        <div>{t}</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                # 左侧显示（对方）
                st.markdown(f"""
                <div style="display:flex; justify-content:flex-start; margin-bottom:10px;">
                    <div style="background-color:#ffffff; padding:10px 15px; border-radius:15px; max-width:70%; box-shadow:0 1px 2px rgba(0,0,0,0.1); border:1px solid #eee;">
                        <div style="font-size:0.8em; color:#555; margin-bottom:2px;">{s} {time_str.split(' ')[1] if ' ' in time_str else ''}</div>
                        <div>{t}</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            
    st.divider()
    if st.button("← 返回列表/首页", use_container_width=True, key="btn_back_from_chat"):
        del st.session_state["chat_with"]
        st.rerun()

# ===================== 入口 =====================
set_style()
init_db()

if "user" not in st.session_state:
    login_page()
else:
    main_page()