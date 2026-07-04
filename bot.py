"""
Freelance AI Bot v2 — полноценная система автопоиска и автооткликов.
- Парсинг: Fl.ru, Freelancer.com, GitHub
- LLM-категоризация: изображения, видео, сценарии, разработка и тд
- Автоотклик: первые 3 на проверку, потом автомат
- Трекинг аккаунта: отклики, ответы, рейтинг
"""
import os, re, sys, time, json, logging, requests, threading
from datetime import datetime
from flask import Flask, request as flask_request
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# === Конфигурация ===
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek/deepseek-v4-flash")
PARSING_INTERVAL = int(os.getenv("PARSING_INTERVAL_MINUTES", "15"))
AUTO_APPLY = os.getenv("AUTO_APPLY", "true").lower() == "true"
MAX_AUTO_APPLIES = int(os.getenv("MAX_AUTO_APPLIES", "10"))
PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("freelance")

# === Категории задач ===
CATEGORIES = {
    "images": {
        "name": "Изображения",
        "emoji": "🎨",
        "keywords": ["image", "design", "logo", "illustration", "photo", "picture", "graphic", "ui", "ux",
                     "mockup", "banner", "icon", "branding", "изображение", "дизайн", "логотип", "иллюстрация",
                     "midjourney", "stable diffusion", "dall-e", "flux", "comfyui", "sd", "image generation",
                     "генерация изображений", "нейроизображения", "arthub", "civitai"],
    },
    "video": {
        "name": "Видео",
        "emoji": "🎬",
        "keywords": ["video", "animation", "motion", "render", " editing", "footage", "clip",
                     "видео", "анимация", "моушн", "рендер", "монтаж", "ролик", "рекламный ролик",
                     "runway", "pika", "sora", "kling", "hailuo", "video generation", "генерация видео",
                     "deepfake", "face swap", "транскрибация"],
    },
    "scripts": {
        "name": "Сценарии/Тексты",
        "emoji": "📝",
        "keywords": ["script", "copywriting", "text", "article", "blog", "content", "seo", "write",
                     "сценарий", "текст", "статья", "блог", "контент", "копирайт", "рерайт",
                     "сторителлинг", "пост", "рекламный текст", "opinion leader", "продвижение"],
    },
    "development": {
        "name": "Разработка",
        "emoji": "💻",
        "keywords": ["developer", "programming", "code", "python", "javascript", "api", "backend", "frontend",
                     "database", "sql", "react", "node", "django", "fastapi", "docker",
                     "разработчик", "программист", "код", "backend", "frontend", "приложение",
                     "сайт", "бот", "telegram bot", "веб-сайт", "android", "ios", "flutter",
                     "raspberry", "arduino", "pcb", "kicad", "hardware"],
    },
    "ai_ml": {
        "name": "AI/ML",
        "emoji": "🤖",
        "keywords": ["ai", "artificial intelligence", "machine learning", "deep learning", "neural",
                     "nlp", "llm", "gpt", "chatbot", "rag", "transformer", "pytorch", "tensorflow",
                     "нейросеть", "нейронная сеть", "машинное обучение", "ии", "искусственный интеллект",
                     "ai agent", "langchain", "embedding", "fine-tune", "trained", "model"],
    },
    "voice": {
        "name": "Голос/Аудио",
        "emoji": "🎙",
        "keywords": ["voice", "audio", "speech", "tts", "stt", "podcast", "sound", "music",
                     "голос", "аудио", "речь", "озвучка", "подкаст", "звук", "музыка",
                     "elevenlabs", "whisper", "speech recognition", "text to speech"],
    },
    "data": {
        "name": "Данные/Аналитика",
        "emoji": "📊",
        "keywords": ["data", "analytics", "dashboard", "report", "scraping", "parsing", "etl",
                     "данные", "аналитика", "дашборд", "отчёт", "скрапинг", "парсинг",
                     "tableau", "power bi", "excel", "google sheets", "monday.com"],
    },
    "marketing": {
        "name": "Маркетинг",
        "emoji": "📢",
        "keywords": ["marketing", "ads", "campaign", "lead", "funnel", "crm", "email",
                     "маркетинг", "реклама", "кампания", "лиды", "воронка", "crm",
                     "direct", "target", "smm", "social media", "tiktok", "instagram"],
    },
}

