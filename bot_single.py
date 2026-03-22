#!/usr/bin/env python3
"""
🎬 وكالة طارق نور
TTS: Hugging Face (Arabic Neural) + gTTS fallback
AI:  OpenRouter (DeepSeek-V3 / Gemini 2.0 Flash) — free
IMG: Pollinations.ai — free
VID: FFmpeg
"""
import os, sys, asyncio, re, logging, tempfile, subprocess, math
import urllib.request, urllib.parse, urllib.error, json, random, time, shutil
from typing import Optional

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
log = logging.getLogger("agency")

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "").strip()
HF_TOKEN     = os.environ.get("HF_TOKEN", "").strip()
OR_KEY       = os.environ.get("OPENROUTER_KEY", "").strip()
GROQ_KEY     = os.environ.get("GROQ_API_KEY", "").strip()
FONT_PATH    = "/tmp/cairo.ttf"
FONT_URL     = "https://github.com/google/fonts/raw/main/ofl/cairo/Cairo%5Bslnt%2Cwght%5D.ttf"

# ── HuggingFace TTS Models (Arabic, best quality first) ──────
HF_TTS_MODELS = [
    "facebook/mms-tts-ara",           # Facebook MMS Arabic
    "esmail-gilany/ar-tts",           # Arabic TTS
]
HF_TTS_URL = "https://api-inference.huggingface.co/models/{model}"

# ── OpenRouter ────────────────────────────────────────────────
OR_URL     = "https://openrouter.ai/api/v1/chat/completions"
OR_MODEL   = "deepseek/deepseek-chat-v3-5"   # free tier
OR_MODEL_2 = "google/gemini-2.0-flash-exp:free"  # free fallback

# ── Groq fallback ─────────────────────────────────────────────
GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

VOICES = {
    "female": {"name": "سلمى",  "gender": "👩", "slow": False},
    "male":   {"name": "شاكر", "gender": "👨", "slow": False},
    "slow":   {"name": "واضح", "gender": "🐢", "slow": True},
}
STYLES = {
    "gold":  {"name": "✨ ذهبي",  "bg": (7,7,14),   "accent": (201,168,76),  "text": (240,232,213), "wave": "c9a84c"},
    "night": {"name": "🌙 ليلي", "bg": (5,10,28),   "accent": (100,149,255), "text": (220,228,255), "wave": "6495ff"},
    "radio": {"name": "📻 راديو","bg": (3,12,3),    "accent": (61,186,78),   "text": (232,245,232), "wave": "3dba4e"},
    "news":  {"name": "📰 أخبار","bg": (10,5,5),    "accent": (220,60,60),   "text": (255,248,248), "wave": "dc3c3c"},
}

_sessions: dict = {}
_history:  dict = {}

def session(uid):
    if uid not in _sessions:
        _sessions[uid] = {"mode": "audio", "voice": "female", "style": "gold"}
    return _sessions[uid]

# ═══════════════════════════════════════════════════════════════
#  FONT
# ═══════════════════════════════════════════════════════════════
_fc: dict = {}

def ensure_font():
    if os.path.exists(FONT_PATH) and os.path.getsize(FONT_PATH) > 10000:
        return
    try:
        urllib.request.urlretrieve(FONT_URL, FONT_PATH)
        log.info("Font ready ✓")
    except Exception as e:
        log.warning(f"Font: {e}")

def get_font(size):
    from PIL import ImageFont
    if size not in _fc:
        try:
            if os.path.exists(FONT_PATH) and os.path.getsize(FONT_PATH) > 10000:
                _fc[size] = ImageFont.truetype(FONT_PATH, size)
                return _fc[size]
        except: pass
        _fc[size] = ImageFont.load_default()
    return _fc[size]

# ═══════════════════════════════════════════════════════════════
#  TEXT UTILS
# ═══════════════════════════════════════════════════════════════
def split_text(text, max_chars=500):
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
    words = text.split()
    lines, cur = [], []
    for word in words:
        test = " ".join(cur + [word])
        try:
            bb = draw.textbbox((0,0), test, font=fnt, direction="rtl")
            w = bb[2]-bb[0]
        except: w = len(test)*18
        if w <= max_w: cur.append(word)
        else:
            if cur: lines.append(" ".join(cur))
            cur = [word]
    if cur: lines.append(" ".join(cur))
    return lines

