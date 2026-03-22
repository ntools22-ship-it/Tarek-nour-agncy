#!/usr/bin/env python3
"""
🎬 وكالة طارق نور — ملف واحد كامل
Single-file deployment — zero import errors.
Set env vars: BOT_TOKEN (required), GROQ_API_KEY (optional but recommended)
"""
import os, sys, asyncio, re, logging, tempfile, subprocess, math
import urllib.request, urllib.parse, urllib.error, json, random, time, shutil
from typing import Optional, Callable

# ═══════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
log = logging.getLogger("agency")

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════
BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = "llama-3.3-70b-versatile"
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
FONT_PATH    = "/tmp/cairo.ttf"
FONT_URL     = "https://github.com/google/fonts/raw/main/ofl/cairo/Cairo%5Bslnt%2Cwght%5D.ttf"

VOICES = {
    "female": {"name": "سلمى",  "gender": "👩", "desc": "أنثى · ناعمة",  "gtts_slow": False, "edge": "ar-EG-SalmaNeural"},
    "male":   {"name": "شاكر", "gender": "👨", "desc": "ذكر · واضح",    "gtts_slow": False, "edge": "ar-EG-ShakirNeural"},
    "slow":   {"name": "واضح", "gender": "🐢", "desc": "بطيء · صافي",   "gtts_slow": True,  "edge": "ar-EG-SalmaNeural"},
}
STYLES = {
    "gold":  {"name": "✨ ذهبي",  "bg": (7,7,14),   "accent": (201,168,76),  "text": (240,232,213), "wave": "c9a84c"},
    "night": {"name": "🌙 ليلي", "bg": (5,10,28),   "accent": (100,149,255), "text": (220,228,255), "wave": "6495ff"},
    "radio": {"name": "📻 راديو","bg": (3,12,3),    "accent": (61,186,78),   "text": (232,245,232), "wave": "3dba4e"},
    "news":  {"name": "📰 أخبار","bg": (10,5,5),    "accent": (220,60,60),   "text": (255,248,248), "wave": "dc3c3c"},
}

# ═══════════════════════════════════════════════════════════════
#  SESSION
# ═══════════════════════════════════════════════════════════════
_sessions: dict = {}
_brain_history: dict = {}

def get_session(uid: int) -> dict:
    if uid not in _sessions:
        _sessions[uid] = {"mode": "audio", "voice": "female", "style": "gold"}
    return _sessions[uid]

def get_history(uid: int) -> list:
    return _brain_history.get(uid, [])

def add_history(uid: int, role: str, content: str):
    if uid not in _brain_history:
        _brain_history[uid] = []
    _brain_history[uid].append({"role": role, "content": content})
    if len(_brain_history[uid]) > 16:
        _brain_history[uid] = _brain_history[uid][-16:]

def clear_history(uid: int):
    _brain_history[uid] = []

# ═══════════════════════════════════════════════════════════════
#  FONT
# ═══════════════════════════════════════════════════════════════
_font_cache: dict = {}

def ensure_font():
    if os.path.exists(FONT_PATH) and os.path.getsize(FONT_PATH) > 10000:
        return FONT_PATH
    try:
        log.info("Downloading Cairo font…")
        urllib.request.urlretrieve(FONT_URL, FONT_PATH)
        log.info("Font ready ✓")
        return FONT_PATH
    except Exception as e:
        log.warning(f"Font download failed: {e}")
        return None

def get_font(size: int):
    from PIL import ImageFont
    if size not in _font_cache:
        if os.path.exists(FONT_PATH) and os.path.getsize(FONT_PATH) > 10000:
            try:
                _font_cache[size] = ImageFont.truetype(FONT_PATH, size)
                return _font_cache[size]
            except Exception:
                pass
        _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]

# ═══════════════════════════════════════════════════════════════
#  TEXT UTILS
# ═══════════════════════════════════════════════════════════════
def split_arabic(text: str, max_chars: int = 3000) -> list:
    text = text.strip()
    if not text: return []
    if len(text) <= max_chars: return [text]
    parts = re.split(r"(?<=[.،؟!؛\n])\s+", text)
    parts = [p.strip() for p in parts if p.strip()]
    chunks, cur = [], ""
    for part in parts:
        if len(cur) + len(part) + 2 <= max_chars:
            cur += ("  " if cur else "") + part
        else:
            if cur: chunks.append(cur)
            if len(part) > max_chars:
                for i in range(0, len(part), max_chars):
                    chunks.append(part[i:i+max_chars])
                cur = ""
            else:
                cur = part
    if cur: chunks.append(cur)
    return chunks or [text]

def wrap_pil(draw, text, fnt, max_w):
    from PIL import ImageFont
    words = text.split()
    lines, cur = [], []
    for word in words:
        test = " ".join(cur + [word])
        try:
            bb = draw.textbbox((0,0), test, font=fnt, direction="rtl")
            w = bb[2] - bb[0]
        except Exception:
            w = len(test) * 20
        if w <= max_w: cur.append(word)
        else:
            if cur: lines.append(" ".join(cur))
            cur = [word]
    if cur: lines.append(" ".join(cur))
    return lines

def truncate(text: str, n: int = 60) -> str:
    return text[:n] + "…" if len(text) > n else text

def parse_pipe(text: str) -> tuple:
    if "|" in text:
        a, b = text.split("|", 1)
        return a.strip(), b.strip()
    return "", text.strip()

def fmt_dur(secs: float) -> str:
    secs = int(secs)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def fmt_size(path: str) -> str:
    mb = os.path.getsize(path) / (1024*1024)
    return f"{mb:.1f} MB"

def safe_del(*paths):
    for p in paths:
        if p and os.path.exists(p):
            try: os.remove(p)
            except: pass