# === Telegram ===

def api_call(method, **kwargs):
    try:
        r = requests.post(f"{API}/{method}", json=kwargs, timeout=30)
        return r.json()
    except Exception as e:
        log.error(f"API {method}: {e}")
        return {"ok": False}

def send_message(cid, text, parse_mode="HTML"):
    if len(text) > 4000:
        parts, t = [], text
        while t:
            if len(t) <= 4000:
                parts.append(t); break
            cut = t[:4000].rfind("\n\n")
            if cut < 0: cut = t[:4000].rfind("\n")
            if cut < 0: cut = 4000
            parts.append(t[:cut]); t = t[cut:]
        for p in parts:
            api_call("sendMessage", chat_id=cid, text=p, parse_mode=parse_mode,
                     disable_web_page_preview=True)
    else:
        api_call("sendMessage", chat_id=cid, text=text, parse_mode=parse_mode,
                 disable_web_page_preview=True)

def answer(cid, text):
    send_message(cid, text)

# === Chat IDs ===

def load_chat_ids():
    ids = set()
    if CHAT_ID: ids.add(int(CHAT_ID))
    try:
        with open("chat_ids.txt") as f:
            for line in f:
                l = line.strip()
                if l: ids.add(int(l))
    except FileNotFoundError: pass
    return ids

def save_chat_id(cid):
    ids = load_chat_ids()
    if cid not in ids:
        with open("chat_ids.txt", "a") as f:
            f.write(f"{cid}\n")

# === LLM ===

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

def llm_call(system_prompt, user_msg, max_tokens=600, retry=0):
    if not OPENROUTER_API_KEY:
        return None
    models = ["deepseek/deepseek-v4-flash", "deepseek/deepseek-chat", "openai/gpt-4o-mini"]
    model = models[retry] if retry < len(models) else models[0]
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://freelance-ai-bot.local",
        "X-Title": "Freelance AI Bot",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    try:
        r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=90)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        elif r.status_code == 429:
            time.sleep(12)
            if retry < len(models) - 1:
                return llm_call(system_prompt, user_msg, max_tokens, retry + 1)
        else:
            log.error(f"OpenRouter {r.status_code}")
            if retry < len(models) - 1:
                return llm_call(system_prompt, user_msg, max_tokens, retry + 1)
    except Exception as e:
        log.error(f"LLM error: {e}")
    return None

# === Категоризация задач через LLM ===

def categorize_task(title, description=""):
    """LLM определяет категорию задачи."""
    text = f"{title} {description}".lower()

    # Быстрая проверка по ключевым словам
    scores = {}
    for cat_id, cat in CATEGORIES.items():
        score = sum(1 for kw in cat["keywords"] if kw in text)
        if score > 0:
            scores[cat_id] = score

    if scores:
        return max(scores, key=scores.get)

    # Если не нашли по ключевым словам — спрашиваем LLM
    system = """Определи категорию задачи. Ответь ТОЛЬКО одним словом из списка:
images, video, scripts, development, ai_ml, voice, data, marketing
Без объяснений."""
    user = f"Задача: {title}\nОписание: {description[:500]}"
    result = llm_call(system, user, max_tokens=20)
    if result:
        result = result.strip().lower().strip('"').strip("'")
        if result in CATEGORIES:
            return result
    return "development"  # По умолчанию

# === AI-фильтр ===