def truncate(t, n=60): return t[:n]+"…" if len(t)>n else t
def pipe(t): return (t.split("|",1)[0].strip(), t.split("|",1)[1].strip()) if "|" in t else ("", t.strip())
def fmt_dur(s):
    s=int(s); m,s=divmod(s,60); h,m=divmod(m,60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
def fmt_mb(p): return f"{os.path.getsize(p)/1024/1024:.1f} MB"
def safe_del(*paths):
    for p in paths:
        if p and os.path.exists(p):
            try: os.remove(p)
            except: pass

def check_ffmpeg():
    try: return subprocess.run(["ffmpeg","-version"],capture_output=True).returncode==0
    except: return False

# ═══════════════════════════════════════════════════════════════
#  TTS ENGINE
# ═══════════════════════════════════════════════════════════════

def _hf_tts_sync(text: str, model: str) -> str:
    """Call HuggingFace Inference API for TTS."""
    url = HF_TTS_URL.format(model=model)
    payload = json.dumps({"inputs": text}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        audio_data = r.read()
    if len(audio_data) < 1000:
        raise RuntimeError(f"HF TTS returned too small ({len(audio_data)} bytes)")
    tmp = tempfile.mktemp(suffix=".wav")
    with open(tmp, "wb") as f:
        f.write(audio_data)
    # Convert WAV to MP3
    mp3 = tempfile.mktemp(suffix=".mp3")
    subprocess.run(["ffmpeg","-y","-loglevel","error","-i",tmp,"-q:a","2",mp3], check=True)
    os.remove(tmp)
    return mp3

def _gtts_sync(text: str, slow: bool = False) -> str:
    from gtts import gTTS
    tmp = tempfile.mktemp(suffix=".mp3")
    gTTS(text=text, lang="ar", slow=slow).save(tmp)
    if not os.path.exists(tmp) or os.path.getsize(tmp) < 100:
        raise RuntimeError("gTTS empty")
    return tmp

async def _tts_chunk(text: str, slow: bool = False) -> str:
    """Try HF TTS models, fall back to gTTS."""
    loop = asyncio.get_event_loop()

    # Try HuggingFace models
    if HF_TOKEN:
        for model in HF_TTS_MODELS:
            for attempt in range(2):
                try:
                    log.info(f"HF TTS: {model} attempt {attempt+1}")
                    path = await loop.run_in_executor(None, _hf_tts_sync, text, model)
                    log.info(f"HF TTS ✓ ({model})")
                    return path
                except Exception as e:
                    log.warning(f"HF TTS {model} failed: {e}")
                    if "loading" in str(e).lower() or "503" in str(e):
                        await asyncio.sleep(8)  # Model loading, wait
                    else:
                        break

    # Fallback: gTTS
    log.info("Falling back to gTTS")
    return await loop.run_in_executor(None, _gtts_sync, text, slow)

def _merge_mp3s(paths: list) -> str:
    from pydub import AudioSegment
    if len(paths) == 1:
        out = tempfile.mktemp(suffix=".mp3")
        shutil.copy(paths[0], out)
        return out
    combined = AudioSegment.empty()
    pause = AudioSegment.silent(duration=400)
    for i, p in enumerate(paths):
        combined += AudioSegment.from_file(p)
        if i < len(paths)-1: combined += pause
    combined = combined.normalize()
    out = tempfile.mktemp(suffix=".mp3")
    combined.export(out, format="mp3", bitrate="128k")
    return out

async def text_to_speech(text: str, voice_key: str = "female",
                          progress_cb=None) -> str:
    slow = VOICES.get(voice_key, VOICES["female"])["slow"]
    segs = split_text(text, max_chars=500)
    total = len(segs)
    parts = []
    try:
        for i, seg in enumerate(segs):
            if progress_cb and total > 1:
                await progress_cb(i+1, total)
            parts.append(await _tts_chunk(seg, slow))
            if i < total-1: await asyncio.sleep(0.5)
        return _merge_mp3s(parts)
    finally:
        for p in parts:
            safe_del(p)

def audio_dur(path):
    from pydub import AudioSegment
    return len(AudioSegment.from_file(path)) / 1000.0

# ═══════════════════════════════════════════════════════════════
#  AI BRAIN — OpenRouter + Groq fallback
# ═══════════════════════════════════════════════════════════════
EGYPT_SYSTEM = """أنت "نور" — المساعد الذكي لوكالة طارق نور الإعلامية المصرية.
شخصيتك: خبير إعلامي مصري محترف، أسلوبك ذكي وعملي وجذاب.
متخصص في: الإعلام الرقمي، يوتيوب، بودكاست، سوشيال ميديا المصرية.
ردودك دائماً عملية ومفيدة، مش نظرية. ركز على المحتوى المصري."""

async def _ai_call(messages: list, system: str = EGYPT_SYSTEM,
                   max_tokens: int = 1500) -> str:
    loop = asyncio.get_event_loop()

    # Try OpenRouter first
    if OR_KEY:
        for model in [OR_MODEL, OR_MODEL_2]:
            try:
                payload = json.dumps({
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": [{"role":"system","content":system}] + messages,
                }).encode("utf-8")
                def _call_or():
                    req = urllib.request.Request(OR_URL, data=payload, headers={
                        "Authorization": f"Bearer {OR_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://tarek-nour.agency",
                        "X-Title": "Tarek Nour Agency",
                    })
                    with urllib.request.urlopen(req, timeout=25) as r:
                        return json.loads(r.read())["choices"][0]["message"]["content"].strip()
                result = await loop.run_in_executor(None, _call_or)
                log.info(f"OpenRouter ✓ ({model})")
                return result
            except Exception as e:
                log.warning(f"OpenRouter {model}: {e}")

    # Fallback: Groq
    if GROQ_KEY:
        try:
            payload = json.dumps({
                "model": GROQ_MODEL, "max_tokens": max_tokens,
                "messages": [{"role":"system","content":system}] + messages,
            }).encode("utf-8")
            def _call_groq():
                req = urllib.request.Request(
                    "https://api.groq.com/openai/v1/chat/completions",
                    data=payload,
                    headers={"Authorization":f"Bearer {GROQ_KEY}","Content-Type":"application/json"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    return json.loads(r.read())["choices"][0]["message"]["content"].strip()
            return await loop.run_in_executor(None, _call_groq)
        except Exception as e:
            log.warning(f"Groq: {e}")

    return "⚠️ خدمة AI غير متاحة حالياً. تحقق من OPENROUTER_KEY أو GROQ_API_KEY."

# History-aware chat
def get_history(uid): return _history.get(uid, [])
def add_history(uid, role, content):
    if uid not in _history: _history[uid] = []
    _history[uid].append({"role": role, "content": content})
    if len(_history[uid]) > 16: _history[uid] = _history[uid][-16:]
def clear_history(uid): _history[uid] = []

async def ai_chat(uid: int, msg: str) -> str:
    hist = get_history(uid)
    reply = await _ai_call(hist + [{"role":"user","content":msg}])
    add_history(uid, "user", msg)
    add_history(uid, "assistant", reply)
    return reply

async def ai_task(prompt: str, max_tokens: int = 1500) -> str:
    return await _ai_call([{"role":"user","content":prompt}], max_tokens=max_tokens)

async def ai_write(topic: str, kind: str = "article") -> str:
    prompts = {
        "article": f"اكتب مقالاً متكاملاً عن: {topic}\nحوالي 600 كلمة، فقرات متدفقة، بدون عناوين.",
        "script":  f"اكتب سكريبت لفيديو/بودكاست عن: {topic}\nحوالي 5 دقائق، أسلوب محادثة مباشرة.",
        "post":    f"اكتب بوست احترافي لإنستغرام وفيسبوك عن: {topic}\nمع 5 هاشتاقات عربية.",
        "news":    f"اكتب نشرة إخبارية احترافية عن: {topic}\n3-4 جمل، أسلوب إخباري رسمي.",
    }
    return await ai_task(prompts.get(kind, prompts["article"]), max_tokens=2000)

async def ai_brainstorm(topic: str) -> str:
    return await ai_task(
        f"عصف ذهني مصري عن: {topic}\n7 أفكار محتوى مبتكرة.\nكل فكرة: العنوان + شرح + المنصة.",
        max_tokens=1200)

async def ai_titles(topic: str) -> str:
    return await ai_task(
        f"10 عناوين جذابة لمحتوى عن: {topic}\nمتنوعة، تناسب الجمهور المصري.",
        max_tokens=600)

async def ai_strategy(ch: str, goal: str) -> str:
    return await ai_task(
        f"استراتيجية محتوى شهرية لـ {ch} هدفها {goal}.\n4 محاور + جدول أسبوعي + أفكار أول 4 إصدارات.",
        max_tokens=1800)

async def ai_improve(text: str, goal: str = "عام") -> str:
    goals = {"يوتيوب":"أكثر جذباً","بودكاست":"مناسب للنطق","سوشيال":"قصير وجذاب","عام":"حسّن الأسلوب"}
    r = await ai_task(f"حسّن هذا النص ({goals.get(goal,goals['عام'])}):\n\n{text[:3000]}\n\nالنص المحسّن فقط:", max_tokens=2000)
    return r if not r.startswith("⚠️") else text

async def ai_trending(niche: str = "مصر") -> str:
    return await ai_task(f"10 مواضيع تريند للمحتوى المصري في: {niche}\nكل موضوع مع السبب وأفضل فورمات.", max_tokens=1000)

async def ai_img_prompt(arabic: str) -> str:
    r = await ai_task(f"Translate to English image prompt (output only):\n{arabic}", max_tokens=100)
    return r if not r.startswith("⚠️") else arabic

# ═══════════════════════════════════════════════════════════════
#  IMAGE ENGINE — Pollinations.ai
# ═══════════════════════════════════════════════════════════════
def _dl_img(url, timeout=90):
    out = tempfile.mktemp(suffix=".png")
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent":"TarekNourBot/2.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if len(data) < 2000: raise RuntimeError(f"Small ({len(data)}B)")
            with open(out,"wb") as f: f.write(data)
            return out
        except Exception as e:
            log.warning(f"Image attempt {i+1}: {e}")
            if i < 3: time.sleep(4*(i+1))
    raise RuntimeError("فشل توليد الصورة بعد 4 محاولات")

async def gen_image(prompt, w=1920, h=1080):
    seed = random.randint(1, 999999)
    full = f"{prompt}, professional, high quality, 8k, cinematic lighting"
    enc  = urllib.parse.quote(full, safe="")
    url  = f"https://image.pollinations.ai/prompt/{enc}?width={w}&height={h}&nologo=true&enhance=true&seed={seed}"
    return await asyncio.get_event_loop().run_in_executor(None, _dl_img, url, 90)

async def gen_portrait(p): return await gen_image(p, 1080, 1920)
async def gen_square(p):   return await gen_image(p, 1080, 1080)

# ═══════════════════════════════════════════════════════════════
#  VIDEO ENGINE
# ═══════════════════════════════════════════════════════════════
def _draw_frame(text, title, style_key, bg_img, W, H):
    from PIL import Image, ImageDraw
    st = STYLES.get(style_key, STYLES["gold"])
    bg, acc, txt = st["bg"], st["accent"], st["text"]
    if bg_img and os.path.exists(bg_img):
        img = Image.open(bg_img).convert("RGB").resize((W,H))
        ov  = Image.new("RGBA",(W,H),(0,0,0,155))
        img = Image.alpha_composite(img.convert("RGBA"),ov).convert("RGB")
    else:
        img = Image.new("RGB",(W,H),bg)
        draw2 = ImageDraw.Draw(img)
        for y in range(H):
            t=y/H; r=min(255,int(bg[0]+20*t)); g2=min(255,int(bg[1]+12*t)); b=min(255,int(bg[2]+28*t))
            draw2.line([(0,y),(W,y)],fill=(r,g2,b))
    draw = ImageDraw.Draw(img)
    draw.rectangle([int(W*.04),46,int(W*.96),51],fill=acc)
    draw.rectangle([int(W*.04),H-56,int(W*.96),H-51],fill=acc)
    draw.text((int(W*.04),16),"وكالة طارق نور",font=get_font(26),fill=acc,direction="rtl")
    if title:
        f=get_font(52)
        try: bb=draw.textbbox((0,0),title,font=f,direction="rtl"); tw=bb[2]-bb[0]; tx=max(int(W*.04),(W-tw)//2)
        except: tx=int(W*.04)
        draw.text((tx,66),title,font=f,fill=acc,direction="rtl")
    fb=get_font(42); lines=wrap_pil(draw,text,fb,int(W*.84))
    sy=150 if title else 110
    for i,line in enumerate(lines[:6]):
        a=0.45 if (i==len(lines[:6])-1 and len(lines)>6) else 1.0
        col=tuple(int(c*a) for c in txt)
        try: bb=draw.textbbox((0,0),line,font=fb,direction="rtl"); lw=bb[2]-bb[0]; x=max(int(W*.04),(W-lw)//2)
        except: x=int(W*.04)
        draw.text((x,sy+i*68),line,font=fb,fill=col,direction="rtl")
    out=tempfile.mktemp(suffix=".png"); img.save(out); return out

def build_frame(text,title="",style_key="gold",bg_img=None,W=1920,H=1080):
    return _draw_frame(text,title,style_key,bg_img,W,H)

def build_news_frame(headline,body="",W=1920,H=1080):
    from PIL import Image, ImageDraw
    st=STYLES["news"]; bg,acc,txt=st["bg"],st["accent"],st["text"]
    img=Image.new("RGB",(W,H),bg); draw=ImageDraw.Draw(img)
    draw.rectangle([0,H-90,W,H],fill=acc)
    draw.rectangle([int(W*.04),46,int(W*.96),50],fill=acc)
    draw.text((int(W*.04),16),"وكالة طارق نور",font=get_font(28),fill=acc,direction="rtl")
    draw.rectangle([int(W*.04),H-78,int(W*.04)+130,H-18],fill=(180,0,0))
    draw.text((int(W*.04)+8,H-72),"عاجل",font=get_font(34),fill=(255,255,255))
    draw.text((int(W*.04)+148,H-72),headline[:75],font=get_font(38),fill=(10,10,10),direction="rtl")
    if body:
        f=get_font(44); lines=wrap_pil(draw,body,f,int(W*.85))
        for i,line in enumerate(lines[:5]):
            try: bb=draw.textbbox((0,0),line,font=f,direction="rtl"); lw=bb[2]-bb[0]; x=max(int(W*.04),(W-lw)//2)
            except: x=int(W*.04)
            draw.text((x,100+i*72),line,font=f,fill=txt,direction="rtl")
    out=tempfile.mktemp(suffix=".png"); img.save(out); return out

def build_story_frame(text,title="",style_key="gold",bg_img=None):
    from PIL import Image, ImageDraw
    W,H=1080,1920; st=STYLES.get(style_key,STYLES["gold"])
    bg,acc,txt=st["bg"],st["accent"],st["text"]
    if bg_img and os.path.exists(bg_img):
        img=Image.open(bg_img).convert("RGB").resize((W,H))
        ov=Image.new("RGBA",(W,H),(0,0,0,165))
        img=Image.alpha_composite(img.convert("RGBA"),ov).convert("RGB")
    else:
        img=Image.new("RGB",(W,H),bg)
    draw=ImageDraw.Draw(img)
    draw.rectangle([int(W*.06),58,int(W*.94),63],fill=acc)
    draw.rectangle([int(W*.06),H-122,int(W*.94),H-117],fill=acc)
    draw.text((int(W*.06),78),"وكالة طارق نور",font=get_font(36),fill=acc,direction="rtl")
    if title:
        f=get_font(56); lines=wrap_pil(draw,title,f,int(W*.85))
        for i,line in enumerate(lines[:2]):
            try: bb=draw.textbbox((0,0),line,font=f,direction="rtl"); lw=bb[2]-bb[0]; x=(W-lw)//2
            except: x=int(W*.06)
            draw.text((x,140+i*78),line,font=f,fill=acc,direction="rtl")
    fb=get_font(44); lines=wrap_pil(draw,text,fb,int(W*.84))
    for i,line in enumerate(lines[:10]):
        try: bb=draw.textbbox((0,0),line,font=fb,direction="rtl"); lw=bb[2]-bb[0]; x=(W-lw)//2
        except: x=int(W*.06)
        draw.text((x,360+i*72),line,font=fb,fill=txt,direction="rtl")
    out=tempfile.mktemp(suffix=".png"); img.save(out); return out

def render_video(audio,frame,style_key="gold",W=1920,H=1080):
    if not check_ffmpeg(): raise RuntimeError("FFmpeg غير مثبت")
    wave_col=STYLES.get(style_key,STYLES["gold"])["wave"]
    ww,wh=int(W*.9),140; wx,wy=int(W*.05),H-200
    flt=(f"[1:a]showwaves=s={ww}x{wh}:mode=cline:rate=30:colors={wave_col}:scale=sqrt[w];"
         f"[0:v][w]overlay={wx}:{wy}:shortest=1[vo]")
    out=tempfile.mktemp(suffix=".mp4")
    cmd=["ffmpeg","-y","-loglevel","error",
         "-loop","1","-framerate","30","-i",frame,
         "-i",audio,"-filter_complex",flt,
         "-map","[vo]","-map","1:a",
         "-c:v","libx264","-preset","fast","-crf","23",
         "-c:a","aac","-b:a","128k",
         "-pix_fmt","yuv420p","-movflags","+faststart","-shortest",out]
    r=subprocess.run(cmd,capture_output=True,text=True)
    if r.returncode!=0: raise RuntimeError(f"FFmpeg: {r.stderr[-300:]}")
    return out

def split_video(path):
    mb=os.path.getsize(path)/1024/1024
    if mb<=49: return [path]
    probe=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=noprint_wrappers=1:nokey=1",path],capture_output=True,text=True)
    dur=float(probe.stdout.strip()); n=math.ceil(mb/49); seg=dur/n; parts=[]
    for i in range(n):
        out=tempfile.mktemp(suffix=f"_p{i+1}.mp4")
        subprocess.run(["ffmpeg","-y","-loglevel","error",
            "-ss",str(i*seg),"-t",str(seg),"-i",path,"-c","copy",out],check=True)
        parts.append(out)
    return parts

# ═══════════════════════════════════════════════════════════════
#  PRODUCERS
# ═══════════════════════════════════════════════════════════════
async def _act(ctx, cid, action):
    try: await ctx.bot.send_chat_action(cid, action)
    except: pass

async def do_audio(update, ctx, text):
    from telegram.constants import ChatAction, ParseMode
    cid=update.effective_chat.id; s=session(update.effective_user.id)
    v=VOICES.get(s["voice"],VOICES["female"])
    st=await update.message.reply_text(
        f"⏳ *تحويل الصوت…*\n📝 {len(text):,} حرف\n{v['gender']} {v['name']}",
        parse_mode=ParseMode.MARKDOWN)
    ap=None
    try:
        await _act(ctx,cid,ChatAction.RECORD_VOICE)
        async def prog(cur,tot):
            if tot>1: await st.edit_text(f"⏳ {cur}/{tot} مقطع…")
        ap=await text_to_speech(text, s["voice"], progress_cb=prog)
        dur=audio_dur(ap)
        await st.edit_text(f"⬆️ رفع… ({fmt_dur(dur)})")
        await _act(ctx,cid,ChatAction.UPLOAD_VOICE)
        with open(ap,"rb") as f:
            await update.message.reply_audio(f, title=truncate(text,55),
                performer="وكالة طارق نور", duration=int(dur),
                caption=f"🎙️ *{v['name']}*  |  ⏱ {fmt_dur(dur)}  |  📝 {len(text):,} حرف",
                parse_mode=ParseMode.MARKDOWN)
        await st.delete()
    except Exception as e:
        log.exception("audio"); await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:200]}`",parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ap)

async def do_image(update, ctx, prompt):
    from telegram.constants import ChatAction, ParseMode
    cid=update.effective_chat.id
    st=await update.message.reply_text(f"🎨 *توليد الصورة…*\n`{truncate(prompt,60)}`",parse_mode=ParseMode.MARKDOWN)
    ip=None
    try:
        await _act(ctx,cid,ChatAction.UPLOAD_PHOTO)
        en=await ai_img_prompt(prompt)
        ip=await gen_image(en)
        with open(ip,"rb") as f:
            await update.message.reply_photo(f,
                caption=f"🖼️ *وكالة طارق نور*\n📌 {truncate(prompt,80)}",parse_mode=ParseMode.MARKDOWN)
        await st.delete()
    except Exception as e:
        log.exception("image"); await st.edit_text(f"❌ *فشل:*\n`{str(e)[:200]}`",parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ip)

async def do_video(update, ctx, raw):
    from telegram.constants import ChatAction, ParseMode
    cid=update.effective_chat.id; s=session(update.effective_user.id)
    title,content=pipe(raw)
    if not content: content=raw
    st=await update.message.reply_text(f"🎬 *إنتاج الفيديو…*\n1️⃣ تحويل الصوت\n📝 {len(content):,} حرف",parse_mode=ParseMode.MARKDOWN)
    ap=ip=fp=vp=None
    try:
        await _act(ctx,cid,ChatAction.RECORD_VIDEO)
        ap=await text_to_speech(content, s["voice"])
        dur=audio_dur(ap)
        await st.edit_text(f"2️⃣ *توليد الخلفية…*\n⏱ {fmt_dur(dur)}",parse_mode=ParseMode.MARKDOWN)
        try:
            en=await ai_img_prompt(title or content[:100])
            ip=await gen_image(en)
        except Exception as e: log.warning(f"BG: {e}"); ip=None
        await st.edit_text("3️⃣ *رسم الإطار…*",parse_mode=ParseMode.MARKDOWN)
        fp=await asyncio.get_event_loop().run_in_executor(None,build_frame,content,title,s["style"],ip)
        await st.edit_text("4️⃣ *تجميع الفيديو…*",parse_mode=ParseMode.MARKDOWN)
        vp=await asyncio.get_event_loop().run_in_executor(None,render_video,ap,fp,s["style"])
        await st.edit_text(f"⬆️ رفع… ({fmt_mb(vp)})")
        await _act(ctx,cid,ChatAction.UPLOAD_VIDEO)
        parts=split_video(vp)
        for i,part in enumerate(parts):
            cap=(f"🎬 *{title or 'وكالة طارق نور'}*"+(f" — جزء {i+1}/{len(parts)}" if len(parts)>1 else "")+
                 f"\n⏱ {fmt_dur(dur)}  |  🎨 {STYLES[s['style']]['name']}")
            with open(part,"rb") as f:
                await update.message.reply_video(f,caption=cap,parse_mode=ParseMode.MARKDOWN,supports_streaming=True)
            if part!=vp: safe_del(part)
        await st.delete()
    except Exception as e:
        log.exception("video"); await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:300]}`",parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ap,ip,fp,vp)

async def do_podcast(update, ctx, raw):
    from telegram.constants import ChatAction, ParseMode
    cid=update.effective_chat.id; s=session(update.effective_user.id)
    title,content=pipe(raw); title=title or "حلقة جديدة"
    st=await update.message.reply_text(f"🎧 *إنتاج البودكاست…*\n📌 {title}",parse_mode=ParseMode.MARKDOWN)
    ap=None
    try:
        await _act(ctx,cid,ChatAction.RECORD_VOICE)
        full=f"أهلاً وسهلاً في وكالة طارق نور. حلقتنا اليوم: {title}.\n\n{content}\n\nشكراً لمتابعتكم وكالة طارق نور."
        ap=await text_to_speech(full, s["voice"])
        dur=audio_dur(ap)
        await st.edit_text(f"⬆️ رفع… ({fmt_dur(dur)})")
        await _act(ctx,cid,ChatAction.UPLOAD_VOICE)
        with open(ap,"rb") as f:
            await update.message.reply_audio(f,title=title,performer="وكالة طارق نور — Podcast",
                duration=int(dur),caption=f"🎧 *{title}*\n⏱ {fmt_dur(dur)}  |  📝 {len(content):,} حرف",
                parse_mode=ParseMode.MARKDOWN)
        await st.delete()
    except Exception as e:
        log.exception("podcast"); await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:200]}`",parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ap)

async def do_content(update, ctx, raw):
    from telegram.constants import ParseMode
    st=await update.message.reply_text("✍️ *جارٍ كتابة المحتوى…*",parse_mode=ParseMode.MARKDOWN)
    try:
        if raw.startswith("سكريبت:"): kind,topic="script",raw.split(":",1)[1].strip()
        elif raw.startswith("بوست:"): kind,topic="post",raw.split(":",1)[1].strip()
        elif raw.startswith("مقال:"): kind,topic="article",raw.split(":",1)[1].strip()
        else: kind,topic="article",raw
        result=await ai_write(topic, kind)
        await st.delete()
        full=f"*✍️ وكالة طارق نور*\n\n{result}"
        for i in range(0,len(full),4000):
            await update.message.reply_text(full[i:i+4000],parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.exception("content"); await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:200]}`",parse_mode=ParseMode.MARKDOWN)

async def do_news(update, ctx, raw):
    from telegram.constants import ChatAction, ParseMode
    cid=update.effective_chat.id; s=session(update.effective_user.id)
    headline,body=pipe(raw)
    if not headline: headline=truncate(body,60)
    st=await update.message.reply_text(f"📰 *إنتاج الخبر…*\n📌 {headline}",parse_mode=ParseMode.MARKDOWN)
    ap=fp=vp=None
    try:
        await st.edit_text("1️⃣ كتابة النشرة…",parse_mode=ParseMode.MARKDOWN)
        bulletin=await ai_write(f"{headline}. {body}", "news")
        await st.edit_text("2️⃣ تحويل الصوت…",parse_mode=ParseMode.MARKDOWN)
        await _act(ctx,cid,ChatAction.RECORD_VIDEO)
        ap=await text_to_speech(bulletin, s["voice"])
        dur=audio_dur(ap)
        await st.edit_text("3️⃣ بناء الكارت…",parse_mode=ParseMode.MARKDOWN)
        fp=await asyncio.get_event_loop().run_in_executor(None,build_news_frame,headline,bulletin)
        await st.edit_text("4️⃣ تجميع الفيديو…",parse_mode=ParseMode.MARKDOWN)
        vp=await asyncio.get_event_loop().run_in_executor(None,render_video,ap,fp,"news")
        await st.edit_text(f"⬆️ رفع… ({fmt_mb(vp)})")
        await _act(ctx,cid,ChatAction.UPLOAD_VIDEO)
        with open(vp,"rb") as f:
            await update.message.reply_video(f,
                caption=f"📰 *عاجل | {headline}*\n\n{bulletin[:300]}",
                parse_mode=ParseMode.MARKDOWN,supports_streaming=True)
        await st.delete()
    except Exception as e:
        log.exception("news"); await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:300]}`",parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ap,fp,vp)

async def do_story(update, ctx, raw):
    from telegram.constants import ChatAction, ParseMode
    cid=update.effective_chat.id; s=session(update.effective_user.id)
    title,content=pipe(raw)
    if not content: content=raw
    st=await update.message.reply_text("📱 *إنتاج الستوري…*",parse_mode=ParseMode.MARKDOWN)
    ap=ip=fp=vp=None
    try:
        await _act(ctx,cid,ChatAction.RECORD_VIDEO)
        ap=await text_to_speech(content, s["voice"])
        dur=audio_dur(ap)
        await st.edit_text("🎨 توليد الخلفية…",parse_mode=ParseMode.MARKDOWN)
        try:
            en=await ai_img_prompt(title or content[:80])
            ip=await gen_portrait(en)
        except: ip=None
        fp=await asyncio.get_event_loop().run_in_executor(None,build_story_frame,content,title,s["style"],ip)
        await st.edit_text("🎬 تجميع…",parse_mode=ParseMode.MARKDOWN)
        vp=await asyncio.get_event_loop().run_in_executor(None,render_video,ap,fp,s["style"],1080,1920)
        await st.edit_text(f"⬆️ رفع… ({fmt_mb(vp)})")
        await _act(ctx,cid,ChatAction.UPLOAD_VIDEO)
        with open(vp,"rb") as f:
            await update.message.reply_video(f,
                caption=f"📱 *{title or 'وكالة طارق نور'}*\n⏱ {fmt_dur(dur)}",
                parse_mode=ParseMode.MARKDOWN,supports_streaming=True)
        await st.delete()
    except Exception as e:
        log.exception("story"); await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:300]}`",parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ap,ip,fp,vp)

async def do_post(update, ctx, topic):
    from telegram.constants import ChatAction, ParseMode
    cid=update.effective_chat.id
    st=await update.message.reply_text(f"📣 *إنتاج البوست…*\n📌 {truncate(topic,50)}",parse_mode=ParseMode.MARKDOWN)
    ip=None
    try:
        await _act(ctx,cid,ChatAction.UPLOAD_PHOTO)
        post_task=asyncio.create_task(ai_write(topic,"post"))
        en=await ai_img_prompt(topic)
        img_task=asyncio.create_task(gen_square(en))
        post_text=await post_task
        try: ip=await img_task
        except Exception as e: log.warning(f"Post img: {e}"); ip=None
        await st.delete()
        if ip and os.path.exists(ip):
            with open(ip,"rb") as f:
                await update.message.reply_photo(f,
                    caption=f"📣 *وكالة طارق نور*\n\n{post_text}",parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"📣 *وكالة طارق نور*\n\n{post_text}",parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.exception("post"); await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:200]}`",parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ip)

async def do_pdf(update, ctx, pdf_path, filename):
    from telegram.constants import ChatAction, ParseMode
    cid=update.effective_chat.id; s=session(update.effective_user.id)
    st=await update.message.reply_text(f"📄 *استخراج النص…*\n📁 {filename}",parse_mode=ParseMode.MARKDOWN)
    ap=None
    try:
        import pdfplumber
        pages=[]
        with pdfplumber.open(pdf_path) as pdf:
            total=len(pdf.pages)
            await st.edit_text(f"📖 استخراج {total} صفحة…",parse_mode=ParseMode.MARKDOWN)
            for pg in pdf.pages:
                t=pg.extract_text()
                if t and t.strip(): pages.append(t.strip())
        full="\n\n".join(pages)
        if not full.strip(): await st.edit_text("⚠️ لم يُعثر على نص."); return
        await st.edit_text(f"🎙️ تحويل {len(full):,} حرف…\n⏳ قد يأخذ وقتاً",parse_mode=ParseMode.MARKDOWN)
        await _act(ctx,cid,ChatAction.RECORD_VOICE)
        async def prog(cur,tot):
            if cur%5==0 or cur==tot: await st.edit_text(f"🎙️ {cur}/{tot} مقطع…",parse_mode=ParseMode.MARKDOWN)
        ap=await text_to_speech(full, s["voice"], progress_cb=prog)
        dur=audio_dur(ap)
        name=filename.rsplit(".",1)[0]
        await st.edit_text(f"⬆️ رفع الكتاب الصوتي… ({fmt_dur(dur)})")
        await _act(ctx,cid,ChatAction.UPLOAD_VOICE)
        with open(ap,"rb") as f:
            await update.message.reply_audio(f,title=name,performer="وكالة طارق نور — Audiobook",
                duration=int(dur),
                caption=f"📚 *{name}*\n⏱ {fmt_dur(dur)}  |  📄 {total} صفحة  |  📝 {len(full):,} حرف",
                parse_mode=ParseMode.MARKDOWN)
        await st.delete()
    except ImportError: await st.edit_text("❌ pdfplumber غير مثبت")
    except Exception as e:
        log.exception("pdf"); await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:300]}`",parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(ap)