def check_ffmpeg() -> bool:
    try:
        return subprocess.run(["ffmpeg","-version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False

# ═══════════════════════════════════════════════════════════════
#  TTS ENGINE  (gTTS primary — always works on cloud)
# ═══════════════════════════════════════════════════════════════
def _gtts_sync(text: str, slow: bool = False) -> str:
    from gtts import gTTS
    tmp = tempfile.mktemp(suffix=".mp3")
    gTTS(text=text, lang="ar", slow=slow).save(tmp)
    if not os.path.exists(tmp) or os.path.getsize(tmp) < 50:
        raise RuntimeError("gTTS: empty output")
    return tmp

async def _gtts_async(text: str, slow: bool = False) -> str:
    return await asyncio.get_event_loop().run_in_executor(None, _gtts_sync, text, slow)

def _merge_mp3s(paths: list) -> str:
    from pydub import AudioSegment
    if len(paths) == 1:
        out = tempfile.mktemp(suffix=".mp3")
        shutil.copy(paths[0], out)
        return out
    combined = AudioSegment.empty()
    pause = AudioSegment.silent(duration=400)
    for i, p in enumerate(paths):
        combined += AudioSegment.from_mp3(p)
        if i < len(paths)-1: combined += pause
    combined = combined.normalize()
    out = tempfile.mktemp(suffix=".mp3")
    combined.export(out, format="mp3", bitrate="128k")
    return out

async def tts(text: str, voice_key: str = "female", progress_cb=None) -> str:
    slow = VOICES.get(voice_key, VOICES["female"])["gtts_slow"]
    segs = split_arabic(text)
    total = len(segs)
    parts = []
    try:
        for i, seg in enumerate(segs):
            if progress_cb and total > 1:
                await progress_cb(i+1, total)
            parts.append(await _gtts_async(seg, slow))
            if i < total-1:
                await asyncio.sleep(0.3)
        return _merge_mp3s(parts)
    finally:
        for p in parts:
            if os.path.exists(p): safe_del(p)

def audio_dur(path: str) -> float:
    from pydub import AudioSegment
    return len(AudioSegment.from_file(path)) / 1000.0

# ═══════════════════════════════════════════════════════════════
#  IMAGE ENGINE  (Pollinations.ai — free, no key)
# ═══════════════════════════════════════════════════════════════
def _dl_image(url: str, timeout: int = 60) -> str:
    out = tempfile.mktemp(suffix=".png")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TarekNourBot/2.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if len(data) < 1000: raise RuntimeError(f"Too small: {len(data)}B")
            with open(out, "wb") as f: f.write(data)
            return out
        except Exception as e:
            log.warning(f"Image attempt {attempt+1}: {e}")
            if attempt < 2: time.sleep(3*(attempt+1))
    raise RuntimeError("فشل توليد الصورة بعد 3 محاولات")

async def gen_image(prompt: str, w: int = 1920, h: int = 1080) -> str:
    seed = random.randint(1, 999999)
    full = f"{prompt}, professional photography, high quality, 8k, cinematic"
    enc  = urllib.parse.quote(full, safe="")
    url  = f"https://image.pollinations.ai/prompt/{enc}?width={w}&height={h}&nologo=true&enhance=true&seed={seed}"
    log.info(f"Image: {prompt[:50]}… seed={seed}")
    return await asyncio.get_event_loop().run_in_executor(None, _dl_image, url, 60)

async def gen_portrait(prompt: str) -> str:
    return await gen_image(prompt, 1080, 1920)

async def gen_square(prompt: str) -> str:
    return await gen_image(prompt, 1080, 1080)

# ═══════════════════════════════════════════════════════════════
#  GROQ BRAIN  (Egyptian AI persona + conversation memory)
# ═══════════════════════════════════════════════════════════════
EGYPT_SYSTEM = """أنت "نور" — المساعد الذكي لوكالة طارق نور الإعلامية المصرية.

شخصيتك:
- خبير إعلامي مصري محترف، عارف بالشارع المصري والعالم العربي
- أسلوبك ذكي وعملي، بتمزج بين الفصحى المبسطة والمصري الراقي
- متخصص في: الإعلام الرقمي، يوتيوب، بودكاست، سوشيال ميديا المصرية
- عندك حس فكاهي خفيف لكن احترافي
- بتقترح أفكار جريئة مناسبة للجمهور المصري والعربي

قواعدك:
- ردودك عملية ومفيدة دايماً
- لما حد يسألك عن فكرة، اعطيه 5-7 أفكار محددة
- ركز على المحتوى المصري: الثقافة، الاقتصاد، الفن، الرياضة، التكنولوجيا
- اقترح دايماً الخطوة التالية العملية"""

async def _groq_call(messages: list, system: str = EGYPT_SYSTEM,
                     max_tokens: int = 1500, temp: float = 0.8) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("no_key")
    payload = json.dumps({
        "model": GROQ_MODEL, "max_tokens": max_tokens, "temperature": temp,
        "messages": [{"role": "system", "content": system}] + messages,
    }).encode("utf-8")
    def _call():
        req = urllib.request.Request(GROQ_URL, data=payload,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Groq {e.code}: {e.read().decode()[:150]}")
    return await asyncio.get_event_loop().run_in_executor(None, _call)

async def brain_chat(uid: int, message: str) -> str:
    history = get_history(uid)
    msgs = history + [{"role": "user", "content": message}]
    try:
        reply = await _groq_call(msgs)
        add_history(uid, "user", message)
        add_history(uid, "assistant", reply)
        return reply
    except Exception as e:
        if "no_key" in str(e):
            return "⚠️ أضف GROQ_API_KEY في إعدادات Railway للنقاش الذكي.\nمجاني من: console.groq.com"
        log.warning(f"Groq chat: {e}")
        return f"⚠️ خطأ مؤقت: {str(e)[:100]}"

async def brain_task(prompt: str, max_tokens: int = 1500, temp: float = 0.8) -> str:
    try:
        return await _groq_call([{"role": "user", "content": prompt}],
                                system=EGYPT_SYSTEM, max_tokens=max_tokens, temp=temp)
    except Exception as e:
        if "no_key" in str(e):
            return "⚠️ أضف GROQ_API_KEY في Railway للحصول على هذه الميزة.\nmجاني: console.groq.com"
        return f"⚠️ خطأ: {str(e)[:150]}"

async def ai_translate_prompt(arabic: str) -> str:
    """Translate Arabic topic to English image prompt."""
    try:
        return await _groq_call(
            [{"role": "user", "content": f"Translate to English image generation prompt (output only the prompt):\n{arabic}"}],
            system="You create concise English image generation prompts.", max_tokens=100, temp=0.5)
    except Exception:
        return arabic

async def ai_write_content(topic: str, kind: str = "article") -> str:
    prompts = {
        "article": f"اكتب مقالاً متكاملاً عن: {topic}\nحوالي 600 كلمة، فقرات متدفقة، بدون عناوين.",
        "script":  f"اكتب سكريبت لفيديو/بودكاست عن: {topic}\nحوالي 5 دقائق، أسلوب محادثة مباشرة.",
        "post":    f"اكتب بوست احترافي لإنستغرام وفيسبوك عن: {topic}\nمع 5 هاشتاقات عربية.",
        "news":    f"اكتب نشرة إخبارية احترافية عن: {topic}\n3-4 جمل، أسلوب إخباري رسمي.",
    }
    prompt = prompts.get(kind, prompts["article"])
    result = await brain_task(prompt, max_tokens=2000)
    if result.startswith("⚠️"):
        fallbacks = {
            "article": f"في عالمنا المتغير، يبرز موضوع {topic} كأحد أهم القضايا التي تستحق الدراسة والتأمل. يرى المتخصصون أن فهم هذا الموضوع يفتح آفاقاً واسعة أمام المهتمين. وتشير التجارب العملية إلى أن الاهتمام بـ{topic} يُسهم في تطوير الرؤية وتوسيع المدارك. وفي النهاية، يبقى هذا الموضوع مجالاً خصباً يستحق المزيد من البحث والاهتمام.",
            "script":  f"أهلاً وسهلاً بكم مستمعينا الكرام. حلقتنا اليوم عن: {topic}. هذا الموضوع يشغل اهتمام كثيرين، ولأسباب وجيهة. سنستعرض معاً أبرز جوانبه بأسلوب بسيط وواضح. شكراً لمتابعتكم.",
            "post":    f"📌 {topic}\n\nموضوع مهم يستحق التأمل والنقاش.\nشاركنا رأيك في التعليقات 👇\n\n#طارق_نور #محتوى_عربي #مصر",
            "news":    f"وكالة طارق نور | {topic}\n\nتتابع الوكالة تطورات هذا الملف وستواصل تقديم التغطية الكاملة.",
        }
        return fallbacks.get(kind, f"محتوى عن: {topic}")
    return result

async def ai_brainstorm(topic: str) -> str:
    prompt = (f"عصف ذهني عن: {topic}\n\n7 أفكار محتوى مبتكرة للإعلام المصري الرقمي.\n"
              "كل فكرة: العنوان + جملة شرح + المنصة المناسبة.")
    return await brain_task(prompt, max_tokens=1200, temp=0.9)

async def ai_analyze(text: str) -> str:
    prompt = (f"كخبير إعلامي مصري، حلل:\n\n{text[:2000]}\n\n"
              "1. نقاط القوة\n2. نقاط الضعف\n3. اقتراحات التحسين\n4. تقييم/10\n5. أفضل منصة")
    return await brain_task(prompt, max_tokens=800)

async def ai_strategy(channel: str, goal: str) -> str:
    prompt = (f"استراتيجية محتوى شهرية لـ {channel} هدفها {goal}.\n"
              "4 محاور + جدول أسبوعي + أفكار لأول 4 إصدارات + نصائح للجمهور المصري.")
    return await brain_task(prompt, max_tokens=1800, temp=0.7)

async def ai_improve(text: str, goal: str = "عام") -> str:
    goals = {"يوتيوب": "أكثر جذباً ليوتيوب", "بودكاست": "مناسب للنطق والاستماع",
             "سوشيال": "قصير وجذاب مع هاشتاقات", "عام": "حسّن الأسلوب والوضوح"}
    prompt = f"حسّن هذا النص ({goals.get(goal, goals['عام'])}):\n\n{text[:3000]}\n\nالنص المحسّن فقط:"
    result = await brain_task(prompt, max_tokens=2000, temp=0.5)
    return result if not result.startswith("⚠️") else text

async def ai_titles(topic: str) -> str:
    prompt = (f"10 عناوين جذابة لمحتوى عن: {topic}\n"
              "متنوعة (أسئلة، أرقام، فضول، فائدة) مناسبة للجمهور المصري.")
    return await brain_task(prompt, max_tokens=600, temp=0.9)

async def ai_trending(niche: str = "مصر") -> str:
    prompt = (f"10 مواضيع تريند للمحتوى المصري في: {niche}\n"
              "كل موضوع مع السبب وأفضل فورمات المحتوى.")
    return await brain_task(prompt, max_tokens=1000, temp=0.9)

async def ai_to_posts(script: str) -> str:
    prompt = (f"حوّل هذا السكريبت لـ 3 بوستات:\n\n{script[:2000]}\n\n"
              "بوست 1: تويتر/X (280 حرف)\nبوست 2: فيسبوك/إنستغرام\nبوست 3: لينكدإن")
    return await brain_task(prompt, max_tokens=800)

# ═══════════════════════════════════════════════════════════════
#  VIDEO ENGINE
# ═══════════════════════════════════════════════════════════════
def build_frame(text: str, title: str = "", style_key: str = "gold",
                bg_img: str = None, W: int = 1920, H: int = 1080) -> str:
    from PIL import Image, ImageDraw
    st = STYLES.get(style_key, STYLES["gold"])
    bg, acc, txt = st["bg"], st["accent"], st["text"]

    if bg_img and os.path.exists(bg_img):
        img = Image.open(bg_img).convert("RGB").resize((W, H))
        from PIL import ImageFilter
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 155))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    else:
        img = Image.new("RGB", (W, H), bg)
        draw = ImageDraw.Draw(img)
        for y in range(H):
            t = y/H
            r = min(255, int(bg[0]+20*t)); g2 = min(255, int(bg[1]+12*t)); b = min(255, int(bg[2]+28*t))
            draw.line([(0,y),(W,y)], fill=(r,g2,b))

    draw = ImageDraw.Draw(img)
    draw.rectangle([int(W*.04), 46, int(W*.96), 51], fill=acc)
    draw.rectangle([int(W*.04), H-56, int(W*.96), H-51], fill=acc)
    draw.text((int(W*.04), 16), "وكالة طارق نور", font=get_font(26), fill=acc, direction="rtl")

    if title:
        f = get_font(52)
        try:
            bb = draw.textbbox((0,0), title, font=f, direction="rtl")
            tw = bb[2]-bb[0]; tx = max(int(W*.04), (W-tw)//2)
        except: tx = int(W*.04)
        draw.text((tx, 66), title, font=f, fill=acc, direction="rtl")

    f_body = get_font(42); max_w = int(W*.84)
    lines = wrap_pil(draw, text, f_body, max_w)
    start_y = 150 if title else 110
    for i, line in enumerate(lines[:6]):
        alpha = 0.45 if (i == len(lines[:6])-1 and len(lines) > 6) else 1.0
        col = tuple(int(c*alpha) for c in txt)
        try:
            bb = draw.textbbox((0,0), line, font=f_body, direction="rtl")
            lw = bb[2]-bb[0]; x = max(int(W*.04), (W-lw)//2)
        except: x = int(W*.04)
        draw.text((x, start_y+i*68), line, font=f_body, fill=col, direction="rtl")

    py = H-38
    draw.rectangle([int(W*.04), py, int(W*.96), py+5], fill=tuple(max(0,c-30) for c in acc))
    out = tempfile.mktemp(suffix=".png")
    img.save(out)
    return out

def build_news_frame(headline: str, body: str = "", W: int = 1920, H: int = 1080) -> str:
    from PIL import Image, ImageDraw
    st = STYLES["news"]
    bg, acc, txt = st["bg"], st["accent"], st["text"]
    img = Image.new("RGB", (W, H), bg); draw = ImageDraw.Draw(img)
    draw.rectangle([0, H-90, W, H], fill=acc)
    draw.rectangle([int(W*.04), 46, int(W*.96), 50], fill=acc)
    draw.text((int(W*.04), 16), "وكالة طارق نور", font=get_font(28), fill=acc, direction="rtl")
    draw.rectangle([int(W*.04), H-78, int(W*.04)+130, H-18], fill=(180,0,0))
    draw.text((int(W*.04)+8, H-72), "عاجل", font=get_font(34), fill=(255,255,255))
    draw.text((int(W*.04)+148, H-72), headline[:75], font=get_font(38), fill=(10,10,10), direction="rtl")
    if body:
        f = get_font(44); lines = wrap_pil(draw, body, f, int(W*.85))
        for i, line in enumerate(lines[:5]):
            try:
                bb = draw.textbbox((0,0), line, font=f, direction="rtl")
                lw = bb[2]-bb[0]; x = max(int(W*.04), (W-lw)//2)
            except: x = int(W*.04)
            draw.text((x, 100+i*72), line, font=f, fill=txt, direction="rtl")
    out = tempfile.mktemp(suffix=".png")
    img.save(out); return out

def build_story_frame(text: str, title: str = "", style_key: str = "gold",
                      bg_img: str = None) -> str:
    from PIL import Image, ImageDraw
    W, H = 1080, 1920
    st = STYLES.get(style_key, STYLES["gold"])
    bg, acc, txt = st["bg"], st["accent"], st["text"]
    if bg_img and os.path.exists(bg_img):
        img = Image.open(bg_img).convert("RGB").resize((W, H))
        overlay = Image.new("RGBA", (W, H), (0,0,0,165))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    else:
        img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle([int(W*.06), 58, int(W*.94), 63], fill=acc)
    draw.rectangle([int(W*.06), H-122, int(W*.94), H-117], fill=acc)
    draw.text((int(W*.06), 78), "وكالة طارق نور", font=get_font(36), fill=acc, direction="rtl")
    if title:
        f = get_font(56); lines = wrap_pil(draw, title, f, int(W*.85))
        for i, line in enumerate(lines[:2]):
            try:
                bb = draw.textbbox((0,0), line, font=f, direction="rtl")
                lw = bb[2]-bb[0]; x = (W-lw)//2
            except: x = int(W*.06)
            draw.text((x, 140+i*78), line, font=f, fill=acc, direction="rtl")
    f_body = get_font(44); lines = wrap_pil(draw, text, f_body, int(W*.84))
    for i, line in enumerate(lines[:10]):
        try:
            bb = draw.textbbox((0,0), line, font=f_body, direction="rtl")
            lw = bb[2]-bb[0]; x = (W-lw)//2
        except: x = int(W*.06)
        draw.text((x, 360+i*72), line, font=f_body, fill=txt, direction="rtl")
    out = tempfile.mktemp(suffix=".png")
    img.save(out); return out

def render_video(audio: str, frame: str, style_key: str = "gold", W=1920, H=1080) -> str:
    if not check_ffmpeg(): raise RuntimeError("FFmpeg غير مثبت")
    wave_col = STYLES.get(style_key, STYLES["gold"])["wave"]
    ww, wh = int(W*.9), 140; wx, wy = int(W*.05), H-200
    flt = (f"[1:a]showwaves=s={ww}x{wh}:mode=cline:rate=30:colors={wave_col}:scale=sqrt[w];"
           f"[0:v][w]overlay={wx}:{wy}:shortest=1[vo]")
    out = tempfile.mktemp(suffix=".mp4")
    cmd = ["ffmpeg","-y","-loglevel","error",
           "-loop","1","-framerate","30","-i",frame,
           "-i",audio,"-filter_complex",flt,
           "-map","[vo]","-map","1:a",
           "-c:v","libx264","-preset","fast","-crf","23",
           "-c:a","aac","-b:a","128k",
           "-pix_fmt","yuv420p","-movflags","+faststart","-shortest",out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0: raise RuntimeError(f"FFmpeg: {r.stderr[-300:]}")
    return out

def split_video(path: str) -> list:
    mb = os.path.getsize(path) / (1024*1024)
    if mb <= 49: return [path]
    probe = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=noprint_wrappers=1:nokey=1",path], capture_output=True, text=True)
    dur = float(probe.stdout.strip())
    n = math.ceil(mb/49); seg = dur/n; parts = []
    for i in range(n):
        out = tempfile.mktemp(suffix=f"_p{i+1}.mp4")
        subprocess.run(["ffmpeg","-y","-loglevel","error",
            "-ss",str(i*seg),"-t",str(seg),"-i",path,"-c","copy",out], check=True)
        parts.append(out)
    return parts

# ═══════════════════════════════════════════════════════════════
#  PRODUCERS
# ═══════════════════════════════════════════════════════════════
async def _action(ctx, cid, action):
    try: await ctx.bot.send_chat_action(cid, action)
    except: pass

async def do_audio(update, ctx, text: str):
    from telegram.constants import ChatAction, ParseMode
    cid = update.effective_chat.id
    s   = get_session(update.effective_user.id)
    v   = VOICES.get(s["voice"], VOICES["female"])
    st  = await update.message.reply_text(
        f"⏳ *تحويل الصوت…*\n📝 {len(text):,} حرف\n{v['gender']} {v['name']}",
        parse_mode=ParseMode.MARKDOWN)
    ap = None
    try:
        await _action(ctx, cid, ChatAction.RECORD_VOICE)
        segs = split_arabic(text); tot = len(segs)
        async def prog(cur, t):
            if tot > 1: await st.edit_text(f"⏳ {cur}/{t} مقطع")
        ap = await tts(text, s["voice"], progress_cb=prog)
        dur = audio_dur(ap)
        await st.edit_text(f"⬆️ رفع… ({fmt_dur(dur)})")
        await _action(ctx, cid, ChatAction.UPLOAD_VOICE)
        with open(ap,"rb") as f:
            await update.message.reply_audio(f, title=truncate(text,55),
                performer="وكالة طارق نور", duration=int(dur),
                caption=f"🎙️ *{v['name']}*  |  ⏱ {fmt_dur(dur)}  |  📝 {len(text):,} حرف",
                parse_mode=ParseMode.MARKDOWN)
        await st.delete()
    except Exception as e:
        log.exception("do_audio")
        await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:200]}`", parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ap)

async def do_image(update, ctx, prompt: str):
    from telegram.constants import ChatAction, ParseMode
    cid = update.effective_chat.id
    st = await update.message.reply_text(f"🎨 *توليد الصورة…*\n`{truncate(prompt,60)}`", parse_mode=ParseMode.MARKDOWN)
    ip = None
    try:
        await _action(ctx, cid, ChatAction.UPLOAD_PHOTO)
        en = await ai_translate_prompt(prompt)
        ip = await gen_image(en)
        with open(ip,"rb") as f:
            await update.message.reply_photo(f,
                caption=f"🖼️ *وكالة طارق نور*\n📌 {truncate(prompt,80)}", parse_mode=ParseMode.MARKDOWN)
        await st.delete()
    except Exception as e:
        log.exception("do_image")
        await st.edit_text(f"❌ *فشل توليد الصورة:*\n`{str(e)[:200]}`", parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ip)

async def do_video(update, ctx, raw: str):
    from telegram.constants import ChatAction, ParseMode
    cid = update.effective_chat.id
    s   = get_session(update.effective_user.id)
    title, content = parse_pipe(raw)
    if not content: content = raw
    st = await update.message.reply_text(
        f"🎬 *إنتاج الفيديو…*\n1️⃣ تحويل الصوت\n📝 {len(content):,} حرف", parse_mode=ParseMode.MARKDOWN)
    ap = ip = fp = vp = None
    try:
        await _action(ctx, cid, ChatAction.RECORD_VIDEO)
        ap = await tts(content, s["voice"])
        dur = audio_dur(ap)
        await st.edit_text(f"2️⃣ *توليد الخلفية AI…*\n⏱ {fmt_dur(dur)}", parse_mode=ParseMode.MARKDOWN)
        try:
            en = await ai_translate_prompt(title or content[:100])
            ip = await gen_image(en)
        except Exception as e:
            log.warning(f"BG image failed: {e}"); ip = None
        await st.edit_text("3️⃣ *رسم الإطار…*", parse_mode=ParseMode.MARKDOWN)
        fp = await asyncio.get_event_loop().run_in_executor(None, build_frame, content, title, s["style"], ip)
        await st.edit_text("4️⃣ *تجميع الفيديو…* قد يأخذ دقيقة", parse_mode=ParseMode.MARKDOWN)
        vp = await asyncio.get_event_loop().run_in_executor(None, render_video, ap, fp, s["style"])
        await st.edit_text(f"⬆️ رفع… ({fmt_size(vp)})")
        await _action(ctx, cid, ChatAction.UPLOAD_VIDEO)
        parts = split_video(vp)
        for i, part in enumerate(parts):
            cap = (f"🎬 *{title or 'وكالة طارق نور'}*" +
                   (f" — جزء {i+1}/{len(parts)}" if len(parts)>1 else "") +
                   f"\n⏱ {fmt_dur(dur)}  |  🎨 {STYLES[s['style']]['name']}")
            with open(part,"rb") as f:
                await update.message.reply_video(f, caption=cap, parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
            if part != vp: safe_del(part)
        await st.delete()
    except Exception as e:
        log.exception("do_video")
        await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:300]}`", parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ap, ip, fp, vp)

async def do_podcast(update, ctx, raw: str):
    from telegram.constants import ChatAction, ParseMode
    cid = update.effective_chat.id
    s   = get_session(update.effective_user.id)
    title, content = parse_pipe(raw)
    title = title or "حلقة جديدة"
    st = await update.message.reply_text(f"🎧 *إنتاج البودكاست…*\n📌 {title}", parse_mode=ParseMode.MARKDOWN)
    ap = None
    try:
        await _action(ctx, cid, ChatAction.RECORD_VOICE)
        full = f"أهلاً وسهلاً في وكالة طارق نور. حلقتنا اليوم: {title}.\n\n{content}\n\nشكراً لمتابعتكم وكالة طارق نور."
        ap = await tts(full, s["voice"])
        dur = audio_dur(ap)
        await st.edit_text(f"⬆️ رفع… ({fmt_dur(dur)})")
        await _action(ctx, cid, ChatAction.UPLOAD_VOICE)
        with open(ap,"rb") as f:
            await update.message.reply_audio(f, title=title, performer="وكالة طارق نور — Podcast",
                duration=int(dur), caption=f"🎧 *{title}*\n⏱ {fmt_dur(dur)}  |  📝 {len(content):,} حرف",
                parse_mode=ParseMode.MARKDOWN)
        await st.delete()
    except Exception as e:
        log.exception("do_podcast")
        await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:200]}`", parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ap)

async def do_content(update, ctx, raw: str):
    from telegram.constants import ParseMode
    st = await update.message.reply_text("✍️ *جارٍ كتابة المحتوى…*", parse_mode=ParseMode.MARKDOWN)
    try:
        if raw.startswith("سكريبت:"): kind, topic = "script", raw.split(":",1)[1].strip()
        elif raw.startswith("بوست:"): kind, topic = "post", raw.split(":",1)[1].strip()
        elif raw.startswith("مقال:"): kind, topic = "article", raw.split(":",1)[1].strip()
        else: kind, topic = "article", raw
        result = await ai_write_content(topic, kind)
        await st.delete()
        full = f"*✍️ وكالة طارق نور*\n\n{result}"
        for i in range(0, len(full), 4000):
            await update.message.reply_text(full[i:i+4000], parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.exception("do_content")
        await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:200]}`", parse_mode=ParseMode.MARKDOWN)

async def do_news(update, ctx, raw: str):
    from telegram.constants import ChatAction, ParseMode
    cid = update.effective_chat.id
    s   = get_session(update.effective_user.id)
    headline, body = parse_pipe(raw)
    if not headline: headline = truncate(body, 60)
    st = await update.message.reply_text(f"📰 *إنتاج الخبر…*\n📌 {headline}", parse_mode=ParseMode.MARKDOWN)
    ap = fp = vp = None
    try:
        await st.edit_text("1️⃣ كتابة النشرة…", parse_mode=ParseMode.MARKDOWN)
        bulletin = await ai_write_content(f"{headline}. {body}", "news")
        await st.edit_text("2️⃣ تحويل الصوت…", parse_mode=ParseMode.MARKDOWN)
        await _action(ctx, cid, ChatAction.RECORD_VIDEO)
        ap = await tts(bulletin, s["voice"])
        dur = audio_dur(ap)
        await st.edit_text("3️⃣ بناء الكارت الإخباري…", parse_mode=ParseMode.MARKDOWN)
        fp = await asyncio.get_event_loop().run_in_executor(None, build_news_frame, headline, bulletin)
        await st.edit_text("4️⃣ تجميع الفيديو…", parse_mode=ParseMode.MARKDOWN)
        vp = await asyncio.get_event_loop().run_in_executor(None, render_video, ap, fp, "news")
        await st.edit_text(f"⬆️ رفع… ({fmt_size(vp)})")
        await _action(ctx, cid, ChatAction.UPLOAD_VIDEO)
        with open(vp,"rb") as f:
            await update.message.reply_video(f,
                caption=f"📰 *عاجل | {headline}*\n\n{bulletin[:300]}",
                parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
        await st.delete()
    except Exception as e:
        log.exception("do_news")
        await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:300]}`", parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ap, fp, vp)

async def do_story(update, ctx, raw: str):
    from telegram.constants import ChatAction, ParseMode
    cid = update.effective_chat.id
    s   = get_session(update.effective_user.id)
    title, content = parse_pipe(raw)
    if not content: content = raw
    st = await update.message.reply_text("📱 *إنتاج الستوري…*", parse_mode=ParseMode.MARKDOWN)
    ap = ip = fp = vp = None
    try:
        await _action(ctx, cid, ChatAction.RECORD_VIDEO)
        ap = await tts(content, s["voice"])
        dur = audio_dur(ap)
        await st.edit_text("🎨 توليد الخلفية…", parse_mode=ParseMode.MARKDOWN)
        try:
            en = await ai_translate_prompt(title or content[:80])
            ip = await gen_portrait(en)
        except: ip = None
        fp = await asyncio.get_event_loop().run_in_executor(None, build_story_frame, content, title, s["style"], ip)
        await st.edit_text("🎬 تجميع…", parse_mode=ParseMode.MARKDOWN)
        vp = await asyncio.get_event_loop().run_in_executor(None, render_video, ap, fp, s["style"], 1080, 1920)
        await st.edit_text(f"⬆️ رفع… ({fmt_size(vp)})")
        await _action(ctx, cid, ChatAction.UPLOAD_VIDEO)
        with open(vp,"rb") as f:
            await update.message.reply_video(f,
                caption=f"📱 *{title or 'وكالة طارق نور'}*\n⏱ {fmt_dur(dur)}",
                parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
        await st.delete()
    except Exception as e:
        log.exception("do_story")
        await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:300]}`", parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ap, ip, fp, vp)