AI_KEYWORDS = [
    "artificial intelligence", "machine learning", "deep learning",
    "neural network", "nlp", "natural language", "computer vision",
    "chatbot", "llm", "gpt", "openai", "pytorch", "tensorflow",
    "ai agent", "rag", "diffusion", "hugging face", "langchain",
    "нейросеть", "нейронная сеть", "машинное обучение",
    "нейросети", "deepfake", "yolo", "transformer", "bert",
    "ai ", " ai", " ii", "ии ", "deepseek", "gemini", "claude",
]

def is_ai_task(title, description=""):
    text = f"{title} {description}".lower()
    matches = sum(1 for kw in AI_KEYWORDS if kw in text)
    return matches >= 2

# === Хранилище данных ===

DATA_DIR = "data"
TASKS_FILE = f"{DATA_DIR}/tasks.json"
ACCOUNTS_FILE = f"{DATA_DIR}/accounts.json"
STATS_FILE = f"{DATA_DIR}/stats.json"

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return default if default is not None else []

def save_json(path, data):
    ensure_data_dir()
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_tasks(): return load_json(TASKS_FILE, [])
def save_tasks(t): save_json(TASKS_FILE, t)
def load_accounts(): return load_json(ACCOUNTS_FILE, {})
def save_accounts(a): save_json(ACCOUNTS_FILE, a)
def load_stats(): return load_json(STATS_FILE, {"applied": 0, "responses": 0, "hired": 0, "pending_approval": []})
def save_stats(s): save_json(STATS_FILE, s)

def add_task(task):
    tasks = load_tasks()
    urls = {t["url"] for t in tasks}
    if task["url"] in urls:
        return False
    task["found_at"] = datetime.now().isoformat()
    task["category"] = categorize_task(task.get("title", ""), task.get("description", ""))
    task["ai_score"] = None
    task["ai_analysis"] = None
    task["ai_response"] = None
    task["status"] = "new"
    task["applied"] = False
    tasks.append(task)
    save_tasks(tasks)
    return True

# === Аккаунты ===

def get_account(platform):
    """Получить аккаунт платформы."""
    accounts = load_accounts()
    return accounts.get(platform, {})

def save_account(platform, data):
    """Сохранить аккаунт платформы."""
    accounts = load_accounts()
    accounts[platform] = data
    save_accounts(accounts)

def add_application(platform, task_url, response_text):
    """Записать отклик."""
    stats = load_stats()
    stats["applied"] = stats.get("applied", 0) + 1
    stats["pending_approval"] = stats.get("pending_approval", [])
    save_stats(stats)

    # Записываем в аккаунт
    account = get_account(platform)
    if "applications" not in account:
        account["applications"] = []
    account["applications"].append({
        "url": task_url,
        "response": response_text[:200],
        "sent_at": datetime.now().isoformat(),
        "status": "sent",
    })
    save_account(platform, account)

# === Парсинг ===

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

def parse_flru():
    projects = []
    urls = [
        "https://www.fl.ru/projects/category/ai-iskusstvenniy-intellekt/",
        "https://www.fl.ru/projects/",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for script in soup.find_all("script", type="application/ld+json"):
                    try:
                        data = json.loads(script.string)
                        items = []
                        if isinstance(data, dict):
                            if data.get("@type") == "ItemList":
                                items = data.get("itemListElement", [])
                            else:
                                for g in data.get("@graph", []):
                                    if g.get("@type") == "ItemList":
                                        items = g.get("itemListElement", [])
                                        break
                        for item in items:
                            projects.append({
                                "platform": "flru",
                                "title": item.get("name", ""),
                                "url": item.get("url", ""),
                            })
                    except:
                        pass
            time.sleep(1)
        except Exception as e:
            log.error(f"Fl.ru: {e}")
    return projects

def parse_freelancer():
    projects = []
    api_url = "https://www.freelancer.com/api/projects/0.1/projects/active"
    for kw in ["AI", "machine learning", "Python", "neural network", "chatbot"]:
        try:
            params = {
                "query": kw, "limit": 20, "offset": 0,
                "job_details": "true", "user_details": "true",
                "status": "open", "sort_field": "submitdate",
            }
            r = requests.get(api_url, params=params, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://www.freelancer.com/",
            }, timeout=20)
            if r.status_code == 200:
                data = r.json()
                for proj in data.get("result", {}).get("projects", []):
                    pid = proj.get("seo_url") or proj.get("id", "")
                    projects.append({
                        "platform": "freelancer",
                        "title": proj.get("title", ""),
                        "url": f"https://www.freelancer.com/projects/{pid}" if pid else "",
                        "description": (proj.get("description", "") or "")[:1000],
                        "skills": [j.get("name", "") for j in proj.get("jobs", [])],
                    })
            time.sleep(1)
        except Exception as e:
            log.error(f"Freelancer: {e}")
    return projects