async def do_brain(update, ctx, mode, text):
    from telegram.constants import ChatAction, ParseMode
    await _act(ctx,update.effective_chat.id,ChatAction.TYPING)
    uid=update.effective_user.id
    st=await update.message.reply_text("🧠 نور بيفكر…")
    try:
        if mode=="chat": result=await ai_chat(uid,text); header="🤖 *نور:*"
        elif mode=="ideas": result=await ai_brainstorm(text); header="💡 *أفكار محتوى*"
        elif mode=="titles": result=await ai_titles(text); header="🔥 *عناوين جذابة*"
        elif mode=="trending": result=await ai_trending(text); header="📈 *مواضيع تريند*"
        elif mode=="strategy":
            parts=text.split("|",1); ch=parts[0].strip(); goal=parts[1].strip() if len(parts)>1 else "النمو"
            result=await ai_strategy(ch,goal); header="📋 *استراتيجية*"
        elif mode=="improve":
            parts=text.split("|",1); goal=parts[0].strip() if len(parts)>1 else "عام"; body=parts[1].strip() if len(parts)>1 else text
            result=await ai_improve(body,goal); header="✏️ *نص محسّن*"
        else: result=await ai_chat(uid,text); header="🤖 *نور:*"
        await st.delete()
        full=f"{header}\n\n{result}"
        for i in range(0,len(full),4000):
            await update.message.reply_text(full[i:i+4000],parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.exception("brain"); await st.edit_text(f"❌ *خطأ:*\n`{str(e)[:200]}`",parse_mode=ParseMode.MARKDOWN)

# ═══════════════════════════════════════════════════════════════
#  KEYBOARDS & MENUS
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
         InlineKeyboardButton("🔥 عناوين",callback_data="M_titles"),
         InlineKeyboardButton("📈 تريند",callback_data="M_trending")],
        [InlineKeyboardButton("✏️ تحسين",callback_data="M_improve"),
         InlineKeyboardButton("📋 استراتيجية",callback_data="M_strategy"),
         InlineKeyboardButton("⚙️ إعدادات",callback_data="M_settings"),
         InlineKeyboardButton("📊 حالة",callback_data="M_status")],
    ])