async def do_post(update, ctx, topic: str):
    from telegram.constants import ChatAction, ParseMode
    cid = update.effective_chat.id
    st  = await update.message.reply_text(f"📣 *إنتاج البوست…*\n📌 {truncate(topic,50)}", parse_mode=ParseMode.MARKDOWN)
    ip  = None
    try:
        await _action(ctx, cid, ChatAction.UPLOAD_PHOTO)
        post_task  = asyncio.create_task(ai_write_content(topic, "post"))
        en         = await ai_translate_prompt(topic)
        image_task = asyncio.create_task(gen_square(en))
        post_text  = await post_task
        try: ip = await image_task
        except Exception as e: log.warning(f"Post image: {e}"); ip = None
        await st.delete()
        if ip and os.path.exists(ip):
            with open(ip,"rb") as f:
                await update.message.reply_photo(f,
                    caption=f"📣 *وكالة طارق نور*\n\n{post_text}", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"📣 *وكالة طارق نور*\n\n{post_text}", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.exception("do_post")
        await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:200]}`", parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ip)

async def do_pdf(update, ctx, pdf_path: str, filename: str):
    from telegram.constants import ChatAction, ParseMode
    cid = update.effective_chat.id
    s   = get_session(update.effective_user.id)
    st  = await update.message.reply_text(f"📄 *استخراج النص…*\n📁 {filename}", parse_mode=ParseMode.MARKDOWN)
    ap  = None
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            await st.edit_text(f"📖 استخراج {total} صفحة…", parse_mode=ParseMode.MARKDOWN)
            for pg in pdf.pages:
                t = pg.extract_text()
                if t and t.strip(): pages.append(t.strip())
        full = "\n\n".join(pages)
        if not full.strip():
            await st.edit_text("⚠️ لم يُعثر على نص في هذا الـ PDF."); return
        await st.edit_text(f"🎙️ تحويل {len(full):,} حرف من {total} صفحة…\n⏳ قد يأخذ وقتاً",
                           parse_mode=ParseMode.MARKDOWN)
        await _action(ctx, cid, ChatAction.RECORD_VOICE)
        segs = split_arabic(full); tot = len(segs)
        async def prog(cur, t):
            if cur % 5 == 0 or cur == t:
                await st.edit_text(f"🎙️ {cur}/{t} مقطع…", parse_mode=ParseMode.MARKDOWN)
        ap = await tts(full, s["voice"], progress_cb=prog)
        dur = audio_dur(ap)
        name = filename.rsplit(".",1)[0]
        await st.edit_text(f"⬆️ رفع الكتاب الصوتي… ({fmt_dur(dur)})")
        await _action(ctx, cid, ChatAction.UPLOAD_VOICE)
        with open(ap,"rb") as f:
            await update.message.reply_audio(f, title=name, performer="وكالة طارق نور — Audiobook",
                duration=int(dur),
                caption=f"📚 *{name}*\n⏱ {fmt_dur(dur)}  |  📄 {total} صفحة  |  📝 {len(full):,} حرف",
                parse_mode=ParseMode.MARKDOWN)
        await st.delete()
    except ImportError:
        await st.edit_text("❌ pdfplumber غير مثبت في requirements.txt")
    except Exception as e:
        log.exception("do_pdf")
        await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:300]}`", parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ap)