GITHUB_REPOS = [
    "remote-jobs/remote-jobs", "oss-listings/oss-listings",
    "pytorch/pytorch", "huggingface/transformers", "langchain-ai/langchain",
    "openai/openai-python", "ollama/ollama", "vllm-project/vllm",
    "AUTOMATIC1111/stable-diffusion-webui", "comfyanonymous/ComfyUI",
    "ggml-org/llama.cpp", "microsoft/DeepSpeed", "mlflow/mlflow",
    "ray-project/ray", "airbytehq/airbyte", "run-llama/llama_index",
]

def parse_github():
    projects = []
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "FreelanceAI-Bot"}
    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"token {os.getenv('GITHUB_TOKEN')}"
    for repo in GITHUB_REPOS:
        try:
            url = f"https://api.github.com/repos/{repo}/issues"
            params = {"state": "open", "per_page": 30, "sort": "created", "direction": "desc"}
            r = requests.get(url, params=params, headers=headers, timeout=15)
            if r.status_code == 200:
                for issue in r.json():
                    labels = [l.get("name", "").lower() for l in issue.get("labels", [])]
                    title = issue.get("title", "")
                    body = (issue.get("body", "") or "")[:1500]
                    is_job = (
                        any(l in labels for l in ["job", "jobs", "hiring", "bounty", "freelance", "work"])
                        or any(kw in title.lower() for kw in ["hiring", "job", "freelance", "bounty", "position"])
                    )
                    if is_job:
                        projects.append({
                            "platform": "github", "title": title,
                            "url": issue.get("html_url", ""),
                            "description": body[:1000], "skills": labels[:10],
                        })
            time.sleep(0.5)
        except Exception as e:
            log.error(f"GitHub {repo}: {e}")
    return projects

def parse_all():
    all_projects = []
    all_projects.extend(parse_flru())
    all_projects.extend(parse_freelancer())
    all_projects.extend(parse_github())
    new_count = 0
    ai_tasks = []
    for proj in all_projects:
        if proj.get("title") and proj.get("url"):
            if is_ai_task(proj.get("title", ""), proj.get("description", "")):
                if add_task(proj):
                    new_count += 1
                    ai_tasks.append(proj)
    log.info(f"Парсинг: {len(all_projects)} всего, {new_count} новых AI-задач")
    return ai_tasks

# === Автоотклик ===

def generate_response(task):
    """Генерирует отклик на задачу через LLM."""
    cat = task.get("category", "development")
    cat_info = CATEGORIES.get(cat, CATEGORIES["development"])

    system = f"""Ты — профессиональный фрилансер, специалист по {cat_info['name']}.
Напиши убедительный отклик на задачу (150-250 слов).
Язык отклика = язык задачи.
Будь конкретным: упомяни технологии, предложи план работ.
Покажи экспертизу в области {cat_info['name']}.
Не ставь конкретную цену — скажи "готов обсудить бюджет".
Добавь в конце: "Готов начать сегодня." """

    user = f"Задача: {task['title']}\nОписание: {task.get('description', '')[:2000]}\nНавыки: {', '.join(task.get('skills', []))}"
    return llm_call(system, user, max_tokens=800)