def back_btn():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ القائمة",callback_data="M_back")]])

MODE_HINTS = {
    "audio":"أرسل النص","video":"أرسل: العنوان | النص","image":"صف الصورة",
    "content":"مقال/سكريبت/بوست: الموضوع","podcast":"العنوان | المحتوى",
    "news":"العنوان | الخبر","story":"العنوان | النص","post":"أرسل الموضوع",
    "chat":"اكتب سؤالك","ideas":"اكتب الموضوع","titles":"اكتب الموضوع",
    "trending":"اكتب المجال","improve":"النص (أو: يوتيوب | النص)","strategy":"القناة | الهدف",
}

PROD_MODES = {"audio","video","image","content","podcast","news","story","post"}
BRAIN_MODES = {"chat","ideas","titles","trending","improve","strategy"}

# ═══════════════════════════════════════════════════════════════
#  HANDLERS
# ═══════════════════════════════════════════════════════════════
async def on_start(update, ctx):
    from telegram.constants import ParseMode
    u=update.effective_user
    await update.message.reply_text(
        f"🎬 *أهلاً {u.first_name}! وكالة طارق نور*\n\n"
        "ستوديو إنتاج إعلامي مصري متكامل ومجاني\n\n"
        "اختر الوضع أو أرسل النص مباشرة 👇",
        parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())

