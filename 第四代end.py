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
import difflib
import shutil  # 【新增】用于文件操作/备份

# ===================== 配置 =====================
BAIDU_API_KEY = os.getenv("BAIDU_API_KEY", "无")
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY", "无")

# 【修改】支持通过环境变量 DB_PATH 指定数据库路径，便于部署时挂载到持久化存储
# 默认仍为当前目录下的 lost_found.db
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "lost_found.db"))

# 【新增】敏感词库 (实际项目中建议从配置文件或数据库加载)
SENSITIVE_WORDS = [
    "傻逼", "sb", "尼玛", "滚", "去死", "废物", "垃圾", "脑残", "智障", 
    "操", "草", "日", "妈的", "他妈", "混蛋", "贱人", "恶心"
]

# ===================== 工具函数 =====================
def hash_password(pwd):
    return hashlib.md5(pwd.encode()).hexdigest()

# 【新增】敏感词检查函数
def contains_sensitive_word(text):
    if not text:
        return False, None
    for word in SENSITIVE_WORDS:
        if word in text:
            return True, word
    return False, None

def img_to_base64(img):
    buf = io.BytesIO()
    if img.mode != 'RGB':
        img = img.convert('RGB')
    img.save(buf, format='JPEG', quality=85)
    return base64.b64encode(buf.getvalue()).decode('utf-8')

def base64_to_img(b64_str):
    if not b64_str:
        return None
    try:
        if isinstance(b64_str, str):
            # 处理 data:image/jpeg;base64,... 格式
            if ',' in b64_str:
                b64_str = b64_str.split(',', 1)[1]
            # 去除空白字符 (空格、换行等)
            b64_str = "".join(b64_str.split())
        
        # 补全 padding
        missing_padding = len(b64_str) % 4
        if missing_padding:
            b64_str += '=' * (4 - missing_padding)
            
        img_data = base64.b64decode(b64_str)
        if not img_data:
            return None

        img = Image.open(io.BytesIO(img_data))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        return img
    except Exception as e:
        # st.warning(f"图片解析失败: {e}")
        return None

# ===================== 百度AI识别 =====================
def get_baidu_token():
    if BAIDU_API_KEY == "无" or BAIDU_SECRET_KEY == "无":
        return None
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
    """
    识别图片内容
    :param img_file: UploadedFile 对象、文件路径字符串 或 字节流
    :return: 识别结果字符串
    """
    if not img_file:
        return "无图片"
    
    token = get_baidu_token()
    if not token:
        return "AI服务未配置"
    
    try:
        byte_data = None
        
        # 【修改】兼容多种输入类型，确保在公网环境下也能正确读取图片数据
        if isinstance(img_file, bytes):
            # 情况1: 直接传入字节流 (优先处理，因为编辑逻辑中我们传入了字节流)
            byte_data = img_file
        elif hasattr(img_file, 'read'):
            # 情况2: Streamlit UploadedFile 对象或类似文件对象
            current_pos = img_file.tell() if hasattr(img_file, 'tell') else None
            byte_data = img_file.read()
            if current_pos is not None and hasattr(img_file, 'seek'):
                img_file.seek(current_pos) # 恢复指针
        elif isinstance(img_file, str):
            # 情况3: 文件路径字符串
            if os.path.exists(img_file):
                with open(img_file, 'rb') as f:
                    byte_data = f.read()
            else:
                return "图片文件不存在"
        else:
            return "不支持的图片格式"

        if not byte_data:
            return "图片数据为空"

        # 调用百度AI接口
        request_url = "https://aip.baidubce.com/rest/2.0/image-classify/v2/advanced_general"
        headers = {'content-type': 'application/x-www-form-urlencoded'}
        params = {"image": base64.b64encode(byte_data).decode('utf-8'), "baike_num": 5}
        
        response = requests.post(request_url, data=params, headers=headers, params={'access_token': token}, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            if 'result' in result and len(result['result']) > 0:
                return result['result'][0]['keyword']
            else:
                # 记录详细错误以便调试
                print(f"百度API返回无结果: {result}")
                return "识别无结果"
        else:
            print(f"百度API请求失败: {response.status_code}, {response.text}")
            return "识别服务异常"

    except Exception as e:
        print(f"AI识别错误: {e}")
        return "识别异常"

# ===================== 数据库初始化 =====================
def init_db():
    # 【新增】确保数据库所在目录存在且有权限
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir)
        except Exception as e:
            st.error(f"无法创建数据库目录 {db_dir}: {e}")

            
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS items
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  item_no TEXT, 
                  type TEXT, name TEXT, category TEXT,
                  time TEXT, location TEXT, description TEXT,
                  image TEXT, ai_result TEXT,
                  publisher TEXT, contact TEXT, create_time TEXT,
                  status INTEGER DEFAULT 0, 
                  is_hidden INTEGER DEFAULT 0,
                  remarks TEXT)''')

    columns_to_check = [
        ("item_no", "ALTER TABLE items ADD COLUMN item_no TEXT"),
        ("status", "ALTER TABLE items ADD COLUMN status INTEGER DEFAULT 0"),
        ("is_hidden", "ALTER TABLE items ADD COLUMN is_hidden INTEGER DEFAULT 0"),
        ("images", "ALTER TABLE items ADD COLUMN images TEXT"),
        ("remarks", "ALTER TABLE items ADD COLUMN remarks TEXT")
    ]
    
    for col_name, alter_sql in columns_to_check:
        try:
            c.execute(f"SELECT {col_name} FROM items LIMIT 1")
        except:
            try:
                c.execute(alter_sql)
            except:
                pass

    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (username TEXT PRIMARY KEY, 
                  user_no TEXT,
                  password TEXT, role TEXT DEFAULT 'user',
                  nickname TEXT, avatar TEXT, contact TEXT, is_active INTEGER DEFAULT 1)''')

    c.execute('''CREATE TABLE IF NOT EXISTS chat_messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  item_id INTEGER,
                  sender TEXT,
                  receiver TEXT,
                  message TEXT,
                  create_time TEXT)''')

    # 【新增】创建用户警告记录表
    c.execute('''CREATE TABLE IF NOT EXISTS user_warnings
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT,
                  reason TEXT,
                  trigger_message TEXT,
                  create_time TEXT)''')

    # 补全用户编号
    c.execute("SELECT username FROM users WHERE user_no IS NULL OR user_no = ''")
    users_to_fix = c.fetchall()
    if users_to_fix:
        c.execute("SELECT MAX(CAST(SUBSTR(user_no, 6) AS INTEGER)) FROM users WHERE user_no LIKE 'USER-%'")
        max_user_no = c.fetchone()[0]
        next_user_no = (max_user_no + 1) if max_user_no else 1
        for u in users_to_fix:
            uname = u[0]
            new_uno = f"USER-{next_user_no:04d}"
            c.execute("UPDATE users SET user_no=? WHERE username=?", (new_uno, uname))
            next_user_no += 1

    # 补全物品编号
    c.execute("SELECT id FROM items WHERE item_no IS NULL OR item_no = ''")
    items_to_fix = c.fetchall()
    if items_to_fix:
        c.execute("SELECT MAX(CAST(SUBSTR(item_no, 6) AS INTEGER)) FROM items WHERE item_no LIKE 'ITEM-%'")
        max_item_no = c.fetchone()[0]
        next_item_no = (max_item_no + 1) if max_item_no else 1
        for it in items_to_fix:
            iid = it[0]
            new_ino = f"ITEM-{next_item_no:04d}"
            c.execute("UPDATE items SET item_no=? WHERE id=?", (new_ino, iid))
            next_item_no += 1

    # 初始化管理员
    c.execute("SELECT * FROM users WHERE username='admin'")
    if not c.fetchone():
        c.execute("SELECT MAX(CAST(SUBSTR(user_no, 6) AS INTEGER)) FROM users WHERE user_no LIKE 'USER-%'")
        max_no = c.fetchone()[0]
        next_no = (max_no + 1) if max_no else 1
        admin_no = f"USER-{next_no:04d}"
        c.execute("INSERT INTO users (username, user_no, password, role, is_active) VALUES (?, ?, ?, 'admin', 1)", 
                  ('admin', admin_no, hash_password("admin123")))

    conn.commit()
    conn.close()