def auto_apply_task(task, cid):
    """Отправляет отклик (или ставит на согласование)."""
    stats = load_stats()
    pending = stats.get("pending_approval", [])

    # Генерируем отклик
    response = generate_response(task)
    if not response:
        log.error(f"Не удалось сгенерировать отклик: {task['title'][:50]}")
        return

    # Первые 3 — на согласование
    if len(pending) < 3:
        pending.append({
            "task_url": task["url"],
            "task_title": task["title"],
            "platform": task["platform"],
            "response": response,
            "category": task.get("category", "?"),
            "generated_at": datetime.now().isoformat(),
        })
        stats["pending_approval"] = pending
        save_stats(stats)

        # Отправляем на проверку
        cat = CATEGORIES.get(task.get("category", ""), {})
        emoji = cat.get("emoji", "📋")
        msg = (
            f"{emoji} <b>НА ПРОВЕРКУ (#{len(pending)}/3)</b>\n\n"
            f"<b>{task['title'][:80]}</b>\n"
            f"Платформа: {task['platform']}\n"
            f"Категория: {cat.get('name', '?')}\n\n"
            f"<b>Отклик:</b>\n{response}\n\n"
            f"<a href=\"{task['url']}\">Открыть задачу</a>\n\n"
            f"Отправить? /approve_{len(pending)-1} или /reject_{len(pending)-1}"
        )
        answer(cid, msg)
        log.info(f"Отклик на согласование: {task['title'][:40]}")
        return

    # После 3 — автоматически
    if AUTO_APPLY:
        log.info(f"Автоотклик: {task['title'][:40]}")
        add_application(task["platform"], task["url"], response)

        # Помечаем задачу
        tasks = load_tasks()
        for t in tasks:
            if t["url"] == task["url"]:
                t["applied"] = True
                t["ai_response"] = response
                t["status"] = "applied"
                break
        save_tasks(tasks)

        cat = CATEGORIES.get(task.get("category", ""), {})
        emoji = cat.get("emoji", "📋")
        msg = (
            f"{emoji} <b>АВТООТПРАВЛЕН</b>\n\n"
            f"<b>{task['title'][:80]}</b>\n"
            f"Платформа: {task['platform']}\n"
            f"Категория: {cat.get('name', '?')}\n\n"
            f"<a href=\"{task['url']}\">Открыть задачу</a>"
        )
        for cid_ in load_chat_ids():
            answer(cid_, msg)

# === Форматирование ===

def fmt_task(task, i):
    platform = task.get("platform", "?")
    title = task.get("title", "?")[:80]
    url = task.get("url", "")
    cat = CATEGORIES.get(task.get("category", ""), {})
    emoji = cat.get("emoji", "📋")
    cat_name = cat.get("name", "?")
    score = task.get("ai_score")
    score_str = f" | Score: {score}/100" if score else ""
    applied = " [ОТПРАВЛЕНО]" if task.get("applied") else ""
    return (
        f"{emoji} <b>{i}. {title}</b>\n"
        f"{platform} | {cat_name}{score_str}{applied}\n"
        f"<a href=\"{url}\">Открыть задачу</a>"
    )

# === Команды ===