async def on_help(update, ctx):
    from telegram.constants import ParseMode
    await update.message.reply_text(
        "📖 *وكالة طارق نور*\n\n"
        "*إنتاج:* `/audio` `/video` `/image` `/content`\n"
        "`/podcast` `/news` `/story` `/post`\n\n"
        "*ذكاء:* `/nour` `/ideas` `/titles` `/trending`\n"
        "`/improve` `/strategy` `/reset`\n\n"
        "*PDF:* أرسل ملف → كتاب صوتي\n"
        "*الصيغة:* `العنوان | المحتوى`\n\n"
        f"*TTS:* {'HuggingFace Neural ✅' if HF_TOKEN else 'gTTS'}\n"
        f"*AI:* {'OpenRouter ✅' if OR_KEY else 'Groq' if GROQ_KEY else '⚠️ غير مفعل'}",
        parse_mode=ParseMode.MARKDOWN)

async def on_status(update, ctx):
    from telegram.constants import ParseMode
    ffok=check_ffmpeg()
    await update.message.reply_text(
        f"📊 *حالة الخدمات*\n\n"
        f"🎙️ HF TTS: {'✅' if HF_TOKEN else '⚠️ gTTS فقط'}\n"
        f"🤖 AI: {'✅ OpenRouter' if OR_KEY else '✅ Groq' if GROQ_KEY else '❌'}\n"
        f"🖼️ Pollinations: ✅\n"
        f"🎬 FFmpeg: {'✅' if ffok else '❌'}\n"
        f"👥 مستخدمون: {len(_sessions)}",
        parse_mode=ParseMode.MARKDOWN)