async def do_brain(update, ctx, mode: str, text: str):
    from telegram.constants import ChatAction, ParseMode
    await _action(ctx, update.effective_chat.id, ChatAction.TYPING)
    uid = update.effective_user.id
    st  = await update.message.reply_text("🧠 نور بيفكر…")
    try:
        if mode == "chat":
            result = await brain_chat(uid, text)
            header = "🤖 *نور:*"
        elif mode == "ideas":
            result = await ai_brainstorm(text); header = "💡 *أفكار محتوى*"
        elif mode == "analyze":
            result = await ai_analyze(text); header = "📊 *تحليل المحتوى*"
        elif mode == "strategy":
            parts = text.split("|",1); ch = parts[0].strip(); goal = parts[1].strip() if len(parts)>1 else "النمو"
            result = await ai_strategy(ch, goal); header = "📋 *استراتيجية*"
        elif mode == "improve":
            parts = text.split("|",1); goal = parts[0].strip() if len(parts)>1 else "عام"; body = parts[1].strip() if len(parts)>1 else text
            result = await ai_improve(body, goal); header = "✏️ *نص محسّن*"
        elif mode == "titles":
            result = await ai_titles(text); header = "🔥 *عناوين جذابة*"
        elif mode == "trending":
            result = await ai_trending(text); header = "📈 *مواضيع تريند*"
        elif mode == "to_posts":
            result = await ai_to_posts(text); header = "🔄 *بوستات سوشيال*"
        else:
            result = await brain_chat(uid, text); header = "🤖 *نور:*"
        await st.delete()
        full = f"{header}\n\n{result}"
        for i in range(0, len(full), 4000):
            await update.message.reply_text(full[i:i+4000], parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.exception("do_brain")
        await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:200]}`", parse_mode=ParseMode.MARKDOWN)

# ═══════════════════════════════════════════════════════════════
#  KEYBOARDS
# ═══════════════════════════════════════════════════════════════
def main_menu():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎙️ صوت",callback_data="M_audio"),
         InlineKeyboardButton("🎬 فيديو",callback_data="M_video"),
         InlineKeyboardButton("🖼️ صورة",callback_data="M_image"),
         InlineKeyboardButton("✍️ محتوى",callback_data="M_content")],
        [InlineKeyboardButton("🎧 بودكاست",callback_data="M_podcast"),
         InlineKeyboardButton("📰 أخبار",callback_data="M_news"),
         InlineKeyboardButton("📱 ستوري",callback_data="M_story"),
         InlineKeyboardButton("📣 بوست",callback_data="M_post")],
        [InlineKeyboardButton("💬 نقاش",callback_data="M_chat"),
         InlineKeyboardButton("💡 أفكار",callback_data="M_ideas"),
         InlineKeyboardButton("📊 تحليل",callback_data="M_analyze"),
         InlineKeyboardButton("📋 استراتيجية",callback_data="M_strategy")],
        [InlineKeyboardButton("✏️ تحسين",callback_data="M_improve"),
         InlineKeyboardButton("🔥 عناوين",callback_data="M_titles"),
         InlineKeyboardButton("📈 تريند",callback_data="M_trending"),
         InlineKeyboardButton("🔄 بوستات",callback_data="M_to_posts")],
        [InlineKeyboardButton("⚙️ الإعدادات",callback_data="M_settings"),
         InlineKeyboardButton("📊 الحالة",callback_data="M_status")],
    ])

def back_btn():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ القائمة",callback_data="M_back")]])

# ═══════════════════════════════════════════════════════════════
#  HANDLERS
# ═══════════════════════════════════════════════════════════════
PROD_MODES = {"audio","video","image","content","podcast","news","story","post"}
BRAIN_MODES = {"chat","ideas","analyze","strategy","improve","titles","trending","to_posts"}

MODE_HINTS = {
    "audio":"أرسل النص","video":"أرسل: العنوان | النص","image":"صف الصورة",
    "content":"أرسل: مقال/سكريبت/بوست: الموضوع","podcast":"أرسل: العنوان | المحتوى",
    "news":"أرسل: العنوان | الخبر","story":"أرسل: العنوان | النص","post":"أرسل الموضوع",
    "chat":"اكتب سؤالك أو فكرتك","ideas":"اكتب الموضوع","analyze":"أرسل النص للتحليل",
    "strategy":"نوع القناة | الهدف","improve":"النص (أو: يوتيوب | النص)",
    "titles":"اكتب الموضوع","trending":"اكتب المجال","to_posts":"أرسل السكريبت",
}

async def on_start(update, ctx):
    from telegram.constants import ParseMode
    u = update.effective_user
    await update.message.reply_text(
        f"🎬 *أهلاً {u.first_name}! وكالة طارق نور*\n\n"
        "ستوديو إنتاج إعلامي مصري متكامل ومجاني\n"
        "صوت · فيديو · صور · بودكاست · أخبار · ستوري · بوست\n"
        "نقاش ذكي · أفكار · تحليل · استراتيجية · تحسين\n"
        "📚 أرسل PDF → كتاب صوتي كامل\n\n"
        "اختر الوضع أو أرسل نصاً مباشرة 👇",
        parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())

async def on_help(update, ctx):
    from telegram.constants import ParseMode
    await update.message.reply_text(
        "📖 *وكالة طارق نور — الدليل*\n\n"
        "*الإنتاج:*\n`/audio` `/video` `/image` `/content`\n"
        "`/podcast` `/news` `/story` `/post`\n\n"
        "*الذكاء:*\n`/nour` نقاش · `/ideas` أفكار · `/analyze` تحليل\n"
        "`/strategy` استراتيجية · `/improve` تحسين\n"
        "`/titles` عناوين · `/trending` تريند · `/toposts` بوستات\n"
        "`/reset` مسح المحادثة\n\n"
        "*الصيغة:* `العنوان | المحتوى`\n"
        "*PDF:* أرسله مباشرة → كتاب صوتي\n\n"
        "المصادر: gTTS · Pollinations · Groq · FFmpeg · pydub · pdfplumber",
        parse_mode=ParseMode.MARKDOWN)

async def on_status(update, ctx):
    from telegram.constants import ParseMode
    ffok = check_ffmpeg()
    await update.message.reply_text(
        f"📊 *حالة الخدمات*\n\n"
        f"🔵 gTTS (صوت): ✅ دائماً\n"
        f"🟡 Pollinations (صور): ✅\n"
        f"🟢 Groq AI: {'✅ مفعل' if GROQ_API_KEY else '⚠️ غير مفعل'}\n"
        f"🟠 FFmpeg (فيديو): {'✅' if ffok else '❌'}\n"
        f"👥 مستخدمون: {len(_sessions)}",
        parse_mode=ParseMode.MARKDOWN)

async def on_text(update, ctx):
    text = (update.message.text or "").strip()
    if not text: return
    uid = update.effective_user.id
    s   = get_session(uid)
    mode = s.get("mode","audio")
    dispatch = {
        "audio": do_audio, "video": do_video, "image": do_image,
        "content": do_content, "podcast": do_podcast, "news": do_news,
        "story": do_story, "post": do_post,
    }
    if mode in dispatch:
        await dispatch[mode](update, ctx, text)
    elif mode in BRAIN_MODES:
        await do_brain(update, ctx, mode, text)
    else:
        if text.endswith("?") or text.endswith("؟") or len(text) < 80:
            await do_brain(update, ctx, "chat", text)
        else:
            await do_audio(update, ctx, text)

async def on_document(update, ctx):
    from telegram.constants import ParseMode
    doc  = update.message.document
    name = doc.file_name or "file"
    ext  = os.path.splitext(name)[1].lower()
    if ext != ".pdf":
        await update.message.reply_text("📎 حالياً أدعم ملفات PDF فقط.\nأرسل PDF → كتاب صوتي 📚")
        return
    size_mb = (doc.file_size or 0) / (1024*1024)
    if size_mb > 50:
        await update.message.reply_text(f"⚠️ الملف كبير جداً ({size_mb:.1f} MB). الحد 50 MB.")
        return
    st = await update.message.reply_text(f"📥 *تحميل الـ PDF…*\n📁 {name}", parse_mode=ParseMode.MARKDOWN)
    tmp = None
    try:
        file_obj = await ctx.bot.get_file(doc.file_id)
        tmp = tempfile.mktemp(suffix=".pdf")
        await file_obj.download_to_drive(tmp)
        await st.delete()
        await do_pdf(update, ctx, tmp, name)
    except Exception as e:
        await st.edit_text(f"❌ فشل التحميل: `{e}`", parse_mode=ParseMode.MARKDOWN)
    finally:
        safe_del(tmp)

async def on_callback(update, ctx):
    from telegram.constants import ParseMode
    q   = update.callback_query; uid = q.from_user.id; d = q.data
    await q.answer(); s = get_session(uid)

    if d == "M_back":
        await q.edit_message_text("🎬 *وكالة طارق نور*\nاختر الوضع أو أرسل النص 👇",
            parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())
        return

    if d == "M_status":
        ffok = check_ffmpeg()
        await q.edit_message_text(
            f"📊 *الحالة*\n🔵 gTTS: ✅\n🟡 Pollinations: ✅\n"
            f"🟢 Groq: {'✅' if GROQ_API_KEY else '⚠️'}\n"
            f"🟠 FFmpeg: {'✅' if ffok else '❌'}\n👥 {len(_sessions)} مستخدم",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_btn())
        return

    if d == "M_settings":
        v = VOICES.get(s["voice"],VOICES["female"]); st2 = STYLES.get(s["style"],STYLES["gold"])
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        await q.edit_message_text(
            f"⚙️ *الإعدادات*\nالصوت: {v['gender']} `{v['name']}`\nالنمط: `{st2['name']}`\nالوضع: `{s['mode']}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎤 الصوت",callback_data="SET_voice")],
                [InlineKeyboardButton("🎨 النمط",callback_data="SET_style")],
                [InlineKeyboardButton("↩️",callback_data="M_back")],
            ]))
        return

    if d.startswith("M_"):
        mode = d[2:]
        s["mode"] = mode
        hint = MODE_HINTS.get(mode, "أرسل النص")
        icons = {"audio":"🎙️","video":"🎬","image":"🖼️","content":"✍️","podcast":"🎧",
                 "news":"📰","story":"📱","post":"📣","chat":"💬","ideas":"💡",
                 "analyze":"📊","strategy":"📋","improve":"✏️","titles":"🔥","trending":"📈","to_posts":"🔄"}
        icon = icons.get(mode,"📌")
        if mode == "trending":
            await q.edit_message_text(f"🧠 *{icon} تريند*\n\n{hint} أو اضغط زر أدناه 👇",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=from_telegram_ikb([
                    [("🔥 تريند مصر الآن","TREND_now")], [("↩️","M_back")]
                ]))
        else:
            await q.edit_message_text(f"*{icon} وضع {mode.upper()}*\n\n{hint} 👇",
                parse_mode=ParseMode.MARKDOWN, reply_markup=back_btn())
        return

    if d == "TREND_now":
        await q.edit_message_text("📈 جارٍ توليد التريند…")
        result = await ai_trending("مصر عامة")
        await q.edit_message_text(f"📈 *مواضيع تريند مصرية*\n\n{result}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_btn())
        return

    if d == "SET_voice":
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        await q.edit_message_text("اختر الصوت:", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"{v['gender']} {v['name']} — {v['desc']}",callback_data=f"V_{k}")]
             for k,v in VOICES.items()] + [[InlineKeyboardButton("↩️",callback_data="M_settings")]]))
        return
    if d.startswith("V_"):
        key = d[2:]; s["voice"] = key; v = VOICES.get(key,VOICES["female"])
        await q.edit_message_text(f"✅ الصوت: *{v['gender']} {v['name']}*",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_btn())
        return
    if d == "SET_style":
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        await q.edit_message_text("اختر النمط البصري:", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(st2["name"],callback_data=f"S_{k}")]
             for k,st2 in STYLES.items()] + [[InlineKeyboardButton("↩️",callback_data="M_settings")]]))
        return
    if d.startswith("S_"):
        key = d[2:]; s["style"] = key; st2 = STYLES.get(key,STYLES["gold"])
        await q.edit_message_text(f"✅ النمط: *{st2['name']}*",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_btn())
        return