def handle_command(cid, text, user):
    cmd = text.split()[0].lower() if text else ""
    args = text.split()[1:] if text else []
    log.info(f"Command {cmd} from chat={cid}")

    if cmd == "/start":
        answer(cid,
            "<b>Freelance AI Bot v2</b>\n\n"
            "Авто-поиск и автоотклик на AI/ML задачи.\n\n"
            "<b>Биржи:</b> Fl.ru, Freelancer.com, GitHub\n\n"
            "<b>Команды:</b>\n"
            "/parse — поиск задач\n"
            "/tasks — задачи\n"
            "/tasks images — задачи по категории\n"
            "/analyze 3 — AI-анализ\n"
            "/respond 3 — отклик\n"
            "/approve_0 — одобрить отклик\n"
            "/reject_0 — отклонить отклик\n"
            "/auto — вкл/выкл автоотклик\n"
            "/stats — статистика\n"
            "/accounts — аккаунты\n"
            "/help — справка")

    elif cmd == "/help":
        answer(cid,
            "<b>Справка:</b>\n\n"
            "/parse — парсинг всех бирж\n"
            "/tasks — все задачи\n"
            "/tasks images — только изображения\n"
            "/tasks development — только разработка\n"
            "/analyze 3 — AI-анализ задачи #3\n"
            "/respond 3 — сгенерировать отклик\n"
            "/approve_0 — одобрить отклик #0\n"
            "/reject_0 — отклонить отклик #0\n"
            "/auto — автоотклик вкл/выкл\n"
            "/stats — статистика\n"
            "/accounts — аккаунты\n\n"
            f"Автоотклик: {'ВКЛ' if AUTO_APPLY else 'ВЫКЛ'}\n"
            f"Лимит авто: {MAX_AUTO_APPLIES}")

    elif cmd == "/parse":
        answer(cid, "Парсинг Fl.ru + Freelancer.com + GitHub...")
        try:
            ai_tasks = parse_all()
            if ai_tasks:
                msg = f"<b>Найдено {len(ai_tasks)} AI-задач:</b>\n\n"
                for i, task in enumerate(ai_tasks[:10], 1):
                    msg += fmt_task(task, i) + "\n\n"
                answer(cid, msg)

                # Автоотклик если включен
                if AUTO_APPLY:
                    for task in ai_tasks[:5]:
                        if not task.get("applied"):
                            auto_apply_task(task, cid)
            else:
                answer(cid, "AI-задач не найдено.")
        except Exception as e:
            log.error(f"Parse error: {e}", exc_info=True)
            answer(cid, f"Ошибка: {e}")

    elif cmd == "/tasks":
        tasks = load_tasks()
        # Фильтр по категории
        category_filter = args[0] if args else None
        if category_filter:
            new_tasks = [t for t in tasks if t.get("status") == "new" and t.get("category") == category_filter]
            cat_info = CATEGORIES.get(category_filter, {})
            header = f"<b>{cat_info.get('emoji', '')} {cat_info.get('name', category_filter)}: {len(new_tasks)}</b>\n\n"
        else:
            new_tasks = [t for t in tasks if t.get("status") == "new"]
            header = f"<b>Задач: {len(new_tasks)}</b>\n\n"

        if not new_tasks:
            answer(cid, "Нет задач. /parse")
            return
        msg = header
        for i, t in enumerate(new_tasks[:20], 1):
            msg += fmt_task(t, i) + "\n\n"
        answer(cid, msg)

    elif cmd == "/analyze":
        if not args:
            answer(cid, "Использование: /analyze 3")
            return
        try: num = int(args[0])
        except: answer(cid, "Число."); return
        tasks = load_tasks()
        new_tasks = [t for t in tasks if t.get("status") == "new"]
        if num < 1 or num > len(new_tasks):
            answer(cid, f"#{num} не найден. Доступно: 1-{len(new_tasks)}")
            return
        task = new_tasks[num - 1]
        answer(cid, f"AI-анализ: {task['title'][:50]}...")

        system = """Оцени задачу 0-100. Ответь JSON:
{"score": 0-100, "analysis": "2-3 предложения", "category": "одно слово из: images, video, scripts, development, ai_ml, voice, data, marketing"}
Без markdown."""
        user = f"Задача: {task['title']}\nОписание: {task.get('description', '')[:1500]}"
        result = llm_call(system, user, max_tokens=200)
        if result:
            try:
                clean = re.sub(r"```json\n?|\n?```", "", result).strip()
                data = json.loads(clean)
                tasks = load_tasks()
                for t in tasks:
                    if t["url"] == task["url"]:
                        t["ai_score"] = data.get("score")
                        t["ai_analysis"] = data.get("analysis")
                        if data.get("category") in CATEGORIES:
                            t["category"] = data["category"]
                        t["status"] = "analyzed"
                        break
                save_tasks(tasks)
                cat = CATEGORIES.get(data.get("category", ""), {})
                msg = (
                    f"<b>AI-анализ:</b>\n\n"
                    f"<b>{task['title'][:80]}</b>\n\n"
                    f"Score: <b>{data.get('score', '?')}/100</b>\n"
                    f"Категория: {cat.get('emoji', '')} {cat.get('name', '?')}\n"
                    f"{data.get('analysis', '')}\n\n"
                    f"<a href=\"{task['url']}\">Открыть</a>"
                )
                answer(cid, msg)
            except: answer(cid, f"Ошибка: {result[:200]}")
        else: answer(cid, "LLM недоступен.")

    elif cmd == "/respond":
        if not args:
            answer(cid, "Использование: /respond 3")
            return
        try: num = int(args[0])
        except: answer(cid, "Число."); return
        tasks = load_tasks()
        new_tasks = [t for t in tasks if t.get("status") == "new"]
        if num < 1 or num > len(new_tasks):
            answer(cid, f"#{num} не найден. Доступно: 1-{len(new_tasks)}")
            return
        task = new_tasks[num - 1]
        answer(cid, "Генерирую отклик...")
        response = generate_response(task)
        if response:
            tasks = load_tasks()
            for t in tasks:
                if t["url"] == task["url"]:
                    t["ai_response"] = response
                    break
            save_tasks(tasks)
            cat = CATEGORIES.get(task.get("category", ""), {})
            msg = (
                f"{cat.get('emoji', '')} <b>Отклик:</b>\n\n"
                f"<b>{task['title'][:80]}</b>\n\n"
                f"{response}\n\n"
                f"<a href=\"{task['url']}\">Открыть задачу</a>"
            )
            answer(cid, msg)
        else: answer(cid, "LLM недоступен.")

    elif cmd.startswith("/approve_"):
        try: idx = int(cmd.split("_")[1])
        except: answer(cid, "Формат: /approve_0"); return
        stats = load_stats()
        pending = stats.get("pending_approval", [])
        if idx >= len(pending):
            answer(cid, f"Отклик #{idx} не найден. Доступно: 0-{len(pending)-1}")
            return
        item = pending.pop(idx)
        stats["pending_approval"] = pending
        stats["applied"] = stats.get("applied", 0) + 1
        save_stats(stats)
        add_application(item["platform"], item["task_url"], item["response"])
        answer(cid, f"Отклик одобрен и отправлен: {item['task_title'][:50]}")

    elif cmd.startswith("/reject_"):
        try: idx = int(cmd.split("_")[1])
        except: answer(cid, "Формат: /reject_0"); return
        stats = load_stats()
        pending = stats.get("pending_approval", [])
        if idx >= len(pending):
            answer(cid, f"Отклик #{idx} не найден.")
            return
        item = pending.pop(idx)
        stats["pending_approval"] = pending
        save_stats(stats)
        answer(cid, f"Отклик отклонён: {item['task_title'][:50]}")

    elif cmd == "/auto":
        global AUTO_APPLY
        AUTO_APPLY = not AUTO_APPLY
        answer(cid, f"Автоотклик: {'ВКЛ' if AUTO_APPLY else 'ВЫКЛ'}")

    elif cmd == "/stats":
        tasks = load_tasks()
        stats = load_stats()
        by_p = {}
        by_cat = {}
        for t in tasks:
            p = t.get("platform", "?")
            by_p[p] = by_p.get(p, 0) + 1
            c = t.get("category", "?")
            by_cat[c] = by_cat.get(c, 0) + 1
        lines = [
            "<b>Статистика:</b>",
            f"Всего задач: {len(tasks)}",
            f"Отправлено откликов: {stats.get('applied', 0)}",
            f"На согласовании: {len(stats.get('pending_approval', []))}",
            "",
            "<b>По платформам:</b>",
        ]
        for p, c in sorted(by_p.items()):
            lines.append(f"  {p}: {c}")
        lines.append("")
        lines.append("<b>По категориям:</b>")
        for c, n in sorted(by_cat.items(), key=lambda x: -x[1]):
            cat = CATEGORIES.get(c, {})
            lines.append(f"  {cat.get('emoji', '')} {cat.get('name', c)}: {n}")
        answer(cid, "\n".join(lines))

    elif cmd == "/accounts":
        accounts = load_accounts()
        if not accounts:
            answer(cid, "Нет настроенных аккаунтов.\nИспользуй /login platform для авторизации.")
            return
        lines = ["<b>Аккаунты:</b>\n"]
        for platform, data in accounts.items():
            apps = len(data.get("applications", []))
            lines.append(f"  {platform}: {apps} откликов")
        answer(cid, "\n".join(lines))

    else:
        answer(cid, "Неизвестная команда. /help")