async def on_text(update, ctx):
    text=(update.message.text or "").strip()
    if not text: return
    uid=update.effective_user.id; s=session(uid); mode=s.get("mode","audio")
    dispatch_prod={"audio":do_audio,"video":do_video,"image":do_image,"content":do_content,
                   "podcast":do_podcast,"news":do_news,"story":do_story,"post":do_post}
    if mode in dispatch_prod: await dispatch_prod[mode](update,ctx,text)
    elif mode in BRAIN_MODES: await do_brain(update,ctx,mode,text)
    else:
        if text.endswith("?") or text.endswith("؟") or len(text)<80:
            await do_brain(update,ctx,"chat",text)
        else:
            await do_audio(update,ctx,text)

async def on_document(update, ctx):
    from telegram.constants import ParseMode
    doc=update.message.document; name=doc.file_name or "file"
    if not name.lower().endswith(".pdf"):
        await update.message.reply_text("📎 أرسل PDF فقط → كتاب صوتي 📚"); return
    if (doc.file_size or 0)/1024/1024 > 50:
        await update.message.reply_text("⚠️ الملف أكبر من 50 MB"); return
    st=await update.message.reply_text(f"📥 *تحميل…*\n📁 {name}",parse_mode=ParseMode.MARKDOWN)
    tmp=None
    try:
        file_obj=await ctx.bot.get_file(doc.file_id)
        tmp=tempfile.mktemp(suffix=".pdf")
        await file_obj.download_to_drive(tmp)
        await st.delete()
        await do_pdf(update,ctx,tmp,name)
    except Exception as e:
        await st.edit_text(f"❌ فشل: `{e}`",parse_mode=ParseMode.MARKDOWN)
    finally: safe_del(tmp)