def from_telegram_ikb(rows):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[InlineKeyboardButton(t,callback_data=c) for t,c in row] for row in rows])

# ── Brain slash commands ──────────────────────────────────────
async def on_nour(update, ctx):
    from telegram.constants import ParseMode
    text = " ".join(ctx.args).strip() if ctx.args else ""
    if text: await do_brain(update, ctx, "chat", text)
    else:
        await update.message.reply_text(
            "🤖 *نور — المساعد الذكي المصري*\n\n"
            "اكتب سؤالك أو `/nour سؤالك`\n\n"
            "أوامر الذكاء:\n"
            "`/ideas` · `/analyze` · `/strategy`\n"
            "`/improve` · `/titles` · `/trending`\n"
            "`/toposts` · `/reset`",
            parse_mode=ParseMode.MARKDOWN)

async def _brain_cmd(update, ctx, mode, need_arg=True):
    text = " ".join(ctx.args).strip() if ctx.args else ""
    if need_arg and not text:
        hints = {"ideas":"الموضوع","analyze":"النص","strategy":"القناة | الهدف",
                 "improve":"النص","titles":"الموضوع","trending":"المجال","to_posts":"السكريبت"}
        await update.message.reply_text(f"أرسل: `/{mode} {hints.get(mode,'النص')}`", parse_mode="Markdown")
        return
    text = text or "مصر عامة"
    await do_brain(update, ctx, mode, text)