# ===================== 物品编辑 =====================
def edit_item_logic(item_id, name, category, time_val, location, description, contact, imgs=None, remarks=None):
    """
    独立的事务处理函数，不依赖外部连接
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("SELECT image, ai_result, remarks FROM items WHERE id=?", (item_id,))
        old_data = c.fetchone()
        if not old_data:
            return False
            
        old_image_json = old_data[0]
        old_ai = old_data[1] if old_data[1] else "未识别"
        old_remarks = old_data[2] if old_data[2] else ""
        
        new_image_json = old_image_json
        new_ai = old_ai
        new_remarks = remarks if remarks is not None else old_remarks
        
        if imgs:
            try:
                # 【修改】为了安全起见，先读取第一个文件的字节流用于AI识别，避免指针问题
                first_img_bytes = imgs[0].read()
                # 重置指针以便后续可能的重用（虽然下面我们是重新处理列表）
                if hasattr(imgs[0], 'seek'):
                    imgs[0].seek(0)
                
                # 使用字节流进行识别
                new_ai = ai_recognize(first_img_bytes)
            except Exception as e:
                print(f"AI识别异常: {e}")
                new_ai = "识别失败"
            
            b64_list = []
            for img_file in imgs:
                try:
                    # 【修改】确保每次循环都从头读取，防止指针残留导致图片为空
                    if hasattr(img_file, 'seek'):
                        img_file.seek(0)
                    img_bytes = img_file.read()
                    
                    if not img_bytes:
                        continue
                        
                    img = Image.open(io.BytesIO(img_bytes))
                    # 强制转换为RGB，避免RGBA模式保存JPEG报错
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    b64_list.append(img_to_base64(img))
                except Exception as e:
                    print(f"图片处理异常: {e}")
                    pass
                    
            if b64_list:
                new_image_json = json.dumps(b64_list)

        c.execute('''UPDATE items SET name=?, category=?, time=?, location=?, description=?, contact=?, image=?, ai_result=?, remarks=? 
                     WHERE id=?''',
                  (name, category, str(time_val), location, description, contact, new_image_json, new_ai, new_remarks, item_id))
        conn.commit()
        
        # 【新增】关键操作后备份数据库（可选，防止数据损坏，小数据量下可行）
        # backup_db() 
        
        return True
    except Exception as e:
        st.error(f"编辑失败: {e}")
        return False
    finally:
        conn.close()

# ===================== 搜索 & 统计 =====================
def search_items(keyword, category, item_type):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    query = "SELECT * FROM items WHERE is_hidden=0"
    params = []
    if keyword:
        # 修改：增加对 item_no 的搜索支持
        query += " AND (name LIKE ? OR description LIKE ? OR publisher LIKE ? OR category LIKE ? OR item_no LIKE ?)"
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    if category and category != "全部":
        query += " AND category = ?"
        params.append(category)
    if item_type != "全部":
        t = "lost" if item_type == "丢失" else "found"
        query += " AND type = ?"
        params.append(t)
    query += " ORDER BY create_time DESC"
    c.execute(query, params)
    items = [dict(row) for row in c.fetchall()]
    conn.close()
    return items

def get_stats():
    conn = sqlite3.connect(DB_PATH)
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

def get_user_items(username):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM items WHERE publisher=? AND is_hidden=0 ORDER BY create_time DESC", (username,))
    items = [dict(row) for row in c.fetchall()]
    conn.close()
    return items

# ===================== 样式 =====================
def set_style():
    st.set_page_config(page_title="校园失物招领", page_icon="🎓", layout="wide")
    st.markdown("""
    <style>
    /* 全局基础样式 - 治愈系浅色主题 */
    body {
        background-color: #fdfbf7; /* 暖白色背景 */
        color: #4a4a4a;
    }
    
    .title-card{
        background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%); /* 淡蓝到淡粉渐变 */
        padding: 25px;
        border-radius: 20px;
        text-align: center;
        margin-bottom: 25px;
        color: #5d5d5d;
        box-shadow: 0 4px 15px rgba(0,0,0,0.05);
    }
    
    .stat-card{
        padding: 20px;
        border-radius: 15px;
        color: #555;
        text-align: center;
        margin-bottom: 10px;
        box-shadow: 0 4px 10px rgba(0,0,0,0.05);
        transition: transform 0.2s;
    }
    .stat-card:hover {
        transform: translateY(-2px);
    }
    
    /* 统计卡片柔和配色 */
    .stat-total{background: linear-gradient(135deg, #e0c3fc 0%, #8ec5fc 100%);} /* 淡紫到淡蓝 */
    .stat-lost{background: linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%);} /* 淡橙暖色 */
    .stat-found{background: linear-gradient(135deg, #d4fc79 0%, #96e6a1 100%);} /* 清新淡绿 */
    .stat-solved{background: linear-gradient(135deg, #84fab0 0%, #8fd3f4 100%);} /* 薄荷蓝绿 */

    /* 搜索与列表卡片 - 纯净白色带柔和阴影 */
    .search-card {
        background: #ffffff;
        padding: 25px;
        border-radius: 20px;
        margin-bottom: 25px;
        color: #444;
        border: 1px solid #f0f0f0;
        box-shadow: 0 5px 15px rgba(0,0,0,0.03);
    }
    
    /* 丢失物品面板 - 柔和暖调 */
    .lost-panel {
        background: #fff5f5; /* 极淡粉红 */
        border: 1px solid #ffe3e3;
        border-radius: 15px;
        padding: 15px;
        margin-bottom: 15px;
        color: #5a5a5a;
        box-shadow: 0 2px 8px rgba(255, 100, 100, 0.05);
    }
    
    /* 捡到物品面板 - 柔和冷调/自然调 */
    .found-panel {
        background: #f0fff4; /* 极淡薄荷绿 */
        border: 1px solid #c6f6d5;
        border-radius: 15px;
        padding: 15px;
        margin-bottom: 15px;
        color: #5a5a5a;
        box-shadow: 0 2px 8px rgba(100, 255, 100, 0.05);
    }
    
    /* 移除所有深色模式适配，强制保持浅色治愈风格 */
    </style>
    """, unsafe_allow_html=True)

# ===================== 登录 =====================
def login_page():
    st.markdown("<div class='title-card'><h1>🎓 校园失物招领平台</h1></div>", unsafe_allow_html=True)
    tab1, tab2 = st.tabs(["登录","注册"])
    with tab1:
        user = st.text_input("用户名", key="login_user")
        pwd = st.text_input("密码", type="password", key="login_pwd")
        if st.button("登录", use_container_width=True, key="btn_login"):
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT password, role, is_active FROM users WHERE username=?", (user,))
            res = c.fetchone()
            conn.close()
            if res:
                hp, role, active = res
                if active==0:
                    st.error("账号已禁用")
                elif hp==hash_password(pwd):
                    st.session_state.user = user
                    st.session_state.role = role
                    st.success("登录成功")
                    st.rerun()
                else:
                    st.error("密码错误")
            else:
                st.error("用户不存在")
    with tab2:
        nu = st.text_input("用户名", key="reg_user")
        np = st.text_input("密码", type="password", key="reg_pwd")
        cf = st.text_input("确认密码", type="password", key="reg_cf")
        if st.button("注册", use_container_width=True, key="btn_reg"):
            if np!=cf:
                st.warning("两次密码不一致")
                return
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT username FROM users WHERE username=?", (nu,))
            if c.fetchone():
                st.error("用户名已存在")
            else:
                c.execute("SELECT MAX(CAST(SUBSTR(user_no,6) AS INTEGER)) FROM users WHERE user_no LIKE 'USER-%'")
                mx = c.fetchone()[0] or 0
                uno = f"USER-{mx+1:04d}"
                c.execute("INSERT INTO users (username,user_no,password,role,is_active) VALUES (?,?,?,'user',1)",
                          (nu,uno,hash_password(np)))
                conn.commit()
                st.success(f"注册成功！编号：{uno}")
            conn.close()

# ===================== 私聊 =====================
def render_chat_interface(user):
    st.markdown("<div class='title-card'><h1>💬 站内私聊</h1></div>", unsafe_allow_html=True)
    chat = st.session_state.get("chat_with")
    if not chat:
        st.warning("会话已过期")
        if st.button("返回", key="chat_back_err"):
            if "chat_with" in st.session_state:
                del st.session_state.chat_with
            st.rerun()
        return
    iid = chat["item_id"]
    target = chat["target_user"]
    iname = chat["item_name"]
    st.subheader(f"物品：{iname}")
    st.caption(f"对话：{target}")

    # 【新增】检查并显示持久化警告
    if st.session_state.get("show_sensitive_warning"):
        warning_msg = st.session_state.get("sensitive_warning_msg", "")
        found_word = st.session_state.get("sensitive_warning_word", "")
        
        st.markdown(
            f"""
            <div style="background-color: #fff3cd; color: #856404; padding: 15px; border-radius: 5px; border: 1px solid #ffeeba; margin-bottom: 10px;">
                <strong>⚠️ 发送失败！</strong><br>
                您的消息包含敏感词汇：<span style="color: red; font-weight: bold;">'{found_word}'</span><br>
                请文明交流，修改后再次尝试发送。
            </div>
            """, 
            unsafe_allow_html=True
        )
        
        # 添加关闭按钮
        if st.button("✅ 我知道了 (关闭警告)", key="close_warning_btn"):
            if "show_sensitive_warning" in st.session_state:
                del st.session_state.show_sensitive_warning
            if "sensitive_warning_msg" in st.session_state:
                del st.session_state.sensitive_warning_msg
            if "sensitive_warning_word" in st.session_state:
                del st.session_state.sensitive_warning_word
            st.rerun()

    with st.form("send_form", clear_on_submit=True):
        msg = st.text_area("输入消息", key="chat_msg_input")
        if st.form_submit_button("发送", key="btn_send_chat") and msg.strip():
            # 【新增】敏感词检查
            is_sensitive, found_word = contains_sensitive_word(msg.strip())
            if is_sensitive:
                # 修改：设置 session_state 以持久化显示警告
                st.session_state.show_sensitive_warning = True
                st.session_state.sensitive_warning_msg = msg.strip()
                st.session_state.sensitive_warning_word = found_word
                
                # 记录警告
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("INSERT INTO user_warnings (username, reason, trigger_message, create_time) VALUES (?, ?, ?, ?)",
                          (user, f"发送包含敏感词 '{found_word}' 的消息", msg.strip(), str(datetime.datetime.now())))
                conn.commit()
                conn.close()
                
                # 重新运行以显示警告
                st.rerun()
            else:
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("INSERT INTO chat_messages VALUES (NULL,?,?,?,?,?)",
                          (iid, user, target, msg.strip(), str(datetime.datetime.now())))
                conn.commit()
                conn.close()
            st.rerun()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if iid==0:
        c.execute('''SELECT * FROM chat_messages WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?) ORDER BY create_time''',
                  (user,target,target,user))
    else:
        c.execute('''SELECT * FROM chat_messages WHERE item_id=? AND (sender=? OR receiver=?) ORDER BY create_time''',
                  (iid,user,user))
    ms = c.fetchall()
    conn.close()

    for m in ms:
        s, t, ts = m[2], m[4], m[5]
        if s==user:
            st.markdown(f"""<div style='text-align:right'><div style='background:#dcf8c6;padding:10px 15px;border-radius:15px;display:inline-block'>我<br>{t}</div></div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""<div style='text-align:left'><div style='background:#fff;padding:10px 15px;border-radius:15px;display:inline-block;border:1px solid #eee'>{s}<br>{t}</div></div>""", unsafe_allow_html=True)

    st.divider()
    if st.button("← 返回", use_container_width=True, key="chat_back_btn"):
        if "chat_with" in st.session_state:
            del st.session_state.chat_with
        st.rerun()

# ===================== 主界面 =====================
def main_page():
    user = st.session_state.user
    role = st.session_state.get("role","user")

    # 检查是否有强制跳转菜单的需求
    if "force_menu_choice" in st.session_state:
        target_menu = st.session_state.force_menu_choice
        del st.session_state.force_menu_choice
        # 注意：直接修改 session_state 中的 widget key 可能不会立即反映在当前运行的脚本周期中
        # 但配合 st.rerun() 使用，可以在下一次运行时生效。
        # 为了确保 selectbox 选中正确值，我们通常需要在 selectbox 定义前处理好逻辑，
        # 或者利用 st.session_state 的持久性。
        # 这里我们采用一种更稳健的方式：在渲染 selectbox 时，如果 session_state 中有特定标记，
        # 但这在 Streamlit 中比较难直接控制已渲染组件的值而不通过回调。
        
        # 修正策略：
        # 1. 设置一个标志位 goto_my_posts
        # 2. 在页面顶部检查该标志位
        # 3. 如果存在，删除标志位，并执行 st.switch_page (如果有多页) 或者简单地
        #    由于我们是单页应用，通过 control flow 来展示对应内容。
        #    但是目前的结构是依赖 `choice` 变量。
        #    我们可以强制设置 `choice` 变量吗？不行，因为它是局部变量且来自 selectbox。
        #    
        #    最佳实践：在 selectbox 之后，立即检查是否需要覆盖 choice。
        pass 

    # 检查是否进入私聊模式
    if st.session_state.get("chat_with"):
        render_chat_interface(user)
        return

    # 【新增】在侧边栏菜单上方添加平台标题
    st.sidebar.markdown(
        """
        <div style="text-align: center; padding: 10px 0; margin-bottom: 10px; border-bottom: 1px solid #eee;">
            <h2 style="margin: 0; color: #4a4a4a; font-size: 1.5rem;">🎓 校园失物招领平台</h2>
        </div>
        """, 
        unsafe_allow_html=True
    )

    menu = ["🏠 首页","🔴 我丢了东西","🟢 我捡到东西","🤖 AI匹配","📋 我的发布","⚙️ 账号设置","💬 我的私聊"]
    if role=="admin":
        menu += ["🔐 管理面板","📊 一键导入数据"]
    
    choice = st.sidebar.selectbox("菜单", menu, key="main_menu_select")
    
    # 【新增】处理发布后的自动跳转逻辑
    # 如果会话中存在 'goto_my_posts' 标记，强制将 choice 设置为“我的发布”
    if st.session_state.get("goto_my_posts"):
        choice = "📋 我的发布"
        del st.session_state.goto_my_posts
        # 注意：此时页面已经渲染了 selectbox，视觉上可能还停留在原选项，
        # 但下方的 if-elif 逻辑会进入“我的发布”分支。
        # 为了视觉一致，最好能更新 selectbox，但这在 Streamlit 同步执行模型中较难。
        # 通常用户点击发布 -> 显示成功 -> rerun -> 进入我的发布页面，体验是可以接受的。

    st.sidebar.write(f"👤 {user} ({role})")
    if st.sidebar.button("退出登录", key="sidebar_logout"):
        st.session_state.clear()
        st.rerun()

    # 【新增】在退出登录下方添加版权和团队信息水印
    st.sidebar.markdown(
        """
        <div style="margin-top: 30px; padding-top: 15px; border-top: 1px solid #eee; text-align: center; color: #999; font-size: 0.8rem;">
            <p style="margin: 5px 0;"><strong>团队信息</strong></p>
            <p style="margin: 2px 0;">队长及平台版权所属：罗元茜</p>
            <p style="margin: 2px 0;">团队成员：徐昕翘、李嘉艺、冯时千</p>
            <p style="margin: 2px 0; color: #888;">团队成员均为华侨大学数学科学学院</p>
            <br>
            <p style="margin: 5px 0; font-size: 0.75rem; color: #bbb;">
                © 2026 校园失物招领平台<br>
                本网站所有内容均受版权保护<br>
                禁止转载、盗用、篡改及二次商用<br>
                侵权必究
            </p>
        </div>
        """, 
        unsafe_allow_html=True
    )

    CATS = ["全部","校园卡","身份证","学生证","耳机","钥匙","手机","电脑","眼镜","钱包","书包","水杯","雨伞","书籍","其他"]

    # --- 用户主页视图 ---
    if "view_user" in st.session_state:
        tu = st.session_state.view_user
        st.markdown(f"<div class='title-card'><h1>👤 {tu} 的主页</h1></div>", unsafe_allow_html=True)
        ui = get_user_items(tu)
        
        lo = [x for x in ui if x.get('type')=="lost"]
        fd = [x for x in ui if x.get('type')=="found"]
        
        if st.button("← 返回首页", key="back_from_user_profile"):
            del st.session_state.view_user
            st.rerun()
            
        c1,c2 = st.columns(2)
        with c1:
            st.subheader("🔴 丢失")
            if not lo: st.caption("无丢失物品")
            for it in lo:
                with st.expander(it.get('name', '未知物品')):
                    render_item_image_dict(it)
                    st.write(f"地点：{it.get('location', '')}")
                    st.write(f"时间：{it.get('time', '')}")
                    if st.button(f"💬 联系 {tu}", key=f"contact_lost_{it.get('id')}"):
                        st.session_state.chat_with = {"item_id":it.get('id'),"target_user":tu,"item_name":it.get('name')}
                        del st.session_state.view_user
                        st.rerun()
        with c2:
            st.subheader("🟢 捡到")
            if not fd: st.caption("无捡到物品")
            for it in fd:
                with st.expander(it.get('name', '未知物品')):
                    render_item_image_dict(it)
                    st.write(f"地点：{it.get('location', '')}")
                    st.write(f"时间：{it.get('time', '')}")
                    if st.button(f"💬 联系 {tu}", key=f"contact_found_{it.get('id')}"):
                        st.session_state.chat_with = {"item_id":it.get('id'),"target_user":tu,"item_name":it.get('name')}
                        del st.session_state.view_user
                        st.rerun()
        return

    # --- 首页 ---
    if choice == "🏠 首页":
        st.markdown("<div class='title-card'><h1>🏠 首页</h1></div>", unsafe_allow_html=True)
        total,lost_cnt,found_cnt,solved = get_stats()
        cols = st.columns(4)
        cols[0].markdown(f"<div class='stat-card stat-total'><h3>总物品</h3><h1>{total}</h1></div>", unsafe_allow_html=True)
        cols[1].markdown(f"<div class='stat-card stat-lost'><h3>丢失</h3><h1>{lost_cnt}</h1></div>", unsafe_allow_html=True)
        cols[2].markdown(f"<div class='stat-card stat-found'><h3>捡到</h3><h1>{found_cnt}</h1></div>", unsafe_allow_html=True)
        cols[3].markdown(f"<div class='stat-card stat-solved'><h3>已解决</h3><h1>{solved}</h1></div>", unsafe_allow_html=True)

        st.markdown("<div class='search-card'>", unsafe_allow_html=True)
        kw = st.text_input("搜索关键词", key="home_search_kw")
        c1,c2 = st.columns(2)
        ct = c1.selectbox("类别", CATS, key="home_search_cat")
        tp = c2.selectbox("类型", ["全部","丢失","捡到"], key="home_search_type")
        st.markdown("</div>", unsafe_allow_html=True)

        items = search_items(kw,ct,tp)
        
        # 初始化详情页状态
        if "home_detail_item_id" not in st.session_state:
            st.session_state.home_detail_item_id = None

        # --- 详情展示区域 (如果选中了物品) ---
        if st.session_state.home_detail_item_id:
            detail_id = st.session_state.home_detail_item_id
            # 查找该物品
            detail_item = next((x for x in items if x.get('id') == detail_id), None)
            
            if detail_item:
                st.markdown("---")
                # 修改：标题中增加编号显示
                st.subheader(f"📄 物品详情: 【{detail_item.get('item_no', 'N/A')}】 {detail_item.get('name')}")
                
                # 显示详细信息
                col_img, col_info = st.columns([1, 2])
                with col_img:
                    render_item_image_dict(detail_item, width=300)
                with col_info:
                    st.write(f"**类型**: {'🔴 丢失' if detail_item.get('type')=='lost' else '🟢 捡到'}")
                    st.write(f"**类别**: {detail_item.get('category', '')}")
                    st.write(f"**地点**: {detail_item.get('location', '')}")
                    st.write(f"**时间**: {detail_item.get('time', '')}")
                    st.write(f"**发布人**: {detail_item.get('publisher', '')}")
                    st.write(f"**联系方式**: {detail_item.get('contact', '')}")
                    st.write(f"**AI识别**: {detail_item.get('ai_result', 'N/A')}")
                    if detail_item.get('remarks'):
                        st.warning(f"**备注**: {detail_item.get('remarks')}")
                    st.write(f"**详细描述**: {detail_item.get('description', '')}")
                
                # 操作按钮
                btn_col1, btn_col2 = st.columns(2)
                with btn_col1:
                    if st.button("💬 联系发布者", key="home_detail_contact"):
                        st.session_state.chat_with = {
                            "item_id": detail_item.get('id'),
                            "target_user": detail_item.get('publisher'),
                            "item_name": detail_item.get('name')
                        }
                        del st.session_state.home_detail_item_id
                        st.rerun()
                with btn_col2:
                    if st.button("⬅️ 返回列表", key="home_detail_back"):
                        del st.session_state.home_detail_item_id
                        st.rerun()
                st.markdown("---")
            else:
                st.error("未找到该物品信息")
                if st.button("⬅️ 返回", key="home_detail_err_back"):
                    del st.session_state.home_detail_item_id
                    st.rerun()
        
        # --- 列表展示区域 (如果没有选中详情) ---
        else:
            # 将物品分为丢失和捡到两类
            lost_items = [it for it in items if it.get('type') == 'lost']
            found_items = [it for it in items if it.get('type') == 'found']
            
            # 使用两列布局，左边丢失，右边捡到
            col_lost, col_found = st.columns(2)
            
            # --- 左侧：丢失物品 ---
            with col_lost:
                st.markdown(f"""<div style='padding:10px; border-radius:10px; background:#fff5f5; border:1px solid #ffe3e3; margin-bottom:15px;'>
                    <h3 style='margin:0; color:#d9534f;'>🔴 丢失物品 ({len(lost_items)})</h3>
                </div>""", unsafe_allow_html=True)
                
                if not lost_items:
                    st.caption("暂无丢失物品信息")
                else:
                    for it in lost_items:
                        status_tag = "✅" if it.get('status') == 1 else ""
                        # 修改：显示编号
                        display_name = f"【{it.get('item_no', 'N/A')}】 {it.get('name', '未知')}"
                        with st.container(border=True):
                            st.write(f"**{display_name}** {status_tag}")
                            st.caption(f"📍 {it.get('location', '')}")
                            st.caption(f"🕒 {it.get('time', '')}")
                            if st.button("查看", key=f"home_view_lost_{it.get('id')}", use_container_width=True):
                                st.session_state.home_detail_item_id = it.get('id')
                                st.rerun()

            # --- 右侧：捡到物品 ---
            with col_found:
                st.markdown(f"""<div style='padding:10px; border-radius:10px; background:#f0fff4; border:1px solid #c6f6d5; margin-bottom:15px;'>
                    <h3 style='margin:0; color:#28a745;'>🟢 捡到物品 ({len(found_items)})</h3>
                </div>""", unsafe_allow_html=True)
                
                if not found_items:
                    st.caption("暂无捡到物品信息")
                else:
                    for it in found_items:
                        status_tag = "✅" if it.get('status') == 1 else ""
                        # 修改：显示编号
                        display_name = f"【{it.get('item_no', 'N/A')}】 {it.get('name', '未知')}"
                        with st.container(border=True):
                            st.write(f"**{display_name}** {status_tag}")
                            st.caption(f"📍 {it.get('location', '')}")
                            st.caption(f"🕒 {it.get('time', '')}")
                            if st.button("查看", key=f"home_view_found_{it.get('id')}", use_container_width=True):
                                st.session_state.home_detail_item_id = it.get('id')
                                st.rerun()

    # --- 发布丢失 ---
    elif choice == "🔴 我丢了东西":
        st.markdown("<div class='title-card'><h1>🔴 发布丢失信息</h1></div>", unsafe_allow_html=True)
        with st.form("form_lost"):
            name = st.text_input("物品名称", key="lost_name")
            cate = st.selectbox("类别", CATS[1:], key="lost_cate")
            t = st.date_input("丢失时间", key="lost_time")
            loc = st.text_input("丢失地点", key="lost_loc")
            desc = st.text_area("详细描述", key="lost_desc")
            contact = st.text_input("联系方式", key="lost_contact")
            remarks = st.text_area("备注", key="lost_remarks")
            imgs = st.file_uploader("上传图片", accept_multiple_files=True, key="lost_imgs")
            
            if st.form_submit_button("发布丢失信息", key="btn_submit_lost"):
                if not name or not loc:
                    st.error("名称和地点不能为空")
                    return
                
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                    
                ai = "未上传"
                imgj = None
                if imgs:
                    try: 
                        # 【修改】ai_recognize 已更新以支持 UploadedFile 对象
                        ai = ai_recognize(imgs[0])
                    except: 
                        ai="识别失败"
                    b64s = []
                    for i in imgs:
                        try:
                            # 【修改】安全读取 UploadedFile 并转换
                            img_bytes = i.read()
                            if img_bytes:
                                b64s.append(img_to_base64(Image.open(io.BytesIO(img_bytes))))
                        except: pass
                    if b64s:
                        imgj = json.dumps(b64s)
                
                # 生成唯一编号
                c.execute("SELECT MAX(CAST(SUBSTR(item_no,6) AS INTEGER)) FROM items WHERE item_no LIKE 'ITEM-%'")
                mx = c.fetchone()[0]
                next_no = (mx + 1) if mx else 1
                ino = f"ITEM-{next_no:04d}"
                
                c.execute('''INSERT INTO items (item_no,type,name,category,time,location,description,image,ai_result,publisher,contact,create_time,status,is_hidden,remarks)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (ino,"lost",name,cate,str(t),loc,desc,imgj,ai,user,contact,str(datetime.datetime.now()),0,0,remarks))
                conn.commit()
                conn.close()
                st.success(f"发布成功！物品编号：{ino}")
                st.balloons()
                
                # 【修改】设置跳转标记并重运行，防止重复提交并跳转到我的发布
                st.session_state.goto_my_posts = True
                st.rerun()

    # --- 发布捡到 ---
    elif choice == "🟢 我捡到东西":
        st.markdown("<div class='title-card'><h1>🟢 发布捡到信息</h1></div>", unsafe_allow_html=True)
        with st.form("form_found"):
            name = st.text_input("物品名称", key="found_name")
            cate = st.selectbox("类别", CATS[1:], key="found_cate")
            t = st.date_input("捡到时间", key="found_time")
            loc = st.text_input("捡到地点", key="found_loc")
            desc = st.text_area("详细描述", key="found_desc")
            contact = st.text_input("联系方式", key="found_contact")
            remarks = st.text_area("备注", key="found_remarks")
            imgs = st.file_uploader("上传图片", accept_multiple_files=True, key="found_imgs")
            
            if st.form_submit_button("发布捡到信息", key="btn_submit_found"):
                if not name or not loc:
                    st.error("名称和地点不能为空")
                    return

                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()

                ai = "未上传"
                imgj = None
                if imgs:
                    try: 
                        # 【修改】ai_recognize 已更新以支持 UploadedFile 对象
                        ai = ai_recognize(imgs[0])
                    except: 
                        ai="识别失败"
                    b64s = []
                    for i in imgs:
                        try:
                            # 【修改】安全读取 UploadedFile 并转换
                            img_bytes = i.read()
                            if img_bytes:
                                b64s.append(img_to_base64(Image.open(io.BytesIO(img_bytes))))
                        except: pass
                    if b64s:
                        imgj = json.dumps(b64s)
                
                c.execute("SELECT MAX(CAST(SUBSTR(item_no,6) AS INTEGER)) FROM items WHERE item_no LIKE 'ITEM-%'")
                mx = c.fetchone()[0]
                next_no = (mx + 1) if mx else 1
                ino = f"ITEM-{next_no:04d}"
                
                c.execute('''INSERT INTO items (item_no,type,name,category,time,location,description,image,ai_result,publisher,contact,create_time,status,is_hidden,remarks)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (ino,"found",name,cate,str(t),loc,desc,imgj,ai,user,contact,str(datetime.datetime.now()),0,0,remarks))
                conn.commit()
                conn.close()
                st.success(f"发布成功！物品编号：{ino}")
                st.balloons()
                
                # 【修改】设置跳转标记并重运行，防止重复提交并跳转到我的发布
                st.session_state.goto_my_posts = True
                st.rerun()

    # --- AI匹配 ---
    elif choice == "🤖 AI匹配":
        st.markdown("<div class='title-card'><h1>🤖 AI智能匹配</h1></div>", unsafe_allow_html=True)
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # 获取当前用户的发布物品
        c.execute("SELECT * FROM items WHERE publisher=? AND is_hidden=0 AND status=0", (user,))
        uitems = [dict(row) for row in c.fetchall()]
        
        # 获取所有其他有效物品
        c.execute("SELECT * FROM items WHERE is_hidden=0 AND status=0 AND publisher!=?", (user,))
        aitems = [dict(row) for row in c.fetchall()]
        conn.close()
        
        if not uitems:
            st.info("您目前没有正在寻找中或待认领的物品，无法进行匹配。")
        else:
            matches = get_ai_matches_dict(uitems, aitems)
            has_match = False
            for my, ms in matches:
                if ms:
                    has_match = True
                    ico = "🔴" if my.get('type')=="lost" else "🟢"
                    with st.expander(f"{ico} 您的物品：{my.get('name')} ({my.get('item_no', '')})"):
                        st.write(f"描述：{my.get('description')}")
                        st.success(f"找到 {len(ms)} 个潜在匹配项")
                        for m in ms:
                            with st.container(border=True):
                                m_ico = "🟢" if m.get('type')=='found' else "🔴"
                                # 修改：在匹配对象名称后增加编号显示
                                st.write(f"{m_ico} **{m.get('name')}** (编号: {m.get('item_no', 'N/A')}) | 地点：{m.get('location')}")
                                st.caption(f"AI识别：{m.get('ai_result', 'N/A')}")
                                if st.button("💬 联系发布者", key=f"ai_match_{my.get('id')}_{m.get('id')}"):
                                    st.session_state.chat_with = {"item_id":m.get('id'),"target_user":m.get('publisher'),"item_name":m.get('name')}
                                    st.rerun()
            if not has_match:
                st.warning("暂未找到高度匹配的物品，请尝试完善物品描述或上传图片以提高AI识别准确率。")

    # --- 我的发布 ---
    elif choice == "📋 我的发布":
        st.markdown("<div class='title-card'><h1>📋 我的发布管理</h1></div>", unsafe_allow_html=True)
        t1,t2 = st.tabs(["进行中","已归档/隐藏"])
        
        with t1:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM items WHERE publisher=? AND is_hidden=0 AND status=0 ORDER BY create_time DESC", (user,))
            its = [dict(row) for row in c.fetchall()]
            
            if not its: st.info("暂无进行中的物品")
            for it in its:
                with st.expander(f"{'🔴' if it.get('type')=='lost' else '🟢'} {it.get('name')} (编号: {it.get('item_no', 'N/A')})"):
                    render_item_image_dict(it)
                    st.write(f"地点：{it.get('location')} | 时间：{it.get('time')}")
                    st.write(f"AI识别：{it.get('ai_result', 'N/A')}")
                    c1,c2,c3 = st.columns(3)
                    if c1.button("✅ 标记为已解决", key=f"mark_solved_{it.get('id')}"):
                        c.execute("UPDATE items SET status=1 WHERE id=?", (it.get('id'),))
                        conn.commit()
                        conn.close()
                        st.rerun()
                    if c2.button("✏️ 编辑", key=f"edit_item_{it.get('id')}"):
                        st.session_state.editing_item_id = it.get('id')
                        conn.close()
                        st.rerun()
                    if c3.button("🗑️ 删除", key=f"delete_item_{it.get('id')}"):
                        c.execute("DELETE FROM items WHERE id=?", (it.get('id'),))
                        conn.commit()
                        conn.close()
                        st.rerun()
                        
            # 编辑模式
            if "editing_item_id" in st.session_state:
                eid = st.session_state.editing_item_id
                c.execute("SELECT * FROM items WHERE id=?", (eid,))
                eitem = c.fetchone()
                # 注意：这里不再关闭连接，因为下面还要用，或者重新打开
                # 为了逻辑清晰，我们在这里关闭之前的查询连接，编辑时重新获取
                conn.close()

                if eitem:
                    st.markdown("---")
                    st.subheader("编辑物品")
                    with st.form("edit_form"):
                        ename = st.text_input("名称", value=eitem['name'], key="edit_name")
                        ecat = st.selectbox("类别", CATS[1:], index=CATS.index(eitem['category']) if eitem['category'] in CATS else 0, key="edit_cat")
                        etime_val = eitem['time']
                        try:
                            etime = st.date_input("时间", value=datetime.datetime.strptime(etime_val, "%Y-%m-%d").date() if etime_val else datetime.date.today(), key="edit_time")
                        except:
                            etime = st.date_input("时间", value=datetime.date.today(), key="edit_time")
                            
                        eloc = st.text_input("地点", value=eitem['location'], key="edit_loc")
                        edesc = st.text_area("描述", value=eitem['description'], key="edit_desc")
                        econt = st.text_input("联系方式", value=eitem['contact'], key="edit_cont")
                        erem = st.text_area("备注", value=eitem['remarks'] if eitem['remarks'] else "", key="edit_rem")
                        eimgs = st.file_uploader("新图片（可选）", accept_multiple_files=True, key="edit_imgs")
                        
                        ec1, ec2 = st.columns(2)
                        if ec1.form_submit_button("保存修改", key="btn_save_edit"):
                            success = edit_item_logic(eid, ename, ecat, etime, eloc, edesc, econt, eimgs, erem)
                            if success:
                                del st.session_state.editing_item_id
                                st.rerun()
                        if ec2.form_submit_button("取消编辑", key="btn_cancel_edit"):
                            del st.session_state.editing_item_id
                            st.rerun()

        with t2:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM items WHERE publisher=? AND (status=1 OR is_hidden=1) ORDER BY create_time DESC", (user,))
            its = [dict(row) for row in c.fetchall()]
            conn.close()

            if not its: st.info("暂无归档物品")
            for it in its:
                with st.expander(f"{it.get('name')} (编号: {it.get('item_no', 'N/A')}) - {'已解决' if it.get('status')==1 else '已隐藏'}"):
                    render_item_image_dict(it)
                    if it.get('status') == 1:
                        if st.button("重新打开", key=f"reopen_{it.get('id')}"):
                            conn = sqlite3.connect(DB_PATH)
                            c = conn.cursor()
                            c.execute("UPDATE items SET status=0 WHERE id=?", (it.get('id'),))
                            conn.commit()
                            conn.close()
                            st.rerun()

    # --- 账号设置 ---
    elif choice == "⚙️ 账号设置":
        st.markdown("<div class='title-card'><h1>⚙️ 账号设置</h1></div>", unsafe_allow_html=True)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT nickname,avatar,contact,user_no FROM users WHERE username=?", (user,))
        ui = c.fetchone()
        conn.close()

        if not ui:
            st.error("用户信息获取失败")
            return

        nick = ui[0] or user
        avatar = ui[1]
        contact = ui[2] or ""
        uno = ui[3] or "未分配"
        
        t1,t2 = st.tabs(["个人资料","安全中心"])
        with t1:
            st.markdown(f"**用户编号：{uno}**")
            nn = st.text_input("昵称", value=nick, key="set_nick")
            nc = st.text_input("联系方式", value=contact, key="set_contact")
            na = st.file_uploader("更换头像", key="set_avatar")
            if st.button("保存资料", key="save_profile_btn"):
                ab = avatar
                if na:
                    try:
                        ab = img_to_base64(Image.open(na))
                    except:
                        st.error("头像图片格式错误")
                        return
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("UPDATE users SET nickname=?,avatar=?,contact=? WHERE username=?", (nn,ab,nc,user))
                conn.commit()
                conn.close()
                st.success("资料保存成功")
                st.rerun()
        with t2:
            with st.form("sec_form"):
                old = st.text_input("当前密码", type="password", key="sec_old")
                newp = st.text_input("新密码", type="password", key="sec_new")
                cfm = st.text_input("确认新密码", type="password", key="sec_cfm")
                if st.form_submit_button("修改密码", key="btn_change_pwd"):
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("SELECT password FROM users WHERE username=?", (user,))
                    hp = c.fetchone()[0]
                    if hp!=hash_password(old):
                        st.error("原密码错误")
                    elif not newp:
                        st.error("新密码不能为空")
                    elif newp!=cfm:
                        st.error("两次输入的新密码不一致")
                    else:
                        c.execute("UPDATE users SET password=? WHERE username=?", (hash_password(newp),user))
                        conn.commit()
                        conn.close()
                        st.success("密码修改成功，请重新登录")
                        st.session_state.clear()
                        st.rerun()
                    conn.close()

    # --- 我的私聊 ---
    elif choice == "💬 我的私聊":
        st.markdown("<div class='title-card'><h1>💬 我的消息</h1></div>", unsafe_allow_html=True)
        
        # 【新增】固定显示联系管理员入口
        if st.button("🛡️ 联系管理员 (系统消息)", use_container_width=True, key="chat_admin_fixed"):
            st.session_state.chat_with = {
                "item_id": 0,
                "target_user": "admin",
                "item_name": "与管理员的对话"
            }
            st.rerun()

        st.divider()

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''SELECT DISTINCT item_id, CASE WHEN sender=? THEN receiver ELSE sender END as other_user FROM chat_messages WHERE sender=? OR receiver=? ORDER BY item_id DESC''',
                  (user,user,user))
        chats = c.fetchall()
        conn.close()

        if not chats:
            st.info("暂无其他聊天记录")
        else:
            for iid,other in chats:
                # 【修改】跳过已经通过固定按钮展示的 admin 聊天（如果数据库中有记录），避免重复，或者保留以显示历史记录入口
                # 这里选择保留，因为用户可能想从历史记录进入。但为了突出管理员入口，我们已经在上面加了。
                
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("SELECT name FROM items WHERE id=?", (iid,))
                row = c.fetchone()
                conn.close()
                name = row[0] if row else "未知物品/已删除"
                if st.button(f"💬 {name} - 与 {other}", use_container_width=True, key=f"chat_list_{iid}_{other}"):
                    st.session_state.chat_with = {"item_id":iid,"target_user":other,"item_name":name}
                    st.rerun()

    # --- 管理员面板 ---
    elif choice == "🔐 管理面板" and role == "admin":
        st.markdown("<div class='title-card'><h1>🔐 系统管理面板</h1></div>", unsafe_allow_html=True)
        
        # 【新增】显示当前数据库路径提示，帮助排查部署问题
        st.info(f"💾 当前数据存储路径: `{DB_PATH}`")
        st.caption("如果是部署在公网，请确保此路径位于持久化存储卷上，否则重启后数据可能丢失。建议下载备份。")

        # 初始化编辑会话状态
        if "admin_editing_id" not in st.session_state:
            st.session_state.admin_editing_id = None
        
        # 【新增】初始化管理员忽略的警告列表 session_state
        if "admin_ignored_warnings" not in st.session_state:
            st.session_state.admin_ignored_warnings = set()


        t1,t2,t3 = st.tabs(["物品管理","用户管理","数据维护"]) # 【修改】增加数据维护标签页
        
        with t1:
            # 搜索与过滤栏
            kw = st.text_input("搜索关键词", key="admin_search_kw")
            c1,c2 = st.columns(2)
            ct = c1.selectbox("类别", CATS, key="admin_search_cat")
            tp = c2.selectbox("类型", ["全部","丢失","捡到"], key="admin_search_type")
            st.divider()

            items = search_items(kw,ct,tp)
            if not items:
                st.info("暂无符合条件的物品")
            else:
                for it in items:
                    with st.expander(f"{'🔴' if it.get('type')=='lost' else '🟢'} {it.get('name')} (编号: {it.get('item_no', 'N/A')})"):
                        render_item_image_dict(it)
                        st.write(f"地点：{it.get('location')} | 时间：{it.get('time')}")
                        st.write(f"AI识别：{it.get('ai_result', 'N/A')}")
                        c1,c2,c3,c4 = st.columns(4)
                        if c1.button("✅ 标记为已解决", key=f"admin_mark_solved_{it.get('id')}"):
                            conn = sqlite3.connect(DB_PATH)
                            c = conn.cursor()
                            c.execute("UPDATE items SET status=1 WHERE id=?", (it.get('id'),))
                            conn.commit()
                            conn.close()
                            st.rerun()
                        if c2.button("🗑️ 删除", key=f"admin_delete_item_{it.get('id')}"):
                            conn = sqlite3.connect(DB_PATH)
                            c = conn.cursor()
                            c.execute("DELETE FROM items WHERE id=?", (it.get('id'),))
                            conn.commit()
                            conn.close()
                            st.rerun()
                        if c3.button("✏️ 编辑", key=f"admin_edit_item_{it.get('id')}"):
                            st.session_state.admin_editing_id = it.get('id')
                            st.rerun()
                        if c4.button("👁️ 隐藏", key=f"admin_hide_item_{it.get('id')}"):
                            conn = sqlite3.connect(DB_PATH)
                            c = conn.cursor()
                            c.execute("UPDATE items SET is_hidden=1 WHERE id=?", (it.get('id'),))
                            conn.commit()
                            conn.close()
                            st.rerun()

            # 编辑模式
            if "admin_editing_id" in st.session_state:
                eid = st.session_state.admin_editing_id
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("SELECT * FROM items WHERE id=?", (eid,))
                eitem = c.fetchone()
                # 注意：这里不再关闭连接，因为下面还要用，或者重新打开
                # 为了逻辑清晰，我们在这里关闭之前的查询连接，编辑时重新获取
                conn.close()

                if eitem:
                    st.markdown("---")
                    st.subheader("编辑物品")
                    with st.form("admin_edit_form"):
                        ename = st.text_input("名称", value=eitem['name'], key="admin_edit_name")
                        ecat = st.selectbox("类别", CATS[1:], index=CATS.index(eitem['category']) if eitem['category'] in CATS else 0, key="admin_edit_cat")
                        etime_val = eitem['time']
                        try:
                            etime = st.date_input("时间", value=datetime.datetime.strptime(etime_val, "%Y-%m-%d").date() if etime_val else datetime.date.today(), key="admin_edit_time")
                        except:
                            etime = st.date_input("时间", value=datetime.date.today(), key="admin_edit_time")
                            
                        eloc = st.text_input("地点", value=eitem['location'], key="admin_edit_loc")
                        edesc = st.text_area("描述", value=eitem['description'], key="admin_edit_desc")
                        econt = st.text_input("联系方式", value=eitem['contact'], key="admin_edit_cont")
                        erem = st.text_area("备注", value=eitem['remarks'] if eitem['remarks'] else "", key="admin_edit_rem")
                        eimgs = st.file_uploader("新图片（可选）", accept_multiple_files=True, key="admin_edit_imgs")
                        
                        ec1, ec2 = st.columns(2)
                        if ec1.form_submit_button("保存修改", key="admin_btn_save_edit"):
                            success = edit_item_logic(eid, ename, ecat, etime, eloc, edesc, econt, eimgs, erem)
                            if success:
                                del st.session_state.admin_editing_id
                                st.rerun()
                        if ec2.form_submit_button("取消编辑", key="admin_btn_cancel_edit"):
                            del st.session_state.admin_editing_id
                            st.rerun()

        with t2:
            st.subheader("👥 用户管理")
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM users ORDER BY is_active DESC, username ASC")
            users = [dict(row) for row in c.fetchall()]
            conn.close()

            if not users:
                st.info("暂无用户")
            else:
                for u in users:
                    with st.expander(f"{u.get('username')} (编号: {u.get('user_no', 'N/A')}) - {'已启用' if u.get('is_active') else '已禁用'}"):
                        st.write(f"昵称：{u.get('nickname', '未设置')}")
                        st.write(f"联系方式：{u.get('contact', '未设置')}")
                        st.write(f"角色：{u.get('role', 'user')}")
                        c1,c2,c3 = st.columns(3)
                        if c1.button("启用" if not u.get('is_active') else "禁用", key=f"user_toggle_{u.get('username')}"):
                            conn = sqlite3.connect(DB_PATH)
                            c = conn.cursor()
                            c.execute("UPDATE users SET is_active=? WHERE username=?", (not u.get('is_active'),u.get('username')))
                            conn.commit()
                            conn.close()
                            st.rerun()
                        if c2.button("重置密码", key=f"user_reset_pwd_{u.get('username')}"):
                            conn = sqlite3.connect(DB_PATH)
                            c = conn.cursor()
                            c.execute("UPDATE users SET password=? WHERE username=?", (hash_password("123456"),u.get('username')))
                            conn.commit()
                            conn.close()
                            st.success(f"用户 {u.get('username')} 的密码已重置为 `123456`")
                        if c3.button("删除", key=f"user_delete_{u.get('username')}"):
                            conn = sqlite3.connect(DB_PATH)
                            c = conn.cursor()
                            c.execute("DELETE FROM users WHERE username=?", (u.get('username'),))
                            conn.commit()
                            conn.close()
                            st.rerun()

        # 【新增】数据维护标签页：提供下载和重置功能
        with t3:
            st.subheader("📦 数据备份与恢复")
            st.warning("重要：在重置或清理数据前，请务必先下载备份！")
            
            col_backup1, col_backup2 = st.columns(2)
            
            # 1. 下载数据库文件
            with col_backup1:
                if os.path.exists(DB_PATH):
                    with open(DB_PATH, "rb") as f:
                        st.download_button(
                            label="⬇️ 下载完整数据库备份 (.db)",
                            data=f,
                            file_name="lost_found_backup.db",
                            mime="application/x-sqlite3",
                            help="下载完整的SQLite数据库文件，可用于恢复数据或迁移。"
                        )
                else:
                    st.error("数据库文件不存在")

            # 2. 清空所有数据（危险操作）
            with col_backup2:
                st.markdown("**⚠️ 危险区域**")
                confirm_reset = st.checkbox("我已知晓这将删除所有物品和用户数据（管理员除外）")
                if st.button("🗑️ 清空所有业务数据", disabled=not confirm_reset):
                    try:
                        conn = sqlite3.connect(DB_PATH)
                        c = conn.cursor()
                        # 保留 admin 用户，清空其他
                        c.execute("DELETE FROM items")
                        c.execute("DELETE FROM chat_messages")
                        c.execute("DELETE FROM user_warnings")
                        c.execute("DELETE FROM users WHERE username != 'admin'")
                        conn.commit()
                        conn.close()
                        st.success("数据已清空！")
                        st.rerun()
                    except Exception as e:
                        st.error(f"清空失败: {e}")

            st.divider()
            st.subheader("📊 数据统计")
            total, lost, found, solved = get_stats()
            st.metric("总记录数", total)

            st.markdown("### 🔍 搜索与过滤")
            s_col1, s_col2, s_col3, s_col4 = st.columns([3, 1, 1, 1])
            search_kw = s_col1.text_input("搜索关键词 (名称/编号/发布人)", key="admin_search_kw")
            filter_type = s_col2.selectbox("类型", ["全部", "丢失", "捡到"], key="admin_filter_type")
            filter_status = s_col3.selectbox("状态", ["全部", "寻找中", "已解决", "已隐藏"], key="admin_filter_status")
            
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            query = "SELECT * FROM items WHERE 1=1"
            params = []
            
            if search_kw:
                query += " AND (name LIKE ? OR item_no LIKE ? OR publisher LIKE ?)"
                params.extend([f"%{search_kw}%", f"%{search_kw}%", f"%{search_kw}%"])
            
            if filter_type != "全部":
                t_val = "lost" if filter_type == "丢失" else "found"
                query += " AND type = ?"
                params.append(t_val)
                
            if filter_status != "全部":
                if filter_status == "寻找中":
                    query += " AND status=0 AND is_hidden=0"
                elif filter_status == "已解决":
                    query += " AND status=1"
                elif filter_status == "已隐藏":
                    query += " AND is_hidden=1"
                    
            query += " ORDER BY create_time DESC"
            
            c.execute(query, params)
            its = [dict(row) for row in c.fetchall()]
            conn.close()

            st.metric("当前显示物品数", len(its))
            
            # 修复：将导出逻辑放入按钮点击事件中，避免每次渲染都生成大对象
            if st.button("📥 生成并下载Excel数据", key="admin_export_excel_btn"):
                ex = export_to_excel_dict(its)
                if ex:
                    st.download_button("⬇️ 点击下载文件", ex, file_name="lost_found_data.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="admin_download_excel")
                else:
                    st.error("生成Excel失败")
            
            st.divider()
            
            # 编辑表单区域 (如果处于编辑模式)
            if st.session_state.admin_editing_id:
                eid = st.session_state.admin_editing_id
                # 获取当前编辑物品的最新数据
                conn_tmp = sqlite3.connect(DB_PATH)
                conn_tmp.row_factory = sqlite3.Row
                c_tmp = conn_tmp.cursor()
                c_tmp.execute("SELECT * FROM items WHERE id=?", (eid,))
                eitem_row = c_tmp.fetchone()
                conn_tmp.close()
                
                if eitem_row:
                    eitem = dict(eitem_row)
                    st.markdown(f"#### ✏️ 正在编辑物品: {eitem.get('name')} (ID: {eid})")
                    with st.form("admin_edit_form"):
                        col_e1, col_e2 = st.columns(2)
                        ename = col_e1.text_input("名称", value=eitem['name'], key="adm_edit_name")
                        ecat = col_e2.selectbox("类别", CATS[1:], index=CATS.index(eitem['category']) if eitem['category'] in CATS else 0, key="adm_edit_cat")
                        
                        col_e3, col_e4 = st.columns(2)
                        eloc = col_e3.text_input("地点", value=eitem['location'], key="adm_edit_loc")
                        econt = col_e4.text_input("联系方式", value=eitem['contact'], key="adm_edit_cont")
                        
                        edesc = st.text_area("描述", value=eitem['description'], key="adm_edit_desc")
                        erem = st.text_area("备注", value=eitem['remarks'] if eitem['remarks'] else "", key="adm_edit_rem")
                        
                        st.caption("上传新图片将触发AI重新识别并替换旧图片")
                        eimgs = st.file_uploader("新图片（可选）", accept_multiple_files=True, key="adm_edit_imgs")
                        
                        col_btn1, col_btn2 = st.columns(2)
                        if col_btn1.form_submit_button("💾 保存修改", key="btn_adm_save_edit"):
                            success = edit_item_logic(eid, ename, ecat, eitem['time'], eloc, edesc, econt, eimgs, erem)
                            if success:
                                st.session_state.admin_editing_id = None
                                st.success("修改成功")
                                st.rerun()
                        if col_btn2.form_submit_button("❌ 取消编辑", key="btn_adm_cancel_edit"):
                            st.session_state.admin_editing_id = None
                            st.rerun()
                    st.divider()

            st.subheader("物品列表")
            if not its:
                st.info("暂无符合条件的物品数据")
            else:
                for it in its:
                    try:
                        item_name = it.get('name', '未知物品')
                        item_id = it.get('id')
                        item_no = it.get('item_no', 'N/A')
                        publisher = it.get('publisher', 'Unknown')
                        item_type = it.get('type')
                        status = it.get('status')
                        is_hidden = it.get('is_hidden')
                        
                        # 确定边框颜色和图标
                        border_color = "#ffe3e3" if item_type == "lost" else "#c6f6d5"
                        type_icon = "🔴" if item_type == "lost" else "🟢"
                        status_tag = "✅ 已解决" if status == 1 else ("👁️ 已隐藏" if is_hidden else "🔍 寻找中")
                        
                        with st.container(border=True):
                            c_head1, c_head2, c_head3 = st.columns([6, 2, 2])
                            c_head1.markdown(f"**{type_icon} {item_name}** <span style='color:gray'>#{item_no}</span>", unsafe_allow_html=True)
                            c_head2.caption(f"发布人: {publisher}")
                            c_head3.caption(f"状态: {status_tag}")
                            
                            # 详细信息行
                            c_det1, c_det2, c_det3 = st.columns(3)
                            c_det1.write(f"📍 **地点**: {it.get('location', '')}")
                            c_det2.write(f"🕒 **时间**: {it.get('time', '')}")
                            c_det3.write(f"🏷️ **类别**: {it.get('category', '')}")
                            
                            # 图片和描述
                            c_img, c_info = st.columns([1, 2])
                            with c_img:
                                render_item_image_dict(it, width=150)
                            with c_info:
                                st.caption(f"📝 **描述**: {it.get('description', '')[:100]}..." if len(it.get('description', '')) > 100 else f"📝 **描述**: {it.get('description', '')}")
                                st.caption(f"🤖 **AI识别**: {it.get('ai_result', 'N/A')}")
                                if it.get('remarks'):
                                    st.caption(f"💬 **备注**: {it.get('remarks')}")
                            
                            # 操作按钮
                            st.divider()
                            c_act1, c_act2, c_act3, c_act4, c_act5 = st.columns(5)
                            
                            # 编辑按钮
                            if c_act1.button("✏️ 编辑", key=f"adm_edit_btn_{item_id}"):
                                st.session_state.admin_editing_id = item_id
                                st.rerun()
                                
                            # 切换状态
                            new_status = 0 if status == 1 else 1
                            status_label = "标记未解决" if status == 1 else "标记已解决"
                            if c_act2.button(status_label, key=f"adm_status_{item_id}"):
                                conn = sqlite3.connect(DB_PATH)
                                c = conn.cursor()
                                c.execute("UPDATE items SET status=? WHERE id=?", (new_status, item_id))
                                conn.commit()
                                conn.close()
                                st.rerun()
                                
                            # 切换隐藏
                            new_hidden = 0 if is_hidden == 1 else 1
                            hidden_label = "取消隐藏" if is_hidden == 1 else "隐藏物品"
                            if c_act3.button(hidden_label, key=f"adm_hide_{item_id}"):
                                conn = sqlite3.connect(DB_PATH)
                                c = conn.cursor()
                                c.execute("UPDATE items SET is_hidden=? WHERE id=?", (new_hidden, item_id))
                                conn.commit()
                                conn.close()
                                st.rerun()
                                
                            # 删除
                            if c_act4.button("🗑️ 删除", key=f"adm_del_{item_id}"):
                                conn = sqlite3.connect(DB_PATH)
                                c = conn.cursor()
                                c.execute("DELETE FROM items WHERE id=?", (item_id,))
                                conn.commit()
                                conn.close()
                                st.rerun()
                                
                            # 查看原始数据 (调试用)
                            if c_act5.button("📄 原始数据", key=f"adm_raw_{item_id}"):
                                with st.expander("查看原始JSON数据"):
                                    display_data = {k:v for k,v in it.items() if k not in ['image', 'images']}
                                    st.json(display_data)

                    except Exception as e:
                        st.error(f"渲染物品 ID {it.get('id')} 时出错: {e}")

        with t2:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT username,user_no,role,nickname,is_active FROM users")
            us = c.fetchall()
            conn.close()

            st.subheader("用户列表")
            if not us:
                st.info("暂无用户数据")
            else:
                for u in us:
                    un, uno, r, nk, act = u
                    
                    # 【修复】如果昵称为空，则使用用户名作为显示名称
                    display_nick = nk if nk else un

                    # 【新增】获取该用户的警告记录
                    conn_warn = sqlite3.connect(DB_PATH)
                    conn_warn.row_factory = sqlite3.Row
                    c_warn = conn_warn.cursor()
                    c_warn.execute("SELECT * FROM user_warnings WHERE username=? ORDER BY create_time DESC LIMIT 5", (un,))
                    warnings = [dict(row) for row in c_warn.fetchall()]
                    conn_warn.close()
                    
                    # 【修改】检查该用户的警告是否被管理员手动忽略
                    is_warning_ignored = un in st.session_state.admin_ignored_warnings
                    
                    warning_badge = ""
                    has_active_warning = False
                    if warnings and not is_warning_ignored:
                        has_active_warning = True
                        warning_count = len(warnings)
                        latest_reason = warnings[0]['reason']
                        warning_badge = f"<span style='color:red; font-weight:bold;'>⚠️ 警告({warning_count}): {latest_reason}</span>"

                    with st.expander(f"{un} ({uno}) - {'活跃' if act else '禁用'}"):
                        # 【修复】使用 display_nick 替代原始的 nk
                        st.markdown(f"角色：{r} | 昵称：{display_nick} {warning_badge}", unsafe_allow_html=True)
                        
                        # 【修改】显示详细警告历史及操作按钮
                        if warnings and not is_warning_ignored:
                            with st.container(border=True):
                                st.caption("**最近警告记录：**")
                                for w in warnings:
                                    st.caption(f"- [{w['create_time']}] {w['reason']} (消息: {w['trigger_message'][:20]}...)")
                                
                                st.divider()
                                col_warn_act1, col_warn_act2 = st.columns(2)
                                
                                # 按钮1：忽略/关闭警告（从当前视图移除）
                                if col_warn_act1.button("❌ 忽略此警告", key=f"ignore_warn_{un}"):
                                    st.session_state.admin_ignored_warnings.add(un)
                                    st.rerun()
                                
                                # 按钮2：保留/标记已阅（此处仅做提示，实际“保留”即为默认状态，也可用于重置忽略状态如果之前被误触）
                                # 为了满足“可以选择保留...或者叉掉”，这里提供一个“确认保留”的视觉反馈，或者仅仅是说明。
                                # 更实用的做法是：如果之前被忽略了，提供“恢复显示”按钮。但需求是“保留或叉掉”。
                                # 我们可以在这里加一个“标记为已处理”并隐藏，或者仅仅提供“忽略”。
                                # 鉴于Streamlit的特性，最简单的“保留”就是什么都不做。
                                # 为了交互完整性，我们可以做一个“标记为已处理”并隐藏，或者仅仅提供“忽略”。
                                # 下面提供一个“恢复显示”按钮，以防误触忽略，体现“保留”的反向操作，或者仅作为提示。
                                
                                if col_warn_act2.button("✅ 我已阅/保留", key=f"ack_warn_{un}"):
                                    # 这里可以选择不做任何事，或者弹出成功提示
                                    st.toast(f"已确认保留用户 {un} 的警告记录")
                                    # 注意：不调用 rerun，保持显示，体现“保留”

                        # 【新增】如果警告被忽略，显示一个小的提示，允许恢复
                        elif warnings and is_warning_ignored:
                             if st.button("↩️ 恢复显示警告", key=f"restore_warn_{un}"):
                                if un in st.session_state.admin_ignored_warnings:
                                    st.session_state.admin_ignored_warnings.discard(un)
                                st.rerun()

                        c1,c2,c3,c4 = st.columns(4)
                        newact = 0 if act else 1
                        if c1.button("禁用/启用", key=f"admin_toggle_user_{un}"):
                            conn = sqlite3.connect(DB_PATH)
                            c = conn.cursor()
                            c.execute("UPDATE users SET is_active=? WHERE username=?", (newact,un))
                            conn.commit()
                            conn.close()
                            st.rerun()
                        if un!="admin" and c2.button("删除用户", key=f"admin_del_user_{un}"):
                            conn = sqlite3.connect(DB_PATH)
                            c = conn.cursor()
                            c.execute("DELETE FROM users WHERE username=?", (un,))
                            conn.commit()
                            conn.close()
                            st.rerun()
                        # 新增：重置密码功能
                        if c3.button("🔄 重置密码", key=f"admin_reset_pwd_{un}"):
                            # 重置为默认密码，这里使用 '123456'，实际项目中可配置
                            default_pwd = "123456"
                            conn = sqlite3.connect(DB_PATH)
                            c = conn.cursor()
                            c.execute("UPDATE users SET password=? WHERE username=?", (hash_password(default_pwd), un))
                            conn.commit()
                            conn.close()
                            st.success(f"用户 [{un}] 的密码已重置为: {default_pwd}")
                            st.rerun()
                        # 新增：私聊功能
                        if c4.button("💬 私聊", key=f"admin_chat_user_{un}"):
                            st.session_state.chat_with = {
                                "item_id": 0,  # 0 表示非物品相关的通用私聊或系统消息
                                "target_user": un,
                                "item_name": f"与 {un} 的对话"
                            }
                            st.rerun()

    # --- 一键导入 ---
    elif choice == "📊 一键导入数据" and role == "admin":
        st.markdown("<div class='title-card'><h1>📊 批量导入数据</h1></div>", unsafe_allow_html=True)
        st.warning("注意：导入的数据将默认标记为'丢失'，发布人为'admin'，且状态为'寻找中'。")
        st.info("💡 提示：若需导入图片，请在Excel中增加一列‘图片路径’。")
        st.caption("⚠️ **公网部署重要提示**：Excel中的图片路径必须是**服务器上的绝对路径**（例如 `/app/data/images/photo.jpg`）。本地路径（如 C:\\...）在服务器上无效。建议先将图片上传至服务器固定目录。")
        
        f = st.file_uploader("选择Excel文件 (.xlsx)", type=["xlsx"], key="admin_import_file")
        
        if f:
            # 预览数据列
            try:
                df_preview = pd.read_excel(f, nrows=5)
                st.dataframe(df_preview)
            except Exception as e:
                st.error(f"预览失败: {e}")

            # 【新增】确认导入按钮
            if st.button("开始导入", key="confirm_import"):
                try:
                    # 重新读取文件，因为之前的预览可能消耗了文件指针
                    f.seek(0) 
                    df = pd.read_excel(f)
                    
                    # 检查必要的列
                    required_cols = ['名称', '地点'] # 假设Excel中有这些列，根据实际需求调整
                    # 这里需要根据实际的Excel列名进行映射，假设用户知道列名对应关系
                    # 为简化，这里假设列名与数据库字段有一定对应，或通过位置获取
                    
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    
                    success_count = 0
                    err_count = 0
                    img_cnt = 0
                    
                    # 获取图片列名，假设用户指定或固定为 '图片路径'
                    img_col_name = '图片路径' 
                    has_img_col = img_col_name in df.columns
                    
                    for index, row in df.iterrows():
                        try:
                            # 获取基本信息，根据实际Excel列名调整 key
                            # 假设 Excel 列名为: 名称, 类别, 时间, 地点, 描述, 联系方式, 备注, 图片路径
                            name = str(row.get('名称', '')).strip()
                            loc = str(row.get('地点', '')).strip()
                            
                            if not name or not loc: 
                                err_count += 1
                                continue
                                
                            c.execute("SELECT MAX(CAST(SUBSTR(item_no,6) AS INTEGER)) FROM items WHERE item_no LIKE 'ITEM-%'")
                            mx = c.fetchone()[0]
                            next_no = (mx + 1) if mx else 1
                            ino = f"ITEM-{next_no:04d}"
                            
                            # 处理图片
                            image_json = None
                            ai_res = "未识别"
                            
                            if has_img_col:
                                img_path = row.get(img_col_name)
                                if pd.notna(img_path) and str(img_path).strip():
                                    path_str = str(img_path).strip()
                                    try:
                                        # 【修改】严格处理路径
                                        full_path = None
                                        if os.path.isabs(path_str):
                                            # 绝对路径，直接使用
                                            full_path = path_str
                                        else:
                                            # 相对路径，结合 BASE_DIR
                                            full_path = os.path.join(BASE_DIR, path_str)
                                        
                                        # 规范化路径（处理 Windows/Linux 分隔符差异）
                                        full_path = os.path.normpath(full_path)

                                        if os.path.exists(full_path):
                                            with Image.open(full_path) as img:
                                                b64 = img_to_base64(img)
                                                if b64:
                                                    image_json = json.dumps([b64])
                                                    # 可选：调用AI识别，但批量导入时建议跳过以加快速度
                                                    # ai_res = ai_recognize(full_path) 
                                                    ai_res = "已导入图片"
                                                    img_cnt += 1
                                        else:
                                            # 记录警告但不中断
                                            print(f"警告: 图片文件不存在: {full_path}")
                                    except Exception as e:
                                        print(f"处理图片出错 {path_str}: {e}")
                            
                            # 获取其他字段，设置默认值
                            cate = str(row.get('类别', '其他')).strip()
                            # 时间处理
                            t_val = row.get('时间')
                            if pd.notna(t_val):
                                time_str = str(t_val)
                            else:
                                time_str = str(datetime.datetime.now().date())
                                
                            desc = str(row.get('描述', '')).strip()
                            contact = str(row.get('联系方式', '')).strip()
                            remarks = str(row.get('备注', '')).strip()
                            
                            # 插入数据库
                            c.execute('''INSERT INTO items (item_no,type,name,category,time,location,description,image,ai_result,publisher,contact,create_time,status,is_hidden,remarks)
                                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                                      (ino, "lost", name, cate, time_str, loc, desc, image_json, ai_res, "admin", contact, str(datetime.datetime.now()), 0, 0, remarks))
                            success_count += 1
                            
                        except Exception as e:
                            err_count += 1
                            print(f"导入行 {index} 出错: {e}")
                            continue
                    
                    conn.commit()
                    conn.close()
                    st.success(f"导入完成！成功: {success_count}, 失败/跳过: {err_count}, 图片: {img_cnt}")
                    
                except Exception as e:
                    st.error(f"导入过程发生严重错误: {e}")

# ===================== 新增/修改辅助函数 =====================

def render_home_item_card(item, column_container, key_prefix):
    """
    在首页列容器中渲染单个物品卡片
    """
    item_id = item.get('id')
    item_name = item.get('name', '未知物品')
    item_type = item.get('type')
    location = item.get('location', '')
    time_val = item.get('time', '')
    description = item.get('description', '')
    publisher = item.get('publisher', '')
    
    # 根据类型确定颜色样式类
    css_class = "lost-panel" if item_type == "lost" else "found-panel"
    
    with column_container:
        # 使用 HTML 容器模拟卡片效果
        st.markdown(f"""
        <div class="{css_class}" style="margin-bottom: 15px;">
            <h4 style="margin: 0 0 5px 0;">{item_name}</h4>
            <p style="margin: 0; font-size: 0.9em; color: #666;">
                📍 {location} | 🕒 {time_val}
            </p>
        </div>
        """, unsafe_allow_html=True)
        
        # 显示图片
        render_item_image_dict(item, width=200)
        
        # 显示简要描述
        if description:
            st.caption(f"📝 {description[:50]}..." if len(description) > 50 else f"📝 {description}")
            
        # 操作按钮
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("💬 联系", key=f"{key_prefix}_contact_{item_id}"):
                st.session_state.chat_with = {
                    "item_id": item_id,
                    "target_user": publisher,
                    "item_name": item_name
                }
                st.rerun()
        with col_btn2:
            # 如果是自己发布的，可以显示查看详情或编辑（可选，首页通常只展示）
            pass

def render_item_image_dict(item_dict, caption="物品图片", width=300):
    """
    适配字典类型的物品数据渲染图片
    优化：多图片时使用网格布局，单图片时居中显示
    """
    img_displayed = False
    
    # 统一从 image 列获取，兼容 JSON 列表格式
    image_str = item_dict.get('image')
    
    # 如果 image 为空，尝试 images 列（旧数据兼容）
    if not image_str:
        image_str = item_dict.get('images')

    if image_str:
        try:
            img_list = []
            if isinstance(image_str, str):
                # 尝试解析 JSON 数组
                if image_str.strip().startswith('['):
                    img_list = json.loads(image_str)
                else:
                    # 单个 base64 字符串
                    img_list = [image_str]
            elif isinstance(image_str, list):
                img_list = image_str
            
            if img_list:
                # 过滤无效图片
                valid_imgs = []
                for b64 in img_list:
                    if not b64: continue
                    img = base64_to_img(b64)
                    if img:
                        valid_imgs.append(img)
                
                if valid_imgs:
                    img_count = len(valid_imgs)
                    img_displayed = True
                    
                    # 优化布局：多张图片使用网格/分列显示
                    if img_count > 1:
                        # 每行最多显示3张图片
                        cols_per_row = 3
                        num_rows = (img_count + cols_per_row - 1) // cols_per_row
                        
                        for row_idx in range(num_rows):
                            # 计算当前行的图片范围
                            start_idx = row_idx * cols_per_row
                            end_idx = min(start_idx + cols_per_row, img_count)
                            current_row_imgs = valid_imgs[start_idx:end_idx]
                            
                            # 创建列
                            cols = st.columns(len(current_row_imgs))
                            for col_idx, img in enumerate(current_row_imgs):
                                with cols[col_idx]:
                                    st.image(img, use_container_width=True, caption=f"{caption} {start_idx + col_idx + 1}")
                    else:
                        # 单张图片：居中显示，限制最大宽度
                        col_center = st.columns([1, 2, 1]) # 利用列布局实现居中效果
                        with col_center[1]:
                            st.image(valid_imgs[0], use_container_width=True, caption=caption)
                        
        except Exception as e:
            # st.warning(f"图片解析调试信息: {e}")
            pass
    
    if not img_displayed:
        st.caption("📷 暂无图片")
        
    return img_displayed

def get_ai_matches_dict(user_items, all_items):
    """
    适配字典列表的AI匹配逻辑
    【重构】优化匹配算法以减少遗漏，提高召回率，解决误配问题
    策略：
    1. 硬性过滤：类型必须相反，ID不同。
    2. 类别强约束与映射：引入类别同义词映射，解决分类不一致问题。
    3. 名称核心匹配：清洗噪声，提取核心词，短名称精确/包含匹配，长名称语义相似度。
    4. AI关键词加权：清洗停用词，计算Jaccard相似度和共现加分。
    5. 地点层级匹配：识别关键地点关键词，允许模糊地点匹配。
    6. 动态阈值：根据名称和类别的匹配强度动态调整通过分数线。
    """
    matches = []
    
    # 【新增】AI关键词停用词表，避免通用词导致误配
    AI_STOP_WORDS = {
        "其他", "物体", "室内", "室外", "近景", "特写", "图片", "照片", 
        "白色", "黑色", "红色", "蓝色", "绿色", "黄色", "灰色", "棕色", "紫色", "粉色",
        "桌面", "地面", "背景", "无人", "有人", "手", "部分", "镜头", "视角",
        "一个", "一只", "一把", "一本", "一部"
    }

    # 【新增】类别同义词/映射表，用于缓解用户选择类别不一致的问题
    CATEGORY_MAP = {
        "校园卡": ["卡", "证件", "卡证", "学生卡", "饭卡"],
        "身份证": ["卡", "证件", "卡证", "身份证"],
        "学生证": ["卡", "证件", "卡证", "学生证"],
        "钥匙": ["钥匙", "门禁卡"],
        "耳机": ["耳机", "电子产品"],
        "手机": ["手机", "电子产品"],
        "电脑": ["电脑", "笔记本", "电子产品"],
        "水杯": ["水杯", "杯子", "生活用品"],
        "雨伞": ["雨伞", "伞", "生活用品"],
        "书籍": ["书", "书籍", "教材", "学习资料"],
        "钱包": ["钱包", "卡包", "财物"],
        "书包": ["书包", "背包", "箱包"],
        "眼镜": ["眼镜", "配饰"]
    }
    
    def normalize_text(text):
        """清洗文本：去除标点、空格、特殊符号，转小写"""
        if not text:
            return ""
        import re
        # 去除常见标点和特殊符号
        text = re.sub(r'[^\w\s]', '', text)
        # 去除多余空格
        text = re.sub(r'\s+', '', text)
        return text.lower()

    # 由于原代码未导入jieba，我们使用简单的split模拟分词，或者依赖difflib
    # 为了保持代码独立性且不引入新依赖，我们优化现有的基于字符和子串的逻辑
    
    def get_category_set(cat_name):
        """获取类别及其同义词集合"""
        if not cat_name:
            return set()
        base_set = {cat_name}
        # 查找映射
        for key, vals in CATEGORY_MAP.items():
            if cat_name in vals or cat_name == key:
                base_set.update(vals)
                base_set.add(key)
        return base_set

    # 构建有效物品字典 {id: item}
    valid = {}
    for item in all_items:
        iid = item.get('id')
        if not iid: continue
        if item.get('is_hidden'): continue
        if item.get('status') == 1: continue
        valid[iid] = item

    for my in user_items:
        iid = my.get('id')
        itype = my.get('type')
        iname_raw = my.get('name', '') or ""
        icategory = my.get('category', '') or ""
        ilocation = my.get('location', '') or ""
        idesc = my.get('description', '') or ""
        itime = my.get('time', '') or ""
        
        if my.get('is_hidden'): continue
        if my.get('status') == 1: continue
        
        # 确定匹配目标类型：丢失配捡到，捡到配丢失
        oppo = "found" if itype == "lost" else "lost"
        
        # 预处理我的AI关键词
        my_ai_str = my.get('ai_result', '') or ""
        invalid_ai_tags = {"未识别", "无图片", "识别失败", "AI服务未配置", "未上传"}
        my_keywords = set()
        if my_ai_str and my_ai_str not in invalid_ai_tags:
            import re
            normalized_str = re.sub(r'[、,，\s]', ' ', my_ai_str)
            # 过滤停用词和单字
            my_keywords = set(k.strip() for k in normalized_str.split() if k.strip() and len(k.strip()) > 1 and k.strip() not in AI_STOP_WORDS)
        
        # 预处理我的名称和类别
        iname_clean = normalize_text(iname_raw)
        my_cat_set = get_category_set(icategory)
        
        cand = []
        for mid, m in valid.items():
            if mid == iid: continue
            if m.get('type') != oppo: continue
            
            mname_raw = m.get('name', '') or ""
            mcategory = m.get('category', '') or ""
            mlocation = m.get('location', '') or ""
            mdesc = m.get('description', '') or ""
            m_ai_str = m.get('ai_result', '') or ""
            mtime = m.get('time', '') or ""
            
            mname_clean = normalize_text(mname_raw)
            m_cat_set = get_category_set(mcategory)

            m_keywords = set()
            if m_ai_str and m_ai_str not in invalid_ai_tags:
                import re
                normalized_m_str = re.sub(r'[、,，\s]', ' ', m_ai_str)
                m_keywords = set(k.strip() for k in normalized_m_str.split() if k.strip() and len(k.strip()) > 1 and k.strip() not in AI_STOP_WORDS)
            
            # --- 评分计算 (Scoring) ---
            
            # 1. 名称相似度 (权重 45) - 核心判断依据
            name_score = 0
            is_name_exact_match = False
            is_name_contain = False
            
            if iname_clean and mname_clean:
                # 精确匹配
                if iname_clean == mname_clean:
                    name_score = 45
                    is_name_exact_match = True
                # 包含匹配 (针对短名称特别重要)
                elif iname_clean in mname_clean or mname_clean in iname_clean:
                    short_len = min(len(iname_clean), len(mname_clean))
                    long_len = max(len(iname_clean), len(mname_clean))
                    # 如果短词长度至少为2，且包含关系成立
                    if short_len >= 2:
                        # 根据重叠比例给分，越接近完全包含分数越高
                        ratio = short_len / long_len
                        name_score = 35 + (ratio * 5) 
                        is_name_contain = True
                        if ratio > 0.8:
                            is_name_exact_match = True
                else:
                    # 模糊匹配 (SequenceMatcher)
                    ratio = difflib.SequenceMatcher(None, iname_clean, mname_clean).ratio()
                    if ratio > 0.8:
                        name_score = 30
                    elif ratio > 0.6:
                        name_score = 20
                    elif ratio > 0.4:
                        name_score = 10
            
            # 2. 类别匹配 (权重 20) - 强约束但带容错
            category_score = 0
            if my_cat_set and m_cat_set:
                intersection = my_cat_set & m_cat_set
                if intersection:
                    category_score = 20
                else:
                    # 如果类别完全不同，且名称不是高度相似，则大幅惩罚
                    if not is_name_exact_match and not is_name_contain:
                        category_score = -15 # 强惩罚
                    else:
                        category_score = 5 # 名称相似时，类别不同可能是用户选错，轻微惩罚或中性
            
            # 3. 地点相似度 (权重 15)
            loc_score = 0
            if ilocation and mlocation:
                l1 = ilocation.strip()
                l2 = mlocation.strip()
                if l1 == l2:
                    loc_score = 15
                elif l1 in l2 or l2 in l1:
                    loc_score = 12
                else:
                    # 检查是否包含共同的关键地点词 (如 "图书馆", "食堂", "教学楼")
                    key_locs = ["图书馆", "食堂", "教学楼", "宿舍", "操场", "体育馆", "教室", "实验室", "办公室"]
                    common_loc = False
                    for kl in key_locs:
                        if kl in l1 and kl in l2:
                            common_loc = True
                            break
                    
                    if common_loc:
                        loc_score = 8 # 同一大类地点，给予中等分数
                    else:
                        ratio = difflib.SequenceMatcher(None, l1, l2).ratio()
                        if ratio > 0.6:
                            loc_score = ratio * 10
            
            # 4. AI 关键词重合度 (权重 15)
            ai_score = 0
            if my_keywords and m_keywords:
                intersection = my_keywords & m_keywords
                union = my_keywords | m_keywords
                if union:
                    jaccard = len(intersection) / len(union)
                    ai_score = jaccard * 15
                
                # 如果有共同关键词，额外加分，特别是当共同关键词数量较多时
                if len(intersection) >= 2:
                    ai_score += 5 
                elif len(intersection) == 1:
                    ai_score += 2
            elif not my_keywords and not m_keywords:
                # 都没有AI识别结果，不加分也不减分
                pass
            else:
                # 一方有一方没有，略微减分，因为信息不对称
                ai_score = -2

            # 5. 描述相似度 (权重 5) - 辅助项
            desc_score = 0
            if idesc and mdesc:
                # 描述通常较长且杂乱，仅做简单相似度参考
                ratio = difflib.SequenceMatcher(None, idesc, mdesc).ratio()
                if ratio > 0.3:
                    desc_score = ratio * 5

            total_score = name_score + category_score + loc_score + ai_score + desc_score
            
            # --- 阈值判定 ---
            # 基础阈值
            threshold = 35
            
            # 如果名称精确匹配或高度包含，阈值降低，确保如“校园卡”能被匹配
            if is_name_exact_match:
                threshold = 25
            elif is_name_contain:
                threshold = 30
            
            # 如果类别不同且名称不精确，必须高分才能通过
            if icategory and mcategory and icategory != mcategory and not is_name_exact_match and not is_name_contain:
                threshold = 55

            # 如果总分高于阈值，加入候选
            if total_score >= threshold: 
                cand.append((total_score, m))
        
        # 按分数降序排列
        cand.sort(key=lambda x: x[0], reverse=True)
        # 返回所有符合阈值的匹配项
        matches.append((my, [x[1] for x in cand]))
    
    return matches

def export_to_excel_dict(items):
    """
    适配字典列表的Excel导出
    注意：此函数仅返回二进制数据，不包含任何 streamlit UI 调用
    """
    try:
        if not items:
            return None
            
        rows = []
        for it in items:
            rows.append({
                "ID": it.get('id'), 
                "物品编号": it.get('item_no'), 
                "类型": "丢失" if it.get('type')=="lost" else "捡到",
                "物品名称": it.get('name'), 
                "类别": it.get('category'), 
                "时间": it.get('time'), 
                "地点": it.get('location'),
                "描述": it.get('description'), 
                "AI识别": it.get('ai_result'), 
                "发布人": it.get('publisher'), 
                "联系方式": it.get('contact'),
                "发布时间": it.get('create_time'), 
                "状态": "已解决" if it.get('status')==1 else "寻找中", 
                "备注": it.get('remarks')
            })
        df = pd.DataFrame(rows)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        return buffer.getvalue()
    except Exception as e:
        # 不在这里调用 st.error，由调用者处理
        raise e

# ===================== 入口 =====================
set_style()
init_db()
if "user" not in st.session_state:
    login_page()
else:
    main_page()