async def on_callback(update, ctx):
    from telegram.constants import ParseMode
    q=update.callback_query; uid=q.from_user.id; d=q.data
    await q.answer(); s=session(uid)

    if d=="M_back":
        await q.edit_message_text("🎬 *وكالة طارق نور*\nاختر الوضع أو أرسل النص 👇",
            parse_mode=ParseMode.MARKDOWN,reply_markup=main_menu()); return

    if d=="M_status":
        ffok=check_ffmpeg()
        await q.edit_message_text(
            f"📊 *الحالة*\n🎙️ HF TTS: {'✅' if HF_TOKEN else '⚠️ gTTS'}\n"
            f"🤖 AI: {'✅ OpenRouter' if OR_KEY else '✅ Groq' if GROQ_KEY else '❌'}\n"
            f"🖼️ Pollinations: ✅\n🎬 FFmpeg: {'✅' if ffok else '❌'}\n👥 {len(_sessions)}",
            parse_mode=ParseMode.MARKDOWN,reply_markup=back_btn()); return

    if d=="M_settings":
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        v=VOICES.get(s["voice"],VOICES["female"]); st2=STYLES.get(s["style"],STYLES["gold"])
        await q.edit_message_text(
            f"⚙️ *الإعدادات*\nالصوت: {v['gender']} `{v['name']}`\nالنمط: `{st2['name']}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎤 الصوت",callback_data="SET_voice")],
                [InlineKeyboardButton("🎨 النمط",callback_data="SET_style")],
                [InlineKeyboardButton("↩️",callback_data="M_back")]])); return

    if d.startswith("M_"):
        mode=d[2:]; s["mode"]=mode
        hint=MODE_HINTS.get(mode,"أرسل النص")
        await q.edit_message_text(f"*وضع {mode.upper()}*\n\n{hint} 👇",
            parse_mode=ParseMode.MARKDOWN,reply_markup=back_btn()); return

    if d=="SET_voice":
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        await q.edit_message_text("اختر الصوت:",reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"{v['gender']} {v['name']}",callback_data=f"V_{k}")]
             for k,v in VOICES.items()]+[[InlineKeyboardButton("↩️",callback_data="M_settings")]])); return
    if d.startswith("V_"):
        key=d[2:]; s["voice"]=key; v=VOICES.get(key,VOICES["female"])
        await q.edit_message_text(f"✅ الصوت: *{v['gender']} {v['name']}*",
            parse_mode=ParseMode.MARKDOWN,reply_markup=back_btn()); return
    if d=="SET_style":
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        await q.edit_message_text("اختر النمط:",reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(st2["name"],callback_data=f"S_{k}")]
             for k,st2 in STYLES.items()]+[[InlineKeyboardButton("↩️",callback_data="M_settings")]])); return
    if d.startswith("S_"):
        key=d[2:]; s["style"]=key; st2=STYLES.get(key,STYLES["gold"])
        await q.edit_message_text(f"✅ النمط: *{st2['name']}*",
            parse_mode=ParseMode.MARKDOWN,reply_markup=back_btn()); return