# === Автопарсинг + автоотклик ===

def scheduler():
    while True:
        try:
            log.info("Автопарсинг...")
            ai_tasks = parse_all()
            if ai_tasks:
                for cid in load_chat_ids():
                    msg = f"<b>Новые AI-задачи! ({len(ai_tasks)})</b>\n\n"
                    for i, t in enumerate(ai_tasks[:5], 1):
                        msg += fmt_task(t, i) + "\n\n"
                    answer(cid, msg)

                # Автоотклик
                if AUTO_APPLY:
                    for task in ai_tasks[:3]:
                        if not task.get("applied"):
                            for cid in load_chat_ids():
                                auto_apply_task(task, cid)
        except Exception as e:
            log.error(f"Scheduler: {e}")
        time.sleep(PARSING_INTERVAL * 60)

# === Flask ===

app = Flask(__name__)

@app.route("/")
def index():
    return "Freelance AI Bot v2 is running!", 200

@app.route("/health")
def health():
    return "OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = flask_request.get_json()
        if not data: return "OK", 200
        msg = data.get("message")
        if not msg: return "OK", 200
        save_chat_id(msg["chat"]["id"])
        text = msg.get("text", "")
        if text.startswith("/"):
            threading.Thread(target=handle_command,
                           args=(msg["chat"]["id"], text, msg.get("from", {})),
                           daemon=True).start()
    except Exception as e:
        log.error(f"Webhook: {e}", exc_info=True)
    return "OK", 200