async def on_ideas(u,c):    await _brain_cmd(u,c,"ideas")
async def on_analyze(u,c):  await _brain_cmd(u,c,"analyze")
async def on_strategy(u,c): await _brain_cmd(u,c,"strategy")
async def on_improve(u,c):  await _brain_cmd(u,c,"improve")
async def on_titles(u,c):   await _brain_cmd(u,c,"titles")
async def on_trending(u,c): await _brain_cmd(u,c,"trending",need_arg=False)
async def on_toposts(u,c):  await _brain_cmd(u,c,"to_posts")
async def on_reset(u,c):
    clear_history(u.effective_user.id)
    await u.message.reply_text("🗑️ تم مسح سجل المحادثة مع نور.")

async def _mode_cmd(update, ctx, mode, fn=None):
    get_session(update.effective_user.id)["mode"] = mode
    text = " ".join(ctx.args).strip() if ctx.args else ""
    if text and fn: await fn(update, ctx, text)
    else: await update.message.reply_text(
        f"*وضع {mode.upper()}*\n{MODE_HINTS.get(mode,'أرسل النص')} 👇",
        parse_mode="Markdown")

async def on_audio(u,c):   await _mode_cmd(u,c,"audio",do_audio)
async def on_video(u,c):   await _mode_cmd(u,c,"video",do_video)
async def on_image(u,c):   await _mode_cmd(u,c,"image",do_image)
async def on_content(u,c): await _mode_cmd(u,c,"content",do_content)
async def on_podcast(u,c): await _mode_cmd(u,c,"podcast")
async def on_news(u,c):    await _mode_cmd(u,c,"news")
async def on_story(u,c):   await _mode_cmd(u,c,"story")
async def on_post(u,c):    await _mode_cmd(u,c,"post")

# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════
async def post_init(app):
    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand("audio","🎙️ نص → صوت"), BotCommand("video","🎬 نص → فيديو"),
        BotCommand("image","🖼️ وصف → صورة"), BotCommand("content","✍️ موضوع → محتوى"),
        BotCommand("podcast","🎧 نص → بودكاست"), BotCommand("news","📰 خبر → فيديو"),
        BotCommand("story","📱 نص → ستوري"), BotCommand("post","📣 موضوع → بوست"),
        BotCommand("nour","🤖 نقاش مع AI مصري"), BotCommand("ideas","💡 أفكار محتوى"),
        BotCommand("analyze","📊 تحليل"), BotCommand("strategy","📋 استراتيجية"),
        BotCommand("improve","✏️ تحسين نص"), BotCommand("titles","🔥 عناوين جذابة"),
        BotCommand("trending","📈 تريند مصري"), BotCommand("toposts","🔄 سكريبت → بوستات"),
        BotCommand("reset","🗑️ مسح النقاش"), BotCommand("status","📊 الحالة"),
        BotCommand("help","📖 المساعدة"), BotCommand("start","🏠 البداية"),
    ])
    ensure_font()
    log.info("✅ وكالة طارق نور — جاهزة!")

def main():
    # Try all possible env var names Railway might use
    token = (
        os.environ.get("BOT_TOKEN") or
        os.environ.get("bot_token") or
        os.environ.get("TELEGRAM_TOKEN") or
        os.environ.get("TELEGRAM_BOT_TOKEN") or ""
    ).strip()

    # Debug log: show what Railway sees
    all_keys = sorted(os.environ.keys())
    log.info(f"ENV VARS AVAILABLE: {all_keys}")

    if not token:
        import time
        log.error("BOT_TOKEN NOT FOUND. Available vars: " + str(all_keys))
        time.sleep(3)
        sys.exit(1)

    groq = (os.environ.get("GROQ_API_KEY") or os.environ.get("groq_api_key") or "").strip()

    global BOT_TOKEN, GROQ_API_KEY
    BOT_TOKEN = token
    GROQ_API_KEY = groq

    if not check_ffmpeg():
        log.warning("⚠️  FFmpeg غير مثبت")
    if not groq:
        log.warning("⚠️  GROQ_API_KEY غير موجود — template mode")

    from telegram.ext import (Application, CommandHandler, MessageHandler,
                               CallbackQueryHandler, filters)
    app = (Application.builder().token(BOT_TOKEN).post_init(post_init)
           .read_timeout(60).write_timeout(120).connect_timeout(30).pool_timeout(60).build())

    for cmd,fn in [
        ("start",on_start),("help",on_help),("status",on_status),
        ("audio",on_audio),("video",on_video),("image",on_image),("content",on_content),
        ("podcast",on_podcast),("news",on_news),("story",on_story),("post",on_post),
        ("nour",on_nour),("ideas",on_ideas),("analyze",on_analyze),("strategy",on_strategy),
        ("improve",on_improve),("titles",on_titles),("trending",on_trending),
        ("toposts",on_toposts),("reset",on_reset),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("🚀 وكالة طارق نور تنطلق…")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message","callback_query"])

if __name__ == "__main__":
    main()