# Brain commands
async def on_nour(u,c):
    text=" ".join(c.args).strip() if c.args else ""
    if text: await do_brain(u,c,"chat",text)
    else: await u.message.reply_text("🤖 اكتب سؤالك بعد `/nour`\nأو اختر وضع النقاش من القائمة.",parse_mode="Markdown")

async def _bc(update,ctx,mode,need=True):
    text=" ".join(ctx.args).strip() if ctx.args else ""
    if need and not text:
        await update.message.reply_text(f"أرسل: `/{mode} النص`",parse_mode="Markdown"); return
    await do_brain(update,ctx,mode,text or "مصر عامة")

async def on_ideas(u,c):    await _bc(u,c,"ideas")
async def on_titles(u,c):   await _bc(u,c,"titles")
async def on_trending(u,c): await _bc(u,c,"trending",need=False)
async def on_improve(u,c):  await _bc(u,c,"improve")
async def on_strategy(u,c): await _bc(u,c,"strategy")
async def on_reset(u,c):
    clear_history(u.effective_user.id)
    await u.message.reply_text("🗑️ تم مسح سجل المحادثة.")

async def _mode_cmd(update,ctx,mode,fn=None):
    session(update.effective_user.id)["mode"]=mode
    text=" ".join(ctx.args).strip() if ctx.args else ""
    if text and fn: await fn(update,ctx,text)
    else: await update.message.reply_text(
        f"*وضع {mode.upper()}*\n{MODE_HINTS.get(mode,'أرسل النص')} 👇",parse_mode="Markdown")

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
        BotCommand("audio","🎙️ نص → صوت"),BotCommand("video","🎬 نص → فيديو"),
        BotCommand("image","🖼️ وصف → صورة"),BotCommand("content","✍️ موضوع → محتوى"),
        BotCommand("podcast","🎧 نص → بودكاست"),BotCommand("news","📰 خبر → فيديو"),
        BotCommand("story","📱 نص → ستوري"),BotCommand("post","📣 موضوع → بوست"),
        BotCommand("nour","🤖 نقاش مع AI مصري"),BotCommand("ideas","💡 أفكار محتوى"),
        BotCommand("titles","🔥 عناوين جذابة"),BotCommand("trending","📈 تريند مصري"),
        BotCommand("improve","✏️ تحسين نص"),BotCommand("strategy","📋 استراتيجية"),
        BotCommand("reset","🗑️ مسح النقاش"),BotCommand("status","📊 الحالة"),
        BotCommand("help","📖 المساعدة"),BotCommand("start","🏠 البداية"),
    ])
    ensure_font()
    log.info(f"✅ وكالة طارق نور — HF_TTS={'✓' if HF_TOKEN else '✗'} OR={'✓' if OR_KEY else '✗'}")

def main():
    token=(os.environ.get("BOT_TOKEN") or "").strip()
    if not token:
        log.error(f"BOT_TOKEN missing. Vars: {sorted(os.environ.keys())}")
        time.sleep(3); sys.exit(1)
    global BOT_TOKEN, HF_TOKEN, OR_KEY, GROQ_KEY
    BOT_TOKEN = token
    HF_TOKEN  = (os.environ.get("HF_TOKEN") or "").strip()
    OR_KEY    = (os.environ.get("OPENROUTER_KEY") or "").strip()
    GROQ_KEY  = (os.environ.get("GROQ_API_KEY") or "").strip()

    from telegram.ext import (Application,CommandHandler,MessageHandler,
                               CallbackQueryHandler,filters)
    app=(Application.builder().token(BOT_TOKEN).post_init(post_init)
         .read_timeout(60).write_timeout(120).connect_timeout(30).pool_timeout(60).build())

    for cmd,fn in [
        ("start",on_start),("help",on_help),("status",on_status),
        ("audio",on_audio),("video",on_video),("image",on_image),("content",on_content),
        ("podcast",on_podcast),("news",on_news),("story",on_story),("post",on_post),
        ("nour",on_nour),("ideas",on_ideas),("titles",on_titles),("trending",on_trending),
        ("improve",on_improve),("strategy",on_strategy),("reset",on_reset),
    ]:
        app.add_handler(CommandHandler(cmd,fn))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Document.ALL,on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,on_text))

    log.info("🚀 وكالة طارق نور تنطلق…")
    app.run_polling(drop_pending_updates=True,allowed_updates=["message","callback_query"])

if __name__=="__main__":
    main()