# === Main ===

def main():
    log.info("Starting Freelance AI Bot v2...")
    if not BOT_TOKEN:
        log.error("TELEGRAM_TOKEN not set!"); return

    webhook_url = RENDER_EXTERNAL_URL
    if webhook_url:
        r = api_call("setWebhook", url=f"{webhook_url}/webhook")
        log.info(f"Webhook set: {r}")
    else:
        log.warning("RENDER_EXTERNAL_URL не задан")

    api_call("setMyCommands", commands=[
        {"command": "parse", "description": "Поиск AI-задач"},
        {"command": "tasks", "description": "Показать задачи"},
        {"command": "analyze", "description": "AI-анализ задачи"},
        {"command": "respond", "description": "Сгенерировать отклик"},
        {"command": "auto", "description": "Автоотклик вкл/выкл"},
        {"command": "stats", "description": "Статистика"},
        {"command": "accounts", "description": "Аккаунты"},
        {"command": "help", "description": "Справка"},
    ])

    threading.Thread(target=scheduler, daemon=True).start()
    log.info(f"Автопарсинг каждые {PARSING_INTERVAL} мин | Автоотклик: {AUTO_APPLY}")

    log.info(f"Server on {PORT}...")
    app.run(host="0.0.0.0", port=PORT, debug=False)

if __name__ == "__main__":
    main()